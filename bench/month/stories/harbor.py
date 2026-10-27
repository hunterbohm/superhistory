from cast import DR_KIM, GH_ME, HARBOR_REPO as REPO, JULES, MARISOL
from model import Mail, Post, Q, Story, claude, codex, commit, email, hermes, note, thread


def build():
    s = Story()
    s.add(email("har-intro", "Appointment reminders for our practice", [
        Mail(MARISOL, JULES, "09-02 12:45", """Hello Jules,

My cousin Benji Okoro works at Fernbrook Bakery and told me you're building their ordering system. I'm the office
manager at Harbor Lane Dental. We have about 70 appointments a day across 4 chairs, and our no-show rate is around
11%. Right now the front desk calls every patient the day before, which takes about 2 hours a day.

We'd like automatic reminder texts, and a follow-up when someone misses an appointment. Is that something you do?

Marisol Ortega
Office Manager, Harbor Lane Dental"""),
        Mail(JULES, MARISOL, "09-02 15:10", """Hi Marisol,

Yes, that's a good fit. Could we do a call next week with you and whoever approves the budget? I'd like to see
how your practice software stores appointments.

Jules"""),
        Mail(MARISOL, JULES, "09-03 08:05", """Tuesday the 8th at 12:30 works. Dr. Kim will join, he owns the
practice and signs off on spending.

Marisol""", cc=DR_KIM),
    ]))

    s.add(note("har-discovery", "Clients/Harbor Lane/2026-09-08 Harbor Lane discovery call.md", """---
date: 2026-09-08
client: Harbor Lane Dental
tags: [meeting, harbor-lane]
---
# Harbor Lane discovery call

Marisol Ortega (office manager), Dr. Evan Kim (owner, approves budget).

## Their setup
- Practice software: **ChairSide**. No API. It can write a nightly CSV export at **11pm** to a shared folder:
  `\\\\HL-SERVER\\exports\\appts_YYYYMMDD.csv`
- Columns: patient id, first name, last name, mobile, appt date, appt time, provider, location, TXT_OK
- `TXT_OK` is the patient's opt-in to texts. Only text patients with TXT_OK = Y.

## Rules
- HIPAA: the texting vendor has to sign a BAA before any real patient data goes through it.
- Texts carry **only** the first name, the appointment date and time, and the location. No procedure, no provider
  notes, nothing clinical.
- Reminders + a no-show follow-up with a link to rebook.

## Timeline
- Go-live target: **Thursday October 15**.

## Next
- [ ] proposal (SMS + email reminders)
- [ ] check which texting vendors sign BAAs. TextLoom does.
"""))

    s.add(email("har-proposal", "Harbor Lane reminders: proposal", [
        Mail(JULES, MARISOL, "09-10 11:20", """Hi Marisol, Dr. Kim,

Attached is the proposal for appointment reminders:

- Reminder texts and emails to patients who opted in, from ChairSide's nightly export.
- A follow-up text when a patient misses an appointment, with a link to rebook.
- Texts go through TextLoom, which signs a BAA with us.

Price: $5,600 fixed, 50% to start. Then $120 a month for hosting and texting.

Jules Marlow""", cc=DR_KIM, attachments=("Marlow Works - Harbor Lane proposal.pdf",)),
        Mail(DR_KIM, JULES, "09-14 19:02", """Jules,

Approved. One change: drop the email reminders and do texts only. Our patients don't read email from us, and it's
one less thing to maintain.

Evan Kim, DDS""", cc=MARISOL),
        Mail(JULES, DR_KIM, "09-15 08:30", """Thanks Dr. Kim. Texts only it is. Price stays the same. The deposit
invoice (MW-1040, $2,800) is attached.

Jules""", cc=MARISOL, attachments=("MW-1040.pdf",)),
    ]))

    s.add(email("har-baa", "Your TextLoom Business Associate Agreement is countersigned", [
        Mail("TextLoom Compliance <compliance@textloom.io>", JULES, "09-16 10:44", """Hi Jules,

Your Business Associate Agreement for the account "Marlow Works / Harbor Lane Dental" was countersigned on
September 16, 2026. A copy is attached and in your dashboard under Settings > Compliance.

HIPAA-eligible messaging is now enabled for this account.

TextLoom Compliance""", attachments=("TextLoom BAA - Marlow Works - countersigned.pdf",)),
    ], labels=("INBOX",)))

    s.add(hermes("har-hermes-call", "09-18 12:40", 7, "Harbor Lane changes from Marisol", """
U: write this down, Marisol from Harbor Lane just called. three changes: 1) reminders go 48 hours before the appointment, not 24. 2) never send anything on a Sunday. if the 48 hour mark falls on a sunday, send it saturday at 10am instead. 3) the no-show text goes out 2 hours after a missed appointment, with the rebooking link
A: Got it. Harbor Lane changes from Marisol's call today:
1. Reminder texts go out 48 hours before the appointment, not 24.
2. Nothing is ever sent on a Sunday. If the 48-hour mark lands on a Sunday, the reminder goes out Saturday at 10:00 instead.
3. The no-show follow-up goes out 2 hours after a missed appointment and includes the rebooking link.
Want me to put these anywhere else?
U: no thats fine, I'll remember when I build the scheduler
A: OK. They're saved in this chat.
"""))

    s.add(codex("har-codex-scheduler", "09-21 09:15", 80, REPO, """
U: harbor-reminders: build the scheduler. read the nightly chairside csv, and for every appointment with TXT_OK=Y schedule a reminder text 24h before the appointment. store scheduled sends in sqlite so a rerun doesn't double-send
T exec_command: ls src/harbor tests
R: src/harbor:
__init__.py  importer.py  send.py
tests:
test_importer.py
T exec_command: sed -n 1,50p src/harbor/importer.py
R: def read_export(path: Path) -> list[Appointment]:
    with path.open(newline="") as fh:
        return [Appointment.from_row(r) for r in csv.DictReader(fh)]
A: I'll add `schedule.py`:

- `plan(appointments, now)` returns one send per appointment with `TXT_OK == "Y"`, at the appointment time minus 24 hours, skipping any send time already in the past.
- Sends go into a `sends` table keyed on `(patient_id, appt_at, kind)`, so importing the same export twice changes nothing.
- `send.py` gets a `due(now)` loop that sends rows whose time has come and marks them sent.
T apply_patch: *** Begin Patch
*** Add File: src/harbor/schedule.py
+REMINDER_LEAD = timedelta(hours=24)
+
+def plan(appointments, now):
+    for appt in appointments:
+        if appt.txt_ok and appt.at - REMINDER_LEAD > now:
+            yield Send(appt.patient_id, appt.at, "reminder", appt.at - REMINDER_LEAD)
*** End Patch
R: Success. Updated the following files:
A src/harbor/schedule.py
T exec_command: uv run pytest -q
R: ...........
11 passed in 0.74s
A: Scheduler is in. Reminders are planned 24 hours before each opted-in appointment, stored in `sends` with a unique key, and a second import of the same CSV adds nothing.
""", branch="scheduler"))

    s.add(commit("har-commit-24h", REPO, "09-21 10:40", """schedule: plan reminder texts from the nightly export

Reminders 24h before each appointment with TXT_OK=Y. Sends are keyed on
(patient_id, appt_at, kind) so re-importing an export is a no-op.""",
                 ["A\tsrc/harbor/schedule.py", "M\tsrc/harbor/send.py", "A\ttests/test_schedule.py"]))

    s.add(thread("har-gh-3", REPO, 3, "issue", "Nightly export sometimes imports empty", GH_ME, "09-22 08:10", """
Two of the last five nights imported 0 appointments. ChairSide starts writing `appts_YYYYMMDD.csv` at 23:00 but
the file isn't complete until about 23:20, and our importer runs at 23:05, so it reads a partial or empty file.

Fix: run the import at 00:30, and refuse a file whose last line isn't a complete row.
""", [Post(GH_ME, "09-22 11:45", "Moved the import to 00:30 and added the last-row check. Closing.")],
        state="closed", closed="09-22 11:46", labels=("bug",)))

    s.add(email("har-test-feedback", "Test reminders", [
        Mail(MARISOL, JULES, "09-23 13:30", """Hi Jules,

The staff test patients got their reminders, thank you! Two things though:

1. They came only one day ahead. We agreed on 48 hours when we talked on Friday the 18th, right?
2. Ana got hers on Sunday. We said never on Sundays.

Also Ana's last name showed up as "Pe�a" instead of "Peña".

Marisol"""),
        Mail(JULES, MARISOL, "09-23 14:05", """You're right on all three, sorry. I'll fix them tomorrow and
re-run the test.

Jules"""),
    ]))

    s.add(claude("har-claude-fix", "09-24 09:30", 75, REPO, "Reminders 48h ahead, skip Sundays, fix encoding", """
U: harbor-reminders has three bugs from marisol's test. 1) reminders should go 48h before, not 24h. she told me that on the phone on the 18th and I built 24h anyway. 2) never send on sunday. if 48h before lands on a sunday, send saturday 10:00 instead. 3) "Peña" comes out as "Pe�a"
T Read: /Users/jules/code/harbor-reminders/src/harbor/schedule.py
R: REMINDER_LEAD = timedelta(hours=24)

def plan(appointments, now):
    for appt in appointments:
A: Fixes:

1. `REMINDER_LEAD` becomes 48 hours.
2. After computing the send time in America/Denver, if it falls on a Sunday, move it to Saturday at 10:00. Saturday 10:00 is always earlier than the Sunday time, so the reminder still arrives before the appointment.
3. The encoding: ChairSide writes the export in Windows-1252 (cp1252), not UTF-8. The cp1252 byte for "ñ" isn't valid UTF-8, and the importer opens the file with `errors="replace"`, so it became "�". I'll open the file with `encoding="cp1252"`.

Already-scheduled sends from the old rules need replanning, so I'll delete unsent reminder rows and plan them again.
T Edit: /Users/jules/code/harbor-reminders/src/harbor/schedule.py
R: The file has been updated successfully.
T Edit: /Users/jules/code/harbor-reminders/src/harbor/importer.py
R: The file has been updated successfully.
T Bash: uv run pytest -q
R: ................
16 passed in 0.81s
T Bash: uv run python -m harbor.cli replan --dry-run
R: 25 unsent reminders replanned: 21 moved 24h earlier, 4 moved from Sunday to Saturday 10:00
A: Done. `tests/test_schedule.py` covers both sides of the Sunday rule: a Monday 9:00 appointment has its 48-hour mark on Saturday 9:00 and keeps it, and a Tuesday 14:00 appointment has its mark on Sunday 14:00, which moves to Saturday 10:00.
U: run the replan for real
T Bash: uv run python -m harbor.cli replan
R: 25 unsent reminders replanned
"""))

    s.add(commit("har-commit-48h", REPO, "09-24 10:50", """schedule: remind 48h ahead and never on Sunday

A send time that lands on a Sunday moves to Saturday 10:00. Read the ChairSide
export as cp1252, which garbled accented names.""",
                 ["M\tsrc/harbor/schedule.py", "M\tsrc/harbor/importer.py", "M\ttests/test_schedule.py",
                  "A\ttests/test_importer_encoding.py"]))

    s.add(note("har-test-run", "Clients/Harbor Lane/Harbor Lane test run.md", """# Harbor Lane test run

25 staff members as test patients, imported from the real ChairSide export, sent through TextLoom.

## Round 1 (Sep 22-23)
- Reminders went 24h ahead. Wrong: Marisol asked for 48h on the phone on Sep 18. Fixed Sep 24.
- One reminder went out on a Sunday. Fixed: Sunday sends move to Saturday 10:00.
- Accented names garbled ("Pe�a"). The export is cp1252. Fixed.

## Round 2 (Sep 25)
- All 25 reminders at the right time. No Sunday sends.
- No-show test: marked one appointment missed, follow-up arrived 2 hours later with the rebooking link.
""", "09-25 16:20"))

    s.add(email("har-golive", "Go-live date", [
        Mail(MARISOL, JULES, "09-28 09:12", """Hi Jules,

Round 2 looked perfect. Dr. Kim would like to go live on Tuesday October 13 instead of the 15th. Monday the 12th is
our staff training day, so everyone will know how it works.

Also, the front desk will start asking patients at check-in whether they want texts, so more of them have TXT_OK
set before we start.

Marisol""", cc=DR_KIM),
        Mail(JULES, MARISOL, "09-28 10:00", """Tuesday October 13 works. I'll watch the first morning's sends.

Your check for the deposit arrived last week, thank you.

Jules"""),
    ]))

    s.ask(
        Q("har-01", "update", "How far ahead of the appointment should Harbor Lane's reminder texts go out?",
          "48 hours before the appointment (not 24 hours, as the first build did), except that a send time "
          "landing on a Sunday moves to Saturday at 10:00.",
          ("har-hermes-call", "har-claude-fix", "har-commit-48h"), evidence_has=("48 hours", "Saturday at 10")),
        Q("har-02", "single", "What happens when a Harbor Lane reminder would go out on a Sunday?",
          "Nothing is sent on Sundays; the reminder goes out Saturday at 10:00 instead.",
          ("har-hermes-call", "har-claude-fix", "har-commit-48h"), evidence_has=("Sunday", "Saturday at 10")),
        Q("har-03", "single", "When does Harbor Lane's no-show follow-up text go out, and what does it include?",
          "2 hours after a missed appointment, with the link to rebook.", ("har-hermes-call", "har-test-run"),
          evidence_has=("2 hours after a missed appointment",)),
        Q("har-04", "single", "What's Harbor Lane Dental's no-show rate?",
          "Around 11%.", ("har-intro",), evidence_has=("11%",)),
        Q("har-05", "single", "How does harbor-reminders get the appointments, given ChairSide has no API?",
          "From ChairSide's nightly CSV export to a shared folder, written at 11pm.", ("har-discovery",),
          evidence_has=("No API", "11pm")),
        Q("har-06", "single", "What time does the Harbor Lane import run, and why that time?",
          "00:30. ChairSide starts writing the export at 23:00 and finishes about 23:20, so the old 23:05 run read "
          "partial or empty files.", ("har-gh-3",), evidence_has=("00:30", "23:20")),
        Q("har-07", "single", "Which texting vendor does Harbor Lane use, and when was its BAA signed?",
          "TextLoom; the Business Associate Agreement was countersigned on September 16, 2026.",
          ("har-baa", "har-proposal"), evidence_has=("TextLoom", "September 16, 2026")),
        Q("har-08", "single", "What is allowed in a Harbor Lane reminder text?",
          "Only the patient's first name, the appointment date and time, and the location; nothing clinical.",
          ("har-discovery",), evidence_has=("first name", "Nothing clinical")),
        Q("har-09", "single", "How do we know a Harbor Lane patient agreed to get texts?",
          "The TXT_OK opt-in column in the ChairSide export must be Y.", ("har-discovery", "har-codex-scheduler"),
          evidence_has=("TXT_OK",)),
        Q("har-10", "single", "What's the price of the Harbor Lane reminders project?",
          "$5,600 fixed, then $120 a month for hosting and texting.", ("har-proposal",),
          evidence_has=("$5,600", "$120 a month")),
        Q("har-11", "update", "Does Harbor Lane get email reminders as well as texts?",
          "No. Dr. Kim asked to drop email reminders when he approved the proposal (the evening of September 14, "
          "Denver time); it's texts only, at the same price.", ("har-proposal",),
          evidence_has=("texts only",)),
        Q("har-12", "single", "Who approves spending at Harbor Lane Dental?",
          "Dr. Evan Kim, the owner.", ("har-intro", "har-discovery"), evidence_has=("Evan Kim",)),
        Q("har-13", "update", "When does Harbor Lane go live?",
          "Tuesday October 13, the day after the staff training on Monday October 12; it was planned for October 15.",
          ("har-golive", "har-discovery"), evidence_has=("October 13",)),
        Q("har-14", "single", "How much time does Harbor Lane's front desk spend on reminder calls today?",
          "About 2 hours a day.", ("har-intro",), evidence_has=("2 hours a day",)),
        Q("har-15", "single", "What encoding does the ChairSide export use?",
          "Windows-1252 (cp1252), not UTF-8; reading it as UTF-8 garbled accented names like Peña.",
          ("har-claude-fix", "har-commit-48h", "har-test-run"), evidence_has=("cp1252",)),
        Q("har-16", "multi", "What went wrong in the first Harbor Lane staff test, and is it fixed?",
          "Reminders went 24 hours ahead instead of 48, one went out on a Sunday, and accented names were garbled "
          "by the cp1252 export. All three were fixed on September 24 and round 2 on September 25 was clean.",
          ("har-test-run", "har-test-feedback", "har-claude-fix"), evidence_has=("Round 2", "Sunday", "cp1252")),
        Q("har-17", "multi", "Why did the first harbor-reminders scheduler send reminders 24 hours ahead?",
          "It was built on September 21 to a 24-hour instruction, although Marisol had asked on the September 18 "
          "call (saved in a Hermes chat) for 48 hours.", ("har-codex-scheduler", "har-hermes-call"),
          evidence_has=("24h before", "48 hours")),
        Q("har-18", "single", "How did Marisol at Harbor Lane hear about me?",
          "From her cousin Benji Okoro, who works at Fernbrook Bakery.", ("har-intro",), evidence_has=("Benji Okoro",)),
        Q("har-19", "single", "What's the Harbor Lane deposit invoice, and has it been paid?",
          "MW-1040 for $2,800; paid by check, which arrived the week of September 21.",
          ("har-proposal", "har-golive"), evidence_has=("MW-1040", "$2,800", "check")),
        Q("har-20", "single", "How many appointments a day does Harbor Lane Dental have?",
          "About 70 a day across 4 chairs.", ("har-intro",), evidence_has=("70 appointments",)),
        Q("har-21", "single", "How is Harbor Lane getting more patients to opt in to texts?",
          "The front desk asks patients at check-in whether they want texts.", ("har-golive",),
          evidence_has=("check-in",)),
        Q("har-22", "single", "Where does ChairSide write Harbor Lane's nightly export?",
          "To \\\\HL-SERVER\\exports\\appts_YYYYMMDD.csv on their shared folder.", ("har-discovery",),
          evidence_has=("HL-SERVER",)),
        Q("har-23", "single", "How does harbor-reminders avoid sending the same reminder twice?",
          "Scheduled sends are stored in a SQLite table keyed on (patient_id, appt_at, kind), so importing the same "
          "export again adds nothing.", ("har-codex-scheduler", "har-commit-24h"), evidence_has=("patient_id, appt_at, kind",)),
    )
    return s
