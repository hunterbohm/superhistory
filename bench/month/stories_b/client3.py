from cast import GH_ME, JULES, ORG
from model import Mail, Post, Q, Story, claude, codex, commit, email, hermes, note, texts, thread

REPO = f"{ORG}/alderfinch-dispatch"

CALLIE = "Callie Brennan <callie@alderfinchpm.com>"
GIDEON = "Gideon Pratt <gideon@alderfinchpm.com>"
MANNY = "Manny Esquivel <manny@alderfinchpm.com>"
MANNY_NAME, MANNY_PHONE = "Manny Esquivel", "+17205550184"


def build():
    s = Story()
    s.add(email("b3-intro", "Maintenance requests are eating Manny alive", [
        Mail(CALLIE, JULES, "09-02 09:20", """Hi Jules,

Your name came up at a landlord meetup as someone who builds the behind-the-scenes stuff. I'm the operations
manager at Alder & Finch Property Management. We manage 182 rental units across 7 buildings in Denver and
Lakewood.

Here's how maintenance works today, which is to say it doesn't:
- Tenants text Manny (our maintenance coordinator) on his personal cell, or email the office, or call.
- After 5pm and on weekends, calls go to an answering service, NightDesk. We pay them $410 a month and they
  mostly read a script and call whichever tech is on call.
- Manny copies everything into a Google Sheet by hand and texts the vendor.

Things fall through the cracks, and Manny hasn't had a quiet weekend since spring. We'd like requests to land in
one place as work orders, get routed to the right vendor, and have real after-hours emergency rules.

Could we talk next week? Gideon owns the company and will want to be on the call.

Callie Brennan
Operations Manager, Alder & Finch Property Management
Office: (720) 555-0140"""),
        Mail(JULES, CALLIE, "09-02 11:05", """Hi Callie,

Happy to. Wednesday the 9th at 10:00 at your office? If Manny can join for part of it, even better. I'd like to
hear which requests he actually gets woken up for.

Jules"""),
        Mail(CALLIE, JULES, "09-02 11:31", """Wednesday at 10 works. Manny will be there, and Gideon for the
first half hour. He signs off on anything we spend.

Callie""", cc=GIDEON),
    ]))

    s.add(note("b3-discovery", "Clients/Alder & Finch/2026-09-09 Alder & Finch discovery.md", """---
date: 2026-09-09
client: Alder & Finch Property Management
tags: [meeting, alder-finch]
---
# Alder & Finch discovery

Callie Brennan (ops manager, day-to-day contact), Gideon Pratt (owner, approves budget), Manny Esquivel
(maintenance coordinator, on-call boss).

## Today
- ~25-40 requests a week. Text, email, phone. NightDesk answering service after hours.
- Tenant roster lives in **Buildium**. Callie can schedule a nightly tenant export (CSV) to an SFTP folder.
  Columns: unit, building, tenant name, mobile, email, lang, lease end.
- `lang` is `en` or `es`. About 30% of households prefer Spanish.

## Business hours
- Mon-Fri **8:00-17:00**, Denver time. Office closed on federal holidays.
- Outside business hours = after hours.

## After-hours emergencies (page the on-call tech)
- Active water leak or flooding
- No heat, **October 1 to April 30 only** (in summer it waits for morning)
- Smell of gas
- Sewage backup
- No power to the whole unit
- Exterior door or window that won't lock

Gas: the reply tells the tenant to leave the unit and call 911 first. We page the on-call tech after that, we don't
send them in first.

Lockouts are **not** emergencies. After hours the tenant gets Lockhart Lock & Key's number and pays the
**$95** after-hours lockout fee directly.

Everything else after hours: auto-reply, handle next business day.

## On-call escalation
- Page the on-call tech (phone call, not just a text). If no answer in **15 minutes**, call Manny.
- Manny: "15 is already long when water's coming through a ceiling." Gideon was fine with 15 for now.

## Vendors
- Vendors can do up to **$500** of work without asking. Over that, Manny approves first.
- Vendor list: Callie will send it (in the client folder).

## Timeline
- Gideon wants it live in all 7 buildings on **October 1**.

## Next
- [ ] proposal
- [ ] get a Twilio number for maintenance texts
"""))

    s.add(note("b3-vendors", "Clients/Alder & Finch/Alder & Finch vendor list.md", """# Alder & Finch vendor list

From Callie, Sep 10. One vendor per trade, all buildings.

| Trade | Vendor | Business hours | After hours |
|---|---|---|---|
| Plumbing | Sixpenny Plumbing | (303) 555-0121 | (303) 555-0122 |
| Electrical | Brightwire Electric | (720) 555-0131 | (720) 555-0132 |
| Heating / AC | Cold Creek Heating & Air | (303) 555-0145 | (303) 555-0146 |
| Locksmith | Lockhart Lock & Key | (720) 555-0158 | (720) 555-0158 |
| Appliances | Manny's techs in house | | |

Regular (non-emergency) jobs: the vendor should accept within 4 business hours or Manny reassigns it.

Appliance jobs never go out to a vendor unless Manny says so.
""", "09-10 14:05"))

    s.add(email("b3-proposal", "Alder & Finch maintenance dispatch: proposal", [
        Mail(JULES, CALLIE, "09-11 10:40", """Hi Callie, Gideon,

Proposal attached. In short:

- One Twilio number for maintenance texts and one email address for repair requests. Both turn into work
  orders automatically, matched to the tenant's unit from the nightly Buildium export.
- Work orders are routed to the vendor for that trade, using your vendor list.
- The after-hours rules from our meeting: emergencies page the on-call tech and escalate to Manny,
  everything else waits for the next business day with an auto-reply in the tenant's language.
- A daily work order sheet for Manny, so the Google Sheet habit keeps working.

Price: $7,400 fixed, half up front. Then $150 a month for hosting, Twilio and email.

Target: live in all 7 buildings on October 1, as Gideon asked.

Jules Marlow
Marlow Works""", cc=GIDEON, attachments=("Marlow Works - Alder & Finch proposal.pdf",)),
        Mail(GIDEON, JULES, "09-14 08:15", """Jules,

Approved. That's less than a year and a half of NightDesk, and we can cancel them once this is running. Callie
will send the deposit.

Gideon Pratt
Alder & Finch Property Management""", cc=CALLIE),
        Mail(JULES, GIDEON, "09-14 09:02", """Thanks Gideon. Deposit invoice for $3,700 is attached. I'll start on
intake this week.

Jules""", cc=CALLIE, attachments=("Marlow Works invoice - Alder & Finch deposit.pdf",)),
    ]))

    s.add(codex("b3-codex-intake", "09-15 13:10", 95, REPO, """
U: alderfinch-dispatch: build intake. twilio sms webhook + postmark inbound webhook both create a work order. match the sender to a unit using the nightly buildium roster csv (phone for texts, email for emails). mms photos go on the work order. reply to the tenant with the work order number
T exec_command: ls -R src tests | head -40
R: src:
dispatch

src/dispatch:
__init__.py
app.py
db.py
models.py
roster.py

tests:
conftest.py
test_roster.py
T exec_command: sed -n 1,60p src/dispatch/roster.py
R: COLUMNS = ("unit", "building", "tenant_name", "mobile", "email", "lang", "lease_end")

def load(path: Path) -> list[Tenant]:
    with path.open(newline="") as fh:
        return [Tenant.from_row(r) for r in csv.DictReader(fh)]

def by_phone(tenants): ...
T exec_command: sed -n 1,40p src/dispatch/models.py
R: class WorkOrder(Base):
    __tablename__ = "work_orders"
    id = mapped_column(Integer, primary_key=True)
    number = mapped_column(String, unique=True)
    unit = mapped_column(String, nullable=True)
    channel = mapped_column(String)  # sms | email
    body = mapped_column(Text)
    status = mapped_column(String, default="new")
A: Plan:

- `POST /hooks/twilio/sms` validates the `X-Twilio-Signature`, normalizes `From` to E.164 and looks it up in the roster by mobile.
- `POST /hooks/postmark/inbound` takes Postmark's inbound JSON, uses `FromFull.Email` (lowercased) to look up the roster by email, and strips the quoted reply text so the body is just the new message.
- Both call one `open_work_order(channel, unit, body, media)`. Numbers look like `AF-26-0001`: a yearly counter in Postgres so two requests at once can't get the same number.
- MMS media URLs from Twilio are downloaded right away (they expire behind auth) and stored in the `af-dispatch-photos` S3 bucket under `work_orders/<number>/`.
- The reply goes back on the same channel: "Got it. Your work order is AF-26-0001."

The maintenance number is +17205550177 (bought on the Marlow Works Twilio account, subaccount "alder-finch"). The repair address is repairs@alderfinchpm.com, which Callie forwards to the Postmark inbound address.
T apply_patch: *** Begin Patch
*** Add File: src/dispatch/intake.py
+def open_work_order(session, channel, tenant, body, media=()):
+    number = next_number(session, year=2026)
+    order = WorkOrder(number=number, unit=tenant.unit if tenant else None, channel=channel, body=body)
+    session.add(order)
+    for url in media:
+        store_photo(number, url)
+    return order
*** End Patch
R: Success. Updated the following files:
A src/dispatch/intake.py
T apply_patch: *** Begin Patch
*** Update File: src/dispatch/app.py
+@app.post("/hooks/twilio/sms")
+async def twilio_sms(request: Request): ...
+
+@app.post("/hooks/postmark/inbound")
+async def postmark_inbound(payload: PostmarkInbound): ...
*** End Patch
R: Success. Updated the following files:
M src/dispatch/app.py
T exec_command: uv run pytest -q
R: .............F..
FAILED tests/test_intake.py::test_text_from_unknown_number - AssertionError: assert None == '2B'
1 failed, 15 passed in 1.42s
A: The failing test is my own: it expected an unknown number to match a unit. For texts from a number that isn't on the roster, the reply asks for the building and unit number and the work order stays open with no unit until Manny or the tenant fills it in. Fixed the test to say that.
T exec_command: uv run pytest -q
R: ................
16 passed in 1.38s
A: Intake is in. Texts to +17205550177 and emails to repairs@alderfinchpm.com both open a work order, matched by phone or email against the nightly Buildium roster. Photos are copied to `af-dispatch-photos`. The tenant gets the work order number back. Emails from unknown senders still need a decision; I left them opening a unit-less work order like texts.
""", branch="intake"))

    s.add(thread("b3-gh-4", REPO, 4, "issue", "Emails from senders not on the roster", GH_ME, "09-17 09:30", """
Postmark inbound gets mail from addresses that aren't in the Buildium export: a tenant writing from a work
account, a parent writing for a student tenant, and plenty of spam that slips past Postmark's filter. Today each
one opens a unit-less work order and auto-replies, which would mean replying to spam.

Proposal: an email whose sender isn't on the roster doesn't open a work order and gets **no auto-reply**. It goes
to an "Unmatched" queue on Manny's dashboard. Manny either links it to a unit (that opens the work order and
sends the normal reply) or dismisses it.

Texts from unknown numbers keep the current behavior (ask for building and unit), since spam texts are rare.
""", [
        Post(GH_ME, "09-17 15:20", "Talked to Callie, she's fine with it. Manny checks the queue each morning and after lunch. Implemented in `intake.py`: `unmatched_emails` table, `/dashboard/unmatched` view with Link and Dismiss."),
        Post(GH_ME, "09-17 15:24", "Closing."),
    ], state="closed", closed="09-17 15:24", labels=("intake",)))

    s.add(texts("b3-manny-leak", MANNY_NAME, MANNY_PHONE, "09-19", """
07:12 Manny Esquivel: Morning. Want a real example for your system? Larkspur Court 3B called NightDesk at 1:40am, water coming out from under the dishwasher
07:13 Manny Esquivel: NightDesk called Dario who was on call. His phone was on do not disturb. They left a voicemail and that was it. No second call, nobody called me
07:14 Manny Esquivel: Tenant finally called my cell at 2:35. By the time I got there it was through the ceiling of 2B
07:15 Jules: Oh no. How bad is 2B?
07:17 Manny Esquivel: Drywall and the light fixture. Sixpenny is coming at 9 to look at the supply line. Callie's guessing $2,300 for the drywall alone
07:18 Jules: That's exactly the escalation step NightDesk doesn't do. In the new setup an unanswered page goes to you automatically
07:19 Manny Esquivel: Good. Callie wants to talk to you about the 15 minutes too. I'll let her email you
07:20 Jules: Sounds good. Go get some coffee
"""))

    s.add(email("b3-escalation", "After-hours escalation after Saturday's leak", [
        Mail(CALLIE, JULES, "09-21 08:40", """Hi Jules,

Manny told me he texted you about Saturday at Larkspur Court. Gideon and I talked it over this weekend and we want
to tighten the escalation before anything goes live:

1. If the on-call tech doesn't answer the page within 10 minutes (not 15), call Manny.
2. If Manny doesn't answer within another 10 minutes, call Gideon.
3. Keep calling until someone picks up. A voicemail doesn't count as answered.

Gideon says he'd rather be woken up than pay for another ceiling.

Callie Brennan
Operations Manager, Alder & Finch Property Management
Office: (720) 555-0140""", cc=GIDEON),
        Mail(JULES, CALLIE, "09-21 09:15", """Hi Callie,

Makes sense. I'll build it that way: 10 minutes to the tech, then Manny, then Gideon after another 10. A page
counts as answered only when the person presses 1 on the call, so voicemail can't count.

Jules""", cc=GIDEON),
    ]))

    s.add(claude("b3-claude-rules", "09-22 09:05", 110, REPO, "After-hours rules and on-call escalation", """
U: alderfinch-dispatch: implement the after-hours rules. business hours mon-fri 8-17 denver, closed federal holidays. emergencies page on-call: active leak/flooding, no heat (oct 1 - apr 30 only), gas smell, sewage backup, no power to whole unit, exterior door/window won't lock. gas: reply tells tenant to leave and call 911, then page. lockouts are not emergencies, reply with lockhart's number + $95 fee. everything else after hours: auto reply, open at 8:00 next business day. escalation per callie's email: 10 min to the tech, then manny, then gideon after another 10, a page only counts if they press 1
T Read: /Users/jules/code/alderfinch-dispatch/src/dispatch/classify.py
R: CATEGORIES = ("leak", "no_heat", "gas", "sewage", "power", "lock", "lockout", "appliance", "other")

def classify(body: str) -> str:
    \"\"\"Keyword rules first; falls back to the LLM for anything they miss.\"\"\"
T Read: /Users/jules/code/alderfinch-dispatch/src/dispatch/routing.py
R: TRADE = {"leak": "plumbing", "sewage": "plumbing", "no_heat": "hvac", "power": "electrical", "lock": "locksmith", "lockout": "locksmith", "gas": "plumbing"}
VENDOR_ACCEPT_WINDOW = timedelta(hours=4)  # business hours, then Manny reassigns
A: I'll put the rules in one table instead of spreading `if`s through intake:

- `rules.py` has `BUSINESS_HOURS` (Mon-Fri 08:00-17:00 America/Denver) and the federal holiday list for 2026-2027.
- `EMERGENCY` is a set of categories, with `no_heat` gated on the date: only October 1 through April 30.
- `after_hours_action(category, when)` returns one of `PAGE`, `GAS_THEN_PAGE`, `LOCKOUT_REPLY` or `NEXT_BUSINESS_DAY`.
- Escalation is a small state machine in `oncall.py`: `paging_tech -> paging_manny -> paging_gideon -> answered`. Each step places a Twilio voice call that says the unit and the problem and asks them to press 1. `ESCALATE_AFTER = timedelta(minutes=10)`. A worker checks open pages every minute. If nobody presses 1 after Gideon's step, it starts again at the tech.

The vendor accept window (4 business hours for regular jobs) is a separate timer and I'm not touching it.
T Edit: /Users/jules/code/alderfinch-dispatch/src/dispatch/rules.py
R: The file has been updated successfully.
T Edit: /Users/jules/code/alderfinch-dispatch/src/dispatch/oncall.py
R: The file has been updated successfully.
T Bash: uv run pytest -q tests/test_rules.py tests/test_oncall.py
R: ........F...........
FAILED tests/test_rules.py::test_no_heat_in_september_waits - AssertionError: assert 'PAGE' == 'NEXT_BUSINESS_DAY'
1 failed, 19 passed in 0.92s
A: The season check compared month numbers with `4 <= month <= 10`, which is backwards. Heat season wraps the new year, so it's `month >= 10 or month <= 4`.
T Edit: /Users/jules/code/alderfinch-dispatch/src/dispatch/rules.py
R: The file has been updated successfully.
T Bash: uv run pytest -q
R: ......................................
38 passed in 2.11s
A: Done. What a tenant sees after hours:

- **Emergency** (leak, no heat in season, sewage, no power, won't lock): "We've paged our on-call technician. Someone will call you back shortly." The on-call tech gets a call. No press-1 in 10 minutes, Manny gets called, then Gideon 10 minutes after that.
- **Gas smell**: "Please leave your unit now and call 911. Then we'll send our on-call technician." The page starts right after the reply.
- **Lockout**: Lockhart Lock & Key's after-hours number and the $95 fee, paid to Lockhart.
- **Anything else**: "Our office is closed. Your work order AF-26-xxxx will be scheduled when we open at 8:00 on the next business day." The work order is queued and goes to the vendor at 8:00.

Replies use the tenant's `lang` from the roster, English or Spanish.
U: commit it
T Bash: git add -A && git commit -q -m "rules: after-hours emergencies and 10-minute on-call escalation" && git log --oneline -1
R: 7c41d09 rules: after-hours emergencies and 10-minute on-call escalation
""", branch="after-hours"))

    s.add(commit("b3-commit-escalation", REPO, "09-22 11:00", """rules: after-hours emergencies and 10-minute on-call escalation

Emergencies page the on-call tech by phone. No press-1 within 10 minutes
calls Manny, then Gideon after another 10, then starts over. Gas replies
tell the tenant to leave and call 911 before the page. No heat is an
emergency only October 1 through April 30.""",
                 ["A\tsrc/dispatch/rules.py", "A\tsrc/dispatch/oncall.py", "M\tsrc/dispatch/intake.py",
                  "A\ttests/test_rules.py", "A\ttests/test_oncall.py"]))

    s.add(hermes("b3-hermes-plumber", "09-23 12:10", 6, "Alder & Finch plumbing vendor change", """
U: note for alder & finch. callie just called, sixpenny plumbing told them they're done with after-hours calls. so they signed redtail plumbing for all plumbing, day and night, starting monday sep 28. redtail after hours line is 720 555 0193, office 720 555 0192. sixpenny finishes the jobs they already have open
A: Saved. Alder & Finch plumbing change:
- Sixpenny Plumbing no longer takes after-hours calls.
- Redtail Plumbing takes all plumbing (day and after hours) starting Monday, September 28.
- Redtail office: (720) 555-0192. After hours: (720) 555-0193.
- Sixpenny finishes the jobs it already has open.
Should I remind you to update the vendor table in the dispatch app?
U: yes tomorrow morning
A: I'll remind you tomorrow at 9:00 to switch plumbing to Redtail in alderfinch-dispatch.
"""))

    s.add(commit("b3-commit-redtail", REPO, "09-24 09:40", """vendors: route plumbing to Redtail Plumbing from Sep 28

Sixpenny Plumbing dropped after-hours service. New plumbing work orders
opened on or after 2026-09-28 go to Redtail; open Sixpenny jobs stay
with Sixpenny.""",
                 ["M\tsrc/dispatch/vendors.py", "M\tseed/vendors.csv", "M\ttests/test_routing.py"]))

    s.add(email("b3-golive", "Go-live plan", [
        Mail(CALLIE, JULES, "09-25 10:30", """Hi Jules,

Gideon changed his mind about going live everywhere on October 1. He'd rather start small:

- Pilot at Larkspur Court and Ashby House starting Monday October 5.
- The other five buildings on Monday October 19, if the pilot goes well.

We'll keep NightDesk until the 19th and cancel after that.

Also, Lucía Ferraz on our leasing team went through all the Spanish replies and fixed a few. Her edits are in the
doc I shared. She says "orden de trabajo" reads better than "pedido" for work order.

Callie""", cc=GIDEON),
        Mail(JULES, CALLIE, "09-25 11:12", """Hi Callie,

October 5 for the two pilot buildings works, and the 19th for the rest. I'll put Lucía's wording in today and send
you both a test text in Spanish.

Jules"""),
    ]))

    s.add(note("b3-test-run", "Clients/Alder & Finch/Alder & Finch staff test.md", """# Alder & Finch staff test

Sep 29, 9:00-11:30. Callie, Manny and two techs played tenants from their own phones, added to the roster as
test units T1-T4. Clock forced to "after hours" for the emergency part.

- Text intake: 9 of 9 opened work orders, AF-26-0001 to AF-26-0009. Photos showed up on the work order.
- Email intake: 4 of 4 matched. One sent from Manny's Gmail (not on the roster) went to Unmatched as it should.
- Leak, after hours: Dario let the page ring out on purpose. Manny got called at 10 minutes, Gideon at 20. Gideon
  pressed 1.
- Lockout: got Lockhart's number and the $95 fee.
- Spanish replies: Lucía's wording, read fine to Callie.
- Plumbing went to Redtail.

Open: the dashboard's Unmatched count doesn't refresh without a reload. Fix before Oct 5.
""", "09-29 13:40"))

    s.ask(
        Q("b3-01", "update", "How long does the on-call tech get to answer an after-hours page before it escalates at Alder & Finch?",
          "10 minutes (it was 15 at first). Then Manny is called, and if Manny doesn't answer within another 10 "
          "minutes, Gideon.",
          ("b3-discovery", "b3-escalation", "b3-claude-rules", "b3-commit-escalation", "b3-test-run"),
          evidence_has=("15 minutes", "10 minutes")),
        Q("b3-02", "update", "Who does Alder & Finch's plumbing now?",
          "Redtail Plumbing, for all plumbing day and after hours, starting September 28. Sixpenny Plumbing dropped "
          "after-hours calls and only finishes its open jobs.",
          ("b3-vendors", "b3-hermes-plumber", "b3-commit-redtail"), evidence_has=("Sixpenny", "Redtail")),
        Q("b3-03", "update", "When does the Alder & Finch dispatch system go live?",
          "A pilot at Larkspur Court and Ashby House starting Monday October 5, then the other five buildings "
          "on Monday October 19 if the pilot goes well. The original plan was all 7 buildings on October 1.",
          ("b3-discovery", "b3-proposal", "b3-golive"), evidence_has=("October 5", "October 19")),
        Q("b3-04", "multi", "What happened with the Larkspur Court leak, and what did it change in the escalation?",
          "On September 19 at 1:40am unit 3B reported a dishwasher leak to NightDesk, which called the on-call tech "
          "Dario, got voicemail and didn't call anyone else; the tenant reached Manny at 2:35 and the water had gone "
          "through the ceiling of 2B. Afterward Callie cut the page escalation from 15 to 10 minutes, then Manny, "
          "then Gideon after another 10, and it keeps calling until someone presses 1 to answer; voicemail doesn't "
          "count as answered.",
          ("b3-manny-leak", "b3-escalation"), evidence_has=("1:40am", "2B", "10 minutes")),
        Q("b3-05", "multi", "How does a repair email become a work order, and what if we don't know who sent it?",
          "Callie forwards repairs@alderfinchpm.com to the Postmark inbound webhook, which matches the sender's email "
          "against the nightly Buildium roster and opens a work order with an auto-reply. A sender not on the roster "
          "gets no work order and no reply; the email goes to the Unmatched queue on Manny's dashboard, where he links "
          "it to a unit or dismisses it.",
          ("b3-codex-intake", "b3-gh-4"), evidence_has=("Postmark", "Unmatched", "no auto-reply")),
        Q("b3-06", "single", "How many rental units does Alder & Finch manage?",
          "182 units across 7 buildings in Denver and Lakewood.", ("b3-intro",), evidence_has=("182 rental units",)),
        Q("b3-07", "single", "Who at Alder & Finch has to sign off on spending?",
          "Gideon Pratt, the owner.", ("b3-intro", "b3-discovery"), evidence_has=("Gideon",)),
        Q("b3-08", "single", "What are we charging Alder & Finch?",
          "$7,400 fixed (half up front, a $3,700 deposit), then $150 a month for hosting, Twilio and email.",
          ("b3-proposal",), evidence_has=("$7,400", "$150 a month")),
        Q("b3-09", "single", "What counts as business hours for Alder & Finch maintenance?",
          "Monday to Friday, 8:00 to 17:00 Denver time, closed on federal holidays.",
          ("b3-discovery", "b3-claude-rules"), evidence_has=("8:00-17:00", "federal holidays")),
        Q("b3-10", "single", "Which after-hours requests should wake up the on-call tech at Alder & Finch?",
          "Active water leak or flooding, no heat (October 1 to April 30 only), smell of gas, sewage backup, no power "
          "to the whole unit, and an exterior door or window that won't lock.",
          ("b3-discovery", "b3-claude-rules"), evidence_has=("Sewage backup", "No power to the whole unit")),
        Q("b3-11", "single", "What does the system tell a tenant who reports a gas smell?",
          "To leave the unit and call 911 first; the on-call tech is paged after that.",
          ("b3-discovery", "b3-claude-rules", "b3-commit-escalation"), evidence_has=("call 911",)),
        Q("b3-12", "single", "What happens when an Alder & Finch tenant gets locked out at night?",
          "It's not an emergency. The tenant gets Lockhart Lock & Key's number and pays Lockhart the $95 after-hours "
          "lockout fee directly.", ("b3-discovery", "b3-claude-rules", "b3-test-run"), evidence_has=("$95",)),
        Q("b3-13", "single", "What happens to a non-urgent request that comes in after the office closes?",
          "The tenant gets an auto-reply that the office is closed, and the work order is queued and sent to the "
          "vendor at 8:00 on the next business day. A lockout is the exception: the tenant gets Lockhart Lock & "
          "Key's after-hours number and the $95 fee instead.", ("b3-claude-rules",),
          evidence_has=("next business day", "8:00")),
        Q("b3-14", "single", "What's the number tenants text for maintenance?",
          "+1 720-555-0177, a Twilio number on the Marlow Works account (subaccount alder-finch).",
          ("b3-codex-intake",), evidence_has=("+17205550177",)),
        Q("b3-15", "single", "How much can a vendor spend on an Alder & Finch job before asking?",
          "Up to $500; anything over that needs Manny's approval first.", ("b3-discovery",),
          evidence_has=("$500",)),
        Q("b3-16", "single", "Who looked over the Spanish replies for Alder & Finch?",
          "Lucía Ferraz on their leasing team; she preferred \"orden de trabajo\" over \"pedido\" for work order.",
          ("b3-golive", "b3-test-run"), evidence_has=("Lucía Ferraz",)),
        Q("b3-17", "single", "How does the dispatch app figure out which unit a text came from?",
          "It looks up the sender's phone number in the nightly tenant export from Buildium. An unknown number gets a "
          "reply asking for the building and unit.", ("b3-codex-intake", "b3-discovery"),
          evidence_has=("Buildium", "mobile")),
        Q("b3-18", "single", "What do Alder & Finch work order numbers look like?",
          "AF-26-0001: AF, the two-digit year, and a yearly counter kept in Postgres.",
          ("b3-codex-intake", "b3-test-run"), evidence_has=("AF-26-0001",)),
        Q("b3-19", "single", "Which company gets no-heat calls for Alder & Finch?",
          "Cold Creek Heating & Air.", ("b3-vendors",), evidence_has=("Cold Creek Heating & Air",)),
        Q("b3-20", "single", "Is no heat an emergency in September?",
          "No. No heat only counts as an after-hours emergency from October 1 to April 30; in September it waits for "
          "the next business day.", ("b3-discovery", "b3-claude-rules", "b3-commit-escalation"),
          evidence_has=("October 1 to April 30",)),
        Q("b3-21", "single", "Where do the photos tenants text in end up?",
          "Downloaded right away and stored on the work order in the af-dispatch-photos S3 bucket, under "
          "work_orders/<number>/.", ("b3-codex-intake",), evidence_has=("af-dispatch-photos",)),
        Q("b3-22", "single", "What does Alder & Finch pay for their answering service?",
          "$410 a month to NightDesk.", ("b3-intro",), evidence_has=("$410 a month",)),
        Q("b3-23", "single", "How long does a vendor have to accept a regular Alder & Finch job?",
          "4 business hours; after that Manny reassigns it.", ("b3-vendors", "b3-claude-rules"),
          evidence_has=("4 business hours",)),
    )
    return s
