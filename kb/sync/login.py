import os
import time

from . import keys, relay, roster
from .. import ids
from ..mask import mask

SCOPES = ("sync", "push", "read")


class Paths:
    """Where this computer keeps Sync's files: a `sync/` folder, mode 700, beside the config. `config_file` is what
    `kb.ids.install_id` reads beside, so the install id here is the one kb writes into its item ids, and `db` is
    kb's own database."""

    def __init__(self, paths):
        self.config_file = paths.config_file
        self.db = paths.db
        self.sync = os.path.join(paths.config_file.parent, "sync")
        self.login = os.path.join(self.sync, "login.json")
        self.device_age = os.path.join(self.sync, "device.age")
        self.device_ssh = os.path.join(self.sync, "device.ssh")
        self.keyring = os.path.join(self.sync, "keyring.json")
        self.roster = os.path.join(self.sync, "roster.json")

    def make(self):
        os.makedirs(self.sync, mode=0o700, exist_ok=True)


class NotSignedIn(Exception):
    pass


def signed_in(paths):
    """The saved login. One from before the app and the relay had separate tokens holds a single token the relay has
    seen, which no longer works anywhere, so it is never sent again."""
    saved = keys.load_json(paths.login)
    if not saved:
        raise NotSignedIn("This computer is not signed in. Run `superhistory sync login` first.")
    if "token" in saved or not (saved.get("sync_token") and saved.get("app_token")):
        raise NotSignedIn("This computer signed in with an older version of Sync. Run `superhistory sync login` "
                          "once to replace its token.")
    return saved


def approve(app, app_url, show, action, token=None, sleep=time.sleep, timeout=600, ssh_path=None):
    """What the app did for `action`, one account change, once the person approved it on the app's own `/device` page,
    never an address the app's answer names. The app does the change itself, so this computer never holds more than its
    own device's tokens, and a computer removed on the website has nothing left. When the change signs an existing
    computer in again, the app asks for proof that this one holds its private key, which `ssh_path` signs."""
    code = app.ask(action, token)
    show(app_url.rstrip("/") + "/device", code["user_code"])
    deadline = time.monotonic() + timeout
    proof = None
    while True:
        try:
            done = app.run(code["device_code"], action, proof)
        except relay.ProofRequired as asked:
            if ssh_path is None:
                raise keys.Unaccepted("The app asked this computer to prove its key, and it has none to sign with.") from None
            proof = {"device_id": asked.device_id, "proof": keys.renew_proof(ssh_path, code["device_code"], asked.device_id)}
            done = None
        if done is not None:
            return done
        if time.monotonic() > deadline:
            raise TimeoutError("It was not approved in the browser in time.")
        sleep(code.get("interval", 5))


def login(kb_paths, transport, app_url, relay_url, label=None, show=None, sleep=time.sleep):
    """Asks the browser to sign this computer in under its install id and public keys. The app gives the account's live
    device for them new tokens, which keeps its label and its place in a sync group, or adds a new device, and the page
    refuses a device that registered other keys or none, since packs signed with new keys would be refused by every
    other computer. `app_token` goes only to the app and `sync_token` only to the relay."""
    for url in (app_url, relay_url):
        relay.check_address(url)
        if mask(url) != url or keys.code_spans(url):
            raise keys.Unaccepted("Leave passwords, tokens and codes out of the app and relay addresses.")
    if label is not None and not roster.valid_label(label):
        raise keys.Unaccepted("A label is up to 64 printable characters, with no password or token in it.")
    paths = Paths(kb_paths)
    prefix = ids.install_id(kb_paths)
    paths.make()
    keys.make_device_keys(paths.device_age, paths.device_ssh)
    action = {"do": "enroll", "prefix": prefix, "scopes": list(SCOPES), "age_recipient": keys.device_recipient(paths.device_age),
              "ssh_key": keys.ssh_public(paths.device_ssh), **({} if label is None else {"label": label})}
    show = show or (lambda uri, code: print(f"Open {uri} and enter {code}.", flush=True))
    app = relay.App(transport)
    try:
        device_id, token, sync_token, label = approve(app, app_url, show, action, sleep=sleep, ssh_path=paths.device_ssh)
    except keys.Unaccepted:
        raise keys.Unaccepted("The browser did not approve this sign-in, so nothing changed. If the page said this "
                              "computer is on your account under other keys, remove it on the website, then run `superhistory sync login` "
                              "again to add it as a new computer.") from None
    if not roster.valid_label(label) or sync_token is None:
        raise relay.RelayError(502, "bad_answer")
    # No `token` key: a client from before the split reads that one and sends it to the relay, so an older copy of the
    # client on this computer stops on this login instead of sending the token that opens Cloud.
    saved = {"device_id": device_id, "app_token": token, "sync_token": sync_token, "label": label, "app": app_url,
             "relay": relay_url}
    support = app.support_email()
    if support:
        saved["support"] = support
    paths.make()
    keys.save_json(paths.login, saved)
    return saved
