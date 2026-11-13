"""One fake network for the egress and hostile-server harnesses: the fake app and relay, a record of every byte the
client sends, and a hook that swaps any answer for a hostile one. The real CLI runs in process against it."""
import contextlib
import io
import json
import os
import re
from unittest import mock

from kb import cli
from kb.paths import HOME_ENV
from kb.sync import pack, records, relay

from .fake_relay import FakeRelay
from .support import EMAIL

def route_of(method, path):
    """The route a request hits, with ids, numbers and the query left out, so one hostile answer covers every call."""
    path = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<id>", path.split("?")[0])
    path = re.sub(r"/[A-Za-z0-9_-]{30,}", "/<id>", re.sub(r"/\d+", "/<n>", path))
    return f"{method} {path}"


class Network:
    """Sends each request to the fake app and relay, keeps every request's bytes, and lets
    `hostile(route, status, answer)` replace any answer on its way back."""

    def __init__(self):
        self.relay = FakeRelay()
        self.relay.sign_up(EMAIL)
        self.relay.browser_user = EMAIL
        self.sent, self.read, self.sealed = [], [], []
        self.hostile = None

    def request(self, method, path, body=None, headers=None):
        self.sent.append((method, path, dict(headers or {}), body or b""))
        status, out = self.relay.request(method, path, body, headers)
        route = route_of(method, path)
        self.read.append(route)
        if self.hostile:
            if isinstance(out, bytes):
                try:
                    out = json.loads(out)
                except ValueError:
                    pass
            status, out = self.hostile(route, status, out)
        if out is None:
            return status, b""
        return status, out if isinstance(out, bytes) else json.dumps(out).encode()

    def outbound(self):
        """Every byte the client sent, request by request, and every plaintext it sealed for the relay."""
        for method, path, headers, body in self.sent:
            yield f"{method} {path}", (path + "\n" + json.dumps(headers) + "\n").encode() + body
        for n, plain in enumerate(self.sealed):
            yield f"sealed plaintext {n}", plain

    @contextlib.contextmanager
    def wired(self):
        """Every HttpTransport the CLI or client makes talks to this network, and every plaintext the client seals for
        the relay is kept, a pack's records decompressed: packs, roster links, envelopes and welcomes. Only what is
        sealed to go out is kept, not what is signed and left waiting."""
        seal, signed, tagged = pack.seal, pack.seal_signed, pack.seal_tagged

        def keep_seal(plain, *args):
            parsed = pack.parse(plain)
            self.sealed.append(pack.canonical(parsed.header) + b"\n" + b"\n".join(
                records.to_json(r).encode() for r in parsed.records))
            return seal(plain, *args)

        def keep_signed(magic, obj, *args):
            self.sealed.append(pack.canonical(obj))
            return signed(magic, obj, *args)

        def keep_tagged(tag_key, recipient, obj, *args, **kwargs):
            self.sealed.append(pack.canonical(obj))
            return tagged(tag_key, recipient, obj, *args, **kwargs)

        with mock.patch.object(relay, "HttpTransport", lambda base, timeout=120: self), \
                mock.patch.object(pack, "seal", keep_seal), mock.patch.object(pack, "seal_signed", keep_signed), \
                mock.patch.object(pack, "seal_tagged", keep_tagged):
            yield


def run_cli(home, *argv, stdin="", answers=("y",)):
    """(exit code, output) of the real command, run in process with kb's home in `home`: `argv` is what follows
    `superhistory`."""
    out, replies = io.StringIO(), list(answers)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
            mock.patch.dict(os.environ, {HOME_ENV: home}), mock.patch("sys.stdin", io.StringIO(stdin)), \
            mock.patch("builtins.input", lambda prompt="": replies.pop(0) if replies else ""):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue()
