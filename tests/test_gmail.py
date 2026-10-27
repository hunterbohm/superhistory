import base64
import hashlib
import http.server
import imaplib
import json
import logging
import os
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlparse

from kb import config, ingest, search, store
from kb.config import Secret
from kb.sources import Excludes, Gone, Item, Progress, Run
from kb.sources import gmail
from tests.support import FakeModel, unused_model

ME = "me@example.com"
PASSWORD = "abcdefghijklmnop"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
PRICING, PROMO, OTHER = "1781000000000000001", "1781000000000000002", "1781000000000000003"
LOG = logging.getLogger("varve-test-gmail")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False


def ago(days=0, hours=0, minutes=0):
    return NOW - timedelta(days=days, hours=hours, minutes=minutes)


def iso(at):
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGmail:
    """`UID n:*` includes the last message even when n is past it, as Gmail does."""

    def __init__(self, all_mail="[Gmail]/All Mail"):
        self.all_mail = all_mail
        self.messages = []
        self.next_uid = 101
        self.uid_next = None
        self.uid_validity = 0
        self.selected = []
        self.signed_in = []
        self.bodies_read = []
        self.access_token = "access-1"

    def add(self, thread, at, sender, body, subject="Pricing question", to=ME, cc="", labels=("\\Inbox",), html=False,
            attachments=()):
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"], msg["Date"] = sender, to, subject, format_datetime(at)
        if cc:
            msg["Cc"] = cc
        msg.set_content(body, subtype="html" if html else "plain")
        for name in attachments:
            msg.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename=name)
        uid, self.next_uid = self.next_uid, self.next_uid + 1
        self.messages.append({"uid": uid, "thread": thread, "at": at, "labels": labels, "raw": msg.as_bytes()})
        return uid

    @property
    def highest(self):
        return max(m["uid"] for m in self.messages)

    def login(self, user, password):
        if (user, password) != (ME, PASSWORD):
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        self.signed_in.append(("LOGIN", user))

    def authenticate(self, mechanism, answer):
        if answer(b"") != f"user={ME}\x01auth=Bearer {self.access_token}\x01\x01".encode():
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        self.signed_in.append((mechanism, ME))

    def list(self):
        quoted = '"' + self.all_mail.replace('"', '\\"') + '"'
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "All Mail"',
                      b'(\\HasChildren \\Noselect) "/" "[Gmail]"', b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
                      f'(\\All \\HasNoChildren) "/" {quoted}'.encode()]

    def select(self, mailbox, readonly=False):
        self.selected.append(("EXAMINE" if readonly else "SELECT", mailbox))
        if mailbox != '"' + self.all_mail.replace('"', '\\"') + '"':
            return "NO", [b"[NONEXISTENT] Unknown Mailbox"]
        return "OK", [str(len(self.messages)).encode()]

    def response(self, code):
        if code == "UIDVALIDITY":
            return code, [str(self.uid_validity).encode()]
        assert code == "UIDNEXT"
        return code, [str(self.uid_next or self.next_uid).encode()]

    def uid(self, command, *args):
        assert self.selected and self.selected[-1][0] == "EXAMINE", "All Mail must be open read-only"
        if command == "SEARCH":
            tokens = " ".join(args).split()
            tests = []
            while tokens:
                tests.append(self._criterion(tokens))
            return "OK", [" ".join(str(m["uid"]) for m in self.messages if all(t(m) for t in tests)).encode()]
        assert command == "FETCH", command
        wanted, items = args
        rows = []
        for n, m in enumerate(self.messages, 1):
            if self._in_set(m["uid"], wanted):
                rows += self._fetch_row(n, m, items)
        return "OK", rows

    def logout(self):
        return "BYE", [b"LOGOUT Requested"]

    def _criterion(self, tokens):
        key = tokens.pop(0)
        if key == "OR":
            a, b = self._criterion(tokens), self._criterion(tokens)
            return lambda m: a(m) or b(m)
        if key == "SINCE":
            day = datetime.strptime(tokens.pop(0), "%d-%b-%Y").date()
            return lambda m: m["at"].date() >= day
        if key == "UID":
            wanted = tokens.pop(0)
            return lambda m: self._in_set(m["uid"], wanted)
        if key == "X-GM-THRID":
            thread = tokens.pop(0)
            return lambda m: m["thread"] == thread
        raise AssertionError(f"unexpected search key {key}")

    def _in_set(self, uid, wanted):
        for part in wanted.split(","):
            low, _, high = part.partition(":")
            low, high = [self.highest if x == "*" else int(x) for x in (low, high or low)]
            if min(low, high) <= uid <= max(low, high):
                return True
        return False

    def _fetch_row(self, n, m, items):
        labels = " ".join(label if label.startswith("\\") else '"' + label + '"' for label in m["labels"])
        head = f"{n} (X-GM-THRID {m['thread']} X-GM-LABELS ({labels})"
        head += f' INTERNALDATE "{m["at"].strftime("%d-%b-%Y %H:%M:%S +0000")}"'
        if "BODY.PEEK[]" in items:
            self.bodies_read.append(m["uid"])
            return [(f"{n} (BODY[] {{{len(m['raw'])}}}".encode(), m["raw"]), f" UID {m['uid']})".encode()]
        if "HEADER.FIELDS" in items:
            literal = b"".join(line + b"\r\n" for line in m["raw"].split(b"\n")
                               if line.lower().startswith((b"from:", b"subject:"))) + b"\r\n"
            return [(f"{head} UID {m['uid']} BODY[HEADER.FIELDS (FROM SUBJECT)] {{{len(literal)}}}".encode(), literal),
                    b")"]
        return [f"{n} (X-GM-THRID {m['thread']} UID {m['uid']})".encode()]


def keeps(skip=("deals@",)):
    def reply(prompt):
        lines = prompt.user.splitlines()
        return json.dumps({"keep": [int(line.split(".", 1)[0]) for line in lines if not any(s in line for s in skip)]})
    return FakeModel(reply)


class GmailCase(unittest.TestCase):
    def setUp(self):
        self.gmail = FakeGmail()
        for patch in (mock.patch.object(gmail, "connect", lambda: self.gmail),
                      mock.patch.dict(os.environ, {"KB_TEST_GMAIL_PASSWORD": PASSWORD})):
            patch.start()
            self.addCleanup(patch.stop)

    def extract(self, since=None, model=None, exclude=(), **options):
        options.setdefault("password", Secret("env:KB_TEST_GMAIL_PASSWORD"))
        run = Run(gmail.Options(address=ME, **options), Excludes(exclude), since, model or keeps())
        return list(gmail.TYPE.extract(run))

    def items(self, events):
        return {e.source_id: e for e in events if isinstance(e, Item)}

    def pricing_thread(self):
        self.gmail.add(PRICING, ago(2, 1), "Ann Lee <ann@example.com>", "Can you send the Q3 pricing?\n\n-- \nAnn\nCEO",
                       cc="Bob <bob@example.com>")
        self.gmail.add(PRICING, ago(2), f"Me <{ME}>", (
            "Sending the pricing sheet now.\n\n"
            "On Mon, Oct 12, 2026 at 3:00 PM Ann Lee <ann@example.com> wrote:\n"
            "> Can you send the Q3 pricing?\n> thanks"), subject="Re: Pricing question", to="ann@example.com",
            labels=("\\Sent",), attachments=("q3-pricing.pdf",))
        return self.gmail.add(PRICING, ago(2) + timedelta(minutes=1), f"Me <{ME}>", "unsent second thoughts",
                              subject="Re: Pricing question", labels=("\\Draft",))

    def promo_thread(self, at):
        return self.gmail.add(PROMO, at, "Shop <deals@shop.example.com>", "Flash sale on lanterns", subject="50% off today",
                              labels=("\\Inbox", "Shopping"))


class ReadingTest(GmailCase):
    def test_first_run_reads_the_window_from_all_mail_read_only_and_renders_each_kept_thread(self):
        self.gmail.add(OTHER, ago(400), "Old <old@example.com>", "from last year", subject="Old news")
        draft_uid = self.pricing_thread()
        events = self.extract()
        self.assertEqual(self.gmail.selected, [("EXAMINE", '"[Gmail]/All Mail"')])
        self.assertEqual(events[-1], Progress(self.gmail.highest))
        [item] = events[:-1]
        self.assertEqual(item.source_id, PRICING)
        self.assertEqual(item.title, "Pricing question")
        self.assertEqual(item.ts, iso(ago(2)))
        self.assertEqual(item.mark, draft_uid)
        first, second = item.content.split("\n\n---\n\n")
        self.assertTrue(first.startswith(f"From: Ann Lee <ann@example.com>\nTo: {ME}\nCc: Bob <bob@example.com>\n"
                                         f"Date: {iso(ago(2, 1))}\n\nCan you send the Q3 pricing?"))
        self.assertNotIn("CEO", first)
        self.assertTrue(second.startswith(f"From: Me <{ME}>\nTo: ann@example.com\nDate: "))
        self.assertIn("Sending the pricing sheet now.\n[attachment: q3-pricing.pdf]", second)
        self.assertNotIn("wrote:", second)
        self.assertNotIn("thanks", second)
        self.assertNotIn("unsent second thoughts", item.content)
        self.assertEqual(item.meta, {
            "url": f"https://mail.google.com/mail/u/?authuser=me%40example.com#all/{int(PRICING):x}",
            "account": ME, "participants": ["ann@example.com", ME, "bob@example.com"], "labels": ["INBOX", "SENT"]})

    def test_a_later_run_reads_only_threads_with_new_mail(self):
        self.pricing_thread()
        self.promo_thread(ago(1))
        mark = max(e.mark for e in self.extract())
        uid = self.gmail.add(OTHER, ago(0, 1), "Dana <dana@example.com>", "Lunch on Friday?", subject="Lunch")
        model = keeps()
        events = self.extract(since=mark, model=model)
        self.assertEqual([type(e) for e in events], [Item, Progress])
        self.assertEqual((events[0].source_id, events[0].mark, events[1].mark), (OTHER, uid, uid))
        self.assertEqual([p.user for p in model.prompts], ["1. Dana <dana@example.com> | Lunch | INBOX | 1 messages"])

    def test_a_run_with_no_new_mail_yields_only_progress(self):
        self.promo_thread(ago(1))
        self.assertEqual(self.extract(since=self.gmail.highest, model=unused_model()), [Progress(self.gmail.highest)])

    def test_a_thread_that_gains_a_message_is_read_again_whole(self):
        self.gmail.add(OTHER, ago(3), "Dana <dana@example.com>", "Can we move the review to Thursday?", subject="Review")
        mark = max(e.mark for e in self.extract())
        uid = self.gmail.add(OTHER, ago(0, 2), "Priya <priya@example.com>", "Thursday works for me.",
                             subject="Re: Review")
        item = self.items(self.extract(since=mark))[OTHER]
        self.assertEqual(item.mark, uid)
        self.assertIn("Can we move the review to Thursday?", item.content)
        self.assertIn("Thursday works for me.", item.content)
        self.assertEqual(item.ts, iso(ago(0, 2)))

    def test_html_only_mail_becomes_text_without_the_quoted_reply(self):
        self.gmail.add(OTHER, ago(1), "Ann Lee <ann@example.com>", (
            "<div dir=\"ltr\">Thanks, got it!&nbsp;See you soon.</div><br><div class=\"gmail_quote\">"
            "<div class=\"gmail_attr\">On Tue, Oct 13, 2026 at 9:00 AM Me &lt;<a href=\"mailto:me@example.com\">"
            "me@example.com</a>&gt; wrote:<br></div><blockquote>Here is the old pricing text</blockquote></div>"),
            subject="Thanks", html=True)
        content = self.items(self.extract())[OTHER].content
        self.assertIn("Thanks, got it! See you soon.", content)
        self.assertNotIn("<div", content)
        self.assertNotIn("old pricing", content)

    def test_drafts_and_trash_or_spam_copies_are_left_out_of_a_thread(self):
        self.gmail.add(OTHER, ago(2), "Dana <dana@example.com>", "The contract is attached.", subject="Contract")
        self.gmail.add(OTHER, ago(1), f"Me <{ME}>", "draft reply", subject="Re: Contract", labels=("\\Draft",))
        self.gmail.add(OTHER, ago(1), "Dana <dana@example.com>", "deleted copy", subject="Re: Contract",
                       labels=("\\Trash",))
        self.gmail.add(OTHER, ago(1), "Dana <dana@example.com>", "spam copy", subject="Re: Contract",
                       labels=("\\Spam",))
        self.gmail.add(PROMO, ago(1), f"Me <{ME}>", "only a draft", subject="Idea", labels=("\\Draft",))
        events = self.extract()
        item = self.items(events)[OTHER]
        self.assertIn("The contract is attached.", item.content)
        for text in ("draft reply", "deleted copy", "spam copy"):
            self.assertNotIn(text, item.content)
        self.assertEqual(item.meta["labels"], ["INBOX"])
        self.assertIn(Gone(PROMO, self.gmail.highest), events)

    def test_an_auth_code_inside_a_conversation_is_cut_and_the_conversation_kept(self):
        self.gmail.add(OTHER, ago(1, 2), "Dana <dana@example.com>", "Here is the invite.\nMeeting ID: 245 933 726",
                       subject="Wednesday")
        self.gmail.add(OTHER, ago(1, 1), "Acme <no-reply@acme.example.com>", "Your verification code is 482913.",
                       subject="Wednesday")
        self.gmail.add(OTHER, ago(1), f"Me <{ME}>", "Wednesday works.", subject="Re: Wednesday", labels=("\\Sent",))
        content = self.items(self.extract())[OTHER].content
        self.assertIn("Meeting ID: 245 933 726", content)
        self.assertIn("Wednesday works.", content)
        self.assertNotIn("482913", content)
        self.assertEqual(content.count("\n\n---\n\n"), 1)

    def test_a_code_in_the_subject_drops_the_message(self):
        self.gmail.add(OTHER, ago(1, 1), "Dana <dana@example.com>", "Notes from the call.", subject="Call notes")
        self.gmail.add(OTHER, ago(1), "Acme <no-reply@acme.example.com>", "482913", subject="Your verification code")
        content = self.items(self.extract(model=unused_model(), sort=False))[OTHER].content
        self.assertIn("Notes from the call.", content)
        self.assertNotIn("482913", content)

    def test_a_thread_of_only_auth_codes_is_gone(self):
        self.gmail.add(OTHER, ago(1, 1), "Acme <no-reply@acme.example.com>", "Your verification code is 482913.",
                       subject="Sign in to Acme")
        uid = self.gmail.add(OTHER, ago(1), "Acme <no-reply@acme.example.com>", "Your verification code is 112233.",
                             subject="Sign in to Acme")
        self.assertEqual(self.extract()[0], Gone(OTHER, uid))

    def test_all_mail_is_found_by_its_flag_under_a_localized_name(self):
        self.gmail.all_mail = "[Gmail]/Alle Nachrichten"
        self.gmail.add(OTHER, ago(1), "Dana <dana@example.com>", "Hallo", subject="Termin")
        events = self.extract()
        self.assertEqual(self.gmail.selected, [("EXAMINE", '"[Gmail]/Alle Nachrichten"')])
        self.assertIn(OTHER, self.items(events))

    def test_a_mailbox_renumbered_below_the_mark_starts_over_from_the_window(self):
        self.gmail.add(OTHER, ago(400), "Old <old@example.com>", "from last year", subject="Old news")
        uid = self.gmail.add(PRICING, ago(2), "Ann Lee <ann@example.com>", "Still on for Friday?", subject="Friday")
        events = self.extract(since=50000)
        self.assertEqual([(type(e), e.mark) for e in events], [(Item, uid), (Progress, uid)])
        self.assertEqual(events[0].source_id, PRICING)

    def test_a_mailbox_renumbered_above_the_mark_starts_over_from_the_window(self):
        self.gmail.uid_validity = 7
        first = self.gmail.add(OTHER, ago(3), "Dana <dana@example.com>", "first", subject="First")
        mark = max(e.mark for e in self.extract())
        self.assertEqual(divmod(mark, 2 ** 32), (7, first))
        self.gmail.uid_validity = 8
        self.gmail.next_uid = first + 1000
        later = self.gmail.add(PRICING, ago(2), "Ann Lee <ann@example.com>", "Still on?", subject="Friday")
        self.gmail.messages[0]["uid"] = later - 1
        events = self.extract(since=mark)
        self.assertEqual(sorted(e.source_id for e in events if isinstance(e, Item)), sorted([OTHER, PRICING]))

    def test_the_run_cap_decides_threads_in_arrival_order_and_the_mark_stops_at_the_last_decided(self):
        uids = [self.gmail.add(f"17810000000000001{n}", ago(3) + timedelta(hours=n), "Ann <ann@example.com>",
                               f"body {n}", subject=f"Thread {n}") for n in range(3)]
        first = self.extract(run_cap=2)
        self.assertEqual([(e.source_id, e.mark) for e in first],
                         [("178100000000000010", uids[0]), ("178100000000000011", uids[1])])
        second = self.extract(since=max(e.mark for e in first), run_cap=2)
        self.assertEqual([(type(e), e.mark) for e in second], [(Item, uids[2]), (Progress, uids[2])])

    def test_a_label_with_non_ascii_letters_is_decoded(self):
        self.gmail.add(OTHER, ago(1), "Dana <dana@example.com>", "Notes from the call", subject="Call",
                       labels=("\\Inbox", "Clientes/A&APE-o"))
        self.assertEqual(self.items(self.extract())[OTHER].meta["labels"], ["INBOX", "Clientes/Año"])


class SortingTest(GmailCase):
    def test_the_model_sees_one_line_per_thread_and_the_about_line(self):
        self.promo_thread(ago(1))
        model = keeps()
        self.extract(model=model, about="I run a small design studio.")
        [prompt] = model.prompts
        self.assertEqual(prompt.user, "1. Shop <deals@shop.example.com> | 50% off today | INBOX, Shopping | 1 messages")
        self.assertIn("About the user: I run a small design studio.", prompt.system)

    def test_a_skipped_thread_is_gone_and_its_body_never_fetched(self):
        uid = self.promo_thread(ago(1))
        self.gmail.add(OTHER, ago(1), "Dana <dana@example.com>", "Lunch on Friday?", subject="Lunch")
        events = self.extract()
        self.assertEqual(events[0], Gone(PROMO, uid))
        self.assertEqual(self.gmail.bodies_read, [uid + 1])

    def test_a_conversation_the_user_wrote_in_is_kept_without_asking(self):
        self.gmail.add(OTHER, ago(1, 1), "Airline <deals@air.example.com>", "Your trip is on hold.", subject="Trip")
        self.gmail.add(OTHER, ago(1), f"Me <{ME}>", "Sam, can you book this one?", subject="Fwd: Trip",
                       labels=("\\Sent",))
        events = self.extract(model=unused_model())
        self.assertIn("Sam, can you book this one?", self.items(events)[OTHER].content)

    def test_a_single_message_the_user_sent_is_still_up_to_the_model(self):
        uid = self.gmail.add(OTHER, ago(1), f"deals@{ME.split('@')[1]}", "1 service down", subject="[ALERT] down",
                             labels=("\\Sent",))
        model = keeps()
        self.assertEqual(self.extract(model=model)[0], Gone(OTHER, uid))
        self.assertEqual(len(model.prompts), 1)

    def test_sort_off_keeps_every_thread_without_the_model(self):
        self.promo_thread(ago(1))
        events = self.extract(model=unused_model(), sort=False)
        self.assertIn("Flash sale on lanterns", self.items(events)[PROMO].content)

    def test_an_excluded_sender_is_skipped_without_the_model(self):
        uid = self.promo_thread(ago(1))
        events = self.extract(model=unused_model(), exclude=("*@shop.example.com",))
        self.assertEqual(events[0], Gone(PROMO, uid))

    def test_numbers_outside_the_batch_and_non_integers_are_ignored(self):
        threads = [f"17810000000000002{n}" for n in range(3)]
        for n, tid in enumerate(threads):
            self.gmail.add(tid, ago(1) + timedelta(minutes=n), "Ann <ann@example.com>", f"body {n}", subject=f"T{n}")
        model = FakeModel('Here: {"keep": [0, 2, 4, 99, -1, "3", true, 2.0]}')
        events = self.extract(model=model)
        self.assertEqual([type(e) for e in events[:3]], [Gone, Item, Gone])
        self.assertEqual(events[1].source_id, threads[1])

    def test_an_answer_without_a_keep_list_fails_the_run_before_anything_is_decided(self):
        self.promo_thread(ago(1))
        with self.assertRaises(gmail.ai.BadAnswer):
            self.extract(model=FakeModel('{"keep": "all"}'))


class SignInTest(GmailCase):
    def test_a_refused_app_password_says_to_create_one_and_never_shows_it(self):
        os.environ["KB_TEST_GMAIL_PASSWORD"] = "wrong-password-1234"
        with self.assertRaises(RuntimeError) as caught:
            self.extract()
        self.assertIn("app password", str(caught.exception))
        self.assertIn("2-Step Verification", str(caught.exception))
        self.assertNotIn("wrong-password-1234", str(caught.exception))
        self.assertEqual(self.gmail.selected, [])

    def test_a_missing_password_setting_is_named(self):
        with self.assertRaisesRegex(RuntimeError, "password"):
            self.extract(password=None)


class TokenServer:
    def __init__(self, test):
        self.forms = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
                outer.forms.append(form)
                status, answer = outer.answer(form)
                body = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)
        patch = mock.patch.object(gmail, "TOKEN_URL", f"http://127.0.0.1:{self.server.server_address[1]}/token")
        patch.start()
        test.addCleanup(patch.stop)

    def answer(self, form):
        if form.get("client_secret") != "client-secret-1":
            return 401, {"error": "invalid_client"}
        if form["grant_type"] == "refresh_token" and form["refresh_token"] == "refresh-1":
            return 200, {"access_token": "access-1", "expires_in": 3599, "token_type": "Bearer"}
        if form["grant_type"] == "authorization_code" and form["code"] == "code-1":
            return 200, {"access_token": "access-2", "refresh_token": "refresh-from-login", "token_type": "Bearer"}
        return 400, {"error": "invalid_grant"}


class OAuthTest(GmailCase):
    def setUp(self):
        super().setUp()
        self.tokens = TokenServer(self)
        os.environ.update(KB_TEST_CLIENT_SECRET="client-secret-1", KB_TEST_REFRESH="refresh-1")

    def oauth(self):
        return {"auth": "oauth", "password": None, "client_id": "client-1.apps.example.com",
                "client_secret": Secret("env:KB_TEST_CLIENT_SECRET"), "refresh_token": Secret("env:KB_TEST_REFRESH")}

    def test_oauth_refreshes_an_access_token_and_signs_in_with_xoauth2(self):
        self.gmail.add(OTHER, ago(1), "Dana <dana@example.com>", "See the plan.", subject="Plan")
        events = self.extract(**self.oauth())
        self.assertIn(OTHER, self.items(events))
        self.assertEqual(self.gmail.signed_in, [("XOAUTH2", ME)])
        self.assertEqual(self.tokens.forms, [{"client_id": "client-1.apps.example.com",
                                              "client_secret": "client-secret-1", "refresh_token": "refresh-1",
                                              "grant_type": "refresh_token"}])

    def test_a_revoked_refresh_token_says_to_check_the_client_and_never_shows_secrets(self):
        os.environ["KB_TEST_REFRESH"] = "revoked-refresh"
        with self.assertRaises(RuntimeError) as caught:
            self.extract(**self.oauth())
        message = str(caught.exception)
        self.assertIn("invalid_grant", message)
        self.assertIn("client_id", message)
        for secret in ("revoked-refresh", "client-secret-1"):
            self.assertNotIn(secret, message)
        self.assertEqual(self.gmail.signed_in, [])

    def test_oauth_login_follows_the_browser_redirect_and_returns_the_refresh_token(self):
        opened, pages = [], []

        def browser(url):
            opened.append(url)
            query = parse_qs(urlparse(url).query)
            target = query["redirect_uri"][0] + "/?" + urlencode({"code": "code-1", "state": query["state"][0]})
            threading.Thread(target=lambda: pages.append(urllib.request.urlopen(target, timeout=10).read()),
                             daemon=True).start()

        token = gmail.oauth_login("client-1.apps.example.com", "client-secret-1", open_browser=browser, timeout=10)
        self.assertEqual(token, "refresh-from-login")
        query = {k: v[0] for k, v in parse_qs(urlparse(opened[0]).query).items()}
        self.assertTrue(opened[0].startswith("https://accounts.google.com/"))
        self.assertEqual(query["scope"], "https://mail.google.com/")
        self.assertEqual(query["code_challenge_method"], "S256")
        self.assertTrue(query["redirect_uri"].startswith("http://127.0.0.1:"))
        [form] = self.tokens.forms
        self.assertEqual((form["grant_type"], form["code"], form["redirect_uri"]),
                         ("authorization_code", "code-1", query["redirect_uri"]))
        digest = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).decode().rstrip("=")
        self.assertEqual(digest, query["code_challenge"])

    def test_oauth_login_rejects_an_answer_for_another_sign_in(self):
        def browser(url):
            target = parse_qs(urlparse(url).query)["redirect_uri"][0] + "/?code=code-1&state=forged"
            threading.Thread(target=lambda: urllib.request.urlopen(target, timeout=10).read(), daemon=True).start()

        with self.assertRaisesRegex(RuntimeError, "does not belong"):
            gmail.oauth_login("client-1.apps.example.com", "client-secret-1", open_browser=browser, timeout=10)
        self.assertEqual(self.tokens.forms, [])


class IngestTest(GmailCase):
    def test_sort_off_needs_no_ai_and_sort_on_says_what_is_missing(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = store.connect(os.path.join(tmp.name, "kb.db"))
        self.addCleanup(db.close)
        self.promo_thread(ago(2))
        table = f'machine = "m1"\n[sources.mail]\ntype = "gmail"\naddress = "{ME}"\npassword = "env:KB_TEST_GMAIL_PASSWORD"\n'
        self.assertEqual(ingest.run(db, config.parse(table + "sort = false\n"), LOG, with_summaries=False), 0)
        self.assertEqual(db.execute("SELECT count(DISTINCT source_id) FROM docs").fetchone()[0], 1)
        db.execute("DELETE FROM ingest_state")
        self.assertEqual(ingest.run(db, config.parse(table), LOG, with_summaries=False), 1)
        self.assertIn("needs an AI plug-in", dict(store.failing(db))["mail@m1"])

    def test_a_thread_kept_earlier_and_skipped_now_leaves_the_knowledge_base(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = store.connect(os.path.join(tmp.name, "kb.db"))
        self.addCleanup(db.close)
        cfg = config.parse(f'machine = "m1"\n[sources.mail]\ntype = "gmail"\naddress = "{ME}"\n'
                           'password = "env:KB_TEST_GMAIL_PASSWORD"\nabout = "I build furniture."\n')
        self.promo_thread(ago(2))
        self.pricing_thread()
        self.assertEqual(ingest.run(db, cfg, LOG, with_summaries=False, model_factory=lambda: keeps(skip=())), 0)
        hits = search.search(db, "lanterns", sources=("mail",))
        self.assertEqual([h["source_id"] for h in hits], [f"m1:{PROMO}"])
        self.assertEqual(hits[0]["meta"]["url"], f"https://mail.google.com/mail/u/?authuser=me%40example.com#all/"
                                                 f"{int(PROMO):x}")
        self.promo_thread(ago(1))
        self.assertEqual(ingest.run(db, cfg, LOG, with_summaries=False, model_factory=keeps), 0)
        self.assertEqual(search.search(db, "lanterns", sources=("mail",)), [])
        self.assertEqual([h["source_id"] for h in search.search(db, "pricing sheet", sources=("mail",))],
                         [f"m1:{PRICING}"])


class RenderedBodyTest(unittest.TestCase):
    def test_a_body_that_starts_with_a_line_separator_keeps_its_text(self):
        for separator in ("\u2028", "\u2029", "\x85"):
            msg = EmailMessage()
            msg["From"], msg["Subject"] = "Dana <dana@example.org>", "Launch"
            msg.set_content(f"{separator}> 5, and the launch is approved.")
            thread = gmail.Thread(PRICING, 0, (gmail.Message(1, ago(days=1).timestamp(), ("\\Inbox",),
                                                             "Dana <dana@example.org>", "Launch"),))
            item = gmail._render(ME, thread, {1: msg.as_bytes()})
            self.assertIn("launch is approved.", item.content, repr(separator))


class CleanBodyTest(unittest.TestCase):
    def test_a_line_separator_does_not_start_a_quoted_line(self):
        body = "Decision: the required comparison is\u2028> 5, and the launch is approved."
        self.assertEqual(gmail._clean_body(body), body)

    def test_a_line_separator_does_not_make_a_reply_delimiter(self):
        for body in ("Status\n---\u2028Original Message---\nThe launch is approved.",
                     "Status\n\u2028> 5, and the launch is approved.",
                     "Status\n--\u2028\nThe launch is approved.",
                     "Status\nOn\u2028Monday the team wrote:\n> earlier\nThe launch is approved."):
            self.assertIn("launch is approved.", gmail._clean_body(body), repr(body))

    def test_crlf_mail_still_drops_its_quoted_reply(self):
        body = "Thanks, see you then.\r\n\r\nOn Mon, Oct 5, 2026 at 9:00 AM Dana <dana@example.org> wrote:\r\n> old\r\n"
        self.assertEqual(gmail._clean_body(body), "Thanks, see you then.")


if __name__ == "__main__":
    unittest.main()
