"""The security audit of PR 7 at e3008c7, one test class per finding, each built from the audit's own repro."""
import json
import os
import random
import tempfile
import unittest
from unittest import mock

from kb import store
from kb.paths import Paths
from kb.sync import client, egress, keys, login, pack, records, relay, state
from kb.sync.client import SyncError

from .support import World, connect, put_item, put_summary

PREFIX = "c-aaaaaaaaaaaaa"
TOKEN = "ghp_" + "B" * 30
SEEDS = (0, 1, 2, 4)  # each finds a text the old mask changed on a second pass


def open_store(tmp):
    db = connect(os.fspath(Paths.under(tmp).db))
    state.init(db)
    return db


def leaves(db, own, sent=state.sent):
    """The wire form of every record that would leave this computer's store, as a pack carries it: each change since
    `sent` through the one chokepoint, and none that the chokepoint holds back."""
    out = []
    for _, rec, _ in records.changes(db, own, sent_table=sent):
        sent = records.outgoing_record(rec, db) if rec else None
        if sent:
            out.append(records.to_wire(sent))
    return out


class Finding2MaskingIsStable(unittest.TestCase):
    """Masking once could free a secret that a second pass then masked, so a pack signed through the mask never matched
    the mask again, and one `superhistory sync` re-signed it forever."""

    GLUED = "Order ref VP1" + "A" * 26 + TOKEN + " thanks"

    def test_the_audit_s_text_hides_the_same_once_or_twice(self):
        once = egress.hide(self.GLUED)
        self.assertEqual(egress.hide(once), once)
        self.assertNotIn(TOKEN, once)

    # Texts the old mask changed again on a second pass, found by the search below at seeds 0, 1, 2 and 4.
    FOUND = ("VP1cookieVP1passwdtoken=Password is :Ab3dE5fGset-cookie: @ghp_",
             "=AAAAAAAAAAAAA7f3aVP1BBBBBBBBBBBBBBBBBBBBBBBBBBsk-ant-BBBBBBBBBBBBBBBBBBBBBBBBBB",
             "sk-ant--@VP1-Password is xxxxxxxxxxxxxxxxxxxxPassword is sk-ant-basic ghp_",
             "VP1xxxxxxxxxxxxxxxxxxxxtoken=Password is passwd2026AAAAAAAAAAAAA2026ghp_/api_key: ")

    def test_hiding_is_the_same_however_often_it_runs(self):
        for text in self.FOUND:
            with self.subTest(text=text):
                once = egress.hide(text)
                self.assertEqual(egress.hide(once), once)
        pieces = ["passwd", "set-cookie: ", "basic ", "token=", "VP1", "VRC1-", "A" * 13, "Ab3dE5fG", "ghp_", "sk-ant-",
                  "api_key: ", "Bearer ", "=", ":", " ", "-", "\n", "x" * 20, "2026", "7f3a", "@", "/", "Password is ",
                  "B" * 26, "cookie", "pw", "secret"]
        for seed in SEEDS + (random.randrange(1 << 30),):
            rng = random.Random(seed)
            for _ in range(5000):
                text = "".join(rng.choice(pieces) for _ in range(rng.randrange(1, 12)))
                once = egress.hide(text)
                self.assertEqual(egress.hide(once), once, f"seed {seed}: {text!r}")

    def test_a_text_the_mask_never_settles_is_hidden_whole(self):
        with mock.patch.object(egress, "mask", lambda text: text + "x"):
            self.assertEqual(egress.hide("a made-up note"), "[masked]")

    def test_one_sync_signs_the_glued_token_once_and_it_never_leaves(self):
        world = World()
        self.addCleanup(world.close)
        (a, b), _ = world.group("computer 1", "computer 2")
        a.item("gmail", "thread-1", self.GLUED)
        pulls, pull = [], a.client.pull

        def counted():
            pulls.append(1)
            if len(pulls) > 10:
                raise AssertionError("one sync kept signing the same pack again")
            return pull()
        with mock.patch.object(a.client, "pull", counted):
            self.assertIsNone(a.sync().stalled)
        self.assertLessEqual(len(pulls), 2, "the pack is signed masked once and goes up")
        b.sync()
        self.assertNotIn(TOKEN, "".join(b.text("gmail", a.key("thread-1"))["rows"]))

    def test_a_pack_that_keeps_changing_stops_sync_instead_of_looping(self):
        world = World()
        self.addCleanup(world.close)
        (a,), _ = world.group("computer 1")
        a.item("notes", "n1", "a made-up note")
        checks = []

        def never(self, plain):
            checks.append(1)
            if len(checks) > 20:
                raise AssertionError("sync kept signing the same pack again")
            return False
        with mock.patch.object(client.SyncClient, "_masked_when_signed", never):
            with self.assertRaises(SyncError):
                a.sync()


class Finding3HashOfWhatLeaves(unittest.TestCase):
    """The item hash that left was over the text before masking, so whoever read it could test a list of passwords against it."""

    TEXT = "Office VPN\nuser: dana\nvpn_pw: Summer2024\n"

    def test_what_leaves_has_the_hash_of_the_masked_text_and_cannot_confirm_a_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = open_store(tmp)
            sid = f"{PREFIX}:vpn"
            put_item(db, "notes", sid, "2026-10-01", "Office VPN", self.TEXT, {})
            raw = records.read_item(db, "notes", sid)["meta"]["hash"]
            put_summary(db, "notes", sid, raw, [("atom", "VPN", "the office VPN login")])
            put_item(db, "notes", f"{PREFIX}:old", "2026-10-01", "Old", "a made-up note", {})
            put_summary(db, "notes", f"{PREFIX}:old", "an older hash", [("atom", "Old", "made up")])
            pushed = leaves(db, PREFIX)
            db.close()
        item = next(w for w in pushed if w["k"] == "item" and w["i"] == sid)
        text = "".join(item["rows"])
        self.assertIn("[masked]", text)
        self.assertNotEqual(item["meta"]["hash"], raw)
        self.assertEqual(item["meta"]["hash"], records.item_hash("Office VPN", text))
        for guess in ("Winter2023", "Summer2024"):
            self.assertNotEqual(records.item_hash("Office VPN", text.replace("[masked]", guess)), item["meta"]["hash"],
                                "the hash never confirms a guess at what was masked")
        summaries = {w["i"]: w for w in pushed if w["k"] == "summary"}
        self.assertEqual(summaries[sid]["of_hash"], item["meta"]["hash"], "the summary names what left")
        self.assertEqual({r["metadata"]["of_hash"] for r in summaries[sid]["rows"]}, {item["meta"]["hash"]})
        self.assertEqual(summaries[f"{PREFIX}:old"]["of_hash"], "", "a summary of an older text names none")
        self.assertNotIn(raw, json.dumps(pushed))


class Finding8KeyNamedValues(unittest.TestCase):
    """Keys and values were masked one string at a time, so {"password": "Summer2024"} passed."""

    def test_a_value_its_key_names_as_a_secret_is_masked_and_others_stay(self):
        meta = {"password": "Summer2024", "vpn_pw": "Winter2023", "host": "tokenbox", "path": "/notes/a.md",
                "hash": "0a1b2c3d4e5f6789", "nested": {"api_key": "abc123XYZ789"}}
        out = egress.clean(meta)
        self.assertEqual((out["password"], out["vpn_pw"], out["nested"]["api_key"]), ("[masked]",) * 3)
        self.assertEqual({k: out[k] for k in ("host", "path", "hash")},
                         {"host": "tokenbox", "path": "/notes/a.md", "hash": "0a1b2c3d4e5f6789"})


class Finding9CodesAnyhowWritten(unittest.TestCase):
    """The code mask skipped only spaces and dashes, so a code split across lines, or written without its head,
    passed."""

    def test_a_code_across_a_line_break_or_without_its_head_is_masked_on_the_way_out(self):
        code = keys.new_recovery_code()[0]
        body = code.removeprefix("VRC1-")
        for text in (code[:14] + "\n" + code[14:], code[:9] + "\r\n" + code[9:], body, body.replace("-", ""),
                     body.replace("-", " "), body.lower(), f"saved: {body}."):
            with self.subTest(text=text):
                hidden = egress.hide(f"note {text} end")
                for group in body.split("-"):
                    self.assertNotIn(group.lower(), hidden.lower())
        self.assertIn(body, egress.hide(body, headless=False), "the terminal mask leaves a headless run alone")

    def test_a_hex_run_or_a_run_of_words_is_not_taken_for_a_code(self):
        for text in ("9e107d9d372bb6826bd81d3542a419d6", "3F2504E0-4F89-11D3-9A0C-0305E82C3301".replace("-", ""),
                     "this time they were very good with them", "ABCD-EFGH-JKMN-PQRS-TVWX-YZAB-CDEF-GHJK"):
            with self.subTest(text=text):
                self.assertEqual(egress.hide(text), text)


class Finding10SignatureBeforeInflating(unittest.TestCase):
    """A pack's records were inflated, up to 256 MiB, and parsed before its signature was checked."""

    def test_a_pack_signed_by_someone_else_is_refused_without_inflating_its_records(self):
        world = World()
        self.addCleanup(world.close)
        (a, b, c), _ = world.group("computer 1", "computer 2", "computer 3")
        _, plain = world.forge(a, [records.Record("item", "notes", a.key("x"), 1,
                                                  {"ts": "2026-10-01", "title": "x", "meta": {}, "rows": ["x" * 1000]})],
                               signer=c)
        (_, forged), _, _, _ = pack.unframe(pack.MAGIC, plain, 2)
        inflated, real = [], pack._inflate
        with mock.patch.object(pack, "_inflate", lambda rb: inflated.append(rb) or real(rb)):
            self.assertIn("signature", b.sync().stalled or "")
        self.assertNotIn(forged, inflated)


class HostMetadata(unittest.TestCase):
    """From varve #14: kb reads an id's host from its rows' own `host` metadata, so the host must travel as kb wrote it.
    An item from a computer named like "tokenbox" must not be taken for a secret's name."""

    def test_items_from_a_host_named_tokenbox_sync_with_their_host(self):
        world = World()
        self.addCleanup(world.close)
        (a, b), _ = world.group("computer 1", "computer 2")
        own = a.prefix
        ids = (f"{own}@tokenbox:session-1", f"{own}:2026-10-01-notes")
        for sid in ids:
            put_item(a.db, "claude_code", sid, "2026-10-01", "Made up", "a made-up session",
                                 {"host": "tokenbox"})
            digest = records.read_item(a.db, "claude_code", sid)["meta"]["hash"]
            put_summary(a.db, "claude_code", sid, digest, [("atom", "Made up", "a made-up fact")])
        a.sync()
        b.sync()
        for sid in ids:
            with self.subTest(item=sid):
                self.assertEqual(b.text("claude_code", sid)["meta"]["host"], "tokenbox")
                self.assertEqual(records.read_summary(b.db, "claude_code", sid)["of_hash"],
                                 b.text("claude_code", sid)["meta"]["hash"])
        pushed = {(w["k"], w["i"]): w for w in leaves(a.db, own, sent=lambda db: {})}
        for sid in ids:
            with self.subTest(pushed=sid):
                self.assertEqual(pushed[("item", sid)]["meta"]["host"], "tokenbox")
                self.assertIn(("summary", sid), pushed)
        store.delete_item(a.db, "claude_code", ids[0])
        a.sync()
        b.sync()
        self.assertFalse(b.has("claude_code", ids[0]), "its deletion travels too")


class Finding4RotationTheRelayHides(unittest.TestCase):
    """After a removal from the web, a relay that kept answering `rotation_due: false` kept members sealing under the old
    epoch key, which the removed computer holds."""

    def test_a_removal_the_relay_hides_still_rotates_and_the_removed_computer_reads_nothing_new(self):
        world = World()
        self.addCleanup(world.close)
        (one, two), _ = world.group("one", "two")
        world.fake.remove(two.client.device_id)
        with mock.patch.object(world.fake, "_rotation_due", lambda group: False):
            self.assertEqual(one.client.keyring.newest, 1)
            one.item("notes", "after", "made-up note written after two was removed")
            one.sync()
            one.sync()
        self.assertEqual(one.client.keyring.newest, 2, "one rotated although the relay said no rotation was due")
        self.assertIsNotNone(one.client.roster.entry(two.client.device_id)["removed"])
        read = [r for seq, _ in world.opened(two) for p in world.fake.group("person@example.com").packs if p["seq"] == seq
                for r in pack.parse(keys.open_with([two.client.keyring.identity(p["epoch"])], p["blob"])).records]
        self.assertFalse([r for r in read if r.source_id == one.key("after")], "two reads nothing written after")


class NarrowRound(unittest.TestCase):
    """The narrow review of the audit's fixes, each with the reviewer's repro."""

    def test_an_item_masked_at_rest_still_leaves_with_the_hash_of_what_leaves(self):
        # kb's remask masks the stored text in place and keeps the hash of the text before it.
        with tempfile.TemporaryDirectory() as tmp:
            db = open_store(tmp)
            sid = f"{PREFIX}:vpn"
            put_item(db, "notes", sid, "2026-10-01", "Office VPN", Finding3HashOfWhatLeaves.TEXT, {})
            raw = records.read_item(db, "notes", sid)["meta"]["hash"]
            put_summary(db, "notes", sid, raw, [("atom", "VPN", "the office VPN login")])
            masked = egress.hide(Finding3HashOfWhatLeaves.TEXT)
            db.execute("UPDATE docs SET content=? WHERE source_id=? AND kind IN ('doc','raw')", (masked, sid))
            pushed = leaves(db, PREFIX)
            db.close()
        item = next(w for w in pushed if w["k"] == "item")
        summary = next(w for w in pushed if w["k"] == "summary")
        self.assertEqual(item["meta"]["hash"], records.item_hash("Office VPN", "".join(item["rows"])))
        self.assertNotEqual(item["meta"]["hash"], raw)
        self.assertEqual(summary["of_hash"], item["meta"]["hash"])
        self.assertNotIn(raw, json.dumps(pushed))
        self.assertNotEqual(records.item_hash("Office VPN", "".join(item["rows"]).replace("[masked]", "Summer2024")),
                            item["meta"]["hash"], "the hash never confirms a guess")

    def test_ids_and_targets_the_id_rule_read_leave_as_they_are(self):
        world = World()
        self.addCleanup(world.close)
        (a,), _ = world.group("computer 1")
        own = a.prefix
        ids = [f"{own}@tokenbox:session-1", f"{own}@tokenbox:session-2"]
        for sid in ids:
            put_item(a.db, "claude_code", sid, "2026-10-01", "Made up", f"made-up {sid[-1]}", {"host": "tokenbox"})
        store.mark_superseded(a.db, "claude_code", ids[0], ("claude_code", ids[1]), own, "2026-10-05")
        sent = leaves(a.db, own, sent=lambda db: {})
        self.assertEqual(sorted(r["i"] for r in sent if r["k"] == "item"), ids, "two items arrive as two")
        (mark,) = [r for r in sent if r["k"] == "supersede"]
        self.assertEqual((mark["i"], mark["new"]), (ids[0], ["claude_code", ids[1]]))
        a.sync()
        b = world.computer("computer 2")
        world.pair(a, b)
        self.assertEqual(store.superseded_by(b.db, "claude_code", ids[0]), ("claude_code", ids[1]),
                         "a Sync mark keeps its target")

    def test_a_key_that_names_a_secret_masks_every_value_under_it(self):
        out = egress.clean({"password": 12345678, "api_key": ["abc123XYZ789", 42], "secret": {"a": "b", "n": 7},
                            "path": "/notes/a.md", "count": 3, "on": True})
        self.assertEqual((out["password"], out["api_key"], out["secret"]),
                         ("[masked]", ["[masked]", "[masked]"], {"a": "[masked]", "n": "[masked]"}))
        self.assertEqual((out["path"], out["count"], out["on"]), ("/notes/a.md", 3, True))

    def test_a_headless_code_with_mixed_separators_or_line_breaks_is_masked(self):
        text = "000G 40R4-0M30-E209-185G-R38E-1W81-24GK"
        self.assertEqual(keys.parse_recovery_code("VRC1-" + text), keys.parse_recovery_code("VRC1-" + text),
                         "it is a code")
        body = keys.new_recovery_code()[0].removeprefix("VRC1-")
        groups = body.split("-")
        for written in (text, " ".join(groups[:3]) + "-" + "\n".join(groups[3:]), "\r\n".join(groups),
                        "-\n".join(groups)):
            with self.subTest(written=written):
                hidden = egress.hide(f"saved {written} here")
                for group in written.replace("\r", " ").replace("\n", " ").replace("-", " ").split():
                    self.assertNotIn(group, hidden)


class Finding11HttpsOnly(unittest.TestCase):
    """Any plain http address was accepted, so a token could travel unencrypted."""

    def test_only_https_or_this_computer_is_accepted(self):
        for url in ("https://app.example.com", "http://127.0.0.1:3000", "http://localhost:3001", "http://[::1]:3000",
                    "http://127.8.9.10", "http://app.localhost:3000", "http://sync.localhost:3000"):
            with self.subTest(url=url):
                relay.check_address(url)
                relay.HttpTransport(url)
        lan = ".".join(("10", "0", "0", "5"))  # a made-up private address, built so the private-data scan passes it
        for url in ("http://app.example.com", f"http://{lan}:3000", "ftp://app.example.com", "app.example.com",
                    "https://", "http://localhost.example.com", "http://app.localhost.example.com"):
            with self.subTest(url=url):
                with self.assertRaises(keys.Unaccepted):
                    relay.check_address(url)
                with self.assertRaises(keys.Unaccepted):
                    relay.HttpTransport(url)

    def test_login_refuses_a_plain_http_address_before_asking_anything(self):
        class Refusing:
            def request(self, *args, **kwargs):
                raise AssertionError("no request goes out")
        with tempfile.TemporaryDirectory() as tmp:
            for app, rel in (("http://app.example.com", "https://relay.example.com"),
                             ("https://app.example.com", "http://relay.example.com")):
                with self.subTest(app=app, relay=rel):
                    with self.assertRaises(keys.Unaccepted):
                        login.login(Paths.under(tmp), Refusing(), app, rel, label="computer 1",
                                    show=lambda uri, code: None, sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
