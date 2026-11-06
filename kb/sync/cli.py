import getpass
import re
import sys

from . import keys, login, relay
from .client import SYNC_LOCK_WAIT, missing_notice

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
