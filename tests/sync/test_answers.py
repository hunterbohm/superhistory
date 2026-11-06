"""Every answer the client reads from the relay or the app is refused whole, as 502 bad_answer, unless each field has its
shape, and the refusal never quotes it."""
import http.server
import json
import threading
import unittest
import uuid

from kb.sync import egress, login, relay

PHONE = "+15550100123"
PRIVATE = f"c-aaaaaaaaaaaaa:people/{PHONE}/notes.md"
SNAPSHOT = str(uuid.uuid4())
STATE = {"group": True, "epoch": 1, "roster_version": 1, "head": 0, "snapshot": None, "rotation_due": False,
         "pack_bytes": 0, "member": True}
PACK = {"seq": 1, "epoch": 1, "snapshot_id": None, "size": 1}
CODE = {"device_code": "d" * 40, "user_code": "ABCD2345", "verification_uri": "http://127.0.0.1:3000/device",
        "expires_in": 600, "interval": 5}
ENROLL = {"do": "enroll", "prefix": "c-aaaaaaaaaaaaa", "scopes": ["sync", "push", "read"],
          "age_recipient": "age1" + "q" * 58, "ssh_key": "ssh-ed25519 " + "A" * 68}
ENROLLED = {"done": "enrolled", "renewed": False, "device_id": str(uuid.uuid4()), "label": "computer 1", "token": "t" * 43,
            "sync_token": "s" * 43}
DEVICE = {"device_id": str(uuid.uuid4()), "label": "computer 1", "created_on": "2026-10-27", "revoked_on": None,
          "prefix": "c-aaaaaaaaaaaaa", "age_recipient": "age1" + "q" * 58, "ssh_key": "ssh-ed25519 " + "A" * 68,
          "chain": {"dseq": 3, "hash": "b" * 64}}
POINT = {"version": 1, "hash": "b" * 64, "active": True}
MEMBER = {"device_id": DEVICE["device_id"], "added_by": DEVICE["device_id"], "added_on": "2026-11-01T10:00:00.000Z"}


class Answer:
    def __init__(self, body, status=200):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status

    def request(self, method, path, data, headers):
        return self.status, self.body


class Answers:
    def __init__(self, *bodies):
        self.bodies = list(bodies)

    def request(self, method, path, data, headers):
        return 200, json.dumps(self.bodies.pop(0)).encode()


RELAY = [  # (what, call, a good answer, bad answers)
    ("state", lambda r: r.state(), STATE,
     [{"group": "yes"}, {**STATE, "snapshot": {"id": PRIVATE, "through": 1}}, {**STATE, "head": PRIVATE}, [STATE]]),
    ("roster links", lambda r: r.roster_links(0), {"links": [{"version": 1, "blob": "AA=="}]},
     [{"links": [{"version": PRIVATE, "blob": "AA=="}]}, {"links": [{"version": 1, "blob": PRIVATE}]}, {"links": PRIVATE}]),
    ("envelope", lambda r: r.envelope(), {"epoch": 1, "blob": "AA=="},
     [{"epoch": PRIVATE, "blob": "AA=="}, {"epoch": 1, "blob": PRIVATE}, {"blob": "AA=="}]),
    ("roster change", lambda r: r.put_roster(1, b"x", [], b"x"), {"roster_version": 2}, [{"roster_version": PRIVATE}, {}]),
    ("rotation", lambda r: r.rotate(1, 1, b"x", [], b"x"), {"epoch": 2, "roster_version": 2},
     [{"epoch": 2, "roster_version": PRIVATE}]),
    ("recovery envelope", lambda r: r.recovery(), {"epoch": 1, "roster_version": 1, "blob": "AA=="},
     [{"epoch": 1, "roster_version": 1, "blob": PRIVATE}, {"epoch": True, "roster_version": 1, "blob": "AA=="}]),
    ("pack upload", lambda r: r.put_pack(str(uuid.uuid4()), 1, b"x"), {"seq": 3}, [{"seq": PRIVATE}, {"seq": -1}]),
    ("pack list", lambda r: r.packs(0), {"packs": [PACK]},
     [{"packs": [{**PACK, "seq": PRIVATE}]}, {"packs": [{**PACK, "snapshot_id": PRIVATE}]}, {"packs": [PRIVATE]}]),
    ("snapshot parts", lambda r: r.snapshot_parts(SNAPSHOT), {"packs": [{**PACK, "snapshot_id": SNAPSHOT}]},
     [{"packs": [{**PACK, "size": PRIVATE}]}]),
    ("slot", lambda r: r.slot(b"s" * 32), {"join": "AA==", "welcome": None}, [{"join": PRIVATE, "welcome": None}, {"join": None}]),
    ("text that is not JSON", lambda r: r.state(), STATE, [f"<html>{PRIVATE}</html>".encode()]),
]

APP = [
    ("sign-in code", lambda a: a.ask(ENROLL), CODE,
     [{**CODE, "user_code": PRIVATE}, {**CODE, "user_code": PHONE[1:]}, {**CODE, "interval": "5"},
      {**CODE, "device_code": f"{PRIVATE} code"}]),
    ("run that enrolled this computer", lambda a: a.run("d" * 40, ENROLL), ENROLLED,
     [{**ENROLLED, "device_id": PRIVATE}, {**ENROLLED, "label": 5}, {**ENROLLED, "token": "a token with spaces"},
      {**ENROLLED, "sync_token": 5}, {**ENROLLED, "renewed": "yes"}, {**ENROLLED, "done": "revoked"},
      {k: v for k, v in ENROLLED.items() if k != "token"}, [ENROLLED]]),
    ("run that revoked a computer", lambda a: a.run("d" * 40, {"do": "remove", "device_id": DEVICE["device_id"]}),
     {"done": "revoked", "revoked": [DEVICE["device_id"]]},
     [{"done": "revoked", "revoked": [PRIVATE]}, {"done": "revoked", "revoked": PRIVATE}, {"done": "enrolled", **ENROLLED},
      {"revoked": [DEVICE["device_id"]]}]),
    ("device list", lambda a: a.devices("t" * 32), {"devices": [DEVICE]},
     [{"devices": [{**DEVICE, "device_id": PRIVATE}]}, {"devices": [{**DEVICE, "revoked_on": PRIVATE}]},
      {"devices": [{**DEVICE, "label": 5}]}, {"devices": [{**DEVICE, "prefix": ["c-aaaaaaaaaaaaa"]}]},
      {"devices": [{**DEVICE, "prefix": PRIVATE}]}, {"devices": [{**DEVICE, "age_recipient": {"age1": PRIVATE}}]},
      {"devices": [{**DEVICE, "ssh_key": 5}]}, {"devices": [{**DEVICE, "ssh_key": None}]},
      {"devices": [{**DEVICE, "age_recipient": PRIVATE}]}, {"devices": [{**DEVICE, "ssh_key": PRIVATE}]},
      {"devices": [{**DEVICE, "age_recipient": "age1" + "q" * 57}]}, {"devices": [{**DEVICE, "ssh_key": "ssh-ed25519 " + "A" * 67}]},
      {"devices": [{**DEVICE, "age_recipient": None}]}, {"devices": [{k: v for k, v in DEVICE.items() if k != "chain"}]},
      {"devices": [{**DEVICE, "chain": {"dseq": 3, "hash": PRIVATE}}]}, {"devices": [{**DEVICE, "chain": {"dseq": -1, "hash": None}}]},
      {"devices": [{**DEVICE, "chain": {"dseq": 0, "hash": "b" * 64}}]}, {"devices": [{**DEVICE, "chain": {"dseq": 3, "hash": None}}]},
      {"devices": [{**DEVICE, "chain": {"dseq": True, "hash": "b" * 64}}]}, {"devices": [{**DEVICE, "chain": [3, "b" * 64]}]}]),
    ("device list with a device that only pushes to Cloud", lambda a: a.devices("t" * 32),
     {"devices": [{**DEVICE, "age_recipient": None, "ssh_key": None, "chain": {"dseq": 0, "hash": None}}]}, []),
    ("roster head", lambda a: a.roster_head("t" * 32), {"version": 1, "hash": "b" * 64},
     [{"version": True, "hash": "b" * 64}, {"version": PRIVATE, "hash": "b" * 64}, {"version": 1, "hash": PRIVATE},
      {"version": 1, "hash": None}, {"version": 1}, {"version": 1, "hash": "b" * 64, "extra": 1},
      {"version": 0, "hash": "b" * 64}]),
    ("roster head of a brand-new group", lambda a: a.roster_head("t" * 32), {"version": 0, "hash": None},
     [{"version": 0, "hash": ""}]),
    ("confirmed roster links", lambda a: a.roster_points("t" * 32, 5), {"points": [POINT]},
     [{"points": [{**POINT, "version": PRIVATE}]}, {"points": [{**POINT, "version": 6}]}, {"points": [{**POINT, "version": 0}]},
      {"points": [{**POINT, "hash": PRIVATE}]}, {"points": [{**POINT, "active": "yes"}]}, {"points": [{**POINT, "extra": 1}]},
      {"points": PRIVATE}]),
    ("a device's confirmed packs", lambda a: a.chain_points("t" * 32, DEVICE["device_id"], 0, 5),
     {"points": [{"dseq": 1, "hash": "b" * 64}]},
     [{"points": [{"dseq": 6, "hash": "b" * 64}]}, {"points": [{"dseq": 0, "hash": "b" * 64}]},
      {"points": [{"dseq": 1, "hash": PRIVATE}]}, {"points": [{"dseq": 1, "hash": "b" * 64, "extra": 1}]}]),
    ("sync group members", lambda a: a.members("t" * 32), {"members": [MEMBER]},
     [{"members": [{**MEMBER, "device_id": PRIVATE}]}, {"members": [{**MEMBER, "added_by": None}]},
      {"members": [{**MEMBER, "added_on": PRIVATE}]}, {"members": [{**MEMBER, "extra": 1}]}, {"members": PRIVATE}]),
]


class AnswerShapeTest(unittest.TestCase):
    def check(self, make, cases):
        for what, call, good, bad_answers in cases:
            with self.subTest(answer=what, shape="good"):
                call(make(Answer(good)))
            for bad in bad_answers:
                with self.subTest(answer=what, shape=str(bad)[:60]):
                    with self.assertRaises(relay.RelayError) as caught:
                        call(make(Answer(bad)))
                    self.assertEqual((caught.exception.status, caught.exception.code), (502, "bad_answer"))
                    self.assertNotIn(PHONE, str(caught.exception))

    def test_relay_answers(self):
        self.check(lambda t: relay.Relay(t, "t" * 32), RELAY)

    def test_app_answers(self):
        self.check(relay.App, APP)

    def test_an_error_code_is_kept_only_when_it_is_one_the_servers_send(self):
        for code, kept in (("rotation_due", "rotation_due"), ("0123456789abcdef0123456789abcdef", ""), (PRIVATE, ""),
                           ([], ""), ({}, ""), (None, "")):
            with self.subTest(code=code), self.assertRaises(relay.RelayError) as caught:
                relay.Relay(Answer({"error": code}, status=409), "t" * 32).state()
            self.assertEqual((caught.exception.status, caught.exception.code), (409, kept))

    def test_sign_in_shows_the_apps_own_device_page_and_never_an_address_the_answer_names(self):
        shown = []
        app = relay.App(Answers({**CODE, "verification_uri": f"http://127.0.0.1:3000/{PRIVATE}",
                                 "verification_uri_complete": f"http://127.0.0.1:3000/{PRIVATE}"}, ENROLLED))
        login.approve(app, "http://127.0.0.1:3000/", lambda uri, code: shown.append((uri, code)), ENROLL, sleep=lambda s: None)
        self.assertEqual(shown, [("http://127.0.0.1:3000/device", "ABCD2345")])


class DeviceCodeTest(unittest.TestCase):
    def test_a_device_code_goes_back_to_the_app_as_it_came(self):
        """The code is the app's own credential for one exchange, and the mask can read a random one as a secret."""
        code = "iSCBVNybSdQzXBaNGVP1xRs1XBOv3SZvakWhI7ow"
        self.assertNotEqual(egress.clean({"device_code": code}), {"device_code": code}, "the mask would change this code")
        sent = []

        class Recorder:
            def request(self, method, path, data, headers):
                sent.append(json.loads(data))
                return 200, json.dumps(ENROLLED).encode()
        relay.App(Recorder()).run(code, ENROLL)
        self.assertEqual(sent, [{"device_code": code}])


TOKEN = "0123456789abcdef0123456789abcdef"


class HostileServer(http.server.BaseHTTPRequestHandler):
    """Answers with a redirect to an address that holds a token, or with a status line that does."""

    def do_GET(self):
        if self.path == "/status-line":
            self.wfile.write(f"HTTP/1.1 {TOKEN} {PRIVATE}\r\n\r\n".encode())
        else:
            self.send_response(302)
            self.send_header("Location", f"http://[{TOKEN}]/")
            self.end_headers()

    def log_message(self, *args):
        pass


class TransportTest(unittest.TestCase):
    def test_a_hostile_answer_never_reaches_an_error_message(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), HostileServer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            transport = relay.HttpTransport(f"http://127.0.0.1:{server.server_port}", timeout=5)
            status, _ = transport.request("GET", "/redirect")
            self.assertEqual(status, 302, "a redirect is never followed")
            with self.assertRaises(relay.RelayError) as caught:
                relay.Relay(transport, "t" * 32).state()
            self.assertEqual(caught.exception.status, 302)
            with self.assertRaises(relay.TransportError) as broken:
                transport.request("GET", "/status-line")
            for text in (str(caught.exception), str(broken.exception)):
                self.assertNotIn(TOKEN, text)
                self.assertNotIn(PHONE, text)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
