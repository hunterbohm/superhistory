"""An in-process double of the app's sign-in and device routes and the relay, following docs/api.md.

Nothing on a computer holds an account session. A computer asks for a code bound to one account change, the person
approves or denies it, and running the approved code does exactly that change once. With `browser_user` set, the person
decides each code as soon as it is asked for: they approve what the app can do and deny what the page would refuse, or
deny everything while `browser_denies` is set.

Tests act as a malicious relay by changing `groups[user]` directly, or with `before` and `after` hooks that run around
each request and may raise TransportError (a dropped request, or a lost response after the work was done).
"""
import base64
import hashlib
import http.server
import json
import re
import secrets
import threading
import time
import urllib.parse
import uuid

from kb.sync import keys
from kb.sync.relay import TransportError

UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
PREFIX = re.compile(r"c-[a-z2-7]{13}")
FRESH = 600
CODE_LIFE, APPROVED_LIFE = 600, 120
SCOPES = {"sync", "push", "read"}
MAX_BODY = 16 << 20


class Reply(Exception):
    def __init__(self, status, error, **extra):
        super().__init__(error)
        self.status, self.error, self.extra = status, error, extra


class Group:
    def __init__(self, roster, envelope_owner, envelope, recovery):
        self.epoch = 1
        self.roster_version = 1
        self.roster = {1: roster}
        self.envelopes = {envelope_owner: (1, envelope)}
        self.recovery = recovery
        self.next_seq = 1
        self.packs = []  # dicts: seq, pack_id, epoch, snapshot_id, blob, day
        self.snapshot = None  # (id, through)
        self.applied = {}


class Request:
    def __init__(self, method, path, query, body, headers, match):
        self.method, self.path, self.query, self.body, self.headers, self.match = method, path, query, body, headers, match
        self.user = self.device = None

    def json(self):
        try:
            return json.loads(self.body or b"null")
        except ValueError:
            raise Reply(400, "bad_json") from None


def _b64(data):
    return base64.b64encode(data).decode() if data is not None else None


def _unb64(text):
    try:
        return base64.b64decode(text, validate=True)
    except (TypeError, ValueError):
        raise Reply(400, "bad_base64") from None


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _valid_action(action):
    """Whether `action` is one the app takes: enroll a computer, remove one, or recover. A device that syncs registers
    both public keys, and a device registers both or neither."""
    if not isinstance(action, dict):
        return False
    if action.get("do") in ("recover", "bootstrap"):
        return True
    if action.get("do") == "remove":
        return (isinstance(action.get("device_id"), str) and UUID4.fullmatch(action["device_id"]) is not None
                and isinstance(action.get("clear"), bool))
    label, prefix, scopes = action.get("label"), action.get("prefix"), action.get("scopes")
    if action.get("do") != "enroll" or not (isinstance(prefix, str) and PREFIX.fullmatch(prefix)):
        return False
    if label is not None and not (isinstance(label, str) and 1 <= len(label) <= 64 and label.isprintable()):
        return False
    if not (isinstance(scopes, list) and all(isinstance(x, str) for x in scopes) and set(scopes) <= SCOPES):
        return False
    age_recipient, ssh_key = action.get("age_recipient"), action.get("ssh_key")
    has_keys = isinstance(age_recipient, str) and keys.AGE_RECIPIENT.fullmatch(age_recipient) and isinstance(
        ssh_key, str) and keys.SSH_KEY.fullmatch(ssh_key)
    if (age_recipient, ssh_key) != (None, None) and not has_keys:
        return False
    return bool(has_keys) or "sync" not in scopes


class FakeRelay:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.users = {}  # user_id -> email
        # device_id -> dict(user, label, prefix, scopes, created, revoked, token_hash, sync_token_hash). The app
        # routes find a device only by token_hash and the relay routes only by sync_token_hash, as the real views do.
        self.devices = {}
        self.members = {}  # user_id -> {device_id: (added_by, added_on)}: the app's record of the sync group
        # user_id -> {(device_id, version): hash}: every roster link a device confirmed while it was a member
        self.roster_points = {}
        # device_code -> dict(user_code, user, status, asked, approved, expires). `asked` is the request as the app keeps
        # it, a removal's asking device included; `approved` is what the person's approval resolved it to.
        self.codes = {}
        self.asks = []  # (action as the app received it, the asking device or None), for every code asked for
        self.issued = []
        self.groups = {}  # user_id -> Group
        self.slots = {}  # (user_id, slot_id bytes) -> dict(opened_by, expires, join, welcome)
        self.no_plan = set()
        self.refuse_packs = None  # a storage-limit code (`quota_day`, `quota_total`) every new pack upload gets as a 507
        self.browser_user = None
        self.browser_denies = False
        self.before_hooks = []
        self.after_hooks = []
        self.rewrites = []
        self.lock = threading.RLock()
        self.log = []

    def sign_up(self, email):
        user = str(uuid.uuid4())
        self.users[user] = email
        return user

    def user_of(self, email):
        return next(u for u, e in self.users.items() if e == email)

    def group(self, email):
        return self.groups[self.user_of(email)]

    def _code_by_user_code(self, user_code):
        for code in self.codes.values():
            if code["user_code"] == user_code:
                return code
        raise KeyError(user_code)

    def approve(self, user_code, email):
        """The person approves the code on the page: what it asks, resolved against their account, or a refusal."""
        code = self._code_by_user_code(user_code)
        code["user"] = self.user_of(email)
        resolved = self._resolve(code["user"], code["asked"])
        if "refused" in resolved:
            return resolved
        code.update(status="approved", approved=resolved, expires=min(code["expires"], self.clock() + APPROVED_LIFE))
        return resolved

    def deny(self, user_code, email):
        code = self._code_by_user_code(user_code)
        code.update(user=self.user_of(email), status="denied")

    def remove(self, device_id, clear=False):
        """The Account page's Remove button, with the choice to keep or delete what the computer read."""
        self.revoke(device_id, clear)

    def before(self, fn):
        self.before_hooks.append(fn)

    def after(self, fn):
        self.after_hooks.append(fn)

    def rewrite(self, fn):
        """fn(method, path, status, out) -> (status, out), to change an answer on its way out."""
        self.rewrites.append(fn)

    def is_member(self, device_id):
        """As the app's `member` view: recorded in its account's sync group and not revoked."""
        dev = self.devices[device_id]
        return device_id in self.members.get(dev["user"], {}) and not dev["revoked"] and "sync" in dev["scopes"]

    def head(self, user):
        """The account's roster head: the newest link a member confirmed, (0, None) before the first."""
        active = [(v, h) for (d, v), h in self.roster_points.get(user, {}).items() if self.is_member(d)]
        return max(active, key=lambda point: point[0], default=(0, None))

    def vouch_roster(self, device_id, version, digest):
        """Record that `device_id` confirmed roster link `version`, as the app would after its own confirmation."""
        self.roster_points.setdefault(self.devices[device_id]["user"], {})[(device_id, version)] = digest

    def chain(self, device_id):
        """The newest of its own packs `device_id` confirmed, (0, None) before the first."""
        return max(self.devices[device_id]["points"].items(), default=(0, None))

    def device_token(self, device_id):
        """A sync token for an existing device, as if the relay were working with whoever holds it."""
        token = secrets.token_urlsafe(32)
        self.devices[device_id]["sync_token_hash"] = _digest(token)
        return token

    def request(self, method, path, body=None, headers=None):
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        for hook in list(self.before_hooks):
            hook(method, path, headers)
        with self.lock:
            status, out = self._handle(method, path, body, headers)
            for fn in list(self.rewrites):
                status, out = fn(method, path, status, out)
        self.log.append((method, path.split("?")[0], status))
        for hook in list(self.after_hooks):
            hook(method, path, status)
        if out is None:
            return status, b""
        return status, out if isinstance(out, bytes) else json.dumps(out).encode()

    def _handle(self, method, path, body, headers):
        parsed = urllib.parse.urlsplit(path)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        for verb, pattern, auth, handler in ROUTES:
            match = re.fullmatch(pattern, parsed.path)
            if verb == method and match:
                req = Request(method, parsed.path, query, body, headers, match)
                try:
                    if body is not None and len(body) > MAX_BODY:
                        raise Reply(413, "too_large")
                    self._auth(req, auth)
                    return handler(self, req)
                except Reply as r:
                    return r.status, {"error": r.error, **r.extra}
        return 404, {"error": "not_found"}

    def _auth(self, req, auth):
        """`app` finds a device by its device token, as the app's `principal` view does, and `sync` by its sync token, as
        the relay's does. Neither takes the other's token."""
        if auth == "none":
            return
        token = req.headers.get("authorization", "").removeprefix("Bearer ")
        field = "token_hash" if auth == "app" else "sync_token_hash"
        digest = _digest(token)
        device_id = next((d for d, dev in self.devices.items() if dev[field] == digest), None)
        if device_id is None:
            raise Reply(401, "unauthorized")
        dev = self.devices[device_id]
        if dev["revoked"]:
            raise Reply(403, "revoked")
        req.user, req.device = dev["user"], device_id
        if auth == "app":
            return
        if "sync" not in dev["scopes"] or dev["user"] in self.no_plan:
            raise Reply(403, "no_plan")

    def _group(self, req):
        group = self.groups.get(req.user)
        if group is None:
            raise Reply(404, "no_group")
        return group

    def _member(self, req, group):
        return req.device in group.envelopes

    def _fresh(self, req):
        dev = self.devices[req.device]
        return self.clock() - max(dev["created"], dev.get("refreshed", dev["created"])) < FRESH

    def _require(self, req, group, rule):
        member, fresh = self._member(req, group), self._fresh(req)
        if rule == "member" and not member:
            raise Reply(403, "not_member")
        if rule == "member_or_fresh" and not (member or fresh):
            raise Reply(403, "not_member")
        if rule == "member_and_fresh" and not member:
            raise Reply(403, "not_member")
        if rule == "member_and_fresh" and not fresh:
            raise Reply(403, "not_fresh")

    def _members(self, group):
        return {d for d in group.envelopes if not self.devices[d]["revoked"]}

    def _rotation_due(self, group):
        return any(self.devices[d]["revoked"] for d in group.envelopes)

    def _slot(self, req):
        slot_id = base64.urlsafe_b64decode(req.match.group(1) + "=" * (-len(req.match.group(1)) % 4))
        slot = self.slots.get((req.user, slot_id))
        if slot is None or slot["expires"] < self.clock():
            raise Reply(404, "not_found")
        return slot_id, slot

    def device_code(self, req):
        """`POST /api/auth/device/code`: a code for one action, as the app checks it. A removal or recovery is asked with
        a live device token of a computer that registered keys, and the app keeps that device as the one asking."""
        body = req.json() or {}
        if not isinstance(body, dict) or body.get("client_id") != "kb-cli":
            raise Reply(400, "invalid_client")
        action = body.get("action")
        if "user_id" in body or "scope" in body or not _valid_action(action):
            raise Reply(400, "invalid_request")
        asked = dict(action)
        if action["do"] in ("remove", "recover", "bootstrap"):
            self._auth(req, "app")
            if self.devices[req.device]["age_recipient"] is None:
                raise Reply(403, "no_scope")
            asked["by"] = req.device
        self.asks.append((action, req.device))
        code, user_code = secrets.token_urlsafe(30), secrets.token_hex(4).upper()
        self.codes[code] = {"user_code": user_code, "user": None, "status": "pending", "asked": asked, "approved": None,
                            "expires": self.clock() + CODE_LIFE}
        if self.browser_user:
            if self.browser_denies or "refused" in self.approve(user_code, self.browser_user):
                self.deny(user_code, self.browser_user)
        return 200, {"device_code": code, "user_code": user_code, "verification_uri": "http://127.0.0.1:3000/device",
                     "expires_in": CODE_LIFE, "interval": 1}

    def _resolve(self, user, asked):
        """What approving `asked` does for `user`'s account, or why the page refuses it, as the app resolves it."""
        mine = {d: dev for d, dev in self.devices.items() if dev["user"] == user}
        live = {d: dev for d, dev in mine.items() if not dev["revoked"]}
        if asked["do"] == "enroll":
            same = next((d for d, dev in live.items() if dev["prefix"] == asked["prefix"]), None)
            if same is None:
                taken = {dev["label"] for dev in mine.values()}
                label = asked.get("label") or next(f"computer {n}" for n in range(1, len(taken) + 2) if f"computer {n}" not in taken)
                return {**asked, "do": "add", "label": label}
            dev = live[same]
            if dev["age_recipient"] is not None and (dev["age_recipient"], dev["ssh_key"]) == (
                    asked.get("age_recipient"), asked.get("ssh_key")):
                return {"do": "renew", "device_id": same}
            return {"refused": "keys_differ", "label": dev["label"]}
        if asked["by"] not in live or asked["do"] == "remove" and asked["device_id"] not in mine:
            return {"refused": "not_on_account"}
        return asked

    def run_route(self, req):
        """`POST /v1/actions/run`: RFC 8628's polling answers until the person decides, then the approved action, once."""
        device_code = (req.json() or {}).get("device_code")
        code = self.codes.get(device_code) if isinstance(device_code, str) else None
        if code is None:
            raise Reply(400, "invalid_grant")
        if code["expires"] < self.clock():
            del self.codes[device_code]
            raise Reply(400, "expired_token")
        if code["status"] == "pending":
            raise Reply(400, "authorization_pending")
        approved = code.get("approved") or {}
        if code["status"] == "approved" and approved.get("do") == "renew":
            # As the app: the terminal proves it holds the renewed device's private key, with a signature in the
            # renewal's namespace over this code and that device's id, before the code is used up.
            body, device_id = req.json() or {}, approved["device_id"]
            dev = self.devices.get(device_id, {})
            key = None if dev.get("revoked") else dev.get("ssh_key")
            if key and (not body.get("proof") or not body.get("device_id")):
                raise Reply(400, "proof_required", device_id=device_id)
            message = f"{device_code}\n{device_id}\n".encode()
            if key and body["device_id"] != device_id or key and not keys.verify(
                    device_id, key, keys.RENEW_NAMESPACE, message, body["proof"].encode()):
                raise Reply(403, "not_proven")
        del self.codes[device_code]
        if code["status"] == "denied":
            raise Reply(400, "access_denied")
        return 200, self._perform(code["user"], code["approved"])

    def _perform(self, user, approved):
        live = lambda d: d in self.devices and self.devices[d]["user"] == user and not self.devices[d]["revoked"]
        if approved["do"] == "add":
            if any(dev["user"] == user and dev["prefix"] == approved["prefix"] and not dev["revoked"]
                   for dev in self.devices.values()):
                raise Reply(409, "prefix_taken")
            device_id = str(uuid.uuid4())
            self.devices[device_id] = {"user": user, "label": approved["label"], "prefix": approved["prefix"],
                                       "scopes": list(dict.fromkeys(approved["scopes"])),
                                       "age_recipient": approved.get("age_recipient"), "ssh_key": approved.get("ssh_key"),
                                       "created": self.clock(), "revoked": None, "revoked_clear": None, "points": {}}
            return {"done": "enrolled", "renewed": False, "device_id": device_id, "label": approved["label"],
                    **self._new_tokens(device_id)}
        if approved["do"] == "renew":
            if not live(approved["device_id"]):
                raise Reply(403, "revoked")
            dev = self.devices[approved["device_id"]]
            return {"done": "enrolled", "renewed": True, "device_id": approved["device_id"], "label": dev["label"],
                    **self._new_tokens(approved["device_id"])}
        if not live(approved["by"]):
            raise Reply(403, "revoked")
        if approved["do"] == "bootstrap":
            self._bootstrap(user, approved["by"])
            self.devices[approved["by"]]["refreshed"] = self.clock()  # its ten minutes again, as the app gives it
            return {"done": "bootstrapped"}
        targets = [approved["device_id"]] if approved["do"] == "remove" else [
            d for d, dev in self.devices.items() if dev["user"] == user and d != approved["by"] and not dev["revoked"]]
        clear = approved["clear"] if approved["do"] == "remove" else False
        for d in targets:
            self.revoke(d, clear)
        if approved["do"] == "recover":
            self._bootstrap(user, approved["by"])
            self.devices[approved["by"]]["refreshed"] = self.clock()
        return {"done": "revoked", "revoked": sorted(targets)}

    def _new_tokens(self, device_id):
        dev, token, sync_token = self.devices[device_id], secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        dev["token_hash"] = _digest(token)
        dev["sync_token_hash"] = _digest(sync_token) if "sync" in dev["scopes"] else None
        return {"token": token, "sync_token": sync_token} if "sync" in dev["scopes"] else {"token": token}

    def list_devices(self, req):
        day = lambda t: time.strftime("%Y-%m-%d", time.gmtime(t)) if t else None
        return 200, {"devices": [{"device_id": d, "label": dev["label"], "prefix": dev["prefix"],
                                  "age_recipient": dev["age_recipient"], "ssh_key": dev["ssh_key"],
                                  "created_on": day(dev["created"]), "revoked_on": day(dev["revoked"]),
                                  "revoked_clear": dev["revoked_clear"],
                                  "chain": dict(zip(("dseq", "hash"), self.chain(d)))}
                                 for d, dev in self.devices.items() if dev["user"] == req.user]}

    def _confirming(self, req, fields):
        """The device token's device, and the body's two fields, as the app parses a confirmation."""
        body, dev = req.json() or {}, self.devices[req.device]
        number, digest = body.get(fields[0]), body.get(fields[1])
        if not (isinstance(number, int) and number >= 1 and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise Reply(400, "bad_request")
        if "sync" not in dev["scopes"]:
            raise Reply(403, "no_scope")
        return dev, number, digest

    def confirm_chain(self, req):
        """As the app: a member's own device token records which of its packs the relay holds, one hash per pack
        number. Any device may repeat one or name an older pack, which records nothing."""
        dev, dseq, digest = self._confirming(req, ("dseq", "hash"))
        if dseq in dev["points"]:
            if dev["points"][dseq] != digest:
                raise Reply(409, "chain_mismatch")
            return 204, None
        if not self.is_member(req.device):
            if self.chain(req.device)[0] >= dseq:
                return 204, None
            raise Reply(403, "not_member")
        dev["points"][dseq] = digest
        return 204, None

    def chain_points(self, req):
        dev = self.devices.get(req.match.group(1))
        if dev is None or dev["user"] != req.user:
            return 200, {"points": []}
        after, upto = int(req.query.get("after", 0)), int(req.query["upto"])
        return 200, {"points": [{"dseq": d, "hash": h} for d, h in sorted(dev["points"].items()) if after < d <= upto]}

    def roster_head(self, req):
        version, digest = self.head(req.user)
        return 200, {"version": version, "hash": digest}

    def roster_points_route(self, req):
        upto, out = int(req.query["upto"]), {}
        for (d, v), h in self.roster_points.get(req.user, {}).items():
            if v <= upto:
                out[(v, h)] = out.get((v, h), False) or self.is_member(d)
        return 200, {"points": [{"version": v, "hash": h, "active": a} for (v, h), a in sorted(out.items())]}

    def confirm_roster(self, req):
        """As the app: a member records the roster link it trusts, never with a second hash at a version it or another
        member confirmed. Any device may repeat the head, an older link or one some device confirmed, which records
        nothing."""
        _, version, digest = self._confirming(req, ("version", "hash"))
        points = self.roster_points.setdefault(req.user, {})
        mine = points.get((req.device, version))
        if mine == digest:
            return 204, None
        if not self.is_member(req.device):
            have = self.head(req.user)
            if have[0] > version or have[1] == digest or digest in {h for (_, v), h in points.items() if v == version}:
                return 204, None
            raise Reply(403, "not_member")
        if mine is not None or any(v == version and h != digest and self.is_member(d) for (d, v), h in points.items()):
            raise Reply(409, "roster_fork")
        points[(req.device, version)] = digest
        return 204, None

    def list_members(self, req):
        group = self.members.get(req.user, {})
        moment = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        return 200, {"members": [{"device_id": d, "added_by": by, "added_on": moment(on)}
                                 for d, (by, on) in group.items() if self.is_member(d)]}

    def add_member(self, req):
        """As the app: a member records a computer it approved. A computer already recorded stays as it was."""
        if not self.is_member(req.device):
            raise Reply(403, "not_member")
        target = self._own_sync_device(req)
        self.members.setdefault(req.user, {}).setdefault(target, (req.device, self.clock()))
        return 204, None

    def _bootstrap(self, user, target):
        """As the app: an approved bootstrap records one of the account's computers as the first member, only while the
        group has no member."""
        dev = self.devices.get(target)
        if dev is None or dev["user"] != user or dev["revoked"] or "sync" not in dev["scopes"]:
            raise Reply(404, "not_found")
        group = self.members.setdefault(user, {})
        if self.is_member(target):
            return
        if any(self.is_member(d) for d in group):
            raise Reply(409, "member_exists")
        group[target] = (target, self.clock())

    def _own_sync_device(self, req):
        device_id = (req.json() or {}).get("device_id")
        dev = self.devices.get(device_id) if isinstance(device_id, str) else None
        if dev is None or dev["user"] != req.user or dev["revoked"] or "sync" not in dev["scopes"]:
            raise Reply(404, "not_found")
        return device_id

    def revoke(self, device_id, clear=False):
        """Revoke a device as the app does, recording whether every computer deletes what it read. Revoking it again
        keeps the day and the choice it was first revoked with. `clear=None` stands for a device revoked before choices
        were recorded."""
        dev = self.devices[device_id]
        if not dev["revoked"]:
            dev["revoked"], dev["revoked_clear"] = self.clock(), clear

    def state(self, req):
        group = self.groups.get(req.user)
        if group is None:
            return 200, {"group": False}
        return 200, {"group": True, "epoch": group.epoch, "roster_version": group.roster_version,
                     "head": max((p["seq"] for p in group.packs), default=0),
                     "snapshot": {"id": group.snapshot[0], "through": group.snapshot[1]} if group.snapshot else None,
                     "rotation_due": self._rotation_due(group), "pack_bytes": sum(len(p["blob"]) for p in group.packs),
                     "member": self._member(req, group)}

    def create_group(self, req):
        if not self.is_member(req.device):
            raise Reply(403, "not_member")  # only a computer the app recorded as a member starts the group
        if req.user in self.groups:
            raise Reply(409, "group_exists")
        body = req.json()
        self.groups[req.user] = Group(_unb64(body["roster"]), req.device, _unb64(body["envelope"]),
                                      _unb64(body["recovery_envelope"]))
        return 201, {}

    def roster_links(self, req):
        group = self._group(req)
        self._require(req, group, "member_or_fresh")
        after = int(req.query.get("after", 0))
        return 200, {"links": [{"version": v, "blob": _b64(b)} for v, b in sorted(group.roster.items()) if v > after]}

    def envelope(self, req):
        group = self._group(req)
        self._require(req, group, "member")
        epoch, blob = group.envelopes[req.device]
        return 200, {"epoch": epoch, "blob": _b64(blob)}

    def put_roster(self, req):
        group = self._group(req)
        self._require(req, group, "member")
        body = req.json()
        if body["expected_version"] != group.roster_version:
            raise Reply(409, "version_changed")
        for env in body["envelopes"]:
            dev = self.devices.get(env["device_id"])
            if dev is None or dev["user"] != req.user or dev["revoked"] or env["device_id"] in group.envelopes:
                raise Reply(400, "bad_device")
        group.roster_version += 1
        group.roster[group.roster_version] = _unb64(body["roster"])
        for env in body["envelopes"]:
            group.envelopes[env["device_id"]] = (group.epoch, _unb64(env["blob"]))
        group.recovery = _unb64(body["recovery_envelope"])
        return 200, {"roster_version": group.roster_version}

    def rotate(self, req):
        group = self._group(req)
        body = req.json()
        recovery = body.get("recovery") is True
        if recovery and (self._member(req, group) or not self._fresh(req)):
            raise Reply(403, "not_fresh")
        if recovery and not self.is_member(req.device):
            raise Reply(403, "not_member")  # only a computer the app recorded once the person approved recovers
        if not recovery:
            self._require(req, group, "member")
        if body["expected_epoch"] != group.epoch:
            raise Reply(409, "epoch_changed")
        if body["expected_version"] != group.roster_version:
            raise Reply(409, "version_changed")
        ids = {env["device_id"] for env in body["envelopes"]}
        members = self._members(group)
        if (recovery and (members or ids != {req.device})) or (not recovery and ids != members):
            raise Reply(400, "coverage")
        group.epoch += 1
        group.roster_version += 1
        group.roster[group.roster_version] = _unb64(body["roster"])
        group.envelopes = {env["device_id"]: (group.epoch, _unb64(env["blob"])) for env in body["envelopes"]}
        group.recovery = _unb64(body["recovery_envelope"])
        return 200, {"epoch": group.epoch, "roster_version": group.roster_version}

    def get_recovery(self, req):
        group = self._group(req)
        self._require(req, group, "member_or_fresh")
        return 200, {"epoch": group.epoch, "roster_version": group.roster_version, "blob": _b64(group.recovery)}

    def put_recovery(self, req):
        group = self._group(req)
        self._require(req, group, "member")
        body = req.json()
        if body["expected_version"] != group.roster_version:
            raise Reply(409, "version_changed")
        group.recovery = _unb64(body["blob"])
        return 204, None

    def delete_all(self, req):
        group = self._group(req)
        self._require(req, group, "member_and_fresh")
        del self.groups[req.user]
        for key in [k for k in self.slots if k[0] == req.user]:
            del self.slots[key]
        return 204, None

    def put_pack(self, req):
        group = self._group(req)
        self._require(req, group, "member")
        pack_id = req.match.group(1)
        if not UUID4.fullmatch(pack_id):
            raise Reply(400, "bad_pack_id")
        for p in group.packs:
            if p["pack_id"] == pack_id:
                return 200, {"seq": p["seq"]}
        if self._rotation_due(group):
            raise Reply(409, "rotation_due")
        if req.headers.get("x-superhistory-epoch") != str(group.epoch):
            raise Reply(409, "stale_epoch")
        if self.refuse_packs:
            raise Reply(507, self.refuse_packs)
        seq = group.next_seq
        group.next_seq += 1
        group.packs.append({"seq": seq, "pack_id": pack_id, "epoch": group.epoch, "blob": req.body,
                            "snapshot_id": req.headers.get("x-superhistory-snapshot"),
                            "day": time.strftime("%Y-%m-%d", time.gmtime(self.clock()))})
        return 200, {"seq": seq}

    def list_packs(self, req):
        group = self._group(req)
        self._require(req, group, "member_or_fresh")
        if "snapshot" in req.query:
            chosen = [p for p in group.packs if p["snapshot_id"] == req.query["snapshot"]]
        else:
            limit = int(req.query.get("limit", 200))
            if not 1 <= limit <= 1000:
                raise Reply(400, "bad_limit")
            after = int(req.query.get("after", 0))
            chosen = [p for p in group.packs if p["seq"] > after][:limit]
        return 200, {"packs": [{"seq": p["seq"], "epoch": p["epoch"], "snapshot_id": p["snapshot_id"],
                                "size": len(p["blob"])} for p in chosen]}

    def get_pack(self, req):
        group = self._group(req)
        self._require(req, group, "member_or_fresh")
        seq = int(req.match.group(1))
        for p in group.packs:
            if p["seq"] == seq:
                return 200, p["blob"]
        raise Reply(404, "not_found")

    def applied(self, req):
        group = self._group(req)
        self._require(req, group, "member")
        group.applied[req.device] = (req.json() or {}).get("seq")
        return 204, None

    def compact(self, req):
        group = self._group(req)
        self._require(req, group, "member")
        body = req.json()
        current = group.snapshot[0] if group.snapshot else None
        if body["replaces"] != current:
            raise Reply(409, "snapshot_changed")
        if sum(1 for p in group.packs if p["snapshot_id"] == body["snapshot_id"]) != body["parts"]:
            raise Reply(400, "parts")
        group.packs = [p for p in group.packs
                       if (p["snapshot_id"] is None and p["seq"] > body["through"]) or p["snapshot_id"] == body["snapshot_id"]]
        group.snapshot = (body["snapshot_id"], body["through"])
        return 200, {}

    def open_slot(self, req):
        group = self._group(req)
        self._require(req, group, "member")
        slot_id = _unb64((req.json() or {}).get("slot_id", ""))
        if len(slot_id) != 32:
            raise Reply(400, "bad_slot")
        if (req.user, slot_id) in self.slots and self.slots[(req.user, slot_id)]["expires"] >= self.clock():
            raise Reply(409, "slot_exists")
        self.slots[(req.user, slot_id)] = {"opened_by": req.device, "expires": self.clock() + FRESH, "join": None,
                                           "welcome": None}
        return 201, {}

    def put_join(self, req):
        _, slot = self._slot(req)
        if slot["join"] is not None:
            raise Reply(409, "slot_used")
        slot["join"] = req.body
        return 204, None

    def put_welcome(self, req):
        _, slot = self._slot(req)
        if slot["opened_by"] != req.device:
            raise Reply(403, "not_opener")
        slot["welcome"] = req.body
        return 204, None

    def get_slot(self, req):
        _, slot = self._slot(req)
        return 200, {"join": _b64(slot["join"]), "welcome": _b64(slot["welcome"])}

    def delete_slot(self, req):
        slot_id, _ = self._slot(req)
        del self.slots[(req.user, slot_id)]
        return 204, None

    def healthz(self, req):
        return 200, {"db": "ok"}

    service_answer = {"name": "Superhistory", "support_email": "support@example.com"}

    def service(self, req):
        return (200, self.service_answer) if self.service_answer is not None else (404, {"error": "not_found"})

    def serve(self):
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _any(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else None
                try:
                    status, out = fake.request(self.command, self.path, body, dict(self.headers))
                except TransportError:
                    self.close_connection = True
                    return
                self.send_response(status)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = do_PUT = do_DELETE = _any

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_address[1]}", server


ROUTES = [
    ("POST", r"/api/auth/device/code", "none", FakeRelay.device_code),
    ("POST", r"/v1/actions/run", "none", FakeRelay.run_route),
    ("GET", r"/v1/devices", "app", FakeRelay.list_devices),
    ("PUT", r"/v1/devices/me/chain", "app", FakeRelay.confirm_chain),
    ("GET", r"/v1/devices/([0-9a-f-]+)/chain", "app", FakeRelay.chain_points),
    ("GET", r"/v1/roster", "app", FakeRelay.roster_head),
    ("GET", r"/v1/roster/points", "app", FakeRelay.roster_points_route),
    ("PUT", r"/v1/roster", "app", FakeRelay.confirm_roster),
    ("GET", r"/v1/members", "app", FakeRelay.list_members),
    ("POST", r"/v1/members", "app", FakeRelay.add_member),
    ("GET", r"/healthz", "none", FakeRelay.healthz),
    ("GET", r"/v1/service", "none", FakeRelay.service),
    ("GET", r"/sync/state", "sync", FakeRelay.state),
    ("POST", r"/sync/group", "sync", FakeRelay.create_group),
    ("GET", r"/sync/roster", "sync", FakeRelay.roster_links),
    ("PUT", r"/sync/roster", "sync", FakeRelay.put_roster),
    ("GET", r"/sync/envelope", "sync", FakeRelay.envelope),
    ("POST", r"/sync/rotate", "sync", FakeRelay.rotate),
    ("GET", r"/sync/recovery", "sync", FakeRelay.get_recovery),
    ("PUT", r"/sync/recovery", "sync", FakeRelay.put_recovery),
    ("DELETE", r"/sync", "sync", FakeRelay.delete_all),
    ("PUT", r"/sync/packs/([^/]+)", "sync", FakeRelay.put_pack),
    ("GET", r"/sync/packs", "sync", FakeRelay.list_packs),
    ("GET", r"/sync/packs/(\d+)", "sync", FakeRelay.get_pack),
    ("POST", r"/sync/applied", "sync", FakeRelay.applied),
    ("POST", r"/sync/compact", "sync", FakeRelay.compact),
    ("POST", r"/sync/slots", "sync", FakeRelay.open_slot),
    ("PUT", r"/sync/slots/([A-Za-z0-9_-]+)/join", "sync", FakeRelay.put_join),
    ("PUT", r"/sync/slots/([A-Za-z0-9_-]+)/welcome", "sync", FakeRelay.put_welcome),
    ("GET", r"/sync/slots/([A-Za-z0-9_-]+)", "sync", FakeRelay.get_slot),
    ("DELETE", r"/sync/slots/([A-Za-z0-9_-]+)", "sync", FakeRelay.delete_slot),
]
