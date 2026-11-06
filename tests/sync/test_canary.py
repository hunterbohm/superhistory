"""Secrets planted in items never leave through the CLI's output, its logs, the config folder or the relay's own fields.

kb.sqlite is the person's own store, so it is the one place the planted text may sit.
"""
import base64
import hashlib
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from kb.paths import HOME_ENV, Paths
from kb.sync import cli, keys, login, pack, records, roster
from kb.sync.client import SyncClient

from .fake_relay import FakeRelay
from .support import EMAIL, connect, put_item, put_summary, store_path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# The real CLI, with every Python log record at DEBUG written to the file named by the first argument.
LOGGED_CLI = ("import logging, sys; logging.basicConfig(filename=sys.argv.pop(1), level=logging.DEBUG); "
              "from kb.cli import main; sys.exit(main())")


def pick(chars, n):
    return "".join(secrets.choice(chars) for _ in range(n))


def shapes(text):
    """`text` as it would look in UTF-8, UTF-16, hex, or base64 at any of the three byte alignments."""
    raw = text.encode()
    out = {"utf-8": raw, "utf-16-le": text.encode("utf-16-le"), "utf-16-be": text.encode("utf-16-be"),
           "hex": raw.hex().encode(), "HEX": raw.hex().upper().encode()}
    for shift, skip in ((0, 0), (1, 2), (2, 3)):
        b64 = base64.b64encode(b"\0" * shift + raw).rstrip(b"=")[skip:-2]
        out[f"base64+{shift}"] = b64
        out[f"base64url+{shift}"] = b64.replace(b"+", b"-").replace(b"/", b"_")
    return out


class Canaries:
    """Secrets in the shapes kb.mask catches, a made-up phone number for a source_id, and plain markers for the title,
    text, source_id, meta and summary that no mask catches."""

    def __init__(self):
        alnum = string.ascii_letters + string.digits
        self.secrets = {
            "anthropic key": "sk-ant-api03-" + pick(alnum, 40),
            "github token": "ghp_" + pick(alnum, 36),
            "aws key": "AKIA" + pick(string.ascii_uppercase + string.digits, 16),
            "bearer token": pick(alnum, 32),
            "password": "Pw7" + pick(alnum, 13),
            "phone": "+1555" + "01" + pick(string.digits, 5),
        }
        self.markers = {name: f"{name.upper()}-{secrets.token_hex(6)}" for name in ("title", "text", "sid", "meta", "summary")}

    def all(self):
        return {**self.secrets, **{f"{k} marker": v for k, v in self.markers.items()}}

    def plant(self, store, prefix, tag):
        s, m = self.secrets, self.markers
        body = (f"{m['text']} {tag}\nAuthorization: Bearer {s['bearer token']}\npassword={s['password']}\n"
                f"{s['anthropic key']}\n{s['aws key']}\n{s['github token']}\n") + "filler line of the note\n" * 300
        db = connect(store)
        try:
            for sid in (f"{prefix}:people/{s['phone']}/{m['sid']}-{tag}.md", f"{prefix}@laptop:{s['phone']}-{tag}"):
                put_item(db, "notes", sid, "2026-10-01", f"{m['title']} {s['github token']}", body,
                                     {"path": f"/notes/{m['meta']}/{s['aws key']}", "account": s["phone"]})
                digest = records.read_item(db, "notes", sid)["meta"]["hash"]
                put_summary(db, "notes", sid, digest, [("atom", m["summary"], f"{s['anthropic key']} "
                                                                                         f"password={s['password']}")])
        finally:
            db.close()


class CanaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="superhistory-canary-")
        self.fake, self.confirmed_before = FakeRelay(), {}
        self.fake.sign_up(EMAIL)
        self.fake.browser_user = EMAIL
        self.url, self.server = self.fake.serve()
        self.canaries = Canaries()
        self.outputs, self.logs, self.codes = [], [], []

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self, home):
        return os.path.join(self.tmp, home)

    def store(self, home):
        return store_path(self.config(home))

    def argv(self, home, *args):
        log = os.path.join(self.tmp, "logs", f"{len(self.logs)}.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        self.logs.append(log)
        return [sys.executable, "-u", "-c", LOGGED_CLI, log, *args]

    def env(self, home):
        return {**os.environ, "PYTHONPATH": ROOT, HOME_ENV: self.config(home)}

    def run_cli(self, home, *args, stdin="", ok=True):
        proc = subprocess.run(self.argv(home, *args), input=stdin, capture_output=True, text=True, env=self.env(home),
                              timeout=120)
        out = proc.stdout + proc.stderr
        self.outputs.append((f"{home}: {' '.join(args)}", out))
        if ok is not None:
            self.assertEqual(proc.returncode == 0, ok, out)
        return out

    def prefix(self, home):
        with open(os.path.join(self.config(home), "install-id"), encoding="ascii") as fh:
            return fh.read().strip()

    def login(self, home, label):
        self.run_cli(home, "sync", "login", "--app", self.url, "--relay", self.url, "--label", label)

    def recovery_code(self, out):
        code = re.search(r"VRC1(?:-[0-9A-Z]{4}){8}", out).group(0)
        self.codes.append(code)
        return code

    def pair(self, member, joiner):
        adder = subprocess.Popen(self.argv(member, "sync", "add-device"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, env=self.env(member))
        shown = ""
        while "VP1-" not in shown:
            shown += adder.stdout.readline()
        code = re.search(r"VP1[-0-9A-Z]+", shown).group(0)
        self.codes.append(code)
        adder.stdin.write("y\n")
        adder.stdin.flush()
        self.run_cli(joiner, "sync", "join", stdin=code + "\n")
        added = shown + adder.communicate(timeout=120)[0]
        self.outputs.append((f"{member}: sync add-device", added))
        self.assertEqual(adder.returncode, 0, added)

    def forge(self, home, lines):
        """A change pack that `home` signed, continuing its chain, put on the relay. Returns its seq."""
        c = SyncClient.load(Paths.under(self.config(home)), transport=self.fake)
        try:
            dseq, prev = c._signing_head()
            header = {"epoch": c.keyring.newest, "author": c.device_id, "dseq": dseq + 1, "prev": prev,
                      "roster_version": c.roster.version, "roster_hash": c.roster.hash, "snapshot": None}
            plain = pack.build(header, lines, c.paths.device_ssh)
            blob = pack.seal(plain, c.keyring.recipient(c.keyring.newest))
            epoch = c.keyring.newest
        finally:
            c.close()
        group = self.fake.group(EMAIL)
        seq, group.next_seq = group.next_seq, group.next_seq + 1
        group.packs.append({"seq": seq, "pack_id": str(uuid.uuid4()), "epoch": epoch, "blob": blob, "snapshot_id": None,
                            "day": time.strftime("%Y-%m-%d")})
        dev = self.fake.devices[header["author"]]
        if header["dseq"] not in dev["points"]:
            dev["points"][header["dseq"]] = hashlib.sha256(plain).hexdigest()
            self.confirmed_before[seq] = (dev, header["dseq"])
        return seq

    def drop_pack(self, seq):
        """As if the pack never went up, so the app no longer lists it as its author's last."""
        group = self.fake.group(EMAIL)
        group.packs = [p for p in group.packs if p["seq"] != seq]
        if seq in self.confirmed_before:
            dev, dseq = self.confirmed_before.pop(seq)
            dev["points"].pop(dseq)

    def relay_fields(self):
        """Every text and number the fake relay keeps, leaving out the sealed blobs."""
        out = []

        def walk(value):
            if isinstance(value, (bytes, bytearray)):
                return
            if isinstance(value, dict):
                for k, v in value.items():
                    walk(k)
                    walk(v)
            elif isinstance(value, (list, tuple, set)):
                for v in value:
                    walk(v)
            elif hasattr(value, "__dict__"):
                walk(vars(value))
            else:
                out.append(str(value))
        fake = self.fake
        walk([fake.users, fake.devices, fake.codes, fake.groups, fake.slots, fake.log, fake.no_plan])
        return "\n".join(out).encode()

    def config_files(self):
        found = {}
        for home in os.listdir(self.tmp):
            for folder, dirs, files in os.walk(os.path.join(self.tmp, home)):
                if folder == os.path.join(self.tmp, home):
                    dirs[:] = [d for d in dirs if d != "data"]  # the person's own store
                for name in files:
                    path = os.path.join(folder, name)
                    with open(path, "rb") as fh:
                        found[os.path.relpath(path, self.tmp)] = fh.read()
        return found

    def leaks(self):
        haystacks = {f"output {n} of {name}": text.encode() for n, (name, text) in enumerate(self.outputs)}
        for log in self.logs:
            if os.path.exists(log):
                with open(log, "rb") as fh:
                    haystacks[f"log {os.path.basename(log)}"] = fh.read()
        haystacks.update(self.config_files())
        haystacks["the relay's fields"] = self.relay_fields()
        return [(what, encoding, where) for what, text in self.canaries.all().items()
                for encoding, needle in shapes(text).items() for where, hay in haystacks.items() if needle in hay]

    def test_planted_secrets_never_leave_through_output_logs_config_or_relay(self):
        self.login("a", "computer 1")
        self.canaries.plant(self.store("a"), self.prefix("a"), "a")
        self.recovery_code(self.run_cli("a", "sync", "init"))
        self.login("b", "computer 2")
        self.pair("a", "b")
        self.canaries.plant(self.store("b"), self.prefix("b"), "b")
        for home in ("b", "a", "b"):
            self.run_cli(home, "sync")
        self.run_cli("a", "sync", "status")
        self.run_cli("b", "sync", "status")
        self.run_cli("a", "sync", "doctor")

        s, m = self.canaries.secrets, self.canaries.markers
        self.fake.rewrite(lambda meth, path, status, out: (500, {"error": f"{s['github token']} {s['phone']}"})
                          if path.startswith("/sync/state") else (status, out))
        self.assertIn("The relay said 500.", self.run_cli("a", "sync", ok=False))
        self.fake.rewrites.clear()
        self.fake.rewrite(lambda meth, path, status, out: (500, f"password={s['password']} {m['text']}".encode())
                          if path.startswith("/sync/state") else (status, out))
        self.run_cli("a", "sync", "status", ok=False)
        self.fake.rewrites.clear()
        private = f"{self.prefix('a')}:{s['phone']}/{m['sid']}"
        for path, answer in (("/sync/state", lambda out: {**out, "snapshot": {"id": private, "through": 10 ** 9}}),
                             ("/sync/packs?after", lambda out: {"packs": [{"seq": private, "epoch": 1, "snapshot_id": None,
                                                                           "size": 1}]})):
            self.fake.rewrite(lambda meth, p, status, out, path=path, answer=answer: (status, answer(out))
                              if p.startswith(path) and isinstance(out, dict) and out.get("group", True) else (status, out))
            self.assertIn("The relay said 502 bad_answer.", self.run_cli("b", "sync", ok=False))
            self.run_cli("b", "sync", "status", ok=None)
            self.fake.rewrites.clear()
        self.run_cli("b", "sync")

        planted_title = {"ts": "t", "title": f"{m['title']} {s['aws key']}", "meta": {"hash": "h"},
                         "rows": [f"{m['text']} {s['anthropic key']}"]}
        forged = {
            "record outside its author's prefix": [records.to_json(records.Record(
                "item", "notes", f"{self.prefix('b')}:{s['phone']}/{m['sid']}", int(time.time() * 1000), planted_title))],
            "item key has no install prefix": [records.to_json(records.Record(
                "item", "notes", f"people/{s['phone']}/{m['sid']}", int(time.time() * 1000), planted_title))],
            "records are not JSON lines": [f"{s['anthropic key']} {m['text']} password={s['password']}"],
        }
        for reason, lines in forged.items():
            seq = self.forge("a", lines)
            out = self.run_cli("b", "sync", ok=False)
            self.assertIn(reason, out)
            self.assertIn(f"pack {seq}", self.run_cli("b", "sync", "status", ok=False))
            self.drop_pack(seq)
            self.run_cli("b", "sync")

        self.assertNotIn(s["password"], self.run_cli("a", "sync", "status", f"--password={s['password']}", ok=False))
        code = self.codes[0]
        for typed in (s["github token"], code, code.lower().replace("-", " "), f"{self.prefix('a')}:{m['sid']}"):
            with self.subTest(typed=typed):
                self.assertNotIn(typed, self.run_cli("a", "sync", "remove", typed, ok=False),
                                 "an unknown label is not repeated, so a code or source id typed there is not shown")
        for argv in (("sync", "status", code), ("sync", "status", code.lower().replace("-", " ")),
                     ("sync", "status", f"{self.prefix('a')}:{m['sid']}"), ("sync", f"{self.prefix('a')}:{m['sid']}")):
            with self.subTest(argv=argv):
                self.assertNotIn(argv[-1], self.run_cli("a", *argv, ok=False), "argparse never quotes what was typed")

        self.recovery_code(self.run_cli("a", "sync", "recovery-code", "--new"))
        group, b_id = self.fake.group(EMAIL), next(d for d, dev in self.fake.devices.items() if dev["label"] == "computer 2")
        real = group.envelopes[b_id]
        group.envelopes[b_id] = (real[0], f"{s['github token']} {m['summary']}".encode())
        self.assertIn("age refused", self.run_cli("b", "sync", ok=False))
        group.envelopes[b_id] = real
        self.run_cli("b", "sync")

        trusted = group.roster_version
        group.roster[trusted + 1], group.roster_version = group.roster[trusted - 1], trusted + 1
        self.assertIn("roster", self.run_cli("b", "sync", ok=False))
        del group.roster[trusted + 1]
        group.roster_version = trusted
        self.run_cli("b", "sync")

        out = self.run_cli("a", "sync", "remove", "computer 2", "--yes", stdin="n\n")
        self.assertRegex(out, r"computer 2 read \d+ items\. Removing it deletes them from every computer\.")
        self.login("c", "computer 3")
        self.recovery_code(self.run_cli("c", "sync", "recover", stdin=self.codes[-1] + "\n"))
        self.run_cli("c", "sync", "status")
        self.run_cli("c", "sync", "doctor")
        self.assertIn("removed from sync", self.run_cli("a", "sync", ok=False))

        self.assertEqual(self.leaks(), [])
        self.assertNotIn("Traceback", "".join(out for _, out in self.outputs))
        tokens = []
        for home in ("a", "b", "c"):
            with open(os.path.join(self.config(home), "sync", "login.json"), encoding="utf-8") as fh:
                found = re.findall(r'"(?:app|sync)_token": "([^"]+)"', fh.read())
                self.assertEqual(len(found), 2, "the app's token and the relay's")
                tokens.extend(found)
        for name, out in self.outputs:
            for token in tokens:
                self.assertNotIn(token, out, f"a device token in the output of {name}")
            for code in self.codes:
                if code in out:
                    self.assertTrue(name.endswith(("sync init", "add-device", "recovery-code --new", "sync recover")),
                                    f"a code in the output of {name}")


class BoundaryTest(unittest.TestCase):
    """Text shaped like a secret is refused where it would enter, so it is never stored or shown."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="superhistory-boundary-")
        self.fake = FakeRelay()
        self.fake.sign_up(EMAIL)
        self.fake.browser_user = EMAIL
        self.secret = "ghp_" + pick(string.ascii_letters + string.digits, 36)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_login_refuses_a_secret_in_the_label_or_an_address_and_writes_nothing(self):
        home = os.path.join(self.tmp, "home")
        code, pairing = keys.new_recovery_code()[0], keys.new_pairing_code()[0]
        for label, relay_url in ((f"laptop {self.secret}", "https://relay.test"), ("laptop", "http://u:hunter2pw@127.0.0.1:3001"),
                                 ("password=Pw7xYz9q", "https://relay.test"), (code, "https://relay.test"),
                                 (f"laptop {pairing.lower().replace('-', ' ')}", "https://relay.test"),
                                 ("laptop", f"http://127.0.0.1:3001/{code}")):
            with self.subTest(label=label, relay=relay_url), self.assertRaises(ValueError):
                login.login(Paths.under(home), self.fake, "https://app.test", relay_url, label=label, show=lambda uri, code: None,
                            sleep=lambda s: None)
        self.assertFalse(os.path.exists(home))
        self.assertEqual(self.fake.devices, {})

    def test_a_roster_entry_whose_label_holds_a_secret_is_refused(self):
        self.assertTrue(roster.valid_label("computer 2"))
        code, pairing = keys.new_recovery_code()[0], keys.new_pairing_code()[0]
        for label in (f"laptop {self.secret}", code, code.replace("-", ""), pairing.lower(), f"vp1 {pairing[4:]}",
                      "c-aaaaaaaaaaaaa:people/notes.md", "laptop c-aaaaaaaaaaaaa@desk:notes", "call +1555 0100 123",
                      "5550100123"):
            with self.subTest(label=label):
                self.assertFalse(roster.valid_label(label))

    def test_every_form_of_a_code_that_parses_is_refused_as_a_label_and_masked(self):
        body = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
        code = "VRC1-" + "-".join(body[i:i + 4] for i in range(0, 32, 4))
        pairing = keys.new_pairing_code()[0]
        forms = [code, code.lower(), code.replace("-", ""), code.replace("-", " - "), "V R C 1" + code[4:],
                 "vr c1" + code[4:], code[:5] + code[5:].replace("0", "o").replace("1", "l"),
                 code[:5] + code[5:].replace("1", "\u0131"), pairing, pairing.lower().replace("-", "--")]
        for form in forms:
            with self.subTest(form=form):
                read = keys.parse_pairing_code if form.upper().startswith("VP") else keys.parse_recovery_code
                read(form)
                self.assertFalse(roster.valid_label(form))
                self.assertEqual(keys.mask_codes(f"a label {form}, then more words"), "a label [masked], then more words")
        self.assertEqual(keys.mask_codes(f"part of one: {code[:-5]}"), "part of one: [masked]", "most of a code is masked")
        self.assertFalse(roster.valid_label(code[:-5]))

    def test_only_a_code_is_shown_unmasked(self):
        code, _ = keys.new_recovery_code()
        pairing, _ = keys.new_pairing_code()
        for shown in (code, pairing):
            self.assertRegex(shown, cli.CODE)
        for text in (self.secret, f"{code} {self.secret}", "VRC1-" + self.secret):
            with self.subTest(text=text), self.assertRaises(ValueError):
                cli._reveal(text)


if __name__ == "__main__":
    unittest.main()
