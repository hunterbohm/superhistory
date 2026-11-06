"""The app's record of the sync group: who added whom, a crash that heals, and a revoked member's made-up claims, which
stop holding the others back as soon as it is revoked."""
import unittest
from unittest import mock

from kb.sync import relay, state

from .support import EMAIL, World, rows

FAR, MADE_UP = 2 ** 31 - 1, "f" * 64


class MembershipTest(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.user = self.world.fake.user_of(EMAIL)

    def tearDown(self):
        self.world.close()

    def members(self):
        """{device_id: who added it} for the account's current members."""
        group = self.world.fake.members.get(self.user, {})
        return {d: by for d, (by, _) in group.items() if self.world.fake.is_member(d)}

    def adds(self):
        return [e for e in self.world.fake.log if e[:2] == ("POST", "/v1/members")]

    def steal(self, computer):
        """What a stolen device token does: it confirms a roster link and a pack of its own that nothing will reach."""
        app = relay.App(self.world.fake)
        app.confirm_roster(computer.client.app_token, FAR, MADE_UP)
        app.confirm_chain(computer.client.app_token, FAR, MADE_UP)
        self.assertEqual(self.world.fake.head(self.user), (FAR, MADE_UP), "while it is a member, the head is what it said")

    def test_setting_up_pairing_and_recovering_record_who_added_whom(self):
        (a, b), code = self.world.group("computer 1", "computer 2")
        ids = a.client.device_id, b.client.device_id
        self.assertEqual(self.members(), {ids[0]: ids[0], ids[1]: ids[0]})
        a.lose_everything()
        b.lose_everything()

        c = self.world.computer("computer 3")
        c.client.recover(code, self.world.browser())
        me = c.client.device_id
        self.assertEqual(self.members(), {me: me}, "recovery made it the sole member under the account's session")
        self.assertEqual(self.world.fake.head(self.user), (c.client.roster.version, c.client.roster.hash),
                         "and it moved the head with no other computer online")

    def test_a_crash_before_the_joiner_is_recorded_heals_on_the_approver_s_next_sync(self):
        a = self.world.computer("computer 1")
        a.client.init(self.world.browser())
        b = self.world.computer("computer 2")
        pairing = a.client.add_device()
        b.client.join(pairing.code)
        with mock.patch.object(a.client.app, "add_member", side_effect=relay.TransportError("the app is unreachable")):
            a.client.approve(pairing, lambda label, words: True, sleep=lambda s: None)
        b.client.finish_join(sleep=lambda s: None)
        self.assertNotIn(b.client.device_id, self.members(), "the call that records the joiner never landed")

        a.open()  # the approver starts again
        self.assertIsNone(a.sync().stalled)
        self.assertEqual(self.members().get(b.client.device_id), a.client.device_id, "its next sync records the joiner")
        written = b.item("notes", "b1", "written once the joiner is a member")
        self.assertIsNone(b.sync().stalled)
        self.assertIsNone(a.sync().stalled)
        self.assertTrue(a.has("notes", written))

        before = len(self.adds())
        a.open()
        a.sync()
        self.assertEqual(len(self.adds()), before, "a second start finds every computer recorded and adds nothing")

    def test_a_computer_that_is_not_a_member_records_no_one(self):
        (a, b), _ = self.world.group("computer 1", "computer 2")
        self.world.fake.members[self.user].pop(b.client.device_id)
        self.world.fake.members[self.user].pop(a.client.device_id)
        before = len(self.adds())
        b.sync()
        self.assertEqual(len(self.adds()), before)
        self.assertEqual(self.members(), {})

    def test_revoking_a_member_whose_token_was_stolen_lets_the_others_sync_again(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        self.world.pair(a, self.world.computer("computer 4"))  # a roster change computer 3 has not walked yet
        written = b.item("notes", "b1", "written on computer 2 before its token was stolen")
        b.sync()  # computer 3 has pulled neither
        self.steal(b)
        next_link = a.client.roster.version + 1  # the link computer 3 makes when it removes computer 2
        relay.App(self.world.fake).confirm_roster(b.client.app_token, next_link, MADE_UP)
        self.assertRegex(c.sync().stalled or "", "not the one the account lists", "the made-up head holds computer 3")

        self.world.fake.remove(b.client.device_id, False)
        self.assertEqual(self.world.fake.head(self.user)[0], a.client.roster.version, "revoking it dropped its claims")
        self.assertIsNone(c.sync().stalled, "computer 3 syncs again, with nothing edited by hand")
        self.assertTrue(c.has("notes", written), "what computer 2 confirmed before the theft still arrives")
        self.assertEqual(c.client.roster.version, next_link)
        self.assertIsNotNone(c.client.roster.entry(b.client.device_id)["removed"])
        self.assertIsNone(a.sync().stalled, "the link computer 2's token claimed first blocks no one")
        mine = a.item("notes", "a1", "written after the removal")
        a.sync()
        c.sync()
        self.assertTrue(c.has("notes", mine))
        self.assertEqual(self.world.fake.head(self.user), (a.client.roster.version, a.client.roster.hash))

    def test_a_snapshot_a_member_made_still_counts_after_its_stolen_token_made_up_a_last_pack(self):
        (a, b, c), _ = self.world.group("computer 1", "computer 2", "computer 3")
        for comp in (a, b, c):
            comp.sync()
        held = b.item("notes", "b1", "only the snapshot holds this once compaction runs")
        b.sync()
        self.assertTrue(b.client.compact(force=True))  # computers 1 and 3 are behind the snapshot
        self.steal(b)
        self.world.fake.remove(b.client.device_id, False)
        for comp in (c, a):
            self.assertIsNone(comp.sync().stalled)
            self.assertTrue(comp.has("notes", held))
            self.assertIsNotNone(comp.client.roster.entry(b.client.device_id)["removed"])

    def test_recovery_after_every_computer_was_revoked_on_the_website_starts_from_a_real_link(self):
        (a, b), code = self.world.group("computer 1", "computer 2")
        a.item("notes", "a1", "from the first computer")
        a.sync()
        b.sync()
        expected = rows(a.db)
        self.steal(b)
        for comp in (a, b):
            self.world.fake.remove(comp.client.device_id, False)
        self.assertEqual(self.world.fake.head(self.user), (0, None), "no member stands behind any link")
        a.lose_everything()
        b.lose_everything()

        c = self.world.computer("computer 3")
        c.client.recover(code, self.world.browser())
        self.assertEqual(rows(c.db), expected)
        self.assertEqual(self.members(), {c.client.device_id: c.client.device_id})
        self.assertEqual(state.get(c.db, "confirmed_roster"), [c.client.roster.version, c.client.roster.hash])


if __name__ == "__main__":
    unittest.main()
