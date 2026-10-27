from cast import GH_ME, JULES, ORG
from model import Mail, Post, Q, Story, claude, codex, commit, email, hermes, note, texts, thread

REPO = f"{ORG}/kindling-waitlist"
NOOR = "Noor Haddad <noor@kindlingyogapilates.com>"
NOOR_NAME, NOOR_PHONE = "Noor Haddad", "+17205550174"
CALLUM = "Callum Reyes <callum@kindlingyogapilates.com>"
CALLUM_NAME, CALLUM_PHONE = "Callum Reyes", "+17205550193"
BEA = "Bea Ostrowski <bea@kindlingyogapilates.com>"


def build():
    s = Story()
    s.add(email("b2-intro", "Waitlist texts for our two studios?", [
        Mail(NOOR, JULES, "09-02 10:15", """Hi Jules,

Leah Strand at Southpaw Climbing gave me your name. She said you built their day-pass check-in thing and it
just works, which is rare.

I own Kindling Yoga & Pilates. We have two studios, Highlands (on Tennyson) and Wash Park, and about 620 active
members between them. Our reformer classes fill up days ahead and always have a waitlist. When someone cancels,
the front desk has to text the waitlist by hand, and by the time anyone answers the class has started. Callum
counted last month: about 30% of reformer spots that open up go empty.

I'd love to have open spots offered automatically by text, and while we're at it, our freeze and late-cancel rules
enforced by software instead of by whoever is at the desk. Can we talk?

Noor Haddad
Owner, Kindling Yoga & Pilates"""),
        Mail(JULES, NOOR, "09-02 13:40", """Hi Noor,

Thanks for reaching out, and say thanks to Leah for me. This sounds like a good fit. Could we meet at one of the
studios next week? I'd like to see how your booking system handles waitlists today.

Jules"""),
        Mail(NOOR, JULES, "09-03 08:20", """Thursday the 10th at 1:30 at the Highlands studio works. It's quiet between the noon
class and the 4:30. Callum, our operations manager, will come over from Wash Park.

Noor""", cc=CALLUM),
    ]))

    s.add(note("b2-site-notes", "Clients/Kindling/Kindling website notes.md", """# Kindling website notes (before the call)

From kindling's public site, for prep. Noor says parts are out of date.

- Classes: Vinyasa, Yin, Hot Flow (mat). Reformer Flow, Reformer Strength (reformer).
- Prices: drop-in $26, 10-class pack $220, unlimited $179/mo, annual prepaid $1,850.
- Cancel policy on the site: "cancel at least 8 hours ahead or pay a $12 fee". Doesn't mention reformer.
- Freeze on the site: 1 freeze per year, up to 30 days, $15 flat.
- Waitlist: "we'll text you if a spot opens", which is the manual process.

Questions for the call: is the waitlist in their booking software? who decides who gets a spot?
""", "09-04 16:20"))

    s.add(note("b2-discovery", "Clients/Kindling/2026-09-10 Kindling discovery.md", """---
date: 2026-09-10
client: Kindling Yoga & Pilates
tags: [meeting, kindling]
---
# Kindling discovery (Highlands studio)

Noor Haddad (owner, signs off), Callum Reyes (operations manager, runs Wash Park). Bea Ostrowski runs the
Highlands front desk, came in for the last 15 min.

## Their setup
- Booking + billing: **Bendwise**. Has a REST API and webhooks (`booking.cancelled`, `waitlist.joined`).
  Callum will get us a sandbox key.
- Capacity: reformer classes have **9 reformers at Highlands, 12 at Wash Park**. Mat classes cap at 28.
- Waitlist today is a list in Bendwise; staff text people from their own phones.

## Waitlist rules (Noor)
- Spot opens -> text the first person on the waitlist. They reply YES to claim it within **20 minutes**,
  otherwise it goes to the next person.
- Inside **90 minutes** of class start there's no time for one-by-one: text everyone left on the waitlist at
  once, first YES gets it. Noor calls this "last call".
- Waitlist closes **30 minutes before class**. No offers after that.
- Quiet hours: no texts between **9pm and 6am**. A spot that opens overnight gets offered at 6:00.

## Late cancels
- Mat yoga: cancelling within **12 hours** of class is a late cancel, **$15**.
- Reformer: within **24 hours**, **$25**.
- Class-pack members don't pay the fee; they **lose the class credit** instead.
- No-show = same as a late cancel.

## Freezes
- Callum to send the current policy doc. Noor: "the website is wrong, don't go by that".

## Next
- [ ] proposal
- [ ] look at Bendwise webhook docs
- [ ] Twilio: local 720 number, 10DLC registration
"""))

    s.add(email("b2-proposal", "Kindling waitlist + membership rules: proposal", [
        Mail(JULES, NOOR, "09-11 15:30", """Hi Noor,

Attached is the proposal we talked about:

- Waitlist offers by text, from Bendwise cancellations, with the one-by-one and last-call rules from Thursday.
- Late-cancel and no-show fees applied automatically in Bendwise.
- Membership freezes checked against your policy and scheduled in Bendwise.
- A small web app (FastAPI + Postgres on Fly.io) that runs all of it, with a page for staff to see who was offered
  what.

Price: $7,400 fixed, 50% to start. After launch, $95 a month for hosting and support. Twilio texting is billed to
you at cost, which at your volume should be well under $40 a month.

Jules Marlow
Marlow Works""", cc=CALLUM, attachments=("Marlow Works - Kindling proposal.pdf",)),
        Mail(NOOR, JULES, "09-12 09:05", """Jules,

Looks great, approved. Send the deposit invoice whenever.

Noor""", cc=CALLUM),
        Mail(JULES, NOOR, "09-12 10:30", """Thanks Noor! Deposit invoice MW-2051 for $3,700 is attached. I'll start on
Tuesday.

Jules""", cc=CALLUM, attachments=("MW-2051.pdf",)),
    ]))

    s.add(claude("b2-claude-scaffold", "09-15 09:10", 95, REPO, "Scaffold kindling-waitlist: Bendwise webhook and offers", """
U: new repo kindling-waitlist. fastapi + postgres. bendwise sends a booking.cancelled webhook when someone cancels. when a spot opens on a class with a waitlist, text the first person via twilio, they reply YES within 20 min to claim, else move to the next one. inside 90 min of class start text everyone at once, first yes wins. waitlist closes 30 min before class
T Bash: ls /Users/jules/code/kindling-waitlist
R: README.md  pyproject.toml
T Read: /Users/jules/code/kindling-waitlist/pyproject.toml
R: [project]
name = "kindling-waitlist"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi", "uvicorn", "psycopg[binary]", "twilio", "httpx"]
A: Plan:

- `app/webhooks.py`: `POST /bendwise/webhook` verifies the signature header and, for `booking.cancelled`, calls `open_spot(class_id)`.
- `app/offers.py`: an `offers` table (class_id, member_id, sent_at, expires_at, status). `open_spot` picks the next waitlisted member who hasn't had an offer for this spot, texts them, and sets `expires_at = sent_at + CLAIM_WINDOW`.
- A worker loop every 30 seconds expires stale offers and moves to the next person.
- Inside `LAST_CALL = 90 min` of class start, `open_spot` texts every remaining waitlisted member and the first YES wins.
- After `WAITLIST_CLOSE = 30 min` before class, nothing is sent.
T Edit: /Users/jules/code/kindling-waitlist/app/offers.py
R: The file has been updated successfully.
T Bash: sed -n 1,12p app/offers.py
R: from datetime import timedelta

CLAIM_WINDOW = timedelta(minutes=20)
LAST_CALL = timedelta(minutes=90)
WAITLIST_CLOSE = timedelta(minutes=30)
T Edit: /Users/jules/code/kindling-waitlist/app/webhooks.py
R: The file has been updated successfully.
T Bash: uv run pytest -q
E: FAILED tests/test_offers.py::test_last_call_texts_everyone - AssertionError: assert 1 == 4
T Read: /Users/jules/code/kindling-waitlist/app/offers.py
R: def open_spot(conn, class_id, now):
    cls = get_class(conn, class_id)
    if cls.starts_at - now < WAITLIST_CLOSE:
        return []
    if cls.starts_at - now > LAST_CALL:
A: The comparison is backwards: last call should apply when the time left is *under* 90 minutes. Fixing.
T Edit: /Users/jules/code/kindling-waitlist/app/offers.py
R: The file has been updated successfully.
T Bash: uv run pytest -q
R: .........
9 passed in 0.62s
A: Scaffold is in. Offers go one at a time with a 20-minute window, switch to last call inside 90 minutes, and stop 30 minutes before class. Twilio is stubbed behind `send_sms()` until the number is registered.
"""))

    s.add(commit("b2-commit-scaffold", REPO, "09-15 11:00", """offers: one-by-one waitlist offers from Bendwise cancellations

A cancelled booking offers the spot to the next waitlisted member for
20 minutes. Inside 90 minutes of class everyone left is texted at once
(last call). Nothing goes out after the waitlist closes 30 minutes before.""",
                 ["A\tapp/offers.py", "A\tapp/webhooks.py", "A\ttests/test_offers.py", "M\tpyproject.toml"]))

    s.add(email("b2-freeze-policy", "Freeze policy (the real one)", [
        Mail(CALLUM, JULES, "09-16 08:45", """Hi Jules,

Here's our membership policy, v3, which Noor signed off on in August. The freeze section is the part you need:

- Members can freeze up to 2 times per calendar year.
- Each freeze is at least 14 days and at most 60 days.
- Freeze fee is $10 for each month frozen (a partial month counts as a month).
- They have to ask at least 5 days before their next billing date, otherwise it starts the cycle after.
- Annual prepaid memberships can't be frozen at all.
- Medical freezes: with a doctor's note there's no fee and it can run up to 90 days. These don't count toward the 2.

Our website still says one freeze a year. That's the old 2025 policy, please ignore it, we're fixing the site.

Also, for the late-cancel stuff: drop-ins pay the same fees as members.

Callum Reyes
Operations Manager, Kindling Yoga & Pilates""", cc=NOOR,
             attachments=("Kindling Membership Policies v3.pdf",)),
        Mail(JULES, CALLUM, "09-16 09:30", """Thanks Callum, this is exactly what I needed. I'll build from v3.

Jules"""),
    ]))

    s.add(hermes("b2-hermes-10dlc", "09-17 12:20", 9, "Twilio 10DLC for Kindling", """
U: just submitted the twilio 10DLC brand + campaign for kindling. use case is "customer care", sample message is the waitlist offer. how long does approval usually take
A: Campaign reviews usually take a few business days, sometimes longer if the reviewer asks for changes. Common reasons for rejection: the sample messages don't match the stated use case, there's no opt-out language ("Reply STOP to opt out"), or the website doesn't show how people consent to texts.
U: sample has STOP in it. consent is the checkbox in bendwise signup. ok note that I submitted it today, sept 17
A: Noted: Twilio 10DLC brand and campaign for Kindling submitted September 17. Use case customer care, sample is the waitlist offer with STOP language, consent via the Bendwise signup checkbox.
"""))

    s.add(texts("b2-texts-callum", CALLUM_NAME, CALLUM_PHONE, "09-18", """
08:52 Callum Reyes: Morning! Bendwise sandbox key is in the 1Password share I sent. Studio id is the same as prod
08:58 Jules: got it, thanks
09:03 Callum Reyes: Bendwise support also said their webhooks retry if you don't answer within 5 seconds. Not sure if that matters
09:05 Jules: it does, good to know. I'll make sure we answer fast
14:10 Callum Reyes: Noor says the deposit went out by ACH this morning btw
14:12 Jules: perfect, thank you both
"""))

    s.add(texts("b2-texts-noor", NOOR_NAME, NOOR_PHONE, "09-19", """
07:40 Noor Haddad: Hey Jules, thinking about the waitlist more. 20 minutes to claim is too long
07:41 Noor Haddad: our 5:30pm reformer at Wash Park is the most waitlisted class we have and if a spot opens at 4 there's only time for a couple of people at 20 min each
07:43 Noor Haddad: can we do 15 minutes?
08:15 Jules: Yes, easy change. 15 minutes for everyone, both studios?
08:17 Noor Haddad: Both studios, all classes 👍
08:18 Jules: done, it'll be in the next build
"""))

    s.add(hermes("b2-hermes-yoga-norms", "09-20 16:05", 5, "Typical studio cancel windows", """
U: whats a normal late cancel window for boutique fitness studios? curious if kindling is strict
A: Most boutique studios use 12 hours for regular classes and 12 to 24 hours for equipment-based classes like reformer or cycling, since an empty machine is hard to fill late. Fees commonly run $10 to $25. Some studios count a no-show as a late cancel; others charge more for it.
U: ok so they're normal. thanks
"""))

    s.add(codex("b2-codex-latecancel", "09-21 09:30", 85, REPO, """
U: kindling-waitlist: add late-cancel fees. mat yoga is a late cancel within 12h, $15. reformer within 24h, $25. class-pack members lose the credit instead of paying. no-shows count the same. drop-ins pay the same as members. apply via bendwise add-charge api
T exec_command: ls app tests
R: app:
__init__.py  bendwise.py  db.py  offers.py  sms.py  webhooks.py
tests:
test_offers.py  test_webhooks.py
T exec_command: grep -n "def " app/bendwise.py
R: 8:def get_class(class_id):
19:def get_member(member_id):
31:def waitlist(class_id):
A: I'll add `app/fees.py`:

- `LATE_CANCEL = {"mat": (timedelta(hours=12), 1500), "reformer": (timedelta(hours=24), 2500)}` keyed by the Bendwise class type, amounts in cents.
- `on_cancel(booking, now)`: if the time to class is inside the window, either forfeit the credit (class pack) or post a charge through `bendwise.add_charge` (memberships and drop-ins).
- `on_no_show(booking)`: same path as a late cancel.
- A spot freed by a late cancel still goes to the waitlist like any other.
T apply_patch: *** Begin Patch
*** Add File: app/fees.py
+LATE_CANCEL = {
+    "mat": (timedelta(hours=12), 1500),
+    "reformer": (timedelta(hours=24), 2500),
+}
*** End Patch
R: Success. Updated the following files:
A app/fees.py
T exec_command: uv run pytest -q
R: ..............
14 passed in 0.88s
A: Late-cancel fees are in. Mat: 12 hours, $15. Reformer: 24 hours, $25. Class-pack bookings forfeit the credit instead, and no-shows go through the same path.
""", branch="late-cancel"))

    s.add(commit("b2-commit-latecancel", REPO, "09-21 11:05", """fees: late cancel and no-show fees through Bendwise

Mat yoga within 12h is $15, reformer within 24h is $25. Class-pack
members forfeit the credit instead of paying. No-shows are charged
like late cancels.""", ["A\tapp/fees.py", "M\tapp/bendwise.py", "M\tapp/webhooks.py", "A\ttests/test_fees.py"]))

    s.add(thread("b2-gh-4", REPO, 4, "issue", "Two people offered the same spot", GH_ME, "09-22 08:30", """
In the sandbox, one cancellation sent waitlist offers to two different members for the same spot.

Cause: the webhook handler calls Bendwise and Twilio before answering, which takes 6-8 seconds. Bendwise retries
any webhook that doesn't get a 2xx within 5 seconds, so the retry arrived while the first offer was still going
out, and `open_spot` ran twice.

Fix: store each Bendwise event id (`evt_...`) with a unique constraint and skip ones we've seen; answer 200 right
away and do the work in the background worker.
""", [Post(GH_ME, "09-22 10:50", "Event ids are deduped and the handler answers in under 100 ms now. Replayed the "
                                 "same event 5 times in the sandbox: one offer. Closing.")],
        state="closed", closed="09-22 10:51", labels=("bug",)))

    s.add(commit("b2-commit-claim", REPO, "09-22 11:20", """offers: 15-minute claim window

Noor asked for 15 minutes instead of 20 so a late-afternoon spot can go
through more of the waitlist before last call. Applies to both studios.""",
                 ["M\tapp/offers.py", "M\ttests/test_offers.py"]))

    s.add(codex("b2-codex-twilio", "09-23 09:00", 40, REPO, """
U: twilio campaign for kindling got approved this morning. wire up the real number and send a test offer to my phone
T exec_command: twilio api:messaging:v1:services:compliance:usa2p:list --messaging-service-sid "$TWILIO_MSG_SERVICE" -o json | jq '.[0].campaignStatus'
R: "VERIFIED"
T exec_command: twilio phone-numbers:list --properties phoneNumber,friendlyName
R: Phone Number  Friendly Name
+17205550186  Kindling waitlist
T apply_patch: *** Begin Patch
*** Update File: app/sms.py
@@
-def send_sms(to, body):
-    log.info("sms stub to=%s body=%s", to, body)
+def send_sms(to, body):
+    return client.messages.create(messaging_service_sid=settings.twilio_msg_service, to=to, body=body)
*** End Patch
R: Success. Updated the following files:
M app/sms.py
T exec_command: uv run python -m app.cli test-offer --to "$JULES_PHONE"
R: queued SM3f0c... from +17205550186
A: The campaign shows VERIFIED, and the test offer went out from +1 720-555-0186, the local number on the Kindling messaging service. The message reads: "A spot opened in Reformer Flow, Thu 5:30pm at Wash Park. Reply YES in 15 min to claim it. Reply STOP to opt out. – Kindling"
U: got it. looks good
"""))

    s.add(thread("b2-gh-6", REPO, 6, "PR", "Membership freezes from the v3 policy", GH_ME, "09-23 15:00", """
Adds freeze requests to the staff page and checks them against the v3 policy Callum sent on Sep 16.

- At most 2 freezes per calendar year, each 14 to 60 days.
- A freeze always starts on the member's next billing date. A request made fewer than 5 days before that date
  starts the cycle after.
- Fee: $10 per frozen month, partial months round up, posted with `bendwise.add_charge`.
- Annual prepaid members are refused.
- Medical freezes skip the fee, allow up to 90 days, and don't count toward the yearly 2.
- The freeze itself is a Bendwise membership pause (`POST /memberships/{id}/pause`), so billing stops on their side.
""", [Post(GH_ME, "09-24 09:40", "Tested against the sandbox with the 6 cases from the policy doc, all pass. Merging.")],
        state="merged", merged="09-24 09:41", branches="freezes -> main", labels=("feature",)))

    s.add(texts("b2-texts-noor-launch", NOOR_NAME, NOOR_PHONE, "09-24", """
12:05 Noor Haddad: Random thought, could we launch both studios on Oct 5?
12:20 Jules: Possibly. Let's see how Callum's staff test goes tomorrow first
12:21 Noor Haddad: Fair. Callum also wants a training week at Wash Park so maybe not
"""))

    s.add(email("b2-staff-test", "Staff test: mostly great, two things", [
        Mail(CALLUM, JULES, "09-25 13:10", """Hi Jules,

We ran the waitlist test with 6 staff at both studios this morning. Offers came fast and last call worked. Two
problems:

1. Every offer for a Wash Park class said "at Highlands".
2. Bea replied "Yes!" and Marco replied "y" and neither got the spot. Only plain "YES" worked. People will not type
   it exactly.

Otherwise, really good. Bea loved the staff page.

Callum""", cc=BEA),
        Mail(JULES, CALLUM, "09-25 14:00", """Thanks Callum, both are on me. I'll fix them tomorrow morning and we can re-run
the Wash Park part.

Jules"""),
    ]))

    s.add(claude("b2-claude-fix", "09-26 08:45", 60, REPO, "Fix Wash Park location name and loose YES replies", """
U: two bugs from callum's staff test. wash park offers say "at Highlands". and replies like "Yes!" or "y" don't claim, only exact YES
T Grep: location
R: app/offers.py:41:    loc = LOCATIONS.get(cls.room_id, "Highlands")
app/bendwise.py:12:        "room_id": raw["room"]["id"],
T Read: /Users/jules/code/kindling-waitlist/app/offers.py
R: LOCATIONS = {"rm_204": "Highlands", "rm_311": "Wash Park"}
A: Found the first one. The location name comes from the room id, and the Wash Park reformer room in the live config is `rm_318`, not `rm_311`, so the lookup falls back to "Highlands". Rooms change; locations don't. I'll key on Bendwise's location id instead: `loc_31` is Highlands and `loc_47` is Wash Park, and an unknown location raises instead of guessing.

For replies, `parse_reply` compares the whole body to "YES". I'll accept any reply whose first word, lowercased with punctuation stripped, is one of yes, y, yep, yeah, or ya.
T Edit: /Users/jules/code/kindling-waitlist/app/offers.py
R: The file has been updated successfully.
T Edit: /Users/jules/code/kindling-waitlist/app/sms.py
R: The file has been updated successfully.
T Bash: uv run pytest -q
R: ..................
18 passed in 0.94s
A: Both fixed. Offers now name the studio from the Bendwise location id (`loc_31` Highlands, `loc_47` Wash Park), and "Yes!", "y", "yep" and "Yeah I'll take it" all claim the spot. "no" and "maybe" don't.
"""))

    s.add(note("b2-overview", "Clients/Kindling/Kindling overview.md", """# Kindling Yoga & Pilates

- Owner: Noor Haddad (approves spending). Ops: Callum Reyes (Wash Park). Highlands desk: Bea Ostrowski.
- Studios: Highlands (Tennyson St), Wash Park. ~620 members.
- Booking/billing: Bendwise. Location ids: loc_31 Highlands, loc_47 Wash Park.
- Repo: marlow-works/kindling-waitlist. FastAPI + Postgres on Fly.io.
- Texts: Twilio, messaging service "Kindling waitlist".

## Money
- $7,400 fixed, deposit MW-2051 ($3,700) paid. $95/mo after launch.

## Status
- Waitlist offers, late-cancel fees, freezes: built.
- Staff test round 1 (Sep 25): Wash Park named as Highlands, loose YES replies. Fixed Sep 26.
""", "09-26 10:30"))

    s.add(email("b2-golive", "Launch dates", [
        Mail(NOOR, JULES, "09-29 11:30", """Hi Jules,

Callum re-ran the Wash Park test and it's all right now. Let's launch at Highlands first on Monday October 5. Wash
Park follows on Monday October 19, after Callum's week of front-desk training there.

Can you be around the morning of the 5th in case something weird happens?

Noor""", cc=CALLUM),
        Mail(JULES, NOOR, "09-29 12:10", """Sounds good. I'll watch the Highlands offers live on the 5th from the 6am class
on.

Jules""", cc=CALLUM),
    ]))

    s.ask(
        Q("b2-01", "update", "How long does someone on the Kindling waitlist have to claim an open spot?",
          "15 minutes, at both studios. It started as 20 minutes; Noor asked for 15 on September 19. Inside 90 "
          "minutes of class, last call texts everyone left at once and the first YES gets the spot. Nothing goes "
          "out within 30 minutes of class, when the waitlist closes.",
          ("b2-texts-noor", "b2-commit-claim", "b2-codex-twilio", "b2-discovery", "b2-claude-scaffold",
           "b2-commit-scaffold"),
          evidence_has=("15 minutes", "30 minutes before")),
        Q("b2-02", "single", "What booking system is Kindling on?",
          "Bendwise, which handles their booking and billing and has a REST API and webhooks.",
          ("b2-discovery", "b2-overview"), evidence_has=("Bendwise",)),
        Q("b2-03", "single", "What's Kindling's cutoff and fee for cancelling a reformer class late?",
          "Within 24 hours of class is a late cancel, $25. Class-pack members lose the class credit instead of "
          "paying.",
          ("b2-discovery", "b2-codex-latecancel", "b2-commit-latecancel"), evidence_has=("24", "$25")),
        Q("b2-04", "single", "What does a mat class late cancel cost at Kindling, and how late counts?",
          "$15, for cancelling within 12 hours of class. Class-pack members lose the class credit instead of paying.",
          ("b2-discovery", "b2-codex-latecancel", "b2-commit-latecancel"), evidence_has=("12", "$15")),
        Q("b2-05", "single", "Do class-pack people at Kindling pay the late-cancel fee?",
          "No. Class-pack members lose the class credit instead of paying the fee.",
          ("b2-discovery", "b2-codex-latecancel", "b2-commit-latecancel"), evidence_has=("credit",)),
        Q("b2-06", "single", "What happens when a Kindling spot opens less than 90 minutes before class?",
          "Last call: everyone left on the waitlist is texted at once and the first YES gets the spot, unless it's "
          "within 30 minutes of class, when the waitlist has closed and no offer goes out.",
          ("b2-discovery", "b2-claude-scaffold", "b2-commit-scaffold"), evidence_has=("90 minutes", "at once")),
        Q("b2-07", "single", "When does a Kindling waitlist stop sending offers before class?",
          "30 minutes before class, when the waitlist closes.",
          ("b2-discovery", "b2-claude-scaffold", "b2-commit-scaffold"), evidence_has=("30 minutes before",)),
        Q("b2-08", "single", "Are there hours when Kindling's waitlist shouldn't text people?",
          "Yes, no texts between 9pm and 6am; a spot that opens overnight is offered at 6:00.",
          ("b2-discovery",), evidence_has=("9pm and 6am",)),
        Q("b2-09", "single", "How many times a year can a Kindling member freeze?",
          "Twice per calendar year (medical freezes don't count toward the two). Annual prepaid memberships "
          "can't be frozen at all.",
          ("b2-freeze-policy", "b2-gh-6"), evidence_has=("2 times per calendar year",)),
        Q("b2-10", "single", "What's the shortest and longest freeze Kindling allows?",
          "At least 14 days and at most 60 days; a medical freeze with a doctor's note can run up to 90 days.",
          ("b2-freeze-policy", "b2-gh-6"),
          evidence_has=("14 days", "60 days")),
        Q("b2-11", "single", "What does it cost a Kindling member to freeze?",
          "$10 for each month frozen; a partial month counts as a full month. A medical freeze with a doctor's note "
          "has no fee.", ("b2-freeze-policy", "b2-gh-6"),
          evidence_has=("$10",)),
        Q("b2-12", "single", "How much notice does a Kindling member need to give to freeze?",
          "At least 5 days before their next billing date; otherwise the freeze starts the cycle after.",
          ("b2-freeze-policy", "b2-gh-6"), evidence_has=("5 days before",)),
        Q("b2-13", "single", "How do medical freezes work at Kindling?",
          "With a doctor's note there's no fee, they can run up to 90 days, and they don't count toward the "
          "two-a-year limit.", ("b2-freeze-policy", "b2-gh-6"), evidence_has=("90 days",)),
        Q("b2-14", "single", "Can annual members at Kindling freeze?",
          "No. Annual prepaid memberships can't be frozen.", ("b2-freeze-policy", "b2-gh-6"),
          evidence_has=("Annual prepaid",)),
        Q("b2-15", "single", "Kindling's website says one freeze a year. Is that right?",
          "No. That's the old 2025 policy; the current v3 policy allows two freezes per calendar year, "
          "excluding medical freezes. Annual prepaid memberships can't be frozen.",
          ("b2-freeze-policy",), evidence_has=("one freeze a year", "2025")),
        Q("b2-16", "single", "What did I quote Kindling for the waitlist app?",
          "$7,400 fixed (50% up front), then $95 a month for hosting and support, with Twilio texting passed through "
          "at cost.", ("b2-proposal", "b2-overview"), evidence_has=("$7,400", "$95")),
        Q("b2-17", "single", "What phone number do Kindling's waitlist texts come from?",
          "+1 720-555-0186, the local number on the \"Kindling waitlist\" Twilio messaging service.",
          ("b2-codex-twilio",), evidence_has=("+17205550186",)),
        Q("b2-18", "single", "Why did two people get offered the same Kindling spot in testing?",
          "Bendwise retries a webhook that isn't answered within 5 seconds; the handler took 6-8 seconds, so the "
          "retry ran open_spot again. Fixed by deduping Bendwise event ids and answering 200 right away.",
          ("b2-gh-4",), evidence_has=("within 5 seconds", "event id")),
        Q("b2-19", "single", "Who sent Noor from Kindling my way?",
          "Leah Strand at Southpaw Climbing.", ("b2-intro",), evidence_has=("Leah Strand",)),
        Q("b2-20", "single", "Before we built anything, how many open reformer spots at Kindling went unfilled?",
          "About 30% of reformer spots that opened up went empty.", ("b2-intro",), evidence_has=("30%",)),
        Q("b2-21", "single", "How many reformers does each Kindling studio have?",
          "9 at Highlands and 12 at Wash Park.", ("b2-discovery",), evidence_has=("9 reformers at Highlands",)),
        Q("b2-22", "single", "Which Kindling class has the longest waitlist?",
          "The 5:30pm reformer class at Wash Park.", ("b2-texts-noor",), evidence_has=("5:30pm reformer",)),
        Q("b2-23", "single", "What replies count as claiming a Kindling spot now?",
          "Any reply whose first word, ignoring case and punctuation, is yes, y, yep, yeah or ya.",
          ("b2-claude-fix",), evidence_has=("yes, y, yep, yeah, or ya",)),
        Q("b2-24", "single", "What are the Bendwise location ids for Kindling's studios?",
          "loc_31 is Highlands and loc_47 is Wash Park.", ("b2-claude-fix", "b2-overview"),
          evidence_has=("loc_31", "loc_47")),
        Q("b2-25", "single", "When does a Kindling freeze actually start?",
          "On the member's next billing date (or the cycle after, if requested fewer than 5 days before it).",
          ("b2-gh-6", "b2-freeze-policy"), evidence_has=("next billing date",)),
        Q("b2-26", "single", "What's the Kindling app hosted on?",
          "FastAPI and Postgres on Fly.io.", ("b2-proposal", "b2-overview"), evidence_has=("Fly.io",)),
        Q("b2-27", "single", "Do drop-ins at Kindling get charged late-cancel fees?",
          "Yes, drop-ins pay the same late-cancel fees as members.",
          ("b2-freeze-policy", "b2-codex-latecancel"), evidence_has=("drop-ins pay the same",)),
        Q("b2-28", "temporal", "How long did Twilio take to approve the Kindling texting campaign?",
          "6 days: submitted September 17, approved (VERIFIED) the morning of September 23.",
          ("b2-hermes-10dlc", "b2-codex-twilio"), evidence_has=("September 17", "approved this morning")),
        Q("b2-29", "temporal", "How far apart are the two Kindling studio launches?",
          "14 days: Highlands launches Monday October 5 and Wash Park Monday October 19.",
          ("b2-golive",), evidence_has=("October 5", "October 19")),
        Q("b2-30", "multi", "What broke in Kindling's staff test and how did we fix it?",
          "Wash Park offers said \"at Highlands\" because the location came from a stale room id; it now uses the "
          "Bendwise location id. Replies like \"Yes!\" or \"y\" didn't claim the spot; any reply whose first word, "
          "ignoring case and punctuation, is yes, y, yep, yeah or ya now does. Fixed September 26.",
          ("b2-staff-test", "b2-claude-fix"), evidence_has=("at Highlands", "Yes!", "location id")),
        Q("b2-31", "multi", "Has Kindling paid the deposit, and how much was it?",
          "Yes. Invoice MW-2051 for $3,700 was paid by ACH on September 18.",
          ("b2-proposal", "b2-texts-callum", "b2-overview"), evidence_has=("MW-2051", "$3,700", "ACH")),
    )
    return s
