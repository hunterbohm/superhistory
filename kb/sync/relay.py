"""The app's and the relay's HTTP API, over a small transport interface so tests can swap HTTP for an in-process double.

A transport has one method: request(method, path, body bytes or None, headers dict) -> (status, body bytes).
"""
import base64
import http.client
import json
import re
import ipaddress
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import egress, keys
from .. import ids

# A device registers these with the app as they are; they are public, and shape-checked where they are made.
PUBLIC_KEYS = ("age_recipient", "ssh_key")
# The error codes the app, the relay and Cloud send, and the sign-in flow's own. Any other code is dropped, so an answer
# cannot put text of its choosing into a message.
ERROR_CODES = frozenset("""
    access_denied ai_unavailable authorization_pending bad_answer bad_device bad_offset bad_origin bad_pack_id
    bad_principal client_too_old
    bad_record bad_request bad_signature bad_token coverage engine_down epoch_changed expired_token foreign_prefix
    chain_mismatch group_exists import_started internal invalid_client invalid_grant invalid_request member_exists
    missing_chunks no_group no_plan
    no_scope not_found not_fresh not_member not_offered not_proven parts prefix_taken proof_required quota_day
    quota_total rate_limited revoked
    roster_fork rotation_due
    slot_exists slot_used slow_down
    snapshot_changed stale_epoch too_large unauthorized unknown_address unsupported_grant_type version_changed
""".split())
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# A plain address: it is printed to the terminal, so nothing but these characters.
SUPPORT_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,8}")
DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TOKEN = re.compile(r"[!-~]{8,512}")
USER_CODE = re.compile(r"[A-Z0-9]{8}")
HEX64 = re.compile(r"[0-9a-f]{64}")
MOMENT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?(Z|[+-][0-9]{2}:[0-9]{2})?")


# Every answer the client reads from the relay or the app is checked field by field where it enters, because its values
# reach messages, stored state, request paths and decisions. An answer that fails is refused whole and never quoted.
def _shaped(ok, out):
    if not ok:
        raise RelayError(502, "bad_answer")
    return out


def _count(x):
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def _matches(pattern, x):
    return isinstance(x, str) and pattern.fullmatch(x) is not None


def _blob(x):
    """The bytes of a base64 field, or None when it is not one."""
    if not isinstance(x, str):
        return None
    try:
        return base64.b64decode(x, validate=True)
    except ValueError:
        return None


def _fields(out, *names):
    """`out`, an object holding at least `names`."""
    return _shaped(isinstance(out, dict) and all(n in out for n in names), out)


def _device_keys(d):
    """Both of a device's public keys in their shapes, or neither, as for a device that only pushes to Cloud."""
    if d.get("age_recipient") is None and d.get("ssh_key") is None:
        return True
    return _matches(keys.AGE_RECIPIENT, d.get("age_recipient")) and _matches(keys.SSH_KEY, d.get("ssh_key"))


def _revocation(d):
    """When the device is revoked, the choice the app recorded with it: True to delete what it read, False to keep it,
    or None for a device revoked before choices were recorded. A device not revoked has no choice."""
    clear = d.get("revoked_clear")
    return clear is None if d.get("revoked_on") is None else clear is None or isinstance(clear, bool)


def _chain(d):
    """The last pack the device confirmed it sent: none yet, or its number and hash."""
    c = d.get("chain")
    if not (isinstance(c, dict) and set(c) == {"dseq", "hash"} and _count(c["dseq"])):
        return False
    return c["hash"] is None if c["dseq"] == 0 else _matches(HEX64, c["hash"])


def _enrolled(out):
    """(device_id, token, sync_token, label) from a run that enrolled this computer, each in its shape."""
    out = _fields(out, "device_id", "token", "label", "renewed")
    sync = out.get("sync_token")
    return _shaped(_matches(UUID, out["device_id"]) and _matches(TOKEN, out["token"]) and isinstance(out["label"], str)
                   and isinstance(out["renewed"], bool) and (sync is None or _matches(TOKEN, sync)),
                   (out["device_id"], out["token"], sync, out["label"]))


def _revoked(out):
    """The device ids a run revoked."""
    revoked = _fields(out, "revoked")["revoked"]
    return _shaped(isinstance(revoked, list) and all(_matches(UUID, i) for i in revoked), revoked)


# What a run answers, by the action it ran, and how its answer is read.
RUNS = {"enroll": ("enrolled", _enrolled), "remove": ("revoked", _revoked), "recover": ("revoked", _revoked),
        "bootstrap": ("bootstrapped", lambda out: True)}


def _items(out, name):
    """The list of objects under `name` in `out`."""
    items = _fields(out, name)[name]
    return _shaped(isinstance(items, list) and all(isinstance(i, dict) for i in items), items)


def _pack_list(out):
    packs = _items(out, "packs")
    return _shaped(all(
        _count(p.get("seq")) and _count(p.get("epoch")) and _count(p.get("size"))
        and (p.get("snapshot_id") is None or _matches(UUID, p.get("snapshot_id"))) for p in packs), packs)


class TransportError(Exception):
    """The request did not complete: no answer, or the answer was lost."""


class ProofRequired(Exception):
    """The app signs this computer in again as `device_id` only once it proves it holds that device's private key."""

    def __init__(self, device_id):
        super().__init__(device_id)
        self.device_id = device_id


class RelayError(Exception):
    def __init__(self, status, code, retry_after=None, device_id=None):
        super().__init__(f"{status} {code}")
        self.status, self.code, self.retry_after, self.device_id = status, code, retry_after, device_id


def _proof_for(code, resp):
    """The device a renewal must be signed for, when the app asks for that proof and names one in a device id's shape."""
    if code != "proof_required":
        return None
    try:
        device_id = json.loads(resp).get("device_id")
    except (ValueError, AttributeError):
        return None
    return device_id if isinstance(device_id, str) and UUID.fullmatch(device_id) else None


def _wait(resp):
    """Seconds a 429 asks for, as a whole number from 1 to MAX_WAIT; the default when it does not say so."""
    try:
        wait = json.loads(resp).get("retry_after")
    except (ValueError, AttributeError):
        return DEFAULT_WAIT
    return min(wait, MAX_WAIT) if isinstance(wait, int) and not isinstance(wait, bool) and wait >= 1 else DEFAULT_WAIT


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hands every redirect back unread, before urllib parses the address it names."""

    def _answer(self, req, fp, code, msg, headers):
        return None

    http_error_301 = http_error_302 = http_error_303 = http_error_307 = http_error_308 = _answer


# A whole request, from connecting to the last byte of the answer, ends by this many seconds, whatever the server sends.
# The socket's timeout ends a request only when the server goes quiet, so a server that sends a byte now and then, a
# header or a chunked trailer line at a time, could otherwise hold it for ever. The largest pack, 16 MiB, fits in this
# at 28 KB/s.
REQUEST_DEADLINE = 600.0
_TLS = ssl.create_default_context()
_TLS.set_alpn_protocols(["http/1.1"])


class _Deadline:
    """Ends one request at its deadline. Looking up the server's address and each try to connect get only the time that
    is left. Once connected, a timer shuts the connection down at the deadline, so any read in progress, of the TLS
    handshake, the status line, a header, the body or a chunked trailer, ends then. It shuts down its own copy of the
    connection's socket, which it closes when the request is done, so it never touches a socket that has gone to another
    connection."""

    def __init__(self, seconds):
        self.lock, self.copy, self.done, self.fired = threading.Lock(), None, False, False
        self.end = time.monotonic() + seconds
        self.timer = threading.Timer(seconds, self._end)
        self.timer.daemon = True

    def _left(self):
        left = self.end - time.monotonic()
        if left <= 0:
            raise TimeoutError("past the request's deadline")
        return left

    def _resolve(self, host, port):
        """The server's addresses, looked up on a thread of their own, since the system's lookup takes no timeout. One
        still running at the deadline is left to finish on its own."""
        found = {}

        def look_up():
            try:
                found["addresses"] = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
            except OSError as exc:
                found["error"] = exc
        thread = threading.Thread(target=look_up, daemon=True)
        thread.start()
        thread.join(self._left())
        if "error" in found:
            raise found["error"]
        if "addresses" not in found:
            raise TimeoutError("past the request's deadline")
        return found["addresses"]

    def _connect(self, address, timeout=None, source_address=None):
        """As `socket.create_connection`, with each try to connect given at most the time left."""
        error = OSError("the server has no address")
        for family, kind, proto, _, where in self._resolve(*address):
            sock = None
            try:
                sock = socket.socket(family, kind, proto)  # inside: a family this computer lacks moves on to the next
                sock.settimeout(min(timeout, self._left()) if timeout is not None else self._left())
                if source_address:
                    sock.bind(source_address)
                sock.connect(where)
                sock.settimeout(timeout)
            except OSError as exc:
                if sock is not None:
                    sock.close()
                if isinstance(exc, TimeoutError) and time.monotonic() >= self.end:
                    raise
                error = exc
                continue
            return self._connected(sock)
        raise error

    def _end(self):
        with self.lock:
            if self.done:
                return
            self.fired = True
            if self.copy is not None:
                try:
                    self.copy.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _connected(self, sock):
        with self.lock:
            if self.fired:
                sock.close()
                raise TimeoutError("past the request's deadline")
            self.copy = sock.dup()
        return sock

    def connection(self, cls):
        """`cls`, an HTTP connection class, whose connections this deadline ends."""
        def make(host, **kwargs):
            conn = cls(host, **kwargs)
            conn._create_connection = self._connect
            return conn
        return make

    def __enter__(self):
        self.timer.start()
        return self

    def __exit__(self, *exc):
        with self.lock:
            self.done = True
            self.timer.cancel()
            if self.copy is not None:
                self.copy.close()


class _Http(urllib.request.HTTPHandler):
    def __init__(self, deadline):
        super().__init__()
        self.deadline = deadline

    def http_open(self, req):
        return self.do_open(self.deadline.connection(http.client.HTTPConnection), req)


class _Https(urllib.request.HTTPSHandler):
    def __init__(self, deadline):
        super().__init__(context=_TLS)
        self.deadline = deadline

    def https_open(self, req):
        return self.do_open(self.deadline.connection(http.client.HTTPSConnection), req, context=self._context)


def _opener(deadline):
    """No answer of the app or the relay redirects, so a redirect comes back as the answer it is, never followed."""
    return urllib.request.build_opener(_NoRedirect, _Http(deadline), _Https(deadline))
# The app sends a removed device's confirmed packs a page at a time.
CHAIN_PAGE = 1000
# Above the largest answer any route sends, a 16 MiB pack, so a larger one is refused before it fills memory.
MAX_ANSWER = 32 << 20
# How long a refused request (429) asks the client to wait, when the answer does not say or says something odd, and the
# longest wait the client will take on a server's word.
DEFAULT_WAIT = 60
MAX_WAIT = 3600


def _read(resp):
    body = resp.read(MAX_ANSWER + 1)
    if len(body) > MAX_ANSWER:
        raise ValueError("answer too large")
    return body


def check_address(url):
    """Refuse an app or relay address that would send tokens unencrypted: anything but https,
    except plain http to this computer itself."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        host = None
    if host and (parts.scheme == "https" or parts.scheme == "http" and _loopback(host)):
        return
    raise keys.Unaccepted("Use an https address for the app and the relay. Plain http works only for this computer, "
                          "at localhost or 127.0.0.1.")


def _loopback(host):
    # RFC 6761 keeps localhost and every name under it for this computer, which is where the local proxy's three
    # origins live: localhost, app.localhost and sync.localhost.
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class HttpTransport:
    def __init__(self, base_url, timeout=120, deadline=REQUEST_DEADLINE):
        check_address(base_url)
        self.base, self.timeout, self.deadline = base_url.rstrip("/"), timeout, deadline

    def request(self, method, path, body=None, headers=None):
        """(status, body). A request that gets no readable answer, or not all of it by its deadline, raises
        TransportError, whose text never quotes the server, since Python's own errors can carry a status line or an
        address the server chose."""
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with _Deadline(self.deadline) as deadline:
                try:
                    with _opener(deadline).open(req, timeout=self.timeout) as resp:
                        answer = resp.status, _read(resp)
                except urllib.error.HTTPError as exc:
                    with exc:
                        answer = exc.code, _read(exc)
        except (http.client.HTTPException, ValueError, OSError):
            raise TransportError("The server could not be reached, or its answer could not be read.") from None
        if deadline.fired:
            raise TransportError("The server could not be reached, or its answer could not be read.")
        return answer


def b64e(data):
    return base64.b64encode(data).decode()


def _slot(slot_id):
    return base64.urlsafe_b64encode(slot_id).decode().rstrip("=")


def encode_json(body):
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()


def _call(transport, token, method, path, body=None, raw=None, headers=None):
    hdrs = dict(headers or {})
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    data = raw
    if body is not None:
        data = encode_json(body)
        hdrs["Content-Type"] = "application/json"
    elif raw is not None:
        hdrs["Content-Type"] = "application/octet-stream"
    status, resp = transport.request(method, path, data, hdrs)
    if not 200 <= status < 300:
        try:
            code = json.loads(resp).get("error", "")
        except (ValueError, AttributeError):
            code = ""
        raise RelayError(status, code if isinstance(code, str) and code in ERROR_CODES else "",
                         _wait(resp) if status == 429 else None, _proof_for(code, resp))
    return resp


def _json(resp):
    try:
        return json.loads(resp) if resp else None
    except ValueError:
        raise RelayError(502, "bad_answer") from None


class App:
    def __init__(self, transport):
        self.t = transport

    def _call(self, token, method, path, body=None):
        """Every body the app gets passes the chokepoint first. A device's public keys pass as they are."""
        return _call(self.t, token, method, path, None if body is None else egress.clean(body, passing=PUBLIC_KEYS))

    def support_email(self):
        """The address the app names for its service's support, or None when it names none this client will print."""
        try:
            out = _json(self._call(None, "GET", "/v1/service"))
        except (RelayError, TransportError):
            return None
        value = out.get("support_email") if isinstance(out, dict) else None
        return value if isinstance(value, str) and len(value) <= 254 and SUPPORT_EMAIL.fullmatch(value) else None

    def ask(self, action, token=None):
        """A code for the browser to approve `action`, one account change. A removal or recovery sends this computer's
        app token, so the page names the computer asking and a computer the account removed cannot ask at all."""
        body = {"client_id": "kb-cli", "action": action}
        out = _fields(_json(self._call(token, "POST", "/api/auth/device/code", body)), "device_code", "user_code")
        return _shaped(_matches(TOKEN, out["device_code"]) and _matches(USER_CODE, out["user_code"])
                       and _count(out.get("interval", 5)), out)

    def run(self, device_code, action, proof=None):
        """What the app did once the browser approved the code for `action`, read by its kind, or None while the browser
        has not decided. A code the browser denied, or that expired, raises Unaccepted. A renewal that needs this
        computer's signature raises ProofRequired naming the device; `proof`, {device_id, proof}, then goes with the code."""
        try:
            # The device code is the app's own credential for this one exchange, sent back only to the app, and the mask
            # can read a random code as a secret, so it goes as it is.
            out = _json(_call(self.t, None, "POST", "/v1/actions/run", {"device_code": device_code, **(proof or {})}))
        except RelayError as exc:
            if exc.status == 400 and exc.code == "proof_required" and exc.device_id and proof is None:
                raise ProofRequired(exc.device_id) from None
            if exc.status == 400 and exc.code in ("authorization_pending", "slow_down"):
                return None
            if exc.status == 400 and exc.code in ("access_denied", "expired_token"):
                raise keys.Unaccepted("The browser did not approve it, so nothing changed.") from None
            raise
        done, read = RUNS[action["do"]]
        return read(_shaped(isinstance(out, dict) and out.get("done") == done, out))

    def devices(self, token):
        devices = _items(_json(self._call(token, "GET", "/v1/devices")), "devices")
        return _shaped(all(_matches(UUID, d.get("device_id")) and isinstance(d.get("label"), str)
                           and (d.get("revoked_on") is None or _matches(DAY, d.get("revoked_on"))) and _revocation(d)
                           and _matches(ids.INSTALL_ID, d.get("prefix")) and _device_keys(d) and _chain(d)
                           for d in devices), devices)

    def roster_head(self, token):
        """(version, hash) of the newest roster link a member told the app it trusts, or (0, None)."""
        out = _fields(_json(self._call(token, "GET", "/v1/roster")), "version", "hash")
        ok = set(out) == {"version", "hash"} and _count(out["version"]) and (
            out["hash"] is None if out["version"] == 0 else _matches(HEX64, out["hash"]))
        return _shaped(ok, (out["version"], out["hash"]))

    def roster_points(self, token, upto):
        """{(version, hash): whether a member still stands behind it} for every roster link a device confirmed while
        it was a member, up to `upto`."""
        points = _items(_json(self._call(token, "GET", f"/v1/roster/points?upto={upto}")), "points")
        ok = all(set(p) == {"version", "hash", "active"} and _count(p["version"]) and 1 <= p["version"] <= upto
                 and _matches(HEX64, p["hash"]) and isinstance(p["active"], bool) for p in points)
        return _shaped(ok, {(p["version"], p["hash"]): p["active"] for p in points})

    def confirm_roster(self, token, version, digest):
        """Tell the app this computer trusts roster link `version`, whose hash is `digest`."""
        self._call(token, "PUT", "/v1/roster", {"version": version, "hash": digest})

    def confirm_chain(self, token, dseq, digest):
        """Tell the app the relay holds this computer's pack `dseq`, whose hash is `digest`."""
        self._call(token, "PUT", "/v1/devices/me/chain", {"dseq": dseq, "hash": digest})

    def chain_points(self, token, device_id, after, upto):
        """{dseq: hash} of the packs `device_id` confirmed while it was a member, after `after` and up to `upto`."""
        out = {}
        while after < upto:
            points = _items(_json(self._call(token, "GET", f"/v1/devices/{device_id}/chain?after={after}&upto={upto}")),
                            "points")
            _shaped(all(set(p) == {"dseq", "hash"} and _count(p["dseq"]) and after < p["dseq"] <= upto
                        and _matches(HEX64, p["hash"]) for p in points), None)
            out.update((p["dseq"], p["hash"]) for p in points)
            if len(points) < CHAIN_PAGE:
                break
            after = max(p["dseq"] for p in points)
        return out

    def members(self, token):
        """The ids of the devices the app records as members of the account's sync group."""
        members = _items(_json(self._call(token, "GET", "/v1/members")), "members")
        return _shaped(all(set(m) == {"device_id", "added_by", "added_on"} and _matches(UUID, m["device_id"])
                           and _matches(UUID, m["added_by"]) and _matches(MOMENT, m["added_on"]) for m in members),
                       {m["device_id"] for m in members})

    def add_member(self, token, device_id):
        """Record `device_id`, which this member approved, as a member. Repeating it changes nothing."""
        self._call(token, "POST", "/v1/members", {"device_id": device_id})


class Relay:
    def __init__(self, transport, token):
        self.t, self.token = transport, token

    def _get(self, path):
        return _json(_call(self.t, self.token, "GET", path))

    def _send(self, method, path, body=None, raw=None, headers=None):
        return _json(_call(self.t, self.token, method, path, body, raw, headers))

    def state(self):
        st = _fields(self._get("/sync/state"), "group")
        ok = isinstance(st["group"], bool)
        if ok and st["group"]:
            snap = st.get("snapshot")
            ok = (all(_count(st.get(k)) for k in ("epoch", "roster_version", "head", "pack_bytes"))
                  and isinstance(st.get("rotation_due"), bool) and isinstance(st.get("member"), bool)
                  and (snap is None or isinstance(snap, dict) and _matches(UUID, snap.get("id")) and _count(snap.get("through"))))
        return _shaped(ok, st)

    def create_group(self, roster, envelope, recovery_envelope):
        self._send("POST", "/sync/group", {"roster": b64e(roster), "envelope": b64e(envelope),
                                           "recovery_envelope": b64e(recovery_envelope)})

    def roster_links(self, after):
        links = [(link.get("version"), _blob(link.get("blob"))) for link in _items(self._get(f"/sync/roster?after={after}"), "links")]
        return _shaped(all(_count(v) and b is not None for v, b in links), links)

    def envelope(self):
        out = _fields(self._get("/sync/envelope"), "epoch", "blob")
        blob = _blob(out["blob"])
        return _shaped(_count(out["epoch"]) and blob is not None, (out["epoch"], blob))

    def put_roster(self, expected_version, roster, envelopes, recovery_envelope):
        version = _fields(self._send("PUT", "/sync/roster", {
            "expected_version": expected_version, "roster": b64e(roster),
            "envelopes": [{"device_id": d, "blob": b64e(b)} for d, b in envelopes],
            "recovery_envelope": b64e(recovery_envelope)}), "roster_version")["roster_version"]
        return _shaped(_count(version), version)

    def rotate(self, expected_epoch, expected_version, roster, envelopes, recovery_envelope, recovery=False):
        out = _fields(self._send("POST", "/sync/rotate", {
            "expected_epoch": expected_epoch, "expected_version": expected_version, "roster": b64e(roster),
            "envelopes": [{"device_id": d, "blob": b64e(b)} for d, b in envelopes],
            "recovery_envelope": b64e(recovery_envelope), "recovery": recovery}), "epoch", "roster_version")
        return _shaped(_count(out["epoch"]) and _count(out["roster_version"]), (out["epoch"], out["roster_version"]))

    def recovery(self):
        out = _fields(self._get("/sync/recovery"), "epoch", "roster_version", "blob")
        blob = _blob(out["blob"])
        return _shaped(_count(out["epoch"]) and _count(out["roster_version"]) and blob is not None,
                       (out["epoch"], out["roster_version"], blob))

    def put_recovery(self, expected_version, blob):
        self._send("PUT", "/sync/recovery", {"expected_version": expected_version, "blob": b64e(blob)})

    def delete_all(self):
        self._send("DELETE", "/sync")

    def put_pack(self, pack_id, epoch, blob, snapshot=None):
        head = f"X-{keys.WIRE_NAME.capitalize()}"
        headers = {f"{head}-Epoch": str(epoch)}
        if snapshot:
            headers[f"{head}-Snapshot"] = snapshot
        seq = _fields(self._send("PUT", f"/sync/packs/{pack_id}", raw=blob, headers=headers), "seq")["seq"]
        return _shaped(_count(seq), seq)

    def packs(self, after, limit=200):
        return _pack_list(self._get(f"/sync/packs?after={after}&limit={limit}"))

    def snapshot_parts(self, snapshot_id):
        return _pack_list(self._get(f"/sync/packs?snapshot={snapshot_id}"))

    def pack(self, seq):
        blob = _call(self.t, self.token, "GET", f"/sync/packs/{seq}")
        return _shaped(isinstance(blob, bytes), blob)

    def applied(self, seq):
        self._send("POST", "/sync/applied", {"seq": seq})

    def compact(self, snapshot_id, through, parts, replaces):
        self._send("POST", "/sync/compact", {"snapshot_id": snapshot_id, "through": through, "parts": parts,
                                             "replaces": replaces})

    def open_slot(self, slot_id):
        self._send("POST", "/sync/slots", {"slot_id": b64e(slot_id)})

    def put_join(self, slot_id, blob):
        self._send("PUT", f"/sync/slots/{_slot(slot_id)}/join", raw=blob)

    def put_welcome(self, slot_id, blob):
        self._send("PUT", f"/sync/slots/{_slot(slot_id)}/welcome", raw=blob)

    def slot(self, slot_id):
        out = _fields(self._get(f"/sync/slots/{_slot(slot_id)}"), "join", "welcome")
        blobs = tuple(None if out[k] is None else _blob(out[k]) for k in ("join", "welcome"))
        return _shaped(all(b is not None or out[k] is None for b, k in zip(blobs, ("join", "welcome"))), blobs)

    def delete_slot(self, slot_id):
        self._send("DELETE", f"/sync/slots/{_slot(slot_id)}")
