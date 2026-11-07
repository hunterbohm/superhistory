import json
import logging
import os
import re
import tempfile
import unittest
from unittest import mock

from kb import decisions, search, store

OWNER = ("Hunter", "hunter@example.com")
LOG = logging.getLogger("test-decisions")


def kind_of(name):
    return {"mail": "gmail", "texts": "imessage"}.get(name, name)


class Model:
    """Answers the judge with `judge(new_text, older {id: text}, passages)` per decision, and the check with
    `verify(old_text, new_text)` per pair."""

    def __init__(self, judge, verify=lambda old, new: True):
        self.judge, self.verify, self.calls = judge, verify, []

    def __call__(self, prompt):
        self.calls.append(prompt)
        data = prompt.user
        if prompt.system.startswith("You check"):
            pairs = re.findall(r"PAIR (\d+)\nOLD \([^)]*\): (.*)\nNEW \([^)]*\): (.*)", data)
            return json.dumps({"results": [{"n": int(n), "replaces": self.verify(old, new)} for n, old, new in pairs]})
        results = []
        for block in data.split("\n\nNEW ")[0:]:
            m = re.search(r"(?:NEW )?(\d+) \([^)]*\): (.*)", block)
            older = dict((int(i), t) for i, t in re.findall(r"#(\d+) \([^)]*\): (.*)", block))
            passages = re.findall(r"^- '(.*)'$", block, re.M)
            results.append({"n": int(m.group(1)), **self.judge(m.group(2), older, passages)})
        return json.dumps({"results": results})


def answer(author="agent", quote="", lasting=True, matters=False, replaces=(), why=""):
    return {"author": author, "quote": quote, "lasting": lasting, "matters": matters, "replaces": list(replaces),
            "why": why}


class Store(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "kb.sqlite")
        self.db = store.connect(self.path)
        self.addCleanup(self.db.close)

    def session(self, sid, ts, decided, text="USER: carry on\n\nASSISTANT: done", source="claude_code"):
        store.store_item(self.db, source, sid, ts, "session", text + "\n" + "filler line\n" * 1200, {})
        store.replace_rows(self.db, source, sid, [
            {"kind": "atom", "ts": ts, "title": f"decision: {d[:60]}", "content": d, "metadata": {"atom": "decision"}}
            for d in decided], store.SUMMARY_KINDS)
        self.db.commit()

    def run_pass(self, model):
        return decisions.run(self.db, model, OWNER, kind_of, LOG, workers=1)

    def ids(self, query, **kw):
        return [h["content"] for h in search.search(self.db, query, **kw) if h["kind"] == "atom"]


class DecisionTest(Store):
    def test_a_replaced_decision_leaves_search_and_comes_back_with_history(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."])
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])
        rows = self.db.execute("SELECT count(*) FROM docs").fetchone()[0]
        stats = self.run_pass(Model(lambda text, older, _: answer(replaces=older if "switched off" in text else ())))
        self.assertEqual(stats.replaced, 1)
        self.assertEqual(self.ids("Composio app accounts"),
                         ["Composio switched off; Executor replaces it for app accounts."])
        hits = {h["content"]: h for h in search.search(self.db, "Composio app accounts", history=True)}
        old = hits["Composio CLI is the default for connected app accounts."]
        self.assertEqual(decisions.describe(self.db, old["replaced_by"])[1],
                         "Composio switched off; Executor replaces it for app accounts.")
        self.assertEqual(self.db.execute("SELECT count(*) FROM docs").fetchone()[0], rows)

    def test_no_mark_when_the_second_check_disagrees(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."])
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])
        model = Model(lambda text, older, _: answer(replaces=older), verify=lambda old, new: False)
        self.assertEqual(self.run_pass(model).replaced, 0)
        self.assertEqual(len(self.ids("Composio app accounts")), 2)

    def test_a_proposal_whose_check_failed_is_checked_on_the_next_run(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."])
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])
        judge = Model(lambda text, older, _: answer(replaces=older if "switched" in text else ()))
        flaky = lambda prompt: "garbled" if prompt.system.startswith("You check") else judge(prompt)
        self.assertEqual(decisions.run(self.db, flaky, OWNER, kind_of, LOG, workers=1).replaced, 0)
        self.assertEqual(len(self.ids("Composio app accounts")), 2)
        later = Model(lambda *a: answer())
        self.assertEqual(self.run_pass(later).replaced, 1)
        self.assertEqual([p.system[:9] for p in later.calls], ["You check"])
        self.assertEqual(self.ids("Composio app accounts"),
                         ["Composio switched off; Executor replaces it for app accounts."])

    def test_an_agents_decision_never_hides_the_owners_own(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."],
                     text="USER: composio cli is the default for app accounts\n\nASSISTANT: ok")
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])

        def judge(text, older, passages):
            if "switched" in text:
                return answer(replaces=older)
            return answer("owner", "composio cli is the default for app accounts")

        self.assertEqual(self.run_pass(Model(judge)).replaced, 1)
        self.assertEqual(len(self.ids("Composio app accounts")), 2)
        new = self.db.execute("SELECT id FROM docs WHERE content LIKE 'Composio switched off%'").fetchone()[0]
        decisions.answer(self.db, new, "confirmed")
        self.assertEqual(self.ids("Composio app accounts"),
                         ["Composio switched off; Executor replaces it for app accounts."])

    def test_a_mark_whose_replacing_decision_is_gone_hides_nothing_even_before_the_next_pass(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."])
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])
        self.run_pass(Model(lambda text, older, _: answer(replaces=older if "switched" in text else ())))
        self.session("new", "2026-10-24T10:00:00Z", ["Executor holds the app logins now."])
        self.assertIn("Composio CLI is the default for connected app accounts.", self.ids("Composio app accounts"))

    def test_rejecting_a_decision_drops_the_replacements_it_still_had_waiting(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."])
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])
        judge = Model(lambda text, older, _: answer(replaces=older if "switched" in text else ()))
        flaky = lambda prompt: "garbled" if prompt.system.startswith("You check") else judge(prompt)
        decisions.run(self.db, flaky, OWNER, kind_of, LOG, workers=1)
        new = self.db.execute("SELECT id FROM docs WHERE content LIKE 'Composio switched off%'").fetchone()[0]
        decisions.answer(self.db, new, "rejected")
        self.assertEqual(self.run_pass(Model(lambda *a: answer())).replaced, 0)
        self.assertIn("Composio CLI is the default for connected app accounts.", self.ids("Composio app accounts"))

    def test_the_owners_decision_an_agent_replaced_can_still_be_replaced_by_his_own(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."],
                     text="USER: composio cli is the default for app accounts\n\nASSISTANT: ok")
        self.session("agent", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])

        def judge(text, older, passages):
            if "Composio CLI" in text:
                return answer("owner", "composio cli is the default for app accounts")
            if "turned off" in text:
                return answer("owner", "composio is turned off for app accounts", replaces=older)
            return answer(replaces=older)

        self.run_pass(Model(judge))
        self.session("own", "2026-10-25T10:00:00Z", ["Composio is turned off for app accounts for good."],
                     text="USER: composio is turned off for app accounts\n\nASSISTANT: ok")
        self.run_pass(Model(judge))
        self.assertNotIn("Composio CLI is the default for connected app accounts.", self.ids("Composio app accounts"))

    def test_a_reject_that_lands_while_the_check_runs_still_stops_the_mark(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."])
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])

        def verify(old, new):  # the owner rejects NEW from another process while the check runs
            other = store.connect(self.path)
            try:
                rid = other.execute("SELECT id FROM docs WHERE content LIKE 'Composio switched off%'").fetchone()[0]
                decisions.answer(other, rid, "rejected")
            finally:
                other.close()
            return True

        model = Model(lambda text, older, _: answer(replaces=older if "switched" in text else ()), verify)
        self.assertEqual(self.run_pass(model).replaced, 0)
        self.assertIn("Composio CLI is the default for connected app accounts.", self.ids("Composio app accounts"))

    def test_the_owners_own_replacement_outlasts_a_later_agent_report(self):
        said = {"old": "composio cli is the default for app accounts", "own": "composio is off for app accounts"}
        for sid, ts, text in (("old", "2026-06-12", "Composio CLI is the default for connected app accounts."),
                              ("own", "2026-10-24", "Composio is off for app accounts; Executor replaces it."),
                              ("agent", "2026-10-26", "Composio switched off; app accounts moved to Executor.")):
            self.session(sid, ts + "T10:00:00Z", [text], text=f"USER: {said.get(sid, 'carry on')}\n\nASSISTANT: ok")

        def judge(text, older, passages):
            if "CLI is the default" in text:
                return answer("owner", said["old"])
            mine = "Composio is off" in text
            return answer("owner" if mine else "agent", said["own"] if mine else "",
                          replaces=[i for i, t in older.items() if "CLI is the default" in t])

        self.run_pass(Model(judge))
        self.assertNotIn("Composio CLI is the default for connected app accounts.", self.ids("Composio app accounts"))

    def test_a_hidden_decision_does_not_take_a_current_one_from_its_item_out_of_search(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts.",
                                                     "Composio audit logs stay on for app accounts."])
        self.session("new", "2026-10-24T10:00:00Z", ["Executor replaces it: Composio CLI is off for accounts."])
        self.run_pass(Model(lambda text, older, _: answer(
            replaces=[i for i, t in older.items() if "CLI is the default" in t] if "Executor" in text else ())))
        self.assertIn("Composio audit logs stay on for app accounts.",
                      self.ids("Composio CLI default connected app accounts audit", limit=5))

    def test_a_pair_the_check_left_out_waits_for_the_next_run(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."])
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])
        judge = Model(lambda text, older, _: answer(replaces=older if "switched" in text else ()))
        silent = lambda prompt: '{"results": []}' if prompt.system.startswith("You check") else judge(prompt)
        self.assertEqual(decisions.run(self.db, silent, OWNER, kind_of, LOG, workers=1).replaced, 0)
        self.assertEqual(self.run_pass(Model(lambda *a: answer())).replaced, 1)

    def test_the_mark_itself_refuses_a_replacement_rejected_after_it_was_read(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."])
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])
        self.run_pass(Model(lambda *a: answer()))
        old, new = sorted(decisions.decisions(self.db), key=lambda d: d.ts)
        decisions.answer(self.db, new.row, "rejected")
        with mock.patch.object(decisions, "_backed", return_value=True), self.db:
            self.assertFalse(decisions.mark_replaced(self.db, old, new, "", decisions.now_iso()))

    def test_a_current_decision_under_many_hidden_ones_of_its_item_still_shows(self):
        rules = [f"Composio app accounts rule {n} uses the Composio CLI {group}." for group in ("alpha", "beta")
                 for n in range(1, 7)]
        kept = "Audit logs stay on for every client workspace, billing review and support account, Composio included."
        self.session("old", "2026-06-12T10:00:00Z", rules + [kept])
        self.session("new", "2026-10-24T10:00:00Z", [f"Executor replaces the {g} Composio CLI rules for app accounts."
                                                     for g in ("alpha", "beta")])
        self.run_pass(Model(lambda text, older, _: answer(replaces=[
            i for i, t in older.items() if "Executor" in text and t.endswith(text.split()[3] + ".")])))
        self.assertEqual(self.db.execute("SELECT count(*) FROM decisions WHERE new_key IS NOT NULL").fetchone()[0], 12)
        self.assertIn(kept, self.ids("Composio app accounts rule uses CLI"))

    def test_other_hits_from_an_item_whose_decision_was_replaced_say_so(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."],
                     text="USER: make the composio cli the default for connected app accounts\n\nASSISTANT: ok")
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])
        self.run_pass(Model(lambda text, older, _: answer(replaces=older if "switched" in text else ())))
        new = self.db.execute("SELECT id FROM docs WHERE content LIKE 'Composio switched off%'").fetchone()[0]
        raw = [h for h in search.search(self.db, "composio cli default connected app accounts") if h["kind"] == "raw"
               and h["source_id"].endswith("old")]
        self.assertEqual(len(raw), 1)
        self.assertEqual(raw[0]["replaced_in_item"], [new])
        self.assertTrue(raw[0]["snippet"].startswith(f"[A decision here was replaced later; see #{new}]"))

    def test_older_candidates_are_found_below_any_number_of_newer_better_matches(self):
        def d(n, ts, text):
            return decisions.Decision(n, "s", f"i{n}", "k", ts, text)
        newer = [d(n, "2026-10-0%d" % n, "Composio CLI default app accounts connected rule") for n in range(1, 8)]
        older = d(9, "2026-01-01", "Composio is used for some things.")
        me = d(10, "2026-09-01", "Composio CLI default app accounts connected rule replaced")
        self.assertEqual([c.row for c in decisions.Index(newer + [older, me]).older(me, k=1)], [9])

    def test_an_agents_report_hides_nothing_until_the_older_decision_is_judged(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."],
                     text="USER: composio cli is the default for app accounts\n\nASSISTANT: ok")
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])
        judge = Model(lambda text, older, _: answer(replaces=older) if "switched" in text
                      else answer("owner", "composio cli is the default for app accounts"))
        decisions.run(self.db, judge, OWNER, kind_of, LOG, limit=1, workers=1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM decisions WHERE new_key IS NOT NULL").fetchone()[0], 1)
        self.assertEqual(len(self.ids("Composio app accounts")), 2)
        self.run_pass(judge)
        self.assertEqual(len(self.ids("Composio app accounts")), 2)

    def test_a_copy_of_the_replaced_text_in_another_item_is_marked_too(self):
        for sid in ("old1", "old2"):
            self.session(sid, "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."])
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])
        self.assertEqual(self.run_pass(Model(lambda text, older, _: answer(
            replaces=older if "switched" in text else ()))).replaced, 2)
        self.assertEqual(self.ids("Composio app accounts"),
                         ["Composio switched off; Executor replaces it for app accounts."])

    def test_the_note_does_not_depend_on_which_row_of_the_item_search_picked(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."])
        store.replace_rows(self.db, "claude_code", "old", [
            {"kind": "segment", "ts": "2026-06-12T10:00:00Z", "title": "Composio CLI default for connected app accounts",
             "content": "Composio CLI default connected app accounts: the Composio CLI is the default.",
             "metadata": {}},
            {"kind": "atom", "ts": "2026-06-12T10:00:00Z", "title": "decision: Composio CLI is the default",
             "content": "Composio CLI is the default for connected app accounts.", "metadata": {"atom": "decision"}}],
            store.SUMMARY_KINDS)
        self.db.commit()
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])
        self.run_pass(Model(lambda text, older, _: answer(replaces=older if "switched" in text else ())))
        new = self.db.execute("SELECT id FROM docs WHERE content LIKE 'Composio switched off%'").fetchone()[0]
        hits = [h for h in search.search(self.db, "Composio CLI default connected app accounts")
                if h["source_id"].endswith("old")]
        self.assertEqual([(h["kind"], h.get("replaced_in_item")) for h in hits], [("segment", [new])])

    def test_review_puts_first_what_would_replace_the_owners_own_words(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."],
                     text="USER: composio cli is the default for app accounts\n\nASSISTANT: ok")
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])
        self.session("later", "2026-11-01T10:00:00Z", ["Invoices go out on the first business day of the month."])

        def judge(text, older, passages):
            if "CLI is the default" in text:
                return answer("owner", "composio cli is the default for app accounts")
            return answer(matters=True, replaces=older if "switched" in text else ())

        self.run_pass(Model(judge))
        self.assertEqual([d.text[:20] for d in decisions.review(self.db)],
                         ["Composio switched of", "Invoices go out on t"])

    def test_review_on_a_store_the_pass_never_ran_on_is_empty(self):
        self.assertEqual(decisions.review(self.db), [])

    def test_owner_needs_his_own_words_quoted(self):
        self.session("typed", "2026-11-01T10:00:00Z", ["Use Executor for every app account."],
                     text="USER: yeah lets just use executor for every app acount from now on\n\nASSISTANT: ok")
        self.session("relayed", "2026-11-02T10:00:00Z", ["Merge trains approved by Hunter for all workers."],
                     text="USER: You own the merge lane. Hunter approved merge trains.\n\nASSISTANT: ok")
        self.session("aside", "2026-11-03T10:00:00Z", ["Invoices are sent in USD."],
                     text="USER: sounds good to me thanks\n\nASSISTANT: ok")

        def judge(text, older, passages):
            if "Executor" in text:
                return answer("owner", "lets just use executor for every app acount")
            if "Invoices" in text:
                return answer("owner", "sounds good to me thanks")
            return answer("owner", "Hunter approved merge trains for every lane today")

        self.assertEqual(self.run_pass(Model(judge)).authors, {"owner": 1, "agent": 2, "other": 0})
        rows = dict(self.db.execute("SELECT source_id, author FROM decisions"))
        self.assertEqual(rows, {"typed": "owner", "relayed": "agent", "aside": "agent"})

    def test_each_decision_is_judged_once_and_a_failed_batch_on_the_next_run(self):
        self.session("a", "2026-11-01T10:00:00Z", ["Prices are quoted in USD."])
        self.assertEqual(decisions.run(self.db, lambda p: "no json here", OWNER, kind_of, LOG, workers=1).failed, 1)
        model = Model(lambda *a: answer())
        self.assertEqual(self.run_pass(model).judged, 1)
        self.assertEqual(self.run_pass(model).judged, 0)
        self.assertEqual(len(model.calls), 1)

    def test_a_mark_lapses_when_the_replacing_decision_is_rewritten_or_rejected(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."])
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."])
        self.run_pass(Model(lambda text, older, _: answer(replaces=older if "switched" in text else ())))
        self.session("new", "2026-10-24T10:00:00Z", ["Executor holds the app logins now."])
        self.run_pass(Model(lambda *a: answer()))
        self.assertIn("Composio CLI is the default for connected app accounts.", self.ids("Composio app accounts"))

        self.session("newer", "2026-10-25T10:00:00Z", ["Composio switched off for good; app accounts go to Executor."])
        self.run_pass(Model(lambda text, older, _: answer(replaces=older if "for good" in text else ())))
        self.assertNotIn("Composio CLI is the default for connected app accounts.", self.ids("Composio app accounts"))
        newer = self.db.execute("SELECT id FROM docs WHERE content LIKE 'Composio switched off for good%'").fetchone()[0]
        decisions.answer(self.db, newer, "rejected")
        found = self.ids("Composio app accounts")
        self.assertIn("Composio CLI is the default for connected app accounts.", found)
        self.assertNotIn("Composio switched off for good; app accounts go to Executor.", found)

    def test_review_lists_unanswered_agent_decisions_that_matter_once_per_wording(self):
        self.session("a", "2026-11-04T10:00:00Z", ["Merge trains approved by Hunter: one worker merges all green PRs."])
        self.session("b", "2026-11-05T10:00:00Z", ["Merge trains approved by Hunter: one worker merges all green PRs now."])
        self.session("c", "2026-11-05T11:00:00Z", ["Rename the CSS class run to ans."])
        self.run_pass(Model(lambda text, *_: answer(matters="Merge" in text)))
        listed = [d.text for d in decisions.review(self.db)]
        self.assertEqual(listed, ["Merge trains approved by Hunter: one worker merges all green PRs now."])
        decisions.answer(self.db, decisions.review(self.db)[0].row, "confirmed")
        self.assertEqual([d.text for d in decisions.review(self.db)],
                         ["Merge trains approved by Hunter: one worker merges all green PRs."])


class DriftTest(Store):
    FILE = ("# Accounts\n\n- Use Composio for every connected app account.\n- Keep keys in Infisical.\n\n"
            "```\ncomposio run x\n```\n")

    def test_only_the_owners_own_decisions_flag_a_contradicting_line(self):
        self.session("old", "2026-06-12T10:00:00Z", ["Composio CLI is the default for connected app accounts."])
        self.session("new", "2026-10-24T10:00:00Z", ["Composio switched off; Executor replaces it for app accounts."],
                     text="USER: turn composio off, executor replaces it for every app acount\n\nASSISTANT: ok")
        self.session("agent", "2026-10-25T10:00:00Z", ["Keys move out of Infisical into Executor."])

        def judge(text, older, passages):
            if "switched" in text:
                return answer("owner", "turn composio off, executor replaces it", replaces=older)
            return answer()

        self.run_pass(Model(judge))
        asked = []

        def model(prompt):
            asked.append(prompt.user)
            ids = re.findall(r"PASSAGE (p\d+\.\d+) [^\n]*\n- Use Composio", prompt.user)
            return json.dumps({"results": [{"passage": i, "contradicts": True,
                                            "fix": "- Use Executor for every connected app account."} for i in ids]})

        found = decisions.drift(self.db, model, OWNER, [("AGENTS.md", self.FILE, "2026-10-20")], "2026-10-01", LOG,
                                workers=1)
        self.assertEqual([(f["path"], f["line"], f["passage"], f["fix"]) for f in found],
                         [("AGENTS.md", 3, "- Use Composio for every connected app account.",
                           "- Use Executor for every connected app account.")])
        self.assertEqual(len(asked), 1)
        self.assertNotIn("Infisical into Executor", asked[0])
        self.assertIn("REPLACED: Composio CLI is the default", asked[0])
        self.assertIn("(2026-10-24, in session): Composio switched off", asked[0])
        self.assertIn("(AGENTS.md, line 3, file changed 2026-10-20)", asked[0])
        self.assertEqual(decisions.drift(self.db, model, OWNER, [("AGENTS.md", self.FILE)], "2026-11-01", LOG), [])

    def test_passages_are_paragraphs_and_list_items_without_headings_or_code(self):
        self.assertEqual([(p.line, p.text) for p in decisions.passages("f", self.FILE + "Last para\nwraps here\n")],
                         [(3, "- Use Composio for every connected app account."), (4, "- Keep keys in Infisical."),
                          (9, "Last para\nwraps here")])


class ReadFilesTest(unittest.TestCase):
    def test_each_file_once_skipping_oversized_and_unreadable_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, data in (("a.md", b"rule"), ("big.md", b"x" * 50), ("bad.md", b"\xff\xfe")):
                with open(os.path.join(tmp, name), "wb") as fh:
                    fh.write(data)
            os.symlink(os.path.join(tmp, "a.md"), os.path.join(tmp, "link.md"))
            got = decisions.read_files([os.path.join(tmp, "*.md"), os.path.join(tmp, "a.md")], limit=10)
            self.assertEqual([g[:2] for g in got], [(os.path.join(tmp, "a.md"), "rule")])
            self.assertRegex(got[0][2], r"^\d{4}-\d\d-\d\d$")

class ParseTest(unittest.TestCase):
    def test_an_id_written_as_text_counts(self):
        older = [decisions.Decision(row, "s", "o", "k", "2026-01-01", "x") for row in (12, 34, 56)]
        batch = [(decisions.Decision(9, "s", "n", "k2", "2026-02-01", "y"), older, None, [])]
        answer = '{"results": [{"n": 1, "author": "agent", "replaces": [12, "34", "#56", "78", "x"]}]}'
        self.assertEqual(decisions.parse_judgments(answer, batch)[0].replaces, (12, 34, 56))
        answer = '{"results": [{"n": "1", "author": "agent", "lasting": "true", "matters": "yes", "replaces": []}]}'
        self.assertTrue(decisions.parse_judgments(answer, batch)[0].matters)
        self.assertEqual(decisions.parse_verdicts('{"results": [{"n": "2", "replaces": "yes"}, {"n": 1}]}', 2), {1: True})
        self.assertEqual(decisions.parse_verdicts('{"results": [{"n": 1, "replaces": false}]}', 2), {0: False})


class OwnPassagesTest(unittest.TestCase):
    def test_each_source_type_gives_the_owner_side(self):
        mail = ("From: Kim <kim@example.com>\nTo: hunter@example.com\nDate: 2026-10-01T10:00:00Z\n\nhi\n"
                "From: Hunter Bohm <hunter@example.com>\nquoted, not a message\n\n---\n\n"
                "From: Hunter Bohm <hunter@example.com>\nDate: 2026-10-02T10:00:00Z\n\nCJIS pack: no charge.")
        self.assertEqual(decisions.own_passages("gmail", mail, OWNER),
                         ["From: Hunter Bohm <hunter@example.com>\nDate: 2026-10-02T10:00:00Z\n\nCJIS pack: no charge."])
        texts = "09:14 Hunter: start that fix\n09:15 Kevin: ok\n09:16 Me: thanks"
        self.assertEqual(decisions.own_passages("imessage", texts, OWNER),
                         ["09:14 Hunter: start that fix", "09:16 Me: thanks"])
        meeting = "**Max:** we keep GitHub\n**Hunter Bohm:** no, TaskNotes from now on\n"
        self.assertEqual(decisions.own_passages("notes", meeting, OWNER), ["**Hunter Bohm:** no, TaskNotes from now on"])
        spoof = ("From: Hunter Bohm <kim@example.com>\nDate: 2026-10-01T10:00:00Z\n\nCJIS is $4,000.\n\n---\n\n"
                 "From: kim@example.com (hunter@example.com)\nDate: 2026-10-01T10:00:00Z\n\nx")
        self.assertEqual(decisions.own_passages("gmail", spoof, OWNER), [])
        self.assertEqual(decisions.own_passages("github", "Hunter: merge trains are off\n", OWNER), [])
        note = "Hunter decided: merge trains stay\n[00:12] Hunter Bohm (host): trains are off\nHunter: ok\n"
        self.assertEqual(decisions.own_passages("notes", note, OWNER),
                         ["[00:12] Hunter Bohm (host): trains are off", "Hunter: ok"])
        session = ("USER: do it\nmore\n\nASSISTANT: done\n\nRESULT: log\nUSER: switch to X\n\nUSER: thanks\n\n"
                   "ERROR: tool failed\n\nUSER: ok\n\nAGENT worker -> lead: use merge trains")
        self.assertEqual(decisions.own_passages("codex", session, OWNER), ["USER: do it\nmore", "USER: thanks", "USER: ok"])


if __name__ == "__main__":
    unittest.main()
