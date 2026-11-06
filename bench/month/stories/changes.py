"""Decisions that change, and decisions that look changed but stand.

Ten stories where a later record replaces an earlier decision, three where a later record argues for a change and the
decision stays, and eighteen questions: ten about the decision as it is now, five about the time before the change, and
three about a decision that stood. `wrong_answer` is what an agent says if it takes the wrong record, which the
benchmark's replaced-decision check looks for.
"""
from cast import (BENJI, DANA, DANA_NAME, DANA_PHONE, GH_ME, GH_THEO, INES, JULES, MARISOL, MARTIN, TRAILHEADS,
                  COPPERLINE_REPO, DOTFILES_REPO, HARBOR_REPO, PINCH_REPO, SITE_REPO)
from model import Mail, Post, Q, Story, claude, commit, email, group, hermes, note, texts, thread

ALPINE = "Alpine Auto Care <service@alpineautocare.example>"
GH_RO = "ro-venn"


def build():
    s = Story()

    # Fernbrook: who gets the evening summary, and when.
    s.add(texts("chg-fern-summary-ask", DANA_NAME, DANA_PHONE, "09-15", """
11:20 Dana Whitfield: one more thing before launch. can the system email me a summary of the day's orders every evening? orders per location, plus anything still sitting in Needs a human
11:24 Jules: Easy. What time should it go out?
11:26 Dana Whitfield: 6pm, to me. I do the close-out call with the managers at 6:30
11:27 Jules: 6pm to you. Will do
"""))
    s.add(email("chg-fern-summary-move", "Evening summary: change from Monday", [
        Mail(DANA, JULES, "09-24 14:10", """Hi Jules,

Small change to the evening order summary. Starting Monday the 28th Benji runs the close-out call instead of me, so
please send the summary to him and not to me. Also make it 5pm instead of 6pm: the Eastgate bakers leave at 5:30 and
Benji wants to see it before they do.

Dana""", cc=BENJI),
        Mail(JULES, DANA, "09-24 15:02", "Done. It goes to Benji at 5pm from Monday the 28th. Jules", cc=BENJI),
    ]))

    # Copperline: how long archived PDFs are kept.
    s.add(commit("chg-cop-archive-90", COPPERLINE_REPO, "09-11 15:40",
                 """feat(archive): move processed PDFs to archive/ and expire them after 90 days

A nightly job deletes anything in archive/ older than 90 days. Raw uploads stay in inbox/ until they are processed.""",
                 ["A\tsrc/copperline/archive.py", "M\tconfig/settings.yaml", "A\ttests/test_archive.py"]))
    s.add(thread("chg-cop-archive-issue", COPPERLINE_REPO, 10, "issue", "Keep archived invoices longer than 90 days",
                 GH_THEO, "09-23 09:05", """
Our auditors sample invoices up to 13 months back, and archive/ only keeps 90 days. Can the retention be longer?
""", [Post(GH_ME, "09-23 17:30", "Yes. `archive_days` is 395 (13 months) now and deployed. Closing.")],
                 state="closed", closed="09-23 17:31", labels=("enhancement",)))

    # Harbor Lane: reminder language.
    s.add(note("chg-harbor-copy", "Clients/Harbor Lane/2026-09-04 Reminder copy rules.md", """---
date: 2026-09-04
client: Harbor Lane Dental
tags: [spec, harbor]
---
# Harbor Lane reminder copy

From the call with Marisol, Sep 4.

- Language: **English only** for v1. Marisol says about 8% of patients would rather get Spanish, but she wants to see
  v1 working first.
- Marisol reads the final wording before launch.
"""))
    s.add(email("chg-harbor-lang", "Spanish reminders?", [
        Mail(MARISOL, JULES, "09-22 13:15", """Hi Jules,

Dr. Kim and I talked it over. ChairSide has a preferred-language field on every patient, and about 8% are set to
Spanish. Can the reminders go out in Spanish for those patients? Everyone else stays on English.

Marisol"""),
        Mail(JULES, MARISOL, "09-23 10:20", """Hi Marisol,

Yes. The nightly export already includes preferred_language, so reminders go out in Spanish when it says es and in
English for everyone else. It goes live with the rest on launch day. I've drafted the Spanish wording; please have
someone at the front desk read it before then.

Jules"""),
    ]))

    # Quill & Ledger: the domain clients forward documents to.
    s.add(note("chg-ql-address-plan", "Clients/Quill & Ledger/2026-09-14 Intake address plan.md", """---
date: 2026-09-14
client: Quill & Ledger
tags: [spec, quill]
---
# Quill & Ledger intake addresses

- Each of their 140 clients gets its own address at **intake.quillledger.co**. Mail sent there is saved into that
  client's Drive folder.
- Ines adds the MX records for intake.quillledger.co; I set up the inbound mail service once they exist.
"""))
    s.add(email("chg-ql-address-move", "Client addresses: use docs.quillledger.co", [
        Mail(MARTIN, JULES, "09-28 09:30", """Jules,

Our IT contractor flagged something. Our spam filter treats everything under the intake subdomain as bulk mail and
would quarantine what clients send. Please use docs.quillledger.co for the client addresses instead. I created the MX
records for docs.quillledger.co this morning, and intake.quillledger.co can be ignored.

Martin""", cc=INES),
        Mail(JULES, MARTIN, "09-28 11:05",
             "Switching to docs.quillledger.co. Nothing was built on the old one yet, so no rework. Jules", cc=INES),
    ]))

    # The studio: office hours.
    s.add(commit("chg-site-hours-tue", SITE_REPO, "09-04 11:30", """content: add a client office hours page

Office hours are Tuesdays 3pm to 5pm Mountain, booked through the calendar link.""",
                 ["A\tsrc/pages/office-hours.md", "M\tsrc/layouts/Base.astro"]))
    s.add(commit("chg-site-hours-thu", SITE_REPO, "09-21 09:15", """content: move office hours to Thursdays 1pm to 3pm

Tuesday afternoons keep getting taken by workshop prep.""", ["M\tsrc/pages/office-hours.md"]))

    # Copperline: invoices per batch.
    s.add(claude("chg-cop-batch-claude", "09-04 10:00", 40, COPPERLINE_REPO, "Batch the nightly invoice run", """
U: copperline-docs: the nightly run takes every invoice in inbox/ in one go. split it into batches so one bad PDF doesn't kill the whole night. pick a sensible batch size
T Read: /Users/jules/code/copperline-docs/src/copperline/run.py
R: def run(inbox): docs = list(inbox.glob("*.pdf")); for d in docs: process(d)
A: I'll process batches of 50 invoices with a try/except around each batch, and write one summary line per batch to the run log. 50 keeps each batch under a minute on the sample set. A failed document is skipped and listed at the end.
T Write: /Users/jules/code/copperline-docs/src/copperline/batches.py
R: File created successfully
T Bash: uv run pytest -q tests/test_batches.py
R: ......
6 passed in 3.1s
A: Done. `BATCH_SIZE = 50` in batches.py. The run logs one line per batch, and a failed document no longer stops the rest of the night.
"""))
    s.add(commit("chg-cop-batch-20", COPPERLINE_REPO, "09-25 10:50", """fix(run): process 20 invoices per batch

Batches of 50 ran the nightly job out of memory on nights with many Bluestem bills of lading, which are large. 20 per
batch stays under 1 GB.""", ["M\tsrc/copperline/batches.py", "M\ttests/test_batches.py"]))

    # The Trailheads: where the October 3 hike goes. It changes twice.
    s.add(group("chg-trail-0917", "Trailheads", TRAILHEADS, "09-17", """
18:40 Nora Vance: next hike: Saturday Oct 3. Chautauqua, the Bluebell-Baird loop. meet at the ranger cottage lot at 7:30
18:42 Sam Ito: in
18:44 Eli Brooks: in, finally no wedding 😅
18:50 Jules: in!
"""))
    s.add(group("chg-trail-0926", "Trailheads", TRAILHEADS, "09-26", """
10:12 Eli Brooks: heads up, Chautauqua has the weekend shuttle now and the lot is full by 7:30. Lair o' the Bear instead? Bear Creek trail, 40 min drive, much quieter
10:15 Nora Vance: ok yes. Lair o' the Bear, meet at the park lot at 7:30 then
10:16 Sam Ito: 👍
10:20 Jules: works for me
"""))
    s.add(group("chg-trail-0930", "Trailheads", TRAILHEADS, "09-30", """
17:30 Sam Ito: forecast has snow above 8,000 ft on Saturday and the Bear Creek crossing will be icy
17:34 Nora Vance: hmm. Betasso Preserve is lower and drier. Canyon Loop?
17:35 Eli Brooks: yes. and it opens at 8 so let's do 8am, main lot
17:36 Jules: Betasso, Canyon Loop, 8am. in
"""))

    # The car: one thread where the shop moves the appointment.
    s.add(email("chg-car-service", "Your appointment at Alpine Auto Care", [
        Mail(ALPINE, JULES, "09-14 10:05", """Hi Jules,

Your appointment is confirmed.

Vehicle: 2016 Subaru Outback
Service: 60,000-mile service
When: Monday, September 28 at 9:00 AM

Please plan to leave the car with us for the day.

Alpine Auto Care"""),
        Mail(ALPINE, JULES, "09-23 15:40", """Hi Jules,

We're sorry: the filters for your Outback are on backorder and won't be here by the 28th. We moved your 60,000-mile
service to Thursday, October 8 at 2:00 PM. Reply to this email if that doesn't work and we'll find another time.

Alpine Auto Care"""),
    ], labels=("INBOX",)))

    # The studio: when weekly client updates go out.
    s.add(note("chg-weekly-v1", "Work/2026-09-02 Weekly client updates.md", """---
date: 2026-09-02
tags: [process, studio]
---
# Weekly client updates

Every Friday at 4pm I email each active client a short update: what shipped this week, what's next, and anything
blocked. One email per client, same template.
"""))
    s.add(note("chg-weekly-v2", "Work/2026-09-23 Weekly client updates, revised.md", """---
date: 2026-09-23
tags: [process, studio]
---
# Weekly client updates, revised

Friday 4pm updates land when clients are leaving for the weekend and nobody reads them. New rule: the update goes out
Monday at 8am Mountain and covers the week before. The first one is Monday, September 28.
"""))

    # The studio: where laptop backups go.
    s.add(hermes("chg-hermes-backup", "09-07 21:00", 10, "Laptop backup plan", """
U: i keep forgetting to back up my laptop. what's a simple setup for ~/code and ~/notes, encrypted, cheap
T web_search: restic backblaze b2 cost per month
R: restic is free and open source; Backblaze B2 storage is about $6 per TB per month ...
A: restic to Backblaze B2 fits: encrypted, deduplicated, and about $1 to $2 a month for a few hundred GB. Run it nightly from launchd.
U: ok do that. nightly at 2:30am, backblaze b2
A: Noted: a nightly restic backup of ~/code and ~/notes to Backblaze B2 at 2:30am. I can write the launchd job when you're at the laptop.
"""))
    s.add(claude("chg-claude-backup-nas", "09-27 11:00", 40, DOTFILES_REPO, "Point the nightly backup at the NAS", """
U: the nightly restic backup goes to backblaze b2. i'd rather keep it local now, i have the synology in the closet. change the repo to the NAS over sftp, keep 2:30am, and remove the b2 credentials from the job
T Read: /Users/jules/dotfiles/launchd/dev.marlowworks.backup.plist
R: RESTIC_REPOSITORY=b2:marlow-laptop:/ ... StartCalendarInterval Hour 2 Minute 30
A: I'll point RESTIC_REPOSITORY at the NAS over SFTP, initialize the repo there, leave the 2:30am schedule alone, and drop the B2 variables from the plist.
T Bash: restic -r sftp:synology.local:/volume1/backups/laptop init
R: created restic repository on sftp:synology.local:/volume1/backups/laptop
T Bash: launchctl kickstart -k gui/501/dev.marlowworks.backup
R: ok
A: Backups now go to the Synology NAS over SFTP (`sftp:synology.local:/volume1/backups/laptop`), still nightly at 2:30am. The Backblaze B2 settings are removed from the plist.
"""))

    # Looks changed, but stands: harbor-reminders stays on Fly.io.
    s.add(claude("chg-harbor-fly", "09-10 14:00", 50, HARBOR_REPO, "Deploy harbor-reminders", """
U: deploy harbor-reminders somewhere cheap. python app plus the scheduler. fly.io is fine
T Bash: fly launch --no-deploy --name harbor-reminders --region den
R: Created app harbor-reminders in organization personal
T Bash: fly volumes create data --size 1 --region den && fly deploy
R: v1 deployed successfully
A: Deployed to Fly.io: one shared-cpu machine in Denver, the scheduler runs in-process, and a 1 GB volume holds the SQLite file. About $5 a month.
"""))
    s.add(claude("chg-harbor-railway", "09-26 16:00", 30, HARBOR_REPO, "Fly.io or Railway for harbor-reminders", """
U: a friend says railway is simpler than fly. how much work would it be to move harbor-reminders, and would it save money
A: Railway would cost about $5 a month too, so there is no saving. A move means a new volume with the SQLite file copied over, new DNS, and re-registering the texting vendor's webhook: about a day, with a window where reminders could miss. I'd stay on Fly.io unless something else is wrong with it.
U: nothing is wrong with it. stay on fly
A: Staying on Fly.io. Nothing changed.
"""))

    # Looks changed, but stands: Quill & Ledger's file names stay date first.
    s.add(email("chg-ql-names", "Quill & Ledger: how documents will be named", [
        Mail(JULES, INES, "09-16 14:20", """Hi Ines,

A detail for the intake step: saved files will be named date first, like `2026-09-14 Staples 84.20.pdf` (date, vendor,
amount). Let me know if the bookkeepers want something different.

Jules""", cc=MARTIN),
        Mail(INES, JULES, "09-16 16:02", "Date first is fine with me. Ines", cc=MARTIN),
    ]))
    s.add(email("chg-ql-names-vendor", "Quill & Ledger: file names, vendor first?", [
        Mail(MARTIN, JULES, "09-29 10:44", """Jules,

One thought on the file names. I sort by vendor when I review, so vendor first would suit me better: Staples
2026-09-14 84.20.pdf. Could we switch?

Martin""", cc=INES),
        Mail(INES, JULES, "09-29 11:20", """Martin, the bookkeepers all search and sort by date, and they are the ones using this every day.
Jules, please keep it as planned.

Ines""", cc=MARTIN),
        Mail(JULES, INES, "09-29 11:45", "Understood, no change to the names. Jules", cc=MARTIN),
    ]))

    # Looks changed, but stands: pinch stays MIT.
    s.add(commit("chg-pinch-license", PINCH_REPO, "09-02 12:20", """chore: add LICENSE (MIT)

MIT, so it can be used in client work without friction.""", ["A\tLICENSE"]))
    s.add(thread("chg-pinch-gpl", PINCH_REPO, 27, "issue", "Would you consider the GPL?", GH_RO, "09-18 20:10", """
pinch is MIT right now. I'd feel better using it at work if it were GPL-3, so improvements flow back. Any chance of
relicensing?
""", [Post(GH_ME, "09-19 09:12", "Thanks for asking. I'm keeping it MIT: I want people to use it inside client "
                                 "projects without thinking about licenses, and I'd rather get a PR than a license "
                                 "requirement. Closing.")],
                 state="closed", closed="09-19 09:13"))

    s.ask(
        Q("chg-01", "update", "Who gets Fernbrook's evening order summary, and when does it go out?",
          "Benji, at 5pm, from Monday September 28. Before that it went to Dana at 6pm.",
          ("chg-fern-summary-move", "chg-fern-summary-ask"), evidence_has=("5pm", "6pm"),
          wrong_answer="Dana, at 6pm."),
        Q("chg-02", "update", "How long does copperline-docs keep archived invoice PDFs?",
          "395 days (13 months). It was 90 days until Copperline's auditors asked for longer.",
          ("chg-cop-archive-issue", "chg-cop-archive-90"), evidence_has=("395", "90 days"),
          wrong_answer="90 days."),
        Q("chg-03", "update", "Which languages do Harbor Lane's reminder texts go out in?",
          "English and Spanish: patients whose ChairSide preferred language is Spanish get the Spanish text and "
          "everyone else gets English. It was English only to start.",
          ("chg-harbor-lang", "chg-harbor-copy"), evidence_has=("Spanish", "English only"),
          wrong_answer="English only."),
        Q("chg-04", "update", "Which address do Quill & Ledger's clients forward their documents to?",
          "A per-client address under docs.quillledger.co. The plan was intake.quillledger.co, but their spam filter "
          "would have quarantined it.", ("chg-ql-address-move", "chg-ql-address-plan"),
          evidence_has=("docs.quillledger.co", "intake.quillledger.co"), wrong_answer="intake.quillledger.co."),
        Q("chg-05", "update", "When are my client office hours?",
          "Thursdays 1pm to 3pm Mountain. They moved from Tuesdays 3pm to 5pm.",
          ("chg-site-hours-thu", "chg-site-hours-tue"), evidence_has=("Thursdays 1pm to 3pm", "Tuesdays 3pm to 5pm"),
          wrong_answer="Tuesdays 3pm to 5pm."),
        Q("chg-06", "update", "How many invoices does copperline-docs process per batch?",
          "20 per batch. It was 50 until Bluestem's large bills of lading ran the nightly job out of memory.",
          ("chg-cop-batch-20", "chg-cop-batch-claude"), evidence_has=("20 invoices per batch", "batches of 50"),
          wrong_answer="50 per batch."),
        Q("chg-07", "update", "Where are the Trailheads hiking on October 3, and when do we meet?",
          "Betasso Preserve, the Canyon Loop, meeting at the main lot at 8am. The plan moved from Chautauqua to "
          "Lair o' the Bear to Betasso.", ("chg-trail-0930", "chg-trail-0926", "chg-trail-0917"),
          evidence_has=("Betasso", "Lair o' the Bear", "Chautauqua"),
          wrong_answer="Chautauqua, or Lair o' the Bear, at 7:30."),
        Q("chg-08", "update", "When is the service appointment for my Outback?",
          "Thursday, October 8 at 2:00 PM at Alpine Auto Care. It moved from Monday, September 28 at 9:00 AM "
          "because the filters were on backorder.", ("chg-car-service",),
          evidence_has=("October 8", "September 28"), wrong_answer="Monday, September 28 at 9:00 AM."),
        Q("chg-09", "update", "When do I send my weekly client update emails?",
          "Mondays at 8am Mountain, covering the week before. They used to go out Fridays at 4pm.",
          ("chg-weekly-v2", "chg-weekly-v1"), evidence_has=("Monday at 8am", "Friday at 4pm"),
          wrong_answer="Fridays at 4pm."),
        Q("chg-10", "update", "Where do my laptop backups go?",
          "To the Synology NAS at home over SFTP (restic), nightly at 2:30am. They went to Backblaze B2 until I moved "
          "them on September 27.", ("chg-claude-backup-nas", "chg-hermes-backup"),
          evidence_has=("Synology", "Backblaze B2"), wrong_answer="Backblaze B2."),
        Q("chb-01", "temporal", "Who was set to get Fernbrook's evening order summary as of September 20, and at "
                                "what time?",
          "Dana, at 6pm.", ("chg-fern-summary-ask",), evidence_has=("6pm",),
          wrong_answer="Benji, at 5pm."),
        Q("chb-02", "temporal", "How long was copperline-docs keeping processed invoice PDFs on September 15?",
          "90 days; a nightly job deleted anything older from archive/.", ("chg-cop-archive-90",),
          evidence_has=("90 days",), wrong_answer="395 days (13 months)."),
        Q("chb-03", "temporal", "Which day and time were my client office hours on September 10?",
          "Tuesdays 3pm to 5pm Mountain.", ("chg-site-hours-tue",), evidence_has=("Tuesdays 3pm to 5pm",),
          wrong_answer="Thursdays 1pm to 3pm."),
        Q("chb-04", "temporal", "How many invoices per batch was the nightly copperline-docs run set to on "
                                "September 15?",
          "50.", ("chg-cop-batch-claude",), evidence_has=("batches of 50",), wrong_answer="20."),
        Q("chb-05", "temporal", "On September 20, where was the Trailheads' October 3 hike planned, and what time "
                                "did we meet?",
          "Chautauqua, the Bluebell-Baird loop, meeting at the ranger cottage lot at 7:30.", ("chg-trail-0917",),
          evidence_has=("Chautauqua", "7:30"), wrong_answer="Betasso Preserve, at 8am."),
        Q("chk-01", "multi", "Where is harbor-reminders hosted?",
          "On Fly.io. I looked at moving it to Railway on September 26 and decided to stay.",
          ("chg-harbor-fly", "chg-harbor-railway"), evidence_has=("Fly.io", "Railway"), wrong_answer="Railway."),
        Q("chk-02", "multi", "How will Quill & Ledger's incoming documents be named?",
          "Date first, then vendor and amount, like 2026-09-14 Staples 84.20.pdf. Martin asked for vendor first on "
          "September 29, but Ines said the bookkeepers work by date, so it stays.",
          ("chg-ql-names", "chg-ql-names-vendor"), evidence_has=("date first", "vendor first"),
          wrong_answer="Vendor first."),
        Q("chk-03", "multi", "What license is pinch released under?",
          "MIT. Someone asked on September 18 about switching to GPL-3, and I decided to keep MIT.",
          ("chg-pinch-gpl", "chg-pinch-license"), evidence_has=("MIT", "GPL-3"), wrong_answer="GPL-3."),
    )
    return s
