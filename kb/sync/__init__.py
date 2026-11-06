"""Superhistory Sync: keeps the knowledge bases of one person's computers in step, end to end encrypted.

This module is imported on every kb command, so it holds only what the parser and the ingest hook need and imports
the client when a command runs. `register` adds the `sync` command, and `after_ingest` is what `kb.ingest.after` calls
once an ingest finishes, when the config has a `[sync]` table.
"""

import argparse
import sys

APP_URL = "https://app.superhistory.dev"
RELAY_URL = "https://sync.superhistory.dev"


class _Parser(argparse.ArgumentParser):
    """Its errors keep the argument's name and the problem, and drop the rest, where argparse quotes what was typed: a
    source_id, a path, a password or a recovery code, which kb's mask does not know. An argument it does not
    recognize is refused here, where the top parser would quote it."""

    def error(self, message):
        self.print_usage(sys.stderr)
        parts = message.split(": ")
        named = ": ".join(parts[:2] if message.startswith("argument ") else parts[:1])
        self.exit(2, f"{self.prog}: error: {named}. `--help` lists what it takes.\n")

    def parse_known_args(self, args=None, namespace=None):
        parsed, extra = super().parse_known_args(args, namespace)
        if extra:
            self.error("unrecognized arguments")
        return parsed, extra


def register(sub):
    """Adds `sync` and its actions to kb's subparsers. With no action it syncs now."""
    default, sub._parser_class = sub._parser_class, _Parser  # add_parser takes no class of its own
    try:
        sy = sub.add_parser("sync", help="keep your computers in step, end to end encrypted; with no action, sync now")
    finally:
        sub._parser_class = default
    sy.set_defaults(fn=run)
    actions = sy.add_subparsers(dest="action", metavar="ACTION")
    lg = actions.add_parser("login", help="sign this computer in to your Superhistory account")
    lg.add_argument("--app", default=APP_URL, help="the app's address")
    lg.add_argument("--relay", default=RELAY_URL, help="the relay's address")
    lg.add_argument("--label", help="the name this computer shows to your others")
    actions.add_parser("init", help="set up Sync from this computer and make the recovery code")
    actions.add_parser("add-device", help="print a pairing code for a new computer and wait for it")
    actions.add_parser("join", help="join your other computers; asks for the pairing code")
    actions.add_parser("status", help="list your computers and what waits to sync")
    actions.add_parser("compact", help="sync, then compact what the relay holds, to make room")
    actions.add_parser("recover", help="set Sync up again from the recovery code; asks for it")
    actions.add_parser("doctor", help="check the programs, keys and settings Sync needs")
    rm = actions.add_parser("remove", help="remove one of your computers from Sync")
    rm.add_argument("label", help="the computer's name, as `status` lists it")
    rm.add_argument("--keep-items", action="store_true", help="leave the items it read on every computer")
    rm.add_argument("--yes", action="store_true", help="delete its items without asking")
    rc = actions.add_parser("recovery-code", help="make a new recovery code")
    rc.add_argument("--new", action="store_true", help="replace the recovery code")


def run(paths, config, args):
    from . import cli
    return cli.run(paths, config, args)


def after_ingest(paths, config, db, log):
    from . import cli
    return cli.after_ingest(paths, config, db, log)
