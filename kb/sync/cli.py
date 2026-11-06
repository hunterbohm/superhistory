import getpass
import os
import re
import sys
import traceback

from . import keys, login, relay
from .client import HOOK_LOCK_WAIT, SYNC_LOCK_WAIT, SyncClient, SyncError, missing_notice
from .. import config as config_mod, store
from ..agents import write as write_file

# What a sync says when the relay refuses a pack for a storage limit. The pack stays in the outbox, so nothing is lost.
QUOTA_DAY = ("Your Superhistory Sync storage allowance for today is used up. Nothing is lost: your changes stay on this "
             "computer and go up on the first sync after midnight UTC.")
QUOTA_TOTAL = ("Your Superhistory Sync storage is full. Nothing is lost: your changes stay on this computer and go up when "
               "there is room. To make room, run `superhistory sync compact` on another of your computers where "
               "`superhistory sync status` shows 0 records waiting: it compacts what the relay holds. If every computer "
               "has records waiting, {who}.")
CODE = re.compile(r"V(?:RC|P)1(?:-[0-9A-Z]{1,4})+")


def quota_total(paths):
    """The full-account line, with the support address the app named at login, read from this computer's login."""
    saved = keys.load_json(login.Paths(paths).login) or {}
    support = saved.get("support")
    who = f"write to {support}" if isinstance(support, str) and relay.SUPPORT_EMAIL.fullmatch(support) else (
        "contact Superhistory support")
    return QUOTA_TOTAL.format(who=who)


def _unmasked(text):
    """Writes past kb's masked output. Only `_reveal` uses it."""
    sys.stdout.flush()
    out = getattr(sys.stdout, "_stream", sys.stdout)
    out.write(text)
    out.flush()


def _reveal(code):
    """Shows a recovery or pairing code unmasked, as the design shows each one once."""
    if not CODE.fullmatch(code):
        raise keys.Unaccepted("Only a recovery or pairing code is shown unmasked.")
    _unmasked(f"  {code}\n")


def _ask_secret(prompt):
    """Codes are typed at a prompt, so they stay out of shell history."""
    return getpass.getpass(prompt) if sys.stdin.isatty() else input(prompt)


def _show_code(uri, code):
    print(f"Open {uri} and enter {code}.", flush=True)


def cmd_login(paths, args):
    saved = login.login(paths, relay.HttpTransport(args.app), args.app, args.relay, label=args.label, show=_show_code)
    print(f'Signed in. This computer is "{saved["label"]}".')


def cmd_init(client, args):
    code, report = client.init(_show_code)
    if code is None:
        print(f"Sync is already set up here. Sent {report.sent} records.")
        return
    print("Made keys for this computer.\n")
    print("Recovery code. Save it in your password manager. Without it and without")
    print("your computers, nobody can get your synced data back, including us.")
    _reveal(code)
    print()
    print(f"Sent {report.sent} records. The relay stores them locked.")


def cmd_add_device(client, args):
    pairing = client.add_device()
    print("On the new computer, run `superhistory sync join` and enter this code:")
    _reveal(pairing.code)
    print("The code works once, for 10 minutes. Waiting...", flush=True)

    def confirm(label, words):
        return input(f'"{label}" wants to join. Its words: {words}. Add it? [y/N] ').strip().lower() == "y"

    print(f'"{client.approve(pairing, confirm)}" joined.')


def _skipped(report):
    if report.skipped:
        print(f"Skipped {report.skipped} records from other computers whose names look like they hold a secret.")
    for label in report.missing:
        print(missing_notice(label), file=sys.stderr)


def cmd_join(client, args):
    words = client.join(_ask_secret("Pairing code: "))
    print(f"Check that the other computer shows the same words: {words}", flush=True)
    report = client.finish_join()
    print(f"Joined. Received {report.received} packs.")
    _skipped(report)


def cmd_sync(client, args):
    report = client.run_once(wait=SYNC_LOCK_WAIT)
    if report.busy:
        print("Another sync is running on this store. Try again when it finishes.", file=sys.stderr)
        return 1
    print(f"Received {report.received} packs, sent {report.sent} records." + (" Compacted." if report.compacted else ""))
    _skipped(report)
    if report.stalled:
        print(f"Sync stopped at {report.stalled}.", file=sys.stderr)
        return 1


def cmd_compact(client, args):
    """Sync, then compact, due or not: the way to make room when the account's storage is full. The sync sends this
    computer's changes first, since a compaction waits until none is left to send."""
    report = client.run_once(wait=SYNC_LOCK_WAIT)
    if report.busy:
        print("Another sync is running on this store. Try again when it finishes.", file=sys.stderr)
        return 1
    if report.compacted or (not report.stalled and client.compact(force=True)):
        print("Compacted.")
        return 0
    print("Not compacted: this computer still has changes to send or packs to take in. Run `superhistory sync`, then try again.")
    return 1


def cmd_status(client, args):
    st = client.status()
    for c in st["computers"]:
        mark = " (this computer)" if c["this"] else " (removed)" if c["removed"] else " (revoked)" if c["revoked"] else ""
        print(f"  {c['label']}{mark}")
    print(f"{st['behind']} packs behind, {st['waiting']} records waiting to send.")
    for problem in st["problems"]:
        print(f"Problem: {problem}")
    return 1 if st["problems"] else 0


def _answer(prompt):
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return ""


def cmd_remove(client, args):
    clear = not args.keep_items
    count = client.items_read_by(args.label)
    items = f"{count} item{'' if count == 1 else 's'}"
    warning = f"{args.label} read {items}. Removing it deletes them from every computer."
    if not clear:
        print(f"{args.label} read {items}. They stay on every computer.")
    elif args.yes:
        print(warning)
    elif _answer(warning + " Remove it? [y/N] ") not in ("y", "yes"):
        print("Nothing was removed. To remove it and keep its items, add --keep-items.")
        return 1
    removal = client.remove(args.label, _show_code, clear=clear)
    print(f"Removed {args.label}. It can no longer reach the relay, and it cannot read")
    print("anything synced from now on. It keeps what it already downloaded.")
    if removal.cleared:
        print("Its items are deleted here, and each other computer deletes them when it next syncs.")
    if removal.recovery_code_due and _answer(
            f"{args.label} still knows your recovery code. Make a new one now? [Y/n] ") != "n":
        code = client.new_recovery_code()
        print("New recovery code:")
        _reveal(code)


def cmd_recover(client, args):
    secret = _ask_secret("Recovery code: ")
    code = client.recover(secret, _show_code)
    print("Recovered. This is now the only computer in the group.")
    print("New recovery code. Save it; the old one no longer opens new data:")
    _reveal(code)


def cmd_recovery_code(client, args):
    if not args.new:
        print("Run `superhistory sync recovery-code --new` to replace the recovery code.")
        return 1
    code = client.new_recovery_code()
    print("New recovery code. The old one opens only data synced before now:")
    _reveal(code)


def cmd_doctor(client, args):
    failed = 0
    for name, ok, detail in client.doctor():
        print(f"{'ok  ' if ok else 'FAIL'}  {name}: {detail}")
        failed += not ok
    return 1 if failed else 0


SYNC = {"init": cmd_init, "add-device": cmd_add_device, "join": cmd_join, "status": cmd_status, "compact": cmd_compact,
        "remove": cmd_remove, "recover": cmd_recover, "recovery-code": cmd_recovery_code, "doctor": cmd_doctor}
# The actions after which this computer is in a sync group, so kb syncs it after every ingest.
JOINS = ("init", "join", "recover")


def ensure_sync_table(paths):
    """Adds an empty [sync] table to the config when it has none, which is what makes kb sync after every ingest, and
    says so. Written as `kb setup` writes the config. A config that would no longer load with it, such as one with a
    GitHub source that does not name its account, is left as it is, and the problem is shown."""
    try:
        with open(paths.config_file, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""
    if "sync" in config_mod.parse(text).extra:
        return
    text = text + ("" if not text or text.endswith("\n") else "\n") + ("\n" if text else "") + "[sync]\n"
    try:
        config_mod.parse(text)
    except config_mod.ConfigError as exc:
        print(f"Sync is set up, but {paths.config_file} would have problems with a [sync] table, so it is unchanged:")
        for problem in exc.problems:
            print(f"  {problem}")
        print("Fix those, then add a line `[sync]` to the file, so `superhistory ingest` syncs too.")
        return
    write_file(str(paths.config_file), text)
    print(f"Added [sync] to {paths.config_file}, so `superhistory ingest` syncs too.")


def run(paths, config, args):
    try:
        if args.action == "login":
            return cmd_login(paths, args) or 0
        client = SyncClient.load(paths)
        try:
            done = SYNC.get(args.action, cmd_sync)(client, args) or 0
        finally:
            client.close()
        if not done and args.action in JOINS:
            ensure_sync_table(paths)
        return done
    except relay.RelayError as exc:
        said = f"The relay said {exc.status} {exc.code}." if exc.code else f"The relay said {exc.status}."
        message = {"revoked": "This computer was removed from sync.", "no_plan": "This account has no Sync plan.",
                   "unauthorized": "Sign in again with `superhistory sync login`.",
                   "quota_day": QUOTA_DAY}.get(exc.code, said)
        if exc.code == "quota_total":
            message = quota_total(paths)
        if exc.status == 429:
            message = f"The relay is busy. Try again in {exc.retry_after} seconds."
        print(message, file=sys.stderr)
    except store.NeedsRebuild as exc:
        print(str(exc), file=sys.stderr)
    except (SyncError, keys.Refused, keys.ToolError, keys.Unaccepted, relay.TransportError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)  # each of these carries only text written here
    except OSError as exc:  # its own text can quote a file's name, so only the system's words for what went wrong
        print(f"A file could not be read or written: {exc.strerror or type(exc).__name__}.", file=sys.stderr)
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
    except Exception as exc:  # its text can quote an item, so name only the kind and the line
        where = traceback.extract_tb(exc.__traceback__)[-1]
        print(f"Stopped by an unexpected {type(exc).__name__} at {os.path.basename(where.filename)} line {where.lineno}.",
              file=sys.stderr)
    return 1


def after_ingest(paths, config, db, log):
    """One sync after an ingest. Raises SyncError, with a message for a person, when this computer is not signed in or
    not in a sync group, or when the sync stopped; kb records that as the `sync` health unit. Another sync running on
    this store is not a failure: this run leaves the work to the next."""
    client = SyncClient.load(paths)
    try:
        report = client.run_once(wait=HOOK_LOCK_WAIT)
    finally:
        client.close()
    if report.stalled:
        raise SyncError(f"Sync stopped at {report.stalled}.")
    log.info("sync: received %d packs, sent %d records%s", report.received, report.sent,
             ", skipped %d" % report.skipped if report.skipped else "")
