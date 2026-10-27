from cast import GH_ME, JULES, ORG
from model import Mail, Post, Q, Story, claude, codex, commit, email, hermes, note, texts, thread

REPO = f"{ORG}/tallgrass-dispatch"
GRAHAM = "Graham Pruitt <graham@tallgrass-home.com>"
GRAHAM_NAME, GRAHAM_PHONE = "Graham Pruitt", "+13035550163"
LETICIA = "Leticia Navarro <leticia@tallgrass-home.com>"
LETICIA_NAME, LETICIA_PHONE = "Leticia Navarro", "+13035550179"
NADIA = "Nadia Szabo <books@tallgrass-home.com>"


def build():
    s = Story()
    s.add(email("b1-intro", "Help with service requests", [
        Mail(GRAHAM, JULES, "09-01 06:42", """Hi Jules,

I heard you talk at the Wheat Ridge Business Association breakfast in August about automating the boring stuff, and
I've got some boring stuff.

I own Tallgrass Home Services in Wheat Ridge. Heating, cooling and plumbing, 12 technicians (8 HVAC, 4 plumbers).
Service requests come in three ways: email to service@, the request form on our website, and texts to our office
line. Leticia, our dispatcher, retypes every one of them into our dispatch spreadsheet. That's about 3 hours of her
day. We get around 40 requests a day this time of year and up to 90 on a cold snap.

The other half of the problem: customers call all day asking when the tech is coming. I'd like them to get a text
with the arrival window as soon as we assign someone.

Can we talk this week? Mornings are best, we start at 6:30.

Graham Pruitt
Owner, Tallgrass Home Services
(303) 555-0163"""),
        Mail(JULES, GRAHAM, "09-01 09:15", """Hi Graham,

Thanks for reaching out, this sounds like a good fit. How about Thursday the 3rd at 7:30? It would help if Leticia
could join, since she knows how the requests really arrive.

Jules"""),
        Mail(GRAHAM, JULES, "09-01 10:02", """Thursday 7:30 works. Leticia will be on. Come by the shop, 4410 Kipling
St, or we can do it on video.

Graham""", cc=LETICIA),
    ]))

    s.add(note("b1-kickoff", "Clients/Tallgrass/2026-09-03 Tallgrass kickoff.md", """---
date: 2026-09-03
client: Tallgrass Home Services
tags: [meeting, tallgrass]
---
# Tallgrass kickoff

Graham Pruitt (owner), Leticia Navarro (dispatcher). At the shop on Kipling.

## How requests arrive today
- Email to service@tallgrass-home.com (Google Workspace).
- Website request form: WordPress + **Gravity Forms**. Fields: name, phone, address, service type, description,
  preferred day.
- Texts to the office line. Leticia reads them on the office phone. Moving the line to a Twilio number.

## Where they go
- Google Sheet **Dispatch Board**, one tab per day. Leticia assigns a tech in the "Tech" column and types a window.
- No ticket numbers today. Leticia finds jobs by customer name.

## Arrival windows
- 4-hour windows: morning 8-12, afternoon 12-4.
- Text the customer the window when a tech is assigned.

## Rules
- Customer texts only between 7:00 and 20:00. Anything outside waits until 7:00.
- Emergencies jump the queue and go to the on-call tech. Gas smell is the big one: never send a window, tell them
  to get out of the house.
- Leticia to send a list of what counts as an emergency.

## Timeline
- Go-live target: **Monday October 5**.

## Next
- [ ] proposal
- [ ] A2P 10DLC registration for the Twilio number (needed before texting customers)
- [ ] read access to the Dispatch Board sheet
"""))

    s.add(email("b1-proposal", "Tallgrass dispatch: proposal", [
        Mail(JULES, GRAHAM, "09-04 09:30", """Hi Graham,

Attached is the proposal for Tallgrass dispatch:

- Every request from email, the website form and texts becomes a ticket on the Dispatch Board automatically.
- Emergencies are flagged and the on-call tech is paged.
- When Leticia assigns a tech, the customer gets a text with the arrival window.

Price: $7,400 fixed, 50% to start. After launch, $95 a month for hosting, plus Twilio usage at cost (usually under
$30 a month at your volume).

Jules Marlow
Marlow Works""", cc=LETICIA, attachments=("Marlow Works - Tallgrass proposal.pdf",)),
        Mail(GRAHAM, JULES, "09-04 12:15", """Looks good. Let's go. Send the invoice to Nadia, she does our books.

Graham""", cc=NADIA),
        Mail(JULES, NADIA, "09-04 13:05", """Hi Nadia,

The deposit invoice for Tallgrass dispatch is attached: MW-2061, $3,700, due on receipt. ACH details are on the
invoice.

Thanks,
Jules""", cc=GRAHAM, attachments=("MW-2061.pdf",)),
    ]))

    s.add(texts("b1-texts-leticia", LETICIA_NAME, LETICIA_PHONE, "09-08", """
08:12 Leticia Navarro: Hi Jules it's Leticia from Tallgrass. Graham said to send you the area stuff
08:13 Leticia Navarro: We go Golden, Arvada, Lakewood, Wheat Ridge, Westminster, Edgewater and west Denver
08:14 Leticia Navarro: Nothing west of Evergreen and nothing north of 120th. People up there still try us every week
08:20 Jules: Thanks! What do you do with those now?
08:22 Leticia Navarro: I text them sorry we don't service that area. But I'd like to see them, Graham keeps asking if we should expand
08:24 Jules: I'll send them a polite decline automatically and put them on an "Out of area" tab instead of the day's board
08:24 Leticia Navarro: Perfect
08:31 Leticia Navarro: Emergency list coming tonight, I want to ask Dmitri first
"""))

    s.add(claude("b1-claude-intake", "09-08 13:10", 140, REPO, "Intake from email, web form and texts", """
U: new repo tallgrass-dispatch. fastapi app. three intake paths into one Ticket model: 1) poll the service@ inbox with the gmail api, 2) gravity forms webhook, 3) twilio inbound sms webhook. each ticket becomes a row on the Dispatch Board google sheet. they have no ticket numbers today so we need a format. also: customers often email AND text about the same problem, don't make two tickets
T Bash: ls /Users/jules/code/tallgrass-dispatch
R: README.md  pyproject.toml  src  tests
T Read: /Users/jules/code/tallgrass-dispatch/pyproject.toml
R: [project]
name = "tallgrass-dispatch"
requires-python = ">=3.12"
dependencies = ["fastapi", "uvicorn", "google-api-python-client", "twilio", "gspread"]
A: Plan:

- `src/dispatch/ticket.py`: one `Ticket` dataclass (source, name, phone, address, service type, description, received_at).
- `src/dispatch/intake/gmail.py`: polls service@ through the Gmail API every 2 minutes, parses each unread message into a ticket, then applies the label `Ticketed` so it is never read twice.
- `src/dispatch/intake/form.py`: `POST /intake/form` for the Gravity Forms webhook add-on. It rejects any request without the right `X-Tallgrass-Secret` header.
- `src/dispatch/intake/sms.py`: `POST /intake/sms` for Twilio inbound messages, checked with Twilio's request signature.
- `src/dispatch/board.py`: appends a row to the day's tab on the Dispatch Board.

Ticket numbers: `TG-YYMM-NNNN`, for example `TG-2609-0001`. The four-digit sequence restarts each month, which keeps the numbers short enough for Leticia to read over the phone.

Duplicates: if a request comes in from a phone number that already has a ticket opened in the last 30 minutes, the new message is appended to that ticket's notes instead of creating a new one.
T Edit: /Users/jules/code/tallgrass-dispatch/src/dispatch/ticket.py
R: The file has been updated successfully.
T Edit: /Users/jules/code/tallgrass-dispatch/src/dispatch/intake/gmail.py
R: The file has been updated successfully.
T Edit: /Users/jules/code/tallgrass-dispatch/src/dispatch/intake/form.py
R: The file has been updated successfully.
T Edit: /Users/jules/code/tallgrass-dispatch/src/dispatch/intake/sms.py
R: The file has been updated successfully.
T Edit: /Users/jules/code/tallgrass-dispatch/src/dispatch/board.py
R: The file has been updated successfully.
T Bash: cd /Users/jules/code/tallgrass-dispatch && uv run pytest -q
E: FAILED tests/test_ticket.py::test_merge_same_phone - AssertionError: 2 tickets, expected 1
phone "(303) 555-0142" vs "+13035550142"
T Edit: /Users/jules/code/tallgrass-dispatch/src/dispatch/ticket.py
R: The file has been updated successfully.
T Bash: cd /Users/jules/code/tallgrass-dispatch && uv run pytest -q
R: ..............
14 passed in 0.62s
A: All three intake paths work against fixtures. The duplicate test caught one thing: the form gives phone numbers as "(303) 555-0142" and Twilio gives "+13035550142", so phones are now normalized to E.164 before the 30-minute match.
U: out of area requests too. nothing west of evergreen or north of 120th. polite decline text, put them on an "Out of area" tab
T Edit: /Users/jules/code/tallgrass-dispatch/src/dispatch/area.py
R: The file has been updated successfully.
T Bash: cd /Users/jules/code/tallgrass-dispatch && uv run pytest -q
R: .................
17 passed in 0.70s
A: Done. Addresses are geocoded once; anything outside the service area gets the decline text and a row on the "Out of area" tab, with no ticket number.
""", branch="intake"))

    s.add(commit("b1-commit-intake", REPO, "09-08 15:40", """intake: tickets from Gmail, Gravity Forms and Twilio SMS

Tickets are numbered TG-YYMM-NNNN, restarting each month. A request from a
phone with a ticket opened in the last 30 minutes is merged into it.""",
                 ["A\tsrc/dispatch/ticket.py", "A\tsrc/dispatch/intake/gmail.py", "A\tsrc/dispatch/intake/form.py",
                  "A\tsrc/dispatch/intake/sms.py", "A\tsrc/dispatch/board.py", "A\tsrc/dispatch/area.py",
                  "A\ttests/test_ticket.py", "A\ttests/test_intake.py"]))

    s.add(hermes("b1-hermes-10dlc", "09-09 11:05", 4, "Tallgrass texting registration", """
U: note for tallgrass: submitted the a2p 10dlc brand and campaign for their twilio number this morning. twilio says review takes 1 to 3 weeks. we can't text customers until it's approved
A: Noted. Tallgrass: A2P 10DLC brand and campaign for the Twilio number submitted Wednesday, September 9. Twilio estimates 1 to 3 weeks for review, and customer texts can't go out until it's approved.
U: if it isnt approved by the 25th remind me, that's when the pilot needs real texts
A: I'll remind you on Friday, September 25 if you haven't told me it's approved.
"""))

    s.add(email("b1-payment", "MW-2061 paid", [
        Mail(NADIA, JULES, "09-10 08:50", """Hi Jules,

ACH for invoice MW-2061 ($3,700) went out this morning. You should see it in 1-2 business days.

Nadia Szabo
Bookkeeping, Tallgrass Home Services"""),
        Mail(JULES, NADIA, "09-10 09:20", """Got it, thank you Nadia!

Jules"""),
    ]))

    s.add(codex("b1-codex-classify", "09-11 08:40", 95, REPO, """
U: tallgrass-dispatch: add priority classification. P1 = emergency, page the on-call tech. leticia's list: gas smell, water leaking / flooding / burst pipe, no heat, no hot water. gas smell is special: don't send an arrival window, send a safety text instead
T exec_command: ls src/dispatch
R: __init__.py  app.py  area.py  board.py  intake  ticket.py
A: I'll add `src/dispatch/rules.py` with keyword rules per priority and a `classify(ticket)` that returns P1 or P2, plus `page_on_call(ticket)` that texts the on-call line with the ticket number, address and issue.
T apply_patch: *** Begin Patch
*** Add File: src/dispatch/rules.py
+P1_PHRASES = {
+    "gas": ["smell gas", "gas smell", "rotten egg"],
+    "water": ["leaking", "flooding", "burst pipe", "water everywhere"],
+    "heat": ["no heat", "furnace out", "furnace not working"],
+    "hot_water": ["no hot water"],
+}
*** End Patch
R: Success. Updated the following files:
A src/dispatch/rules.py
T exec_command: uv run pytest -q tests/test_rules.py
R: ........
8 passed in 0.21s
U: change of plan, leticia just texted. no hot water is NOT an emergency unless water is leaking too. and no heat only counts as P1 from october 1 through april 30, in summer it's a normal call
T apply_patch: *** Begin Patch
*** Update File: src/dispatch/rules.py
-    "hot_water": ["no hot water"],
 }
+HEAT_SEASON = ((10, 1), (4, 30))
*** End Patch
R: Success. Updated the following files:
M src/dispatch/rules.py
T exec_command: uv run pytest -q
R: .........................
25 passed in 0.88s
A: Updated rules:

- P1: gas smell; water leaking, flooding or a burst pipe; no heat between October 1 and April 30.
- "No hot water" on its own is P2. It becomes P1 only if the message also has a leak phrase.
- For a gas smell, no arrival window is ever sent. The customer gets this text right away: "If you smell gas, leave the house now. Don't use light switches or start a car in the garage. Call 911 from outside. We've alerted our on-call technician." The on-call line is paged at the same time.
- Every P1 ticket is highlighted red on the Dispatch Board and pinned to the top of the day's tab.
""", branch="priority"))

    s.add(commit("b1-commit-classify", REPO, "09-11 10:20", """rules: flag emergencies and page the on-call line

Gas smell gets a safety text and never an arrival window. No heat is P1
only from Oct 1 to Apr 30. No hot water alone stays P2.""",
                 ["A\tsrc/dispatch/rules.py", "M\tsrc/dispatch/app.py", "A\ttests/test_rules.py"]))

    s.add(note("b1-oncall", "Clients/Tallgrass/Tallgrass on-call.md", """# Tallgrass on-call

On-call line: +13035550191. Twilio forwards calls and texts to whoever is on call that week. Emergency pages from
tallgrass-dispatch go to this line, never to a tech's own phone.

Rotation runs Monday 7:00 to the next Monday 7:00. From Leticia's sheet:

| Week of | On call |
|---|---|
| Sep 14 | Dmitri Kowal |
| Sep 21 | Ray Tanaka |
| Sep 28 | Carmen Ruiz |
| Oct 5 | Dmitri Kowal |

Swaps go through Leticia. She changes the forward in the Twilio console; nothing in the app needs to change.
""", "09-14 08:30"))

    s.add(thread("b1-gh-4", REPO, 4, "issue", "Arrival text says \"today\" for tomorrow's jobs", GH_ME, "09-15 07:50", """
Leticia assigned three of Tuesday's jobs on Monday evening around 6:15 pm. All three customers got "your technician
will arrive today between 8 and 12", the night before.

Cause: `render_window()` decides "today" vs "tomorrow" with `datetime.utcnow().date()`. After 18:00 in Denver it's
already the next day in UTC, so tomorrow's job reads as today.

Fix: compare dates in America/Denver, and say the weekday ("Tuesday") instead of today/tomorrow.
""", [Post(GH_ME, "09-15 09:30", "Fixed in the commit that uses America/Denver for every date. Texts now name the "
           "weekday. Closing.")], state="closed", closed="09-15 09:31", labels=("bug",)))

    s.add(commit("b1-commit-tz", REPO, "09-15 09:25", """texts: use America/Denver dates and name the weekday

utcnow() made evening assignments for tomorrow say "today". Fixes #4.""",
                 ["M\tsrc/dispatch/texts.py", "M\ttests/test_texts.py"]))

    s.add(email("b1-windows", "Arrival windows", [
        Mail(GRAHAM, JULES, "09-16 06:55", """Jules,

Three customers this week told Leticia a 4-hour window is too long to sit at home. The outfit in Arvada gives 2-hour
windows and we're losing jobs to them.

Can the texts use 2-hour windows instead? 8-10, 10-12, 12-2, 2-4. Leticia is fine picking from those four.

And can customers get a reminder the morning of the job, with the window again?

Graham"""),
        Mail(JULES, GRAHAM, "09-16 08:10", """Hi Graham,

Yes to both, at the same price. 2-hour windows (8-10, 10-12, 12-2, 2-4) and a reminder text at 7:00 the morning of
the job. I'll have it in tomorrow.

Jules""", cc=LETICIA),
    ]))

    s.add(texts("b1-texts-graham-0917", GRAHAM_NAME, GRAHAM_PHONE, "09-17", """
09:02 Jules: Good news, Twilio approved our texting registration this morning. We can send real customer texts now
09:05 Graham Pruitt: Great. So we're on track for the pilot next week?
09:06 Jules: Yes. Tuesday and Wednesday the 22nd and 23rd, live requests, real texts
09:06 Graham Pruitt: 👍
"""))

    s.add(claude("b1-claude-windows", "09-17 09:30", 110, REPO, "2-hour arrival windows and morning-of reminder", """
U: tallgrass: graham wants 2 hour windows now: 8-10, 10-12, 12-2, 2-4. replaces the 4 hour morning/afternoon ones. plus a reminder text at 7:00 the morning of the job with the window again. keep the 7-20 quiet hours
T Read: /Users/jules/code/tallgrass-dispatch/src/dispatch/texts.py
R: WINDOWS = {
    "AM": (time(8), time(12)),
    "PM": (time(12), time(16)),
}
QUIET_START, QUIET_END = time(20), time(7)
T Grep: WINDOWS|"AM"|"PM"
R: src/dispatch/texts.py:3:WINDOWS = {
src/dispatch/board.py:41:    window = WINDOWS[row["Window"]]
tests/test_texts.py:12:    assert render_window("AM") ==
A: Changes:

- `WINDOWS` becomes four 2-hour windows keyed `8-10`, `10-12`, `12-2`, `2-4`. The Dispatch Board's Window column gets a dropdown with exactly those four, so Leticia can't type a window the code doesn't know.
- New `morning_reminders(day)` job runs at 7:00 America/Denver and texts every customer with a job that day. 7:00 is the start of the allowed hours, so it never collides with the 20:00 to 7:00 quiet hours.
- The arrival text becomes: "Hi {first}, {tech} from Tallgrass will arrive {weekday} between {start} and {end}. Reply C to confirm or R to reschedule."
- A reply of R adds a "Call back to reschedule" note on the ticket and highlights it yellow for Leticia. C marks the ticket confirmed.
T Edit: /Users/jules/code/tallgrass-dispatch/src/dispatch/texts.py
R: The file has been updated successfully.
T Edit: /Users/jules/code/tallgrass-dispatch/src/dispatch/board.py
R: The file has been updated successfully.
T Edit: /Users/jules/code/tallgrass-dispatch/src/dispatch/replies.py
R: The file has been updated successfully.
T Bash: cd /Users/jules/code/tallgrass-dispatch && uv run pytest -q
R: ...............................
31 passed in 1.04s
T Bash: cd /Users/jules/code/tallgrass-dispatch && uv run python -m dispatch.cli send-test --to +13035550179 --window 10-12
R: queued SM2f0c8e1a: "Hi Leticia, Dmitri from Tallgrass will arrive Friday between 10:00 and 12:00. Reply C to confirm or R to reschedule."
A: Done, and Leticia's phone got the test text. Old rows that still say AM or PM are mapped to 8-10 and 12-2 until she reassigns them.
"""))

    s.add(commit("b1-commit-windows", REPO, "09-17 11:30", """texts: 2-hour arrival windows and a 7:00 morning-of reminder

Windows are 8-10, 10-12, 12-2 and 2-4, replacing 4-hour AM/PM. Customers
can reply C to confirm or R to reschedule.""",
                 ["M\tsrc/dispatch/texts.py", "M\tsrc/dispatch/board.py", "A\tsrc/dispatch/replies.py",
                  "M\ttests/test_texts.py", "A\ttests/test_replies.py"]))

    s.add(note("b1-pilot", "Clients/Tallgrass/2026-09-24 Tallgrass pilot.md", """---
date: 2026-09-24
client: Tallgrass Home Services
tags: [tallgrass, pilot]
---
# Tallgrass pilot, Sep 22-23

Two days of live requests with real customer texts. Leticia still watched the inboxes in case something slipped.

## Numbers
- 71 requests: 19 email, 22 web form, 30 text.
- All 71 ended up on the Dispatch Board. 6 were merged into an existing ticket (same phone within 30 minutes).
- 3 out of area, declined and put on the "Out of area" tab.
- Request to ticket: about 40 seconds on average. Leticia's retyping took about 25 minutes per request at busy times.
- 2 P1s (one burst pipe, one gas smell). Both paged the on-call line.

## Problems
- 4 customers never got their arrival text. Twilio error 30006: the number is a landline. Leticia had to call them.
- One customer replied "c" in lowercase. Worked fine.

## Next
- Check the line type before texting, and flag landline tickets for a phone call.
"""))

    s.add(hermes("b1-hermes-golive", "09-25 12:20", 6, "Tallgrass go-live change", """
U: graham called about tallgrass. two things. 1) move go-live from oct 5 to monday oct 12. leticia is out oct 5 to 9 for her sister's wedding and he wants her there the first week. 2) the texts should say "Tallgrass Home Services", not just "Tallgrass"
A: Got it. Tallgrass changes from Graham's call:
1. Go-live moves from Monday October 5 to Monday October 12, because Leticia is out October 5 to 9 and Graham wants her there for the first week.
2. Customer texts should name the company as "Tallgrass Home Services" instead of "Tallgrass".
U: right. thanks
"""))

    s.add(thread("b1-gh-6", REPO, 6, "PR", "Check line type before texting; flag landlines", GH_ME, "09-25 13:30", """
In the pilot, 4 customers never got their arrival text: Twilio error 30006, the number is a landline.

- Before the first text to a new number, look it up with Twilio Lookup (line type intelligence) and cache the result
  on the ticket.
- Landline: send no texts, set the ticket's Contact column to `CALL CUSTOMER` and highlight it orange so Leticia
  phones them with the window instead.
- Also renames the sender in every text to "Tallgrass Home Services".
""", [Post(GH_ME, "09-25 15:05", "Tested against the 4 pilot numbers: all 4 come back as landline and get flagged.")],
        state="merged", merged="09-25 15:10", branches="line-type -> main"))

    s.add(texts("b1-texts-graham-0928", GRAHAM_NAME, GRAHAM_PHONE, "09-28", """
07:40 Graham Pruitt: Another thing. Could the system text people after the job asking for a Google review?
07:52 Jules: Yes. That's a small add-on, $600: a text 2 hours after Leticia marks the job done, with your review link. Skips anyone who got a complaint flag
07:58 Graham Pruitt: Let's hold that until spring. I want the dispatch part solid first
07:59 Jules: Makes sense. I'll put it in the notes for March
"""))

    s.ask(
        Q("b1-01", "update", "How long are the arrival windows in Tallgrass's texts?",
          "2 hours: 8-10, 10-12, 12-2 and 2-4. They replaced the 4-hour morning and afternoon windows planned at "
          "kickoff.", ("b1-kickoff", "b1-windows", "b1-claude-windows", "b1-commit-windows"),
          evidence_has=("2-hour", "8-10, 10-12, 12-2")),
        Q("b1-02", "update", "When does Tallgrass go live?",
          "Monday October 12, moved from Monday October 5 because Leticia is out October 5 to 9.",
          ("b1-kickoff", "b1-hermes-golive"), evidence_has=("October 12",)),
        Q("b1-03", "temporal", "How long did the 10DLC approval take for Tallgrass's texting number?",
          "8 days: submitted September 9, approved the morning of September 17.",
          ("b1-hermes-10dlc", "b1-texts-graham-0917"), evidence_has=("September 9", "approved our texting registration")),
        Q("b1-04", "temporal", "How many days after I sent the Tallgrass deposit invoice did they pay it?",
          "6 days: MW-2061 was sent September 4 and Nadia sent the ACH on September 10.",
          ("b1-proposal", "b1-payment"), evidence_has=("MW-2061", "ACH")),
        Q("b1-05", "multi", "If someone reported a gas smell to Tallgrass on Wednesday Sep 23, who got paged and what "
          "did the customer get?",
          "Ray Tanaka, on call the week of Sep 21, through the on-call line. The customer got a safety text "
          "(leave the house, no light switches or starting a car in the garage, call 911 from outside) and no "
          "arrival window.",
          ("b1-oncall", "b1-codex-classify"), evidence_has=("Ray Tanaka", "call 911 from outside")),
        Q("b1-06", "multi", "What went wrong in the Tallgrass pilot and how did we fix it?",
          "4 customers on landlines never got their arrival text (Twilio error 30006). PR #6 checks the line type "
          "with Twilio Lookup before texting and flags landline tickets CALL CUSTOMER for Leticia to phone.",
          ("b1-pilot", "b1-gh-6"), evidence_has=("30006", "CALL CUSTOMER")),
        Q("b1-07", "single", "How many techs does Tallgrass have?",
          "12 technicians: 8 HVAC and 4 plumbers.", ("b1-intro",), evidence_has=("12 technicians",)),
        Q("b1-08", "single", "How much of Leticia's day went to retyping requests before the automation?",
          "About 3 hours a day.", ("b1-intro",), evidence_has=("3 hours",)),
        Q("b1-09", "single", "How many service requests does Tallgrass get a day?",
          "Around 40 a day this time of year, up to 90 on a cold snap.", ("b1-intro",),
          evidence_has=("40 requests", "90")),
        Q("b1-10", "single", "What does Tallgrass's website use for its request form?",
          "Gravity Forms on WordPress; it posts to /intake/form with an X-Tallgrass-Secret header.",
          ("b1-kickoff", "b1-claude-intake"), evidence_has=("Gravity Forms",)),
        Q("b1-11", "single", "What do Tallgrass ticket numbers look like?",
          "TG-YYMM-NNNN, for example TG-2609-0001; the sequence restarts each month.",
          ("b1-claude-intake", "b1-commit-intake"), evidence_has=("TG-YYMM-NNNN",)),
        Q("b1-12", "single", "What happens when a Tallgrass customer emails and then texts about the same problem?",
          "If the phone number already has a ticket opened in the last 30 minutes, the new message is merged into "
          "that ticket instead of making a new one.", ("b1-claude-intake", "b1-commit-intake", "b1-pilot"),
          evidence_has=("30 minutes",)),
        Q("b1-13", "single", "How often does tallgrass-dispatch check the service@ inbox?",
          "Every 2 minutes, through the Gmail API; processed mail gets the Ticketed label.", ("b1-claude-intake",),
          evidence_has=("every 2 minutes",)),
        Q("b1-14", "single", "Where won't Tallgrass send a tech?",
          "Nothing west of Evergreen and nothing north of 120th; those requests get a polite decline and go on the "
          "Out of area tab.", ("b1-texts-leticia", "b1-claude-intake"), evidence_has=("west of Evergreen", "120th")),
        Q("b1-15", "single", "Is no hot water an emergency for Tallgrass?",
          "No. On its own it's P2; it's only P1 if water is also leaking.", ("b1-codex-classify", "b1-commit-classify"),
          evidence_has=("no hot water",)),
        Q("b1-16", "single", "When does a no-heat call count as P1 at Tallgrass?",
          "Only from October 1 through April 30; in the rest of the year it's a normal call.",
          ("b1-codex-classify", "b1-commit-classify"), evidence_has=("october 1",)),
        Q("b1-17", "single", "What hours are we allowed to text Tallgrass customers?",
          "Between 7:00 and 20:00; anything outside waits until 7:00.", ("b1-kickoff", "b1-claude-windows"),
          evidence_has=("7:00",)),
        Q("b1-18", "single", "What time does the Tallgrass day-of-job reminder go out?",
          "7:00 in the morning, Denver time.", ("b1-windows", "b1-claude-windows", "b1-commit-windows"),
          evidence_has=("7:00",)),
        Q("b1-19", "single", "What can a Tallgrass customer reply to the arrival text, and what happens?",
          "C confirms the ticket; R adds a call-back-to-reschedule note and highlights the ticket yellow for Leticia.",
          ("b1-claude-windows", "b1-commit-windows"), evidence_has=("Reply C to confirm or R to reschedule",)),
        Q("b1-20", "single", "Why did some Tallgrass customers get 'arriving today' texts the night before?",
          "The code used UTC dates (datetime.utcnow), so after 18:00 in Denver tomorrow's jobs read as today. Fixed "
          "to use America/Denver and name the weekday.", ("b1-gh-4", "b1-commit-tz"), evidence_has=("utcnow",)),
        Q("b1-21", "single", "What's Tallgrass paying for the dispatch project?",
          "$7,400 fixed (50% up front), then $95 a month for hosting plus Twilio usage at cost.", ("b1-proposal",),
          evidence_has=("$7,400", "$95 a month")),
        Q("b1-22", "single", "What's the Tallgrass deposit invoice?",
          "MW-2061 for $3,700, sent to Nadia Szabo, who does their books.", ("b1-proposal", "b1-payment"),
          evidence_has=("MW-2061", "$3,700")),
        Q("b1-23", "single", "Which number do Tallgrass emergency pages go to?",
          "The on-call line, +13035550191, which forwards to whoever is on call that week.", ("b1-oncall",),
          evidence_has=("+13035550191",)),
        Q("b1-24", "single", "How did Graham Pruitt find me?",
          "He heard Jules talk at the Wheat Ridge Business Association breakfast in August.", ("b1-intro",),
          evidence_has=("Wheat Ridge Business Association",)),
        Q("b1-25", "single", "How fast does a Tallgrass request turn into a ticket now?",
          "About 40 seconds on average in the pilot, against about 25 minutes when Leticia retyped them at busy "
          "times.",
          ("b1-pilot",), evidence_has=("40 seconds",)),
        Q("b1-26", "single", "What did I quote Graham for review request texts, and is he doing it?",
          "$600 add-on; Graham wants to hold it until spring.", ("b1-texts-graham-0928",),
          evidence_has=("$600", "until spring")),
        Q("b1-27", "single", "What company name should Tallgrass's customer texts use?",
          "\"Tallgrass Home Services\", not just \"Tallgrass\".", ("b1-hermes-golive", "b1-gh-6"),
          evidence_has=("Tallgrass Home Services",)),
        Q("b1-28", "single", "Where do Tallgrass tickets end up?",
          "On the Dispatch Board Google Sheet, one tab per day.", ("b1-kickoff",),
          evidence_has=("Dispatch Board", "one tab per day")),
        Q("b1-29", "single", "How many requests came in during the Tallgrass pilot?",
          "71: 19 email, 22 web form, 30 text.", ("b1-pilot",), evidence_has=("71 requests",)),
        Q("b1-30", "single", "Who handles Tallgrass's on-call swaps?",
          "Leticia; she changes the forward in the Twilio console.", ("b1-oncall",),
          evidence_has=("Swaps go through Leticia",)),
    )
    return s
