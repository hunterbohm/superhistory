"""One reader per online account: a second computer that reads a Gmail or GitHub account another computer in the group
already reads is refused with a plain message, and the first keeps syncing."""
import hashlib
import logging
import re
import unittest
from unittest import mock

from kb import config as config_mod, ingest, store
from kb.sync import keys, roster
from kb.sync.client import SyncError

from .network import Network, run_cli
from .support import EMAIL, World

DOMAIN = "gmail.com"
ADDRESS = f"person@{DOMAIN}"
GMAIL = f'[sources.gmail]\naddress = "{ADDRESS}"\n'
GMAIL_SPELLED_OTHERWISE = '[sources.work_mail]\ntype = "gmail"\naddress = "Per.Son@GoogleMail.com"\n'
GITHUB = '[sources.github]\ninvolves = "octocat"\n'
SECRETS = ("person@", "gmail.com", "googlemail", "octocat")
HEX64 = re.compile(r"[0-9a-f]{64}")


def digest(key):
    return hashlib.sha256(b"superhistory-sync-account\0" + key.encode()).hexdigest()


class AccountsTest(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.fake = self.world.fake
        self.addCleanup(self.world.close)

    def start(self, name, *sources):
        """A computer that reads `sources` and sets up the group."""
        computer = self.world.computer(name)
        computer.reads(*sources)
        computer.client.init(self.world.browser())
        return computer

    def join(self, member, name, *sources):
        computer = self.world.computer(name)
        computer.reads(*sources)
        self.world.pair(member, computer)
        return computer

    def accounts_of(self, computer, viewer=None):
        return (viewer or computer).client.roster.entry(computer.client.device_id)["accounts"]

    def sent_to_sync(self, since):
        return [e for e in self.fake.log[since:] if e[0] != "GET" and e[1].startswith("/sync") and e[1] != "/sync/applied"]

    def assertPlain(self, text):
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertIsNone(HEX64.search(text), "no digest in a message")

    def refused_at_join(self, first, second, source_name):
        a = self.start("laptop", first)
        b = self.world.computer("desk")
        b.reads(second)
        pairing = a.client.add_device()
        b.client.join(pairing.code)
        asked = []
        with self.assertRaises(SyncError) as at_a:
            a.client.approve(pairing, lambda label, words: asked.append(label) or True, sleep=lambda s: None)
        with self.assertRaises(SyncError) as at_b:
            b.client.finish_join(sleep=lambda s: None)

        self.assertEqual(asked, ["desk"], "the words are asked first, as for any joiner")
        said_a, said_b = str(at_a.exception), str(at_b.exception)
        self.assertIn('"desk"', said_a)
        self.assertIn('"laptop"', said_a)
        self.assertIn("superhistory sync add-device", said_a)
        self.assertIn(f"[sources.{source_name}]", said_b)
        self.assertIn('"laptop"', said_b)
        self.assertIn("`superhistory sync join`", said_b)
        self.assertIn("`superhistory forget", said_b)
        for said in (said_a, said_b):
            self.assertPlain(said)
        self.assertEqual(self.fake.group(EMAIL).roster_version, 1, "the relay's roster did not change")
        self.assertEqual(a.client.roster.version, 1)
        self.assertEqual([e["label"] for e in a.client.roster.active()], ["laptop"])
        self.assertIsNone(b.client.roster, "the refused computer trusts no roster")
        self.assertFalse(self.fake.is_member(b.client.device_id))

        written = a.item("notes", "n1", "read on the first computer after the refusal")
        self.assertIsNone(a.sync().stalled)
        third = self.join(a, "third")
        self.assertIsNone(third.sync().stalled)
        self.assertTrue(third.has("notes", written), "the first computer keeps syncing")

    def test_a_second_computer_on_the_same_gmail_account_is_refused_at_join_on_both_sides(self):
        self.refused_at_join(GMAIL, GMAIL, "gmail")

    def test_a_second_computer_on_the_same_github_account_is_refused_at_join_on_both_sides(self):
        self.refused_at_join(GITHUB, GITHUB, "github")

    def test_a_config_that_spells_the_gmail_address_otherwise_still_clashes(self):
        self.assertEqual(config_mod.parse(GMAIL + "[sync]\n").account_keys()["gmail"],
                         config_mod.parse(GMAIL_SPELLED_OTHERWISE + "[sync]\n").account_keys()["work_mail"])
        self.refused_at_join(GMAIL, GMAIL_SPELLED_OTHERWISE, "work_mail")

    def test_a_joiner_whose_words_are_refused_gets_no_refusal_even_when_its_accounts_clash(self):
        a = self.start("laptop", GMAIL)
        b = self.world.computer("stranger")
        b.reads(GMAIL)
        pairing = a.client.add_device()
        b.client.join(pairing.code)
        slot = next(iter(self.fake.slots))
        since = len(self.fake.log)

        with self.assertRaisesRegex(SyncError, "Not added"):
            a.client.approve(pairing, lambda label, words: False, sleep=lambda s: None)
        self.assertEqual([e for e in self.fake.log[since:] if e[1].endswith("/welcome")], [], "no welcome was written")
        self.assertNotIn(slot, self.fake.slots, "the slot is deleted")
        self.assertEqual(self.fake.group(EMAIL).roster_version, 1)

    def test_a_computer_that_reads_a_different_account_joins(self):
        a = self.start("laptop", GMAIL)
        b = self.join(a, "desk", GITHUB)
        self.assertEqual(self.accounts_of(b, a), [digest("github:octocat")])
        self.assertEqual(self.accounts_of(a, b), [digest(f"gmail:{ADDRESS}")])

    def test_init_and_recover_put_the_digests_of_this_computer_s_accounts_in_its_entry(self):
        a = self.start("laptop", GMAIL, GITHUB)
        self.assertEqual(self.accounts_of(a), sorted([digest(f"gmail:{ADDRESS}"), digest("github:octocat")]))
        code, gone = a.client.new_recovery_code(), a.client.device_id
        a.lose_everything()
        c = self.world.computer("replacement")
        c.reads(GMAIL)
        c.client.recover(code, self.world.browser())
        self.assertEqual(self.accounts_of(c), [digest(f"gmail:{ADDRESS}")], "it replaces the computer it revokes")
        self.assertIsNotNone(c.client.roster.entry(gone)["removed"])
        raw = repr(c.client.roster.link)
        self.assertNotIn("person@", raw)

    def test_a_member_that_adds_a_taken_account_is_refused_on_its_next_sync_and_the_first_keeps_syncing(self):
        a = self.start("laptop", GMAIL)
        b = self.join(a, "desk")
        b.reads(GMAIL)
        stuck = b.item("notes", "b1", "written on the second computer while it is refused")
        version, head = self.fake.group(EMAIL).roster_version, self.fake.group(EMAIL).next_seq
        since = len(self.fake.log)

        with self.assertRaises(SyncError) as refused:
            b.sync()
        said = str(refused.exception)
        self.assertIn("[sources.gmail]", said)
        self.assertIn('"laptop"', said)
        self.assertIn("`superhistory sync`", said)
        self.assertPlain(said)
        self.assertEqual(self.sent_to_sync(since), [], "no pack and no link left the refused computer")
        self.assertEqual((self.fake.group(EMAIL).roster_version, self.fake.group(EMAIL).next_seq), (version, head))

        mine = a.item("notes", "a1", "written on the first computer")
        self.assertIsNone(a.sync().stalled)
        self.assertFalse(a.has("notes", stuck))

        b.reads()
        self.assertIsNone(b.sync().stalled, "removing the source from the config lets it sync again")
        self.assertTrue(b.has("notes", mine))
        self.assertIsNone(a.sync().stalled)
        self.assertTrue(a.has("notes", stuck), "what it held back arrives once it syncs")
        self.assertEqual(self.accounts_of(b, a), [])

    def test_a_member_that_adds_an_account_alone_claims_it_with_one_link_of_its_own(self):
        a = self.start("laptop")
        b = self.join(a, "desk")
        a.reads(GMAIL)
        before = a.client.roster.version
        self.assertIsNone(a.sync().stalled)
        self.assertEqual(a.client.roster.version, before + 1)
        link = a.client.roster.link
        self.assertEqual(link["signer"], a.client.device_id)
        self.assertEqual(self.accounts_of(a), [digest(f"gmail:{ADDRESS}")])
        self.assertEqual(self.accounts_of(b, a), [], "only its own entry changed")
        self.assertIsNone(b.sync().stalled)
        self.assertEqual(self.accounts_of(a, b), [digest(f"gmail:{ADDRESS}")])
        self.assertIsNone(a.sync().stalled)
        self.assertEqual(a.client.roster.version, before + 1, "a claim that has not changed writes no link")

    def test_after_ingest_records_the_refusal_as_the_sync_unit_and_a_config_without_the_source_syncs_again(self):
        a = self.start("laptop", GMAIL)
        b = self.join(a, "desk")
        net = Network()
        net.relay = self.fake
        log = logging.getLogger("kb.test-accounts")

        def after(text):
            with open(b.paths.config_file, "w", encoding="utf-8") as fh:
                fh.write(text + "\n[sync]\n")
            with net.wired(), self.assertLogs(log, "ERROR") if "gmail" in text else mock.MagicMock():
                return ingest.after(b.paths, config_mod.load(b.paths.config_file), b.db, log)

        since = len(self.fake.log)
        self.assertEqual(after(GMAIL), 1)
        failure = store.get_watermark(b.db, store.STATE, "fail:sync")
        self.assertIn("[sources.gmail]", failure)
        self.assertPlain(failure)
        self.assertEqual(self.sent_to_sync(since), [])

        self.assertEqual(after(""), 0)
        self.assertIsNone(store.get_watermark(b.db, store.STATE, "fail:sync"))

    def test_the_sync_command_refuses_in_plain_words_and_status_lists_the_sources_it_reads(self):
        a = self.start("laptop", GMAIL)
        b = self.join(a, "desk")
        net = Network()
        net.relay = self.fake

        def kb(computer, *argv):
            with net.wired():
                return run_cli(computer.home, *argv)

        with open(a.paths.config_file, "w", encoding="utf-8") as fh:
            fh.write(GMAIL + "\n[sync]\n")
        code, out = kb(a, "sync", "status")
        self.assertEqual(code, 0, out)
        self.assertIn("[sources.gmail]", out)
        self.assertPlain(out)

        with open(b.paths.config_file, "w", encoding="utf-8") as fh:
            fh.write(GMAIL + "\n[sync]\n")
        code, out = kb(b, "sync")
        self.assertEqual(code, 1, out)
        self.assertIn("[sources.gmail]", out)
        self.assertIn('"laptop"', out)
        self.assertPlain(out)
        code, out = kb(b, "sync", "status")
        self.assertEqual(code, 1, out)
        self.assertIn("[sources.gmail]", out)
        self.assertIn("Problem:", out)
        self.assertPlain(out)

        with open(b.paths.config_file, "w", encoding="utf-8") as fh:
            fh.write("[sync]\n")
        code, out = kb(b, "sync")
        self.assertEqual(code, 0, out)

    def test_moving_a_reader_the_first_removes_the_source_and_syncs_then_the_second_adds_it(self):
        a = self.start("laptop", GMAIL)
        b = self.join(a, "desk")
        b.reads(GMAIL)
        with self.assertRaises(SyncError):
            b.sync()

        a.reads()
        self.assertIsNone(a.sync().stalled)
        self.assertEqual(self.accounts_of(a), [], "its claim went with the source")
        self.assertIsNone(b.sync().stalled)
        self.assertEqual(self.accounts_of(b), [digest(f"gmail:{ADDRESS}")])

        a.reads(GMAIL)
        with self.assertRaises(SyncError) as back:
            a.sync()
        self.assertIn('"desk"', str(back.exception))

    def test_removing_a_computer_frees_its_accounts_for_another(self):
        a = self.start("laptop", GMAIL)
        b = self.join(a, "desk")
        c = self.join(a, "tablet")
        c.reads(GMAIL)
        with self.assertRaises(SyncError):
            c.sync()

        self.fake.remove(a.client.device_id, False)
        self.assertIsNone(b.sync().stalled)
        self.assertIsNotNone(b.client.roster.entry(a.client.device_id)["removed"])
        self.assertIsNone(c.sync().stalled, "the removal link landed, so the claim is free")
        self.assertEqual(self.accounts_of(c), [digest(f"gmail:{ADDRESS}")])

    def test_two_computers_adding_one_account_at_once_land_exactly_one_claim(self):
        a = self.start("laptop")
        b = self.join(a, "desk")
        a.sync()
        b.sync()
        a.reads(GMAIL)
        b.reads(GMAIL)
        landed = []
        real = b.client._may_write_next_link

        def other_lands_first():
            if not landed:
                landed.append(a.sync())
            return real()

        with mock.patch.object(b.client, "_may_write_next_link", other_lands_first):
            with self.assertRaises(SyncError) as refused:
                b.sync()
        self.assertIsNone(landed[0].stalled)
        self.assertIn('"laptop"', str(refused.exception))
        self.assertEqual(self.accounts_of(a, b), [digest(f"gmail:{ADDRESS}")])
        self.assertEqual(self.accounts_of(b, b), [])
        holders = [e["label"] for e in b.client.roster.active() if digest(f"gmail:{ADDRESS}") in e["accounts"]]
        self.assertEqual(holders, ["laptop"])


class RosterRulesTest(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.a = self.world.computer("laptop")
        self.a.reads(GMAIL)
        self.a.client.init(self.world.browser())
        self.b = self.world.computer("desk")
        self.world.pair(self.a, self.b)
        self.ro, self.ring = self.a.client.roster, self.a.client.keyring

    def link(self, signer, entries, epoch=None, ring=None):
        ring = ring or self.ring
        epoch = epoch or self.ro.epoch
        link = roster.new_link(self.ro, epoch, signer.client.device_id, entries, ring)
        blob = roster.seal_link(link, signer.client.paths.device_ssh, ring.recipient(epoch))
        return roster.open_link(blob, ring)

    def with_accounts(self, who, accounts):
        return [{**e, "accounts": accounts} if e["device_id"] == who.client.device_id else e for e in self.ro.entries]

    def test_an_entry_may_change_its_own_accounts_in_a_link_it_signed(self):
        mine = [digest("github:octocat")]
        got = self.ro.accept(*self.link(self.b, self.with_accounts(self.b, mine)))
        self.assertEqual(got.entry(self.b.client.device_id)["accounts"], mine)

    def test_a_link_may_not_change_the_accounts_of_another_entry(self):
        for signer, target in ((self.b, self.a), (self.a, self.b)):
            with self.subTest(signer=signer.name):
                with self.assertRaisesRegex(keys.Refused, "online account"):
                    self.ro.accept(*self.link(signer, self.with_accounts(target, [digest("github:octocat")])))

    def test_a_link_may_not_change_two_entries_accounts_at_once(self):
        entries = self.with_accounts(self.b, [digest("github:octocat")])
        entries = [{**e, "accounts": []} if e["device_id"] == self.a.client.device_id else e for e in entries]
        with self.assertRaisesRegex(keys.Refused, "online account"):
            self.ro.accept(*self.link(self.a, entries))

    def test_a_rotation_keeps_every_entry_s_accounts(self):
        ring = self.ring.with_epoch(self.ring.newest + 1, keys.new_identity())
        kept = self.with_accounts(self.a, self.ro.entry(self.a.client.device_id)["accounts"])
        self.assertEqual(self.ro.accept(*self.link(self.a, kept, epoch=2, ring=ring)).epoch, 2)
        with self.assertRaisesRegex(keys.Refused, "online account"):
            self.ro.accept(*self.link(self.a, self.with_accounts(self.a, []), epoch=2, ring=ring))

    def test_a_link_that_removes_a_computer_keeps_its_accounts(self):
        ring = self.ring.with_epoch(self.ring.newest + 1, keys.new_identity())
        removed = {"in_version": self.ro.version + 1, "head_dseq": 0, "head_hash": "", "clear": False}
        entries = [{**e, "removed": removed, "accounts": []} if e["device_id"] == self.a.client.device_id else e
                   for e in self.ro.entries]
        with self.assertRaisesRegex(keys.Refused, "online account"):
            self.ro.accept(*self.link(self.b, entries, epoch=2, ring=ring))

    def test_a_new_entry_may_carry_accounts(self):
        c = self.world.computer("tablet")
        c.reads(GITHUB)
        new = {**c.client._my_entry(self.ro.version + 1)}
        got = self.ro.accept(*self.link(self.a, self.ro.entries + [new]))
        self.assertEqual(got.entry(c.client.device_id)["accounts"], [digest("github:octocat")])

    def test_no_link_gives_one_digest_to_two_active_entries(self):
        taken = digest(f"gmail:{ADDRESS}")
        entries = self.with_accounts(self.b, [taken])
        with self.assertRaisesRegex(keys.Refused, "online account"):
            roster.check_link(roster.new_link(self.ro, self.ro.epoch, self.a.client.device_id, entries, self.ring))
        with self.assertRaisesRegex(keys.Refused, "online account"):
            self.ro.accept(*self.link(self.b, entries))

    def test_a_removed_entry_holds_nothing(self):
        taken = digest(f"gmail:{ADDRESS}")
        removed = {"in_version": 2, "head_dseq": 0, "head_hash": "", "clear": False}
        entries = [{**e, "removed": removed} if e["device_id"] == self.a.client.device_id else
                   {**e, "accounts": [taken]} for e in self.ro.entries]
        roster.check_link(roster.new_link(self.ro, self.ro.epoch, self.a.client.device_id, entries, self.ring))

    def test_an_entry_s_accounts_are_sorted_unique_digests(self):
        entry = self.ro.entry(self.a.client.device_id)
        one, two = sorted([digest("github:octocat"), digest("github:other")])
        roster.check_entry({**entry, "accounts": [one, two]})
        for bad in ([two, one], [one, one], [ADDRESS], [one.upper()], "x", None):
            with self.subTest(bad=bad), self.assertRaises(keys.Refused):
                roster.check_entry({**entry, "accounts": bad})
        with self.assertRaises(keys.Refused):
            roster.check_entry({k: v for k, v in entry.items() if k != "accounts"})


if __name__ == "__main__":
    unittest.main()
