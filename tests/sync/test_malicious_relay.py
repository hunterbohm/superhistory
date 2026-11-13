import base64
import secrets
import unittest
import uuid
from unittest import mock

from kb import store
from kb.sync import keys, pack, records, relay, roster, state

from kb.sync.client import SyncError, _recovery_aad

from .support import EMAIL, World, put_item


class MaliciousRelayTest(unittest.TestCase):
    """The relay can stall sync or hold data back. It cannot make a computer accept a changed or invented record, an
    older roster, or an older record in place of a newer one."""

    def setUp(self):
        self.world = World()

    def tearDown(self):
        self.world.close()

    def plant_envelope(self, signer, member, epoch):
        sc, attacker = signer.client, keys.new_identity()
        planted = keys.KeyRing({**sc.keyring.epochs, epoch: (attacker, keys.recipient_of(attacker))}, sc.keyring.recovery)
        envelope = roster.seal_envelope(planted, sc.roster, sc.roster.entry(member.client.device_id), sc.device_id,
                                        sc.paths.device_ssh)
        group = self.world.fake.group(EMAIL)
        real = group.envelopes[member.client.device_id]
        group.envelopes[member.client.device_id] = (epoch, envelope)
        return attacker, real

    def assert_nothing_opens_with(self, attacker):
        for p in self.world.fake.group(EMAIL).packs:
            self.assertRaises(keys.Refused, keys.open_with, [attacker], p["blob"])

    def test_removed_device_cannot_sign_an_envelope(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        a.client.remove("computer 3", self.world.browser(), clear=True)
        b.sync()
        group = self.world.fake.group(EMAIL)
        attacker, real = self.plant_envelope(c, b, 3)
        group.epoch = 3
        bait = self.world.put_raw(keys.seal([keys.recipient_of(attacker)], b"bait"), 3)
        secret = b.item("notes", "secret", "PRIVATE written after the attack")

        report = b.client.pull()
        self.assertNotIn(3, b.client.keyring.epochs, "the envelope from a removed device was refused")
        self.assertIn("envelope", report.stalled or "")

        group.packs = [p for p in group.packs if p["seq"] != bait]
        group.epoch, group.envelopes[b.client.device_id] = 2, real
        self.assertIsNone(b.sync().stalled)
        a.sync()
        self.assertTrue(a.has("notes", secret))
        self.assert_nothing_opens_with(attacker)

    def test_envelope_key_waits_for_its_roster_link(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        a.client.remove("computer 3", self.world.browser(), clear=True)
        attacker, real = self.plant_envelope(c, b, 2)

        def hide_removal(method, path, status, out):
            if method == "GET" and path.startswith("/sync/roster") and isinstance(out, dict):
                return status, {"links": [link for link in out["links"] if link["version"] <= 3]}
            return status, out
        self.world.fake.rewrite(hide_removal)
        secret = b.item("notes", "secret", "PRIVATE written while the removal is hidden")
        self.assertIsNotNone(b.client.pull().stalled)
        self.assertNotIn(2, b.client.keyring.epochs, "no key counts before a trusted roster link sets its epoch")

        self.world.fake.rewrites.clear()
        self.world.fake.group(EMAIL).envelopes[b.client.device_id] = real
        self.assertIsNone(b.sync().stalled)
        a.sync()
        self.assertTrue(a.has("notes", secret))
        self.assert_nothing_opens_with(attacker)

    def test_stale_roster_is_refused(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        a.client.remove("computer 3", self.world.browser(), clear=True)
        b.sync()
        trusted = b.client.roster.version
        group = self.world.fake.group(EMAIL)
        group.roster[trusted + 1] = group.roster[trusted - 1]  # the link where computer 3 was still active
        group.roster_version = trusted + 1
        report = b.sync()
        self.assertIn("roster", report.stalled or "")
        self.assertEqual(b.client.roster.version, trusted)
        self.assertIsNotNone(b.client.roster.entry(c.client.device_id)["removed"])

        del group.roster[trusted + 1]
        group.roster_version = trusted - 1
        b.sync()
        self.assertEqual(b.client.roster.version, trusted, "a computer never goes back to an older roster")
        self.assertTrue(any("older roster" in p for p in b.client.status()["problems"]))

    def test_forked_roster_is_reported(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        rec = records.Record("item", "notes", a.key("f"), int(self.world.now * 1000),
                             {"ts": "t", "title": "F", "meta": {"hash": "f"}, "rows": ["f"]})
        self.world.forge(a, [rec], header={"roster_hash": "f" * 64})
        b.sync()
        self.assertTrue(any("different roster" in p for p in b.client.status()["problems"]))

    def serve_fork(self, b, c, entries):
        """Computer 3, already removed, signs a rotation that follows the roster computer 2 trusts and holds `entries`
        made from it. The relay shows computer 2 that link and the envelope that goes with it."""
        ro, cc = b.client.roster, c.client
        epoch = ro.epoch + 1
        ring = cc.keyring.with_epoch(epoch, keys.new_identity())
        forked = roster.Roster(roster.new_link(ro, epoch, cc.device_id, entries(ro), ring))
        link = base64.b64encode(roster.seal_link(forked.link, cc.paths.device_ssh, ring.recipient(epoch))).decode()
        envelope = roster.seal_envelope(ring, forked, forked.entry(b.client.device_id), cc.device_id, cc.paths.device_ssh)
        served = {f"/sync/roster?after={ro.version}": {"links": [{"version": forked.version, "blob": link}]},
                  "/sync/envelope": {"epoch": epoch, "blob": base64.b64encode(envelope).decode()}}
        self.world.fake.rewrite(lambda m, p, s, out: (s, served.get(p, {"links": []})) if p.startswith(
            ("/sync/roster", "/sync/envelope")) else (s, out))
        return ro

    def test_a_removed_computer_s_rotation_cannot_give_the_group_a_recovery_key_it_holds(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        self.world.fake.revoke(c.client.device_id)
        self.hide_the_removal(refuse_rotation=False)
        ro, cc = b.client.roster, c.client
        held = keys.new_identity()
        tag = secrets.token_bytes(32)
        epoch = ro.epoch + 1
        ring = cc.keyring.with_epoch(epoch, keys.new_identity()).with_recovery(keys.recipient_of(held), tag, ro.version + 1)
        forged = roster.Roster(roster.new_link(ro, epoch, cc.device_id, ro.entries, ring))
        group = self.world.fake.group(EMAIL)  # the relay stores the rotation computer 3 signed after its removal
        group.roster[forged.version] = roster.seal_link(forged.link, cc.paths.device_ssh, ring.recipient(epoch))
        group.roster_version, group.epoch = forged.version, epoch
        for e in forged.active():
            group.envelopes[e["device_id"]] = (epoch, roster.seal_envelope(ring, forged, e, cc.device_id, cc.paths.device_ssh))
        b.sync()
        self.assertNotEqual(b.client.keyring.recovery["recipient"], keys.recipient_of(held))
        with self.assertRaises(keys.Refused, msg="the key it holds opens no recovery envelope"):
            pack.open_tagged(tag, group.recovery, _recovery_aad(group.epoch, group.roster_version), identity=held)

    def test_a_removed_computer_s_envelope_cannot_give_the_group_a_recovery_key_it_holds(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        a.client.new_recovery_code()  # computer 2 is away and has not opened its envelope for the new key
        c.sync()
        self.world.fake.revoke(c.client.device_id)
        self.hide_the_removal(refuse_rotation=False)
        group, cc = self.world.fake.group(EMAIL), c.client
        held, tag = keys.new_identity(), secrets.token_bytes(32)
        ring = cc.keyring.with_recovery(keys.recipient_of(held), tag, cc.roster.version)
        group.envelopes[b.client.device_id] = (group.epoch, roster.seal_envelope(
            ring, cc.roster, cc.roster.entry(b.client.device_id), cc.device_id, cc.paths.device_ssh))
        b.sync()
        self.assertNotEqual(b.client.keyring.recovery["recipient"], keys.recipient_of(held))
        with self.assertRaises(keys.Refused, msg="the key it holds opens no recovery envelope"):
            pack.open_tagged(tag, group.recovery, _recovery_aad(group.epoch, group.roster_version), identity=held)

    def test_a_welcome_a_removed_approver_forges_is_refused(self):
        (a, c), _ = self.world.group("computer 1", "computer 3")
        for comp in (a, c):
            comp.sync()
        d = self.world.computer("computer 4")
        pairing = c.client.add_device()
        d.client.join(pairing.code)
        held, tag = keys.new_identity(), secrets.token_bytes(32)
        cc = c.client
        cc.keyring = cc.keyring.with_recovery(keys.recipient_of(held), tag, cc.roster.version + 1)
        with mock.patch.object(cc, "_confirm_roster", lambda: None):  # its keys, used after its removal, confirm nothing
            cc.approve(pairing, lambda label, words: True, sleep=lambda s: None)
        self.world.fake.revoke(cc.device_id)
        self.hide_the_removal(refuse_rotation=False)
        group = self.world.fake.group(EMAIL)
        sealed_by_the_attacker = (group.epoch, group.recovery)
        with self.assertRaises((keys.Refused, SyncError)):
            d.client.finish_join(sleep=lambda s: None)
        self.assertNotEqual(self.world.fake.head(self.world.fake.user_of(EMAIL))[0],
                            cc.roster.version, "the joiner did not vouch for the forged link")
        self.assertEqual((group.epoch, group.recovery), sealed_by_the_attacker,
                         "no new epoch key went into a recovery envelope the attacker's key opens")

    def forge_welcome(self, approver, joiner, pairing, base, base_ring):
        """`approver`, a removed insider still holding the pairing welcome_key, seals a welcome for `joiner` naming a
        roster link it forged on `base`: the link adds the joiner, marks `approver` removed with its items cleared, names
        an active member as its signer (though `approver` signed it), and commits a recovery key only the attacker holds.
        The link is stored on the relay at `base.version + 1`. Returns (held recovery identity, forged Roster)."""
        held, tag = keys.new_identity(), secrets.token_bytes(32)
        ring = base_ring.with_recovery(keys.recipient_of(held), tag, base.version + 1)
        signer = next(e["device_id"] for e in base.active() if e["device_id"] != approver.client.device_id)
        gone = {"in_version": base.version + 1, "head_dseq": 0, "head_hash": "", "clear": True}
        entries = [{**e, "removed": gone} if e["device_id"] == approver.client.device_id else e for e in base.entries]
        entries.append(joiner.client._my_entry(base.version + 1))
        forged = roster.Roster(roster.new_link(base, base.epoch, signer, entries, ring))
        group, pk = self.world.fake.group(EMAIL), keys.PairingKeys(pairing.secret)
        group.roster[forged.version] = roster.seal_link(forged.link, approver.client.paths.device_ssh, ring.recipient(base.epoch))
        welcome = {"keyring": ring.to_json(), "roster": {"version": forged.version, "hash": forged.hash}}
        self.world.fake.slots[(self.world.fake.user_of(EMAIL), pk.slot_id)]["welcome"] = pack.seal_tagged(
            pk.welcome_key, joiner.client._my_entry(1)["age_recipient"], welcome)
        return held, forged

    def assert_forged_welcome_reaches_no_trust(self, joiner, held, cleared_prefix):
        victim = f"{cleared_prefix}:victim"
        put_item(joiner.db, "notes", victim, "2026-10-01", "V", "a removed computer's item", {})
        joiner.db.commit()
        before = len(self.world.fake.log)
        try:
            joiner.client.finish_join(sleep=lambda s: None)
        except (keys.Refused, SyncError, relay.RelayError, relay.TransportError):
            pass
        confirms = [e for e in self.world.fake.log[before:] if e[:2] == ("PUT", "/v1/roster")]
        self.assertEqual(confirms, [], "no roster confirmation was sent for the forged link")
        saved = keys.load_json(joiner.client.paths.keyring)
        self.assertTrue(saved is None or saved["recovery"]["recipient"] != keys.recipient_of(held),
                        "no key only the attacker holds was saved")
        self.assertTrue(joiner.has("notes", victim), "a removed computer's items were not cleared")
        self.assertIsNone(joiner.client.roster, "the forged link was never trusted")

    def test_a_welcome_link_above_the_account_head_reaches_no_trust(self):
        # Finding 1: a link forged a version above the account's roster head, naming an active member as its signer,
        # must not be trusted on its hash alone; its signature does not verify, so the walk from the account head refuses.
        (a, c), _ = self.world.group("computer 1", "computer 3")
        for comp in (a, c):
            comp.sync()
        base, base_ring = c.client.roster, c.client.keyring  # version 2, the account's roster head
        d = self.world.computer("computer 4")
        pairing = c.client.add_device()
        d.client.join(pairing.code)
        held, forged = self.forge_welcome(c, d, pairing, base, base_ring)
        self.assertGreater(forged.version, self.world.fake.head(self.world.fake.user_of(EMAIL))[0],
                           "the forged link sits above the account's roster head")
        self.world.fake.revoke(c.client.device_id)
        self.hide_the_removal(refuse_rotation=False)
        self.assert_forged_welcome_reaches_no_trust(d, held, c.prefix)

    def test_a_welcome_link_that_forks_below_the_account_head_reaches_no_trust(self):
        # Finding 2: a lower-version fork must reach no _trust before the walk rejects it. The fix anchors at the account
        # head and proves the whole chain before any side effect, so the fork saves no key, clears no item, confirms none.
        (a, c), _ = self.world.group("computer 1", "computer 3")
        for comp in (a, c):
            comp.sync()
        base, base_ring = c.client.roster, c.client.keyring  # version 2
        for name in ("computer 2", "computer 5"):  # move the account's roster head above the fork, without a rotation
            self.world.pair(a, self.world.computer(name))
        d = self.world.computer("computer 4")
        pairing = c.client.add_device()
        d.client.join(pairing.code)
        held, forged = self.forge_welcome(c, d, pairing, base, base_ring)
        self.assertLess(forged.version, self.world.fake.head(self.world.fake.user_of(EMAIL))[0],
                        "the fork sits below the account's roster head")
        self.world.fake.revoke(c.client.device_id)
        self.hide_the_removal(refuse_rotation=False)
        self.assert_forged_welcome_reaches_no_trust(d, held, c.prefix)

    def test_a_welcome_whose_ring_swaps_an_old_epoch_s_private_identity_is_refused(self):
        # pr10-r6c finding 3: the roster commitment hashes only the public recipients, so a ring that keeps an old
        # epoch's genuine recipient but swaps that epoch's private identity still matches the commitment and still
        # opens the current head. _trust's keys.is_pair check (client.py ~:183) is the only guard that each secret key
        # is the one its public key names. A removed approver that keeps the welcome_key seals such a ring under a
        # welcome naming the honest head; removing is_pair lets the joiner save the poisoned ring.
        (a, c), _ = self.world.group("computer 1", "computer 3")
        for comp in (a, c):
            comp.sync()
        a.client.new_recovery_code()  # rotate to epoch 2, so the ring carries an older epoch to poison
        c.sync()
        base, base_ring = c.client.roster, c.client.keyring  # the honest head and c's real ring (epochs 1 and 2)
        self.assertEqual(base.epoch, 2)
        d = self.world.computer("computer 4")
        pairing = c.client.add_device()
        d.client.join(pairing.code)
        bad = keys.new_identity()
        poisoned = keys.KeyRing({**base_ring.epochs, 1: (bad, base_ring.recipient(1))}, base_ring.recovery)
        self.assertEqual(roster.ring_commitment(poisoned.upto(base.epoch)), base.link["keys"],
                         "the poisoned ring still matches the commitment")
        welcome = {"keyring": poisoned.to_json(), "roster": {"version": base.version, "hash": base.hash}}
        pk = keys.PairingKeys(pairing.secret)
        self.world.fake.slots[(self.world.fake.user_of(EMAIL), pk.slot_id)]["welcome"] = pack.seal_tagged(
            pk.welcome_key, d.client._my_entry(1)["age_recipient"], welcome)
        self.world.fake.revoke(c.client.device_id)
        self.hide_the_removal(refuse_rotation=False)

        before = len(self.world.fake.log)
        try:
            d.client.finish_join(sleep=lambda s: None)
        except (keys.Refused, SyncError, relay.RelayError, relay.TransportError):
            pass
        self.assertIsNone(d.client.roster, "the poisoned ring was never trusted")
        self.assertIsNone(keys.load_json(d.client.paths.keyring), "no poisoned key ring was saved")
        self.assertEqual([e for e in self.world.fake.log[before:] if e[:2] == ("PUT", "/v1/roster")], [],
                         "no roster confirmation was sent for the poisoned ring")

    def test_a_joiner_approved_honestly_joins_though_its_approver_is_removed_right_after(self):
        (a, c), _ = self.world.group("computer 1", "computer 3")
        for comp in (a, c):
            comp.sync()
        d = self.world.computer("computer 4")
        pairing = c.client.add_device()
        d.client.join(pairing.code)
        c.client.approve(pairing, lambda label, words: True, sleep=lambda s: None)
        self.world.fake.revoke(c.client.device_id)
        report = d.client.finish_join(sleep=lambda s: None)
        self.assertIsNone(report.stalled)
        self.assertIsNotNone(d.client.roster.entry(c.client.device_id)["removed"])

    def test_a_roster_change_made_before_its_signer_s_removal_still_counts(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        c.client.new_recovery_code()  # computer 2 has not seen it when computer 3 is removed
        self.world.fake.revoke(c.client.device_id)
        report = b.sync()
        self.assertIsNone(report.stalled)
        self.assertGreater(b.client.roster.version, 4, "computer 2 took computer 3's change, then removed it")
        self.assertIsNotNone(b.client.roster.entry(c.client.device_id)["removed"])

    def test_a_link_at_the_account_s_roster_head_must_be_the_one_it_lists(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        self.world.fake.revoke(c.client.device_id)
        version, digest = b.client.roster.version + 1, "0" * 64
        self.world.fake.vouch_roster(a.client.device_id, version, digest)  # a member trusts a link the relay lacks
        ro, cc = b.client.roster, c.client
        epoch = ro.epoch + 1
        ring = cc.keyring.with_epoch(epoch, keys.new_identity())
        forged = roster.Roster(roster.new_link(ro, epoch, cc.device_id, ro.entries, ring))
        group = self.world.fake.group(EMAIL)
        group.roster[forged.version] = roster.seal_link(forged.link, cc.paths.device_ssh, ring.recipient(epoch))
        group.roster_version, group.epoch = forged.version, epoch
        for e in forged.active():
            group.envelopes[e["device_id"]] = (epoch, roster.seal_envelope(ring, forged, e, cc.device_id, cc.paths.device_ssh))
        self.assertRegex(b.sync().stalled or "", "not the one the account lists")
        self.assertEqual(b.client.roster.version, ro.version)

    def test_a_computer_on_a_fork_of_the_account_s_roster_is_told(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        b.sync()
        user, version = self.world.fake.user_of(EMAIL), b.client.roster.version
        del self.world.fake.roster_points[user][(b.client.device_id, version)]
        self.world.fake.vouch_roster(a.client.device_id, version, "0" * 64)  # another member trusts a fork of b's head
        state.put(b.db, "confirmed_roster", None)
        self.assertRegex(b.sync().stalled or "", "not the one the account lists")

    def test_a_computer_ahead_of_the_account_s_head_but_off_it_takes_nothing_new(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        b.sync()
        trusted = b.client.roster.version
        a.client.new_recovery_code()
        user = self.world.fake.user_of(EMAIL)
        self.world.fake.vouch_roster(a.client.device_id, trusted, "0" * 64)  # a member trusts a fork at b's version
        mine = {k: v for k, v in self.world.fake.roster_points[user].items() if k[0] == b.client.device_id}
        self.assertRegex(b.sync().stalled or "", "not the one the account lists")
        self.assertEqual(b.client.roster.version, trusted)
        self.assertEqual({k: v for k, v in self.world.fake.roster_points[user].items() if k[0] == b.client.device_id},
                         mine, "computer 2 does not move the account's head onto its own roster")

    def test_a_computer_strictly_ahead_of_a_forked_account_head_takes_nothing_new(self):
        # Round-5 part C, finding 2: the historical-fork check must fire when the account head is strictly BELOW this
        # computer's version, not only at equality. b reaches `start`; the app head names an earlier version whose hash
        # differs from b's (a fork below it); a newer link waits on the relay. b must stall and move nothing. (The
        # earlier ahead-of-head test only set the head to exactly b's version, so `head_version <= start` read the same
        # as `head_version == start`; this case separates them.)
        (a, b), _ = self.world.group("computer 1", "computer 2")
        b.sync()
        a.client.new_recovery_code()
        b.sync()
        mid = b.client.roster.version              # b holds its own real hash at this version
        a.client.new_recovery_code()
        b.sync()
        start = b.client.roster.version
        self.assertLess(mid, start, "b is strictly ahead of the version the account head will name")
        a.client.new_recovery_code()               # a newer link waits on the relay, not yet taken by b
        user = self.world.fake.user_of(EMAIL)
        self.world.fake.vouch_roster(a.client.device_id, mid, "0" * 64)   # a member's link forks below b
        mine = {k: v for k, v in self.world.fake.roster_points[user].items() if k[0] == b.client.device_id}
        self.assertRegex(b.sync().stalled or "", "not the one the account lists")
        self.assertEqual(b.client.roster.version, start, "b took no newer link off its own history")
        self.assertEqual({k: v for k, v in self.world.fake.roster_points[user].items() if k[0] == b.client.device_id},
                         mine, "b did not move the account head onto its own chain")

    def test_an_approval_counts_for_the_others_though_its_approver_is_removed_right_after(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        d = self.world.computer("computer 4")
        pairing = c.client.add_device()
        d.client.join(pairing.code)
        c.client.approve(pairing, lambda label, words: True, sleep=lambda s: None)
        self.world.fake.revoke(c.client.device_id)
        self.assertIsNone(b.sync().stalled)
        self.assertIsNotNone(b.client.roster.entry(d.client.device_id), "computer 2 took the approval")
        self.assertIsNotNone(b.client.roster.entry(c.client.device_id)["removed"])

    def test_a_forked_roster_changes_nothing_the_account_did_not(self):
        def removing_computer_1(clear):
            def entries(ro):
                gone = {"in_version": ro.version + 1, "head_dseq": 0, "head_hash": "", "clear": clear}
                return [{**e, "removed": gone} if e["label"] == "computer 1" else e for e in ro.entries]
            return entries

        def adding_a_device_the_account_lacks(ro):
            made_up = {**ro.entry(ro.link["signer"]), "device_id": str(uuid.uuid4()), "label": "made up",
                       "prefix": "c-" + "q" * 13, "added_in": ro.version + 1}
            return ro.entries + [made_up]

        def adding_computer_4_under_keys_it_chose(ro):
            return ro.entries + [{**ro.entry(c.client.device_id), "device_id": d.client.device_id, "prefix": d.prefix,
                                  "label": "computer 4", "added_in": ro.version + 1}]

        def removing_itself(ro):
            gone = {"in_version": ro.version + 1, "head_dseq": 0, "head_hash": "", "clear": False}
            return [{**e, "removed": gone} if e["device_id"] == c.client.device_id else e for e in ro.entries]

        for name, entries in (("clears computer 1", removing_computer_1(True)),
                              ("removes computer 1", removing_computer_1(False)),
                              ("adds a device the account lacks", adding_a_device_the_account_lacks),
                              ("adds a device the account has, under keys it chose", adding_computer_4_under_keys_it_chose),
                              ("removes itself, so its new key would count", removing_itself)):
            with self.subTest(fork=name):
                self.world.close()
                self.world = World()
                (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
                d = self.world.computer("computer 4")
                mine = a.item("notes", "a1", "computer 1's note")
                a.sync()
                b.sync()
                a.client.remove("computer 3", self.world.browser(), clear=False)
                ro = self.serve_fork(b, c, entries)

                self.assertIsNotNone(b.sync().stalled)
                self.assertTrue(b.has("notes", mine), "computer 1 was never revoked, so nothing it wrote is deleted")
                self.assertEqual(b.client.roster.version, ro.version)

    def test_each_device_binding_field_is_checked_against_the_app_on_its_own(self):
        # pr10-r6c finding 4: _confirm_with_app must check the install id, the age key, and the signing key of an added
        # device against the app independently (DEVICE_KEYS at client.py:70). A link at the account head adds computer 4
        # with exactly one field disagreeing with the app and the other two matching. The head-hash check passes (the
        # link is the account head), so only DEVICE_KEYS stands between the mismatch and the roster. Dropping any one
        # field from DEVICE_KEYS lets that field's mismatch through.
        for field in ("prefix", "age_recipient", "ssh_key"):
            with self.subTest(field=field):
                self.world.close()
                self.world = World()
                (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
                for comp in (a, b, c):
                    comp.sync()
                spare = self.world.computer("spare")
                d = self.world.computer("computer 4")  # the app lists it with its real prefix, age key and signing key
                ro, cc = b.client.roster, c.client
                mismatched = {**d.client._my_entry(1), "added_in": ro.version + 1,
                              field: spare.client._my_entry(1)[field]}
                entries = ro.entries + [mismatched]
                forged = roster.Roster(roster.new_link(ro, ro.epoch, cc.device_id, entries, cc.keyring))
                group = self.world.fake.group(EMAIL)
                group.roster[forged.version] = roster.seal_link(forged.link, cc.paths.device_ssh, cc.keyring.recipient(ro.epoch))
                group.roster_version = forged.version
                self.world.fake.vouch_roster(a.client.device_id, forged.version, forged.hash)

                report = b.client.pull()
                self.assertIn("roster", report.stalled or "", f"a {field} that disagrees with the app is refused")
                self.assertIsNone(b.client.roster.entry(d.client.device_id),
                                  f"computer 4 was not added under a {field} the app does not list")
                self.assertEqual(b.client.roster.version, ro.version)

    def hide_the_removal(self, refuse_rotation):
        """The relay says no removal is due, and when `refuse_rotation`, never lets a rotation through."""
        self.world.fake.rewrite(lambda m, p, s, out: (s, {**out, "rotation_due": False})
                                if p.startswith("/sync/state") and isinstance(out, dict) and "rotation_due" in out
                                else (s, out))

        def hold(method, path, headers):
            if refuse_rotation and (method, path) == ("POST", "/sync/rotate"):
                raise relay.TransportError("held back")
        self.world.fake.before_hooks.append(hold)

    def uploads(self):
        return [e for e in self.world.fake.log if e[0] == "PUT" and e[1].startswith("/sync/packs/")]

    def test_a_computer_seals_nothing_while_a_removal_the_relay_hides_is_unfinished(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        self.world.fake.remove(c.client.device_id)
        self.hide_the_removal(refuse_rotation=True)
        b.item("notes", "b1", "written after computer 3 was removed")
        before = len(self.uploads())
        for send in (b.sync, b.client.push, lambda: b.client.compact(force=True)):
            with self.assertRaises(relay.TransportError):
                send()
        self.assertEqual(len(self.uploads()), before, "nothing went up under the key computer 3 still holds")
        self.assertIsNone(b.client.roster.entry(c.client.device_id)["removed"])

    def test_a_computer_finishes_a_removal_the_relay_hides_before_it_seals_anything(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        self.world.fake.remove(c.client.device_id)
        self.hide_the_removal(refuse_rotation=False)
        b.item("notes", "b1", "written after computer 3 was removed")
        before = len(self.world.fake.group(EMAIL).packs)
        self.assertIsNone(b.sync().stalled)
        self.assertIsNotNone(b.client.roster.entry(c.client.device_id)["removed"])
        self.assertEqual({p["epoch"] for p in self.world.fake.group(EMAIL).packs[before:]}, {2})

    def test_a_removal_during_a_long_push_reaches_every_pack_signed_after_it(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for n in range(3):
            b.item("notes", f"big {n}", f"{n} " * 700_000)
        fake, epochs = self.world.fake, []

        def remove_computer_3_after_the_first_pack(method, path, headers):
            if method == "PUT" and path.startswith("/sync/packs/") and "x-superhistory-snapshot" not in headers:
                epochs.append(int(headers["x-superhistory-epoch"]))
                if len(epochs) == 1:
                    fake.revoke(c.client.device_id)
        fake.before_hooks.append(remove_computer_3_after_the_first_pack)
        with mock.patch.object(fake, "_rotation_due", lambda group: False):  # the relay hides the removal and takes every pack
            self.assertEqual(b.sync().sent, 3)
        self.assertEqual(epochs[:3], [1, 2, 2], "only the pack signed before the removal used the old key")
        self.assertEqual(set(epochs[1:]), {2})

    def test_a_pack_resigned_after_a_refused_upload_seals_nothing_for_a_removal_the_relay_hides(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        written = b.item("notes", "b1", "written while computer 3 is removed mid-push")
        fake, group, epochs, refused = self.world.fake, self.world.fake.group(EMAIL), [], []

        def note(method, path, headers):
            if method == "PUT" and path.startswith("/sync/packs/") and "x-superhistory-snapshot" not in headers:
                epochs.append(int(headers["x-superhistory-epoch"]))

        def remove_computer_3_and_refuse_the_first_pack(method, path, status, out):
            if not refused and method == "PUT" and path.startswith("/sync/packs/") and status == 200:
                refused.append(out["seq"])
                fake.revoke(c.client.device_id)
                group.packs = [p for p in group.packs if p["seq"] != out["seq"]]
                return 409, {"error": "stale_epoch"}
            return status, out
        fake.before_hooks.append(note)
        fake.rewrite(remove_computer_3_and_refuse_the_first_pack)
        before = len(group.packs)
        with mock.patch.object(fake, "_rotation_due", lambda group: False):  # the relay hides the removal and takes every pack
            self.assertIsNone(b.sync().stalled)
        self.assertTrue(refused, "the relay refused the first upload")
        self.assertEqual(epochs[:2], [1, 2], "the pack signed again after the refusal is sealed under the new key")
        self.assertIsNotNone(b.client.roster.entry(c.client.device_id)["removed"])
        sent = [p for p in group.packs[before:] if p["snapshot_id"] is None]
        self.assertTrue(sent)
        self.assertEqual({p["epoch"] for p in sent}, {2})
        for p in sent:
            self.assertRaises(keys.Refused, keys.open_with, [c.client.keyring.identity(1)], p["blob"])
        a.sync()
        self.assertTrue(a.has("notes", written), "the resigned pack still reaches the remaining members")

    def test_a_pack_resigned_through_the_mask_seals_nothing_for_a_removal_landing_during_its_pull(self):
        # pr10-r7c finding 2: a pack signed before masking is signed again under the same dseq, after a pull that finds
        # the relay does not hold it. A removal landing during that pull must be finished before the pack is sealed
        # again, so the relay, hiding the removal, never gets it under the key computer 3 holds.
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        secret = "sk-ant-api03-" + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAb7cDe0f"
        line = records.to_json(records.Record("item", "notes", a.key("waiting"), int(self.world.now * 1000), {
            "ts": "2026-10-01", "title": "Waiting", "meta": {"hash": "h"}, "rows": [secret]}))
        with state.tx(a.db):
            state.add_outbox(a.db, [line])
        dseq, prev = a.client._signing_head()
        header = {"epoch": a.client.keyring.newest, "author": a.client.device_id, "dseq": dseq + 1, "prev": prev,
                  "roster_version": a.client.roster.version, "roster_hash": a.client.roster.hash, "snapshot": None}
        a.client._save_pending([rid for rid, rec in state.unsigned(a.db, 1 << 30) if rec == line], dseq + 1, prev,
                               pack.build(header, [line], a.client.paths.device_ssh))  # signed unmasked, never sent
        fake, group, pull, landed = self.world.fake, self.world.fake.group(EMAIL), a.client.pull, []

        def remove_computer_3_then_pull():
            if not landed:  # the pull inside the resign, after push's own check
                landed.append(1)
                fake.revoke(c.client.device_id)
            return pull()
        before = len(group.packs)
        with mock.patch.object(fake, "_rotation_due", lambda group: False), \
                mock.patch.object(a.client, "pull", remove_computer_3_then_pull):
            a.client.push()
        self.assertTrue(landed)
        self.assertIsNotNone(a.client.roster.entry(c.client.device_id)["removed"])
        sent = [p for p in group.packs[before:] if p["snapshot_id"] is None]
        self.assertTrue(sent)
        self.assertEqual({p["epoch"] for p in sent}, {2}, "the pack signed again went up under the new key")
        for p in sent:
            self.assertRaises(keys.Refused, keys.open_with, [c.client.keyring.identity(1)], p["blob"])
            self.assertNotIn(secret.encode(), keys.open_with([a.client.keyring.identity(p["epoch"])], p["blob"]))

    def test_a_roster_link_whose_signature_does_not_verify_reaches_no_trust(self):
        # pr10-r7c finding 1: a link that follows the trusted one, names an honest active member as its signer, keeps
        # every entry and the key ring every member holds, and adds a computer the app lists under its own keys, so
        # every other check passes. Only its signature, made with another computer's key, is wrong.
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        d = self.world.computer("computer 4")
        ro, ring = b.client.roster, b.client.keyring
        forged = roster.Roster(roster.new_link(ro, ro.epoch, a.client.device_id,
                                               ro.entries + [d.client._my_entry(ro.version + 1)], ring))
        self.assertEqual(forged.link["keys"], ro.link["keys"], "it names the key ring every member holds")
        group = self.world.fake.group(EMAIL)
        group.roster[forged.version] = roster.seal_link(forged.link, c.client.paths.device_ssh, ring.recipient(ro.epoch))
        group.roster_version = forged.version
        before = len(self.world.fake.log)
        report = b.client.pull()
        self.assertIn("signature", report.stalled or "")
        self.assertEqual(b.client.roster.version, ro.version)
        self.assertIsNone(b.client.roster.entry(d.client.device_id), "the computer it adds was never trusted")
        self.assertEqual([e for e in self.world.fake.log[before:] if e[:2] == ("PUT", "/v1/roster")], [])
        self.assertEqual(roster.Roster(keys.load_json(b.client.paths.roster)["link"]).version, ro.version)

    def test_the_sealing_gate_rotates_again_for_a_computer_revoked_during_a_rotation(self):
        # The gate that finishes removals before sealing rotates to drop computer 3. Computer 4 is revoked DURING that
        # rotation, after it read the app's list, so the new epoch was sealed to computer 4 too. The gate must ask the
        # app again and rotate once more, and must not return while any revoked computer is still a member — or the
        # pack the caller seals next would go out under a key computer 4 holds.
        (a, b, c, d), _ = self.world.group("computer 1", "computer 2", "computer 3", "computer 4")
        for comp in (a, b, c, d):
            comp.sync()
        fake, rotations = self.world.fake, []
        fake.revoke(c.client.device_id)

        def remove_4_after_the_first_rotation(method, path, status):
            if (method, path) == ("POST", "/sync/rotate") and status < 300:
                rotations.append(path)
                if len(rotations) == 1:  # the new epoch is in, sealed to computer 4, before computer 4 is revoked
                    fake.revoke(d.client.device_id)
        fake.after_hooks.append(remove_4_after_the_first_rotation)
        with mock.patch.object(fake, "_rotation_due", lambda group: False):  # the relay hides both removals
            b.client._seal_allowed()
        self.assertGreaterEqual(len(rotations), 2, "a second rotation ran for the computer revoked during the first")
        self.assertEqual(b.client._revoked_members(), set(), "the gate did not return while a revoked computer was active")
        for gone in (c, d):
            self.assertIsNotNone(b.client.roster.entry(gone.client.device_id)["removed"], "both computers are removed")

    def snapshot_uploads(self):
        """Every snapshot part this computer sends, as (the epoch it names, its body), whether the relay takes it or not."""
        fake, request, uploads = self.world.fake, self.world.fake.request, []

        def note(method, path, body=None, headers=None):
            named = {k.lower(): v for k, v in (headers or {}).items()}
            if method == "PUT" and path.startswith("/sync/packs/") and "x-superhistory-snapshot" in named:
                uploads.append((int(named["x-superhistory-epoch"]), body))
            return request(method, path, body, headers)
        patcher = mock.patch.object(fake, "request", note)
        patcher.start()
        self.addCleanup(patcher.stop)
        return uploads

    def assert_no_key_of(self, computer, uploads):
        """None of the keys `computer` holds opens any of `uploads`: what the epoch says is not trusted."""
        held = [identity for identity, _ in computer.client.keyring.epochs.values()]
        for epoch, body in uploads:
            with self.subTest(epoch=epoch), self.assertRaises(keys.Refused, msg="a key it holds opens this part"):
                keys.open_with(held, body)

    def test_a_removal_during_compaction_s_pull_reaches_the_snapshot(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        b.item("notes", "b1", "written before computer 3 was removed")
        b.sync()
        fake, pull, uploads = self.world.fake, b.client.pull, self.snapshot_uploads()

        def pull_then_remove_computer_3():
            report = pull()
            fake.revoke(c.client.device_id)
            b.item("notes", "b1", "written after computer 3 was removed")
            return report

        with mock.patch.object(fake, "_rotation_due", lambda group: False), \
                mock.patch.object(b.client, "pull", pull_then_remove_computer_3):
            # The edit the pull's hook makes is not sent yet, so no snapshot may hold it under its old version.
            self.assertFalse(b.client.compact(force=True), "an edit not sent yet waits for a sync")
        self.assertEqual(uploads, [], "nothing was sealed")
        b.sync()
        self.assertTrue(b.client.compact(force=True))
        self.assertTrue(uploads, "the snapshot went up")
        self.assert_no_key_of(c, uploads)
        self.assertEqual({epoch for epoch, _ in uploads}, {2}, "the snapshot is sealed under a key computer 3 never held")
        self.assertIsNotNone(b.client.roster.entry(c.client.device_id)["removed"])

    def test_a_removal_during_compaction_s_pull_with_nothing_unsent_seals_only_under_the_new_key(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        b.item("notes", "b1", "written before computer 3 was removed")
        b.sync()
        fake, pull, uploads, removed = self.world.fake, b.client.pull, self.snapshot_uploads(), []

        def pull_then_remove_computer_3():
            report = pull()
            if not removed:
                removed.append(1)
                fake.revoke(c.client.device_id)
            return report

        with mock.patch.object(fake, "_rotation_due", lambda group: False), \
                mock.patch.object(b.client, "pull", pull_then_remove_computer_3):
            compacted = b.client.compact(force=True)
        self.assertTrue(removed, "the removal landed during compaction's pull")
        self.assert_no_key_of(c, uploads)  # whatever epoch a part names, no key computer 3 holds may open it
        self.assertTrue(compacted, "with nothing unsent, the compaction goes ahead")
        self.assertTrue(uploads, "the snapshot went up")
        self.assertEqual({epoch for epoch, _ in uploads}, {2}, "every part names the epoch computer 3 never held")
        self.assertIsNotNone(b.client.roster.entry(c.client.device_id)["removed"])

    def test_a_removal_after_compaction_s_last_check_seals_no_snapshot_under_the_old_key(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        b.item("notes", "b1", "written before computer 3 was removed")
        b.sync()
        fake, replica, snapshot_epochs = self.world.fake, records.replica, []
        landed = []

        def remove_computer_3_while_the_snapshot_is_read(db):
            if not landed:  # after compaction's pull, its checks and its last relay request
                landed.append(1)
                fake.revoke(c.client.device_id)
                b.item("notes", "b1", "written after computer 3 was removed")
            return replica(db)

        def note(method, path, headers):
            if method == "PUT" and path.startswith("/sync/packs/") and "x-superhistory-snapshot" in headers:
                snapshot_epochs.append(int(headers["x-superhistory-epoch"]))
        fake.before_hooks.append(note)
        with mock.patch.object(fake, "_rotation_due", lambda group: False), \
                mock.patch.object(records, "replica", remove_computer_3_while_the_snapshot_is_read):
            b.client.compact(force=True)
        self.assertTrue(landed, "the removal landed while the snapshot was read")
        self.assertNotIn(1, snapshot_epochs, "no snapshot went up under the key computer 3 holds")
        self.assertIsNotNone(b.client.roster.entry(c.client.device_id)["removed"])

    def test_approval_seals_nothing_for_a_joiner_the_account_revoked_after_it_asked(self):
        (a,), _ = self.world.group("computer 1")
        c = self.world.computer("computer 3")
        pairing = a.client.add_device()
        c.client.join(pairing.code)
        self.world.fake.remove(c.client.device_id)
        sent = []
        self.world.fake.before_hooks.append(
            lambda method, path, headers: sent.append(path) if (method, path) == ("PUT", "/sync/roster") else None)
        with self.assertRaises(keys.Refused):
            a.client.approve(pairing, lambda label, words: True, sleep=lambda s: None)
        self.assertEqual(sent, [], "no roster change or envelope went to the relay for the revoked joiner")
        self.assertIsNone(a.client.roster.entry(c.client.device_id))

    def test_approval_builds_on_the_roster_left_after_it_finishes_a_removal(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        c = self.world.computer("computer 3")
        self.world.fake.remove(b.client.device_id)
        self.hide_the_removal(refuse_rotation=False)
        pairing = a.client.add_device()
        c.client.join(pairing.code)
        put_roster = a.client.relay.put_roster

        def take_any_version(*args):
            try:
                return put_roster(*args)
            except relay.RelayError as exc:
                if exc.status != 409:
                    raise
        with mock.patch.object(a.client.relay, "put_roster", take_any_version):
            a.client.approve(pairing, lambda label, words: True, sleep=lambda s: None)
        self.assertEqual(a.client.roster.epoch, 2, "the removal's new key stays the current one")
        self.assertIsNotNone(a.client.roster.entry(b.client.device_id)["removed"])
        self.assertIsNotNone(a.client.roster.entry(c.client.device_id))

    def test_a_removal_landing_while_approval_asks_the_app_is_finished_before_the_joiner_is_added(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        c = self.world.computer("computer 3")
        pairing = a.client.add_device()
        c.client.join(pairing.code)
        fake, confirm, landed, sealed_with_b_active = self.world.fake, a.client._confirm_with_app, [], []

        def remove_computer_2_then_confirm(ro):
            if not landed and ro.entry(c.client.device_id):
                landed.append(1)
                fake.revoke(b.client.device_id)
            return confirm(ro)

        def note(method, path, headers):
            if (method, path) == ("PUT", "/sync/roster"):
                sealed_with_b_active.append(a.client.roster.entry(b.client.device_id)["removed"] is None)
        fake.before_hooks.append(note)
        with mock.patch.object(a.client, "_confirm_with_app", remove_computer_2_then_confirm):
            a.client.approve(pairing, lambda label, words: True, sleep=lambda s: None)
        self.assertTrue(landed, "computer 2 was removed while approval asked the app")
        self.assertEqual(sealed_with_b_active, [False], "computer 3 was added only after computer 2's removal")
        self.assertEqual(a.client.roster.epoch, 2)

    def removed_computer_s_pack(self, author, victim_item):
        """What a removed computer's keys can sign: a new item, and a mark that demotes another computer's item to it."""
        v = int(self.world.now * 1000)
        evil = author.key("after-removal")
        item = records.Record("item", "notes", evil, v, {"ts": "2026-10-02", "title": "Evil", "meta": {"hash": "e"},
                                                         "rows": ["written after the removal"]})
        mark = records.Record("supersede", "notes", victim_item, v, {"by": author.prefix, "new": ["notes", evil],
                                                                       "at": "2026-10-02"})
        self.world.forge(author, [item, mark], confirm=False)
        return evil

    def test_a_removed_computer_s_pack_after_its_removal_never_applies_and_its_last_one_before_does(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        mine = b.item("notes", "b1", "computer 2's note")
        for comp in (a, b, c):
            comp.sync()
        last = c.item("notes", "c1", "written on computer 3 before its removal")
        c.sync()
        self.world.fake.revoke(c.client.device_id)
        self.hide_the_removal(refuse_rotation=False)
        evil = self.removed_computer_s_pack(c, mine)
        self.world.tick()
        report = b.sync()
        self.assertIsNone(report.stalled)
        self.assertTrue(b.has("notes", last), "what computer 3 sent while a member still arrives")
        self.assertFalse(b.has("notes", evil), "what its keys signed after the removal never applies")
        self.assertIsNone(store.superseded_by(b.db, "notes", mine), "computer 2's own item is not demoted")
        self.assertIsNotNone(b.client.roster.entry(c.client.device_id)["removed"])

    def test_a_removal_landing_during_a_pull_cannot_bring_in_a_pack_signed_after_it(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        mine = b.item("notes", "b1", "computer 2's note")
        for comp in (a, b, c):
            comp.sync()
        self.hide_the_removal(refuse_rotation=False)
        fake, packs, landed, evil = self.world.fake, b.client.relay.packs, [], []

        def remove_computer_3_then_list(**page):
            if not landed:  # after the pull began, before it reads the packs
                landed.append(1)
                fake.revoke(c.client.device_id)
                evil.append(self.removed_computer_s_pack(c, mine))
            return packs(**page)
        with mock.patch.object(b.client.relay, "packs", remove_computer_3_then_list):
            b.client.pull()
        self.assertTrue(landed)
        self.assertFalse(b.has("notes", evil[0]))
        b.sync()
        self.assertFalse(b.has("notes", evil[0]), "nor once the removal is finished")
        self.assertIsNone(store.superseded_by(b.db, "notes", mine))
        self.assertIsNotNone(b.client.roster.entry(c.client.device_id)["removed"])

    def test_a_pack_the_relay_fills_in_after_a_removal_mid_pull_never_applies(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        mine = b.item("notes", "b1", "computer 2's note")
        for comp in (a, b, c):
            comp.sync()
        self.hide_the_removal(refuse_rotation=False)
        a.item("notes", "a1", "computer 1's note, read first")
        a.sync()
        listed_seq, _ = self.world.forge(c, [], confirm=False)  # the relay lists a pack it fills in later
        group, fetch, evil = self.world.fake.group(EMAIL), b.client.relay.pack, []

        def fill_in_after_the_removal(seq):
            if seq == listed_seq and not evil:
                self.world.fake.revoke(c.client.device_id)
                group.packs = [p for p in group.packs if p["seq"] != seq]
                evil.append(self.removed_computer_s_pack(c, mine))
                filled = group.packs.pop()
                group.packs.append({**filled, "seq": seq})
                group.packs.sort(key=lambda p: p["seq"])
            return fetch(seq)
        with mock.patch.object(b.client.relay, "pack", fill_in_after_the_removal):
            b.client.pull()
        self.assertTrue(evil, "the relay filled the listed pack in after the removal")
        self.assertTrue(b.has("notes", a.key("a1")), "the pack before it applied")
        self.assertFalse(b.has("notes", evil[0]))
        self.assertIsNone(store.superseded_by(b.db, "notes", mine))

    def test_a_removal_marks_the_last_pack_the_app_lists_even_when_the_relay_held_it_back(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        last = c.item("notes", "c1", "computer 3's last note before its removal")
        c.sync()
        self.world.fake.revoke(c.client.device_id)
        group = self.world.fake.group(EMAIL)
        held = [p for p in group.packs if p["seq"] == max(q["seq"] for q in group.packs)]
        group.packs = [p for p in group.packs if p not in held]
        self.assertIsNone(b.sync().stalled)
        entry = b.client.roster.entry(c.client.device_id)
        self.assertIsNotNone(entry["removed"], "computer 2 finished the removal without the held pack")
        self.assertEqual(entry["removed"]["head_dseq"], self.world.fake.chain(c.client.device_id)[0],
                         "at the last pack the app lists for computer 3, not at what computer 2 had applied")
        group.packs = sorted(group.packs + held, key=lambda p: p["seq"])
        self.assertIsNone(b.sync().stalled)
        self.assertTrue(b.has("notes", last), "computer 3's last note still arrives")

    def test_a_pack_sent_but_not_confirmed_is_confirmed_on_the_next_sync(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        with mock.patch.object(c.client.app, "confirm_chain", side_effect=relay.TransportError("app unreachable")):
            last = c.item("notes", "c1", "sent while the app was unreachable")
            with self.assertRaises(relay.TransportError):
                c.sync()
        c.sync()
        self.world.fake.revoke(c.client.device_id)
        b.sync()
        self.assertTrue(b.has("notes", last), "its next sync confirmed it before computer 3 was removed")

    def test_a_new_recovery_code_seals_nothing_for_a_revoked_computer_the_relay_hides(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        self.world.fake.remove(c.client.device_id)
        self.hide_the_removal(refuse_rotation=False)
        rotate, sealed_for = b.client.relay.rotate, []

        def watch(epoch, version, link, envelopes, recovery):
            sealed_for.append({device for device, _ in envelopes})
            return rotate(epoch, version, link, envelopes, recovery)
        with mock.patch.object(b.client.relay, "rotate", watch):
            self.assertRegex(b.client.new_recovery_code(), r"^VRC1-")
        self.assertTrue(sealed_for)
        self.assertTrue(all(c.client.device_id not in ids for ids in sealed_for),
                        "no envelope holding the new recovery tag is sealed for computer 3")
        self.assertIsNotNone(b.client.roster.entry(c.client.device_id)["removed"])

    def test_a_snapshot_announced_before_its_author_s_removal_counts_on_every_computer_behind_it(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        a.item("notes", "a1", "computer 1's note")
        a.sync()
        held = c.item("notes", "c1", "only the snapshot holds this once compaction runs")
        c.sync()
        self.assertTrue(c.client.compact(force=True))
        self.world.fake.remove(c.client.device_id)
        for comp in (b, a):  # both are behind the snapshot, and neither has seen the removal
            self.assertIsNone(comp.sync().stalled)
            self.assertTrue(comp.has("notes", held))
            self.assertIsNotNone(comp.client.roster.entry(c.client.device_id)["removed"])

    def test_a_remaining_member_s_snapshot_restores_what_the_removed_one_s_dropped(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        # An item computer 1 owns that no snapshot holds yet, which computer 2 has but computer 3 never synced.
        keep = a.key("keep")
        for store in (a.db, b.db):
            put_item(store, "notes", keep, "2026-10-01", "Keep", "a real item computer 3 never saw", {})
        c.item("notes", "c1", "computer 3's note")
        c.sync()
        self.assertTrue(c.client.compact(force=True))  # computer 3's snapshot lacks keep
        self.world.fake.remove(c.client.device_id)

        self.assertIsNone(b.sync().stalled)  # computer 2 is behind the snapshot and applies it
        self.assertFalse(b.has("notes", keep), "the removed computer's snapshot dropped an item it never saw")
        self.assertIsNotNone(b.client.roster.entry(c.client.device_id)["removed"])

        a.sync()  # computer 1 still holds keep; its pack and then its snapshot carry it back
        self.assertIsNone(a.sync().stalled)
        self.assertTrue(a.client.compact(force=True))
        self.assertIsNone(b.sync().stalled)
        self.assertTrue(b.has("notes", keep), "a remaining member's snapshot brings the item back; none is lost for good")

    def test_a_removed_author_s_snapshot_forked_below_its_frozen_last_pack_is_refused(self):
        # pr10-r6c finding 2: a removed author's snapshot announced strictly below its frozen confirmed last pack must
        # adopt only when its chain runs on to that frozen pack. _continues checks the frozen hash at equality only, so
        # the below-frozen announcement passes it; the _read_ahead call at client.py:679 reads the chain forward to the
        # frozen pack and refuses a fork. Deleting that call lets the forged snapshot's records and deletions land.
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        keepsake = a.item("notes", "keepsake", "an item computer 1 owns")
        a.sync()
        b.sync()  # computer 2 holds keepsake and stays here, behind the forged snapshot
        self.assertTrue(b.has("notes", keepsake))
        cursor_before = state.get(b.db, "cursor", 0)
        ha = state.head(b.db, a.client.device_id)  # the head computer 2 recorded for computer 1
        c.item("notes", "c1", "computer 3's first real pack")
        c.sync()
        c.item("notes", "c2", "computer 3's frozen last pack")
        c.sync()
        frozen = self.world.fake.chain(c.client.device_id)  # (2, hc2), the app's frozen last for computer 3
        self.assertEqual(frozen[0], 2)
        self.world.fake.revoke(c.client.device_id)

        group, cc = self.world.fake.group(EMAIL), c.client
        through, sid, v = max(p["seq"] for p in group.packs), str(uuid.uuid4()), int(self.world.now * 1000)
        self.assertGreater(through, cursor_before, "computer 2 is behind the snapshot")
        heads = {a.client.device_id: list(ha), cc.device_id: [0, ""]}
        evil = records.Record("item", "notes", c.key("evil"), v,
                              {"ts": "2026-10-02", "title": "Evil", "meta": {"hash": "e"}, "rows": ["from the forged snapshot"]})
        part = pack.build({"epoch": cc.keyring.newest, "author": cc.device_id, "dseq": None, "prev": None,
                           "roster_version": cc.roster.version, "roster_hash": cc.roster.hash,
                           "snapshot": {"id": sid, "through": through, "part": 0, "parts": 1, "heads": heads}},
                          [records.to_json(evil)], cc.paths.device_ssh)
        announce = records.Record("snapshot", "", "", v, {"id": sid, "through": through, "parts": 1,
                                                          "manifest": pack.manifest([part])})
        ann = pack.build({"epoch": cc.keyring.newest, "author": cc.device_id, "dseq": 1, "prev": "",
                          "roster_version": cc.roster.version, "roster_hash": cc.roster.hash, "snapshot": None},
                         [records.to_json(announce)], cc.paths.device_ssh)
        self.world.put_raw(pack.seal(part, cc.keyring.recipient(cc.keyring.newest)), cc.keyring.newest, snapshot_id=sid)
        self.world.put_raw(pack.seal(ann, cc.keyring.recipient(cc.keyring.newest)), cc.keyring.newest)
        group.snapshot = (sid, through)

        report = b.client.pull()
        self.assertFalse(b.has("notes", c.key("evil")), "the forged snapshot's record did not apply")
        self.assertTrue(b.has("notes", keepsake), "the forged snapshot's deletions did not apply")
        self.assertIsNotNone(report.stalled, "the forged snapshot stalled sync")

    def test_recovery_envelope_older_than_relay_state_is_caught(self):
        a = self.world.computer("computer 1")
        code, _ = a.client.init(self.world.browser())
        group = self.world.fake.group(EMAIL)
        older = {"epoch": group.epoch, "roster_version": group.roster_version,
                 "blob": base64.b64encode(group.recovery).decode()}
        self.world.pair(a, self.world.computer("computer 2"))
        self.world.fake.rewrite(lambda m, p, s, out: (s, older) if (m, p) == ("GET", "/sync/recovery") else (s, out))

        c = self.world.computer("computer 3")
        with self.assertRaises(keys.Refused):
            c.client.recover(code, self.world.browser())
        self.assertFalse([d for d, dev in self.world.fake.devices.items() if dev["revoked"]], "nothing was revoked")
        self.assertEqual((group.epoch, group.roster_version), (1, 2))

    def test_recovery_anchors_on_the_app_s_head_not_the_recovery_envelope(self):
        # pr10-r6c finding 1: recovery must take its trusted start from the app's roster head, not the head the
        # recovery envelope names. A removed insider that still knows the recovery code seals an envelope naming a
        # link it forged a version above the account head, with an honest member as the named signer but the removed
        # key's signature, and a recovery key only the attacker holds. Anchoring on that head (the mutation
        # `self._anchor(ring, own=head)`) trusts the forged link by its hash alone; anchoring on the app's head walks
        # to it and refuses, since its signature does not verify under the honest signer.
        (a, b, c), code = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        base, base_ring = c.client.roster, c.client.keyring  # the real roster, held by the removed insider
        self.world.fake.revoke(c.client.device_id)
        group, held, tag = self.world.fake.group(EMAIL), keys.new_identity(), secrets.token_bytes(32)
        ring = base_ring.with_recovery(keys.recipient_of(held), tag, base.version + 1)
        signer = next(e["device_id"] for e in base.active() if e["device_id"] != c.client.device_id)
        gone = {"in_version": base.version + 1, "head_dseq": 0, "head_hash": "", "clear": True}
        entries = [{**e, "removed": gone} if e["device_id"] == c.client.device_id else e for e in base.entries]
        forged = roster.Roster(roster.new_link(base, base.epoch, signer, entries, ring))
        group.roster[forged.version] = roster.seal_link(forged.link, c.client.paths.device_ssh, ring.recipient(base.epoch))
        group.roster_version, group.epoch = forged.version, base.epoch
        rident, rtag = keys.recovery_keys(keys.parse_recovery_code(code))
        body = {"keyring": ring.to_json(), "roster": {"version": forged.version, "hash": forged.hash}}
        group.recovery = pack.seal_tagged(rtag, keys.recipient_of(rident), body, _recovery_aad(base.epoch, forged.version))
        self.assertGreater(forged.version, self.world.fake.head(self.world.fake.user_of(EMAIL))[0],
                           "the forged link sits above the account's roster head")

        d = self.world.computer("computer 4")
        victim = f"{c.prefix}:victim"
        put_item(d.db, "notes", victim, "2026-10-01", "V", "a removed computer's item", {})
        d.db.commit()
        before, revoked_before = len(self.world.fake.log), self._revoked_ids()
        try:
            d.client.recover(code, self.world.browser())
        except (keys.Refused, SyncError, relay.RelayError, relay.TransportError):
            pass
        confirms = [e for e in self.world.fake.log[before:] if e[:2] == ("PUT", "/v1/roster")]
        self.assertEqual(confirms, [], "no roster confirmation was sent for the forged link")
        saved = keys.load_json(d.client.paths.keyring)
        self.assertTrue(saved is None or saved["recovery"]["recipient"] != keys.recipient_of(held),
                        "no key only the attacker holds was saved")
        self.assertTrue(d.has("notes", victim), "the forged clear deleted nothing")
        self.assertEqual(self._revoked_ids(), revoked_before, "recovery revoked no computer off the forged link")

    def _revoked_ids(self):
        return {d for d, dev in self.world.fake.devices.items() if dev["revoked"]}

    def test_replayed_older_record_after_delete(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        key = a.item("notes", "x", "will be deleted")
        a.sync()
        b.sync()
        group = self.world.fake.group(EMAIL)
        carried = dict(group.packs[-1])
        a.client.db.execute("DELETE FROM docs WHERE source_id=?", (key,))
        a.sync()
        b.sync()
        self.assertFalse(b.has("notes", key))
        self.world.put_raw(carried["blob"], carried["epoch"])
        self.assertIsNotNone(b.sync().stalled)
        self.assertFalse(b.has("notes", key), "the replayed Item does not bring the item back")

    def test_removed_author_fork_below_head_is_refused(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        group = self.world.fake.group(EMAIL)
        first = c.item("notes", "c1", "first")
        c.sync()
        b.sync()
        self.assertTrue(b.has("notes", first))
        second = c.item("notes", "c2", "second")
        c.sync()
        real = group.packs[-1]
        third = c.item("notes", "c3", "third")
        c.sync()
        a.sync()

        def no_compaction(method, path, headers):
            if method == "POST" and path == "/sync/compact":
                raise relay.TransportError("held back")
        self.world.fake.before(no_compaction)
        with self.assertRaises(relay.TransportError):
            a.client.remove("computer 3", self.world.browser(), clear=False)
        self.world.fake.before_hooks.clear()
        self.assertEqual(b.client.roster.entry(c.client.device_id)["removed"], None, "b has not seen the removal yet")

        dseq, prev = state.head(b.db, c.client.device_id)
        cc = c.client
        forked = records.Record("item", "notes", c.key("forked"), int(self.world.now * 1000),
                                {"ts": "t", "title": "Forked", "meta": {"hash": "f"}, "rows": ["written after removal"]})
        plain = pack.build({"epoch": real["epoch"], "author": cc.device_id, "dseq": dseq + 1, "prev": prev,
                            "roster_version": cc.roster.version, "roster_hash": cc.roster.hash, "snapshot": None},
                           [records.to_json(forked)], cc.paths.device_ssh)
        honest, real["blob"] = real["blob"], pack.seal(plain, cc.keyring.recipient(real["epoch"]))

        report = b.sync()
        self.assertFalse(b.has("notes", c.key("forked")), "nothing from the fork applies")
        self.assertFalse(b.has("notes", second) or b.has("notes", third))
        self.assertIn("removed computer", report.stalled or "")
        self.assertEqual(state.head(b.db, cc.device_id), (dseq, prev))
        self.assertLess(state.get(b.db, "cursor", 0), real["seq"], "sync stalls on the forked pack")

        real["blob"] = honest
        self.assertIsNone(b.sync().stalled)
        self.assertTrue(b.has("notes", second) and b.has("notes", third), "the real chain applies once it is served")
        self.assertFalse(b.has("notes", c.key("forked")))

    def test_older_snapshot_cannot_roll_back(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        group = self.world.fake.group(EMAIL)
        for name in ("c1", "c2", "c3"):
            c.item("notes", name, name)
            c.sync()
        a.item("notes", "a1", "before the snapshot")
        a.sync()
        first = dict(group.packs[-1])
        self.assertTrue(a.client.compact(force=True))
        older = (group.snapshot, [dict(p) for p in group.packs])
        announce = dict(group.packs[-1])
        newer = a.item("notes", "newer", "written after the snapshot")
        a.sync()
        latest = dict(group.packs[-1])

        group.snapshot = None
        group.packs = [dict(first, seq=1), dict(announce, seq=2), dict(latest, seq=3)]
        report = b.client.pull()
        self.assertRegex(report.stalled or "", r"has not served packs confirmed by .*computer 3",
                      "a relay that keeps back packs computer 3 confirmed stops the pull, visibly")
        self.assertTrue(b.has("notes", newer))

        group.snapshot, group.packs = older
        self.assertGreater(group.snapshot[1], state.get(b.db, "cursor"), "the older snapshot reaches past the cursor")
        report = b.client.pull()
        self.assertTrue(b.has("notes", newer), "an older snapshot does not roll back what was applied")
        self.assertIsNotNone(report.stalled, "and the packs the relay rolled back stop the pull at their author's chain")

    def test_a_snapshot_that_does_not_list_a_computer_that_joined_after_it_keeps_its_items(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        group = self.world.fake.group(EMAIL)
        for name in ("c1", "c2", "c3"):
            c.item("notes", name, name)
            c.sync()
        a.item("notes", "a1", "before the snapshot")
        a.sync()
        first = dict(group.packs[-1])
        self.assertTrue(a.client.compact(force=True))
        older = (group.snapshot, [dict(p) for p in group.packs])
        d = self.world.computer("computer 4")
        self.world.pair(a, d)
        joiner = d.item("notes", "d1", "from a computer that joined after the snapshot")
        d.sync()
        late = dict(group.packs[-1])

        group.snapshot = None
        group.packs = [dict(first, seq=1), dict(late, seq=2)]
        report = b.client.pull()
        self.assertRegex(report.stalled or "", r"has not served packs confirmed by .*computer 3",
                      "a relay that keeps back packs computer 3 confirmed stops the pull, visibly")
        self.assertTrue(b.has("notes", joiner))
        group.snapshot, group.packs = older
        report = b.client.pull()
        self.assertTrue(b.has("notes", joiner), "a snapshot keeps the items of an author it does not reach")
        self.assertIsNone(report.stalled, "an author the snapshot predates is ordinary, not a reason to stop")

    def test_hidden_pack_stalls_instead_of_dropping(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        first = a.item("notes", "first", "hidden by the relay")
        a.sync()
        second = a.item("notes", "second", "after the hidden one")
        a.sync()
        group = self.world.fake.group(EMAIL)
        group.packs[-2]["snapshot_id"] = str(uuid.uuid4())
        report = b.sync()
        self.assertIn("gap", report.stalled or "")
        self.assertFalse(b.has("notes", first))
        self.assertFalse(b.has("notes", second))


if __name__ == "__main__":
    unittest.main()
