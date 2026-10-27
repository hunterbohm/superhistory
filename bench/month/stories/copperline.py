import cast
from cast import COPPERLINE_REPO as REPO, GH_ME, GH_THEO, JULES, RENATA, THEO
from model import Mail, Post, Q, Story, claude, codex, commit, email, hermes, note, thread


def build():
    s = Story()
    s.add(email("cop-intro", "Copperline: carrier invoice extraction", [
        Mail(THEO, JULES, "09-01 09:20", """Hi Jules,

Dana Whitfield at Fernbrook gave me your name. Quick version of our problem: Copperline is a 14-person freight
brokerage in Commerce City. Our AP team keys about 600 carrier invoices a month into Rosebud TMS by hand, and it
eats most of Lupe's week. Most carriers email us PDFs, a few still fax.

What I'd like: something that reads the invoice and BOL PDFs and gives us a CSV that Rosebud's import screen takes.
I've put 40 samples from our six biggest carriers in the zip (Northgate Haulage, Bluestem Logistics, Ridgeway
Carriers, Pell & Sons, Kettle Creek Transport, Lone Cedar Freight).

Can we talk this week?

Theo Brandt
Operations Manager, Copperline Freight""", attachments=("copperline-samples-40.zip",)),
        Mail(JULES, THEO, "09-01 11:05", """Hi Theo,

Thanks for the samples. I skimmed them: five of the six carriers send text-based PDFs, which is the easy case.
Ridgeway's are scans, so they need OCR.

Here's how I'd split it:

Phase 1: text-based PDFs from the five carriers into a CSV for the Rosebud import. Fixed price $6,500, about
four weeks, with a demo on your real invoices at the end.
Phase 2: OCR for scanned carriers like Ridgeway, and pushing straight into Rosebud's API instead of a CSV. I'd quote
that once phase 1 is in.

If that works I'll send a statement of work tomorrow.

Jules"""),
        Mail(THEO, JULES, "09-02 08:30", """Works for me. Renata Solis, our controller, signs anything with a dollar
sign on it, so send the SOW to her and cc me. Let's aim the demo for Wednesday September 30.

Theo"""),
    ]))

    s.add(note("cop-kickoff", "Clients/Copperline/2026-09-03 Copperline kickoff.md", """---
date: 2026-09-03
client: Copperline Freight
tags: [meeting, copperline]
---
# Copperline kickoff call

With Theo Brandt (ops) and Lupe Herrera (AP specialist, does the keying today).

## Today
- Lupe keys every invoice into Rosebud TMS by hand: about 6 minutes per invoice, more for multi-stop loads.
- ~600 carrier invoices a month. Spikes at month end.
- Rosebud import screen takes a CSV, one row per invoice. Lupe will send the import template.

## Fields they need
carrier, invoice number, invoice date, PRO number, BOL number, origin zip, destination zip, weight, line haul,
fuel surcharge, accessorials, total

## Notes
- Northgate Haulage is in Winnipeg and bills in Canadian dollars. Rosebud has a currency column.
- Ridgeway sends scans. Out of phase 1.
- Bugs and data problems go in GitHub issues on the repo (Theo has a GitHub account). Business stuff by email.
- Weekly check-in Thursdays at 10:00, 20 min, video.

## Next
- [x] SOW to Renata
- [ ] repo + carriers config
- [ ] get the Rosebud import template from Lupe
""", "09-03 11:40"))

    s.add(email("cop-sow", "Copperline Freight: statement of work, phase 1", [
        Mail(JULES, RENATA, "09-04 10:12", """Hi Renata,

Attached is the statement of work for phase 1 of the invoice extraction project Theo and I discussed: $6,500 fixed,
50% deposit to start, the rest on acceptance after the demo. My standard terms are net-15.

Happy to answer anything.

Jules Marlow
Marlow Works""", cc=THEO, attachments=("Marlow Works - Copperline SOW phase 1.pdf",)),
        Mail(RENATA, JULES, "09-08 13:47", """Jules,

Signed copy attached. Two changes on our side, which I've initialed: our vendor policy is net-30 for everything,
and please address invoices to ap@copperline-freight.com rather than to me.

Renata Solis
Controller, Copperline Freight""", cc=THEO, attachments=("Copperline SOW phase 1 - signed.pdf",)),
        Mail(JULES, RENATA, "09-08 14:20", """Net-30 is fine. The deposit invoice (MW-1039, $3,250) is on its way
to ap@. Thanks Renata.

Jules""", cc=THEO),
    ]))

    s.add(claude("cop-claude-setup", "09-04 13:30", 85, REPO, "Scaffold copperline-docs extraction project", """
U: new client project. copperline-docs. reads carrier invoice PDFs and BOLs, outputs a CSV for their TMS import. samples in ~/Downloads/copperline-samples-40. set up the repo: uv, src layout, pytest. one module per carrier would get messy, I'd rather have a carriers config file
T Bash: ls ~/Downloads/copperline-samples-40 | head -20
R: BS-BOL-558201.pdf
BS-INV-558201.pdf
BS-INV-558377.pdf
KC-2026-08-1182.pdf
LC-INV-30917.pdf
NG-20260304-118.pdf
NG-20260311-204.pdf
NG-20260402-019.pdf
PS-104432.pdf
RW-scan-0822.pdf
T Bash: cd ~/code/copperline-docs && uv init --package --name copperline && uv add pdfplumber pyyaml && uv add --dev pytest ruff
R: Initialized project `copperline`
Resolved 9 packages in 412ms
Installed 9 packages in 38ms
T Bash: uv run python -c "import pdfplumber; p=pdfplumber.open('samples/NG-20260304-118.pdf'); print(p.pages[0].extract_text()[:600])"
R: NORTHGATE HAULAGE LTD.
1290 Logan Ave, Winnipeg MB
INVOICE NG-20260304-118
Invoice Date: 04/03/2026
PRO: 7731024   BOL: CF-88213
Ship From: Commerce City CO 80022
Ship To: Brandon MB R7A 5Y2
Weight: 18,420 LB
Line Haul ........ 2,140.00
Fuel Surcharge ...... 385.20
Total CAD 2,525.20
A: All the samples I opened so far have a real text layer, so pdfplumber can read them directly. Only `RW-scan-0822.pdf` has no text at all; that's the Ridgeway scan, which is out of phase 1.

I'd structure it like this:

- `src/copperline/extract/` with a generic extractor driven by per-carrier settings: regexes for the header fields, and table settings for line items.
- `config/carriers.yaml` with one block per carrier.
- `src/copperline/export/` for the CSV writer, once we have the Rosebud template.
- `tests/` with one test per sample PDF and its expected fields.

I'm using pdfplumber rather than camelot or an OCR pipeline: the PDFs are text-based, pdfplumber gives word positions when a regex isn't enough, and it has no system dependencies.
U: good. go
T Write: /Users/jules/code/copperline-docs/config/carriers.yaml
R: File created successfully
T Write: /Users/jules/code/copperline-docs/src/copperline/extract/fields.py
R: File created successfully
T Write: /Users/jules/code/copperline-docs/tests/test_samples.py
R: File created successfully
T Bash: uv run pytest -q
R: ........x.
9 passed, 1 xfailed in 1.84s
A: Scaffold is in. Nine samples extract their header fields; the Ridgeway scan is marked xfail. Next step is line items and the CSV writer.
"""))

    s.add(claude("cop-claude-dates", "09-09 09:12", 52, REPO, "Northgate invoices parse into the wrong month", """
U: lupe ran the march batch and says a bunch of Northgate invoices show up in the wrong month in the csv. not all of them. can you dig in
T Bash: uv run python -m copperline.cli extract samples/ --carrier northgate --format table
R: file                    invoice_date  total
NG-20260304-118.pdf     2026-04-03    2525.20
NG-20260311-204.pdf     2026-11-03    1980.00
NG-20260317-311.pdf     2026-03-17    2210.45
NG-20260402-019.pdf     2026-02-04    2655.10
T Read: /Users/jules/code/copperline-docs/src/copperline/extract/fields.py
R: from dateutil import parser as dateparser
...
def parse_date(raw: str) -> date:
    return dateparser.parse(raw).date()
T Bash: pdftotext -layout samples/NG-20260304-118.pdf - | grep -i date
R: Invoice Date: 04/03/2026
Ship Date: 02/03/2026
A: Found it. Northgate Haulage prints its dates day-first, DD/MM/YYYY, which is normal in Canada: `04/03/2026` on invoice NG-20260304-118 means 4 March 2026, and the invoice number itself encodes 20260304. Our `parse_date` calls `dateutil.parser.parse` with its default, which reads an ambiguous date month-first, so it becomes April 3.

That also explains why only some of them are wrong. When the day is 13 or more, like `17/03/2026`, there is no month 17, so dateutil falls back to reading it day-first and gets it right. Only invoices dated on days 1 to 12 of a month get swapped, so it looks random.

Fix I'd suggest: stop guessing. Give each carrier an explicit `date_format` in `config/carriers.yaml` (`"%d/%m/%Y"` for Northgate, `"%m/%d/%Y"` for the US carriers) and parse with `datetime.strptime` using that format. A wrong format then raises instead of silently swapping day and month. I'd add tests for NG-20260304-118 (expect 2026-03-04) and NG-20260402-019 (expect 2026-04-02).

One more thing I noticed in the same files: Northgate totals are in Canadian dollars (`Total CAD`). Right now we drop the currency. Rosebud needs to know, so the carrier config should carry `currency: CAD` too.
U: makes sense. but park it for now, theo wants bluestem done first and I'm out of time today. write down what you found so we don't lose it
T Write: /Users/jules/code/copperline-docs/docs/notes/northgate-dates.md
R: File created successfully
A: Written to `docs/notes/northgate-dates.md` (not committed): the cause, why only days 1 to 12 swap, the per-carrier `date_format` fix, the two test cases, and the CAD currency note.
"""))

    s.add(thread("cop-gh-7", REPO, 7, "issue", "Northgate invoices land in the wrong month", GH_THEO, "09-10 08:41", """
Lupe found these in the March batch export. NG-20260304-118 shows invoice date 2026-04-03 in the CSV but the PDF
says 04/03/2026 and it's a March invoice. Same for NG-20260311-204 (shows November!). Others from Northgate look fine.

This one matters because Rosebud posts the invoice to the wrong accounting period.
""", [
        Post(GH_ME, "09-10 10:02", "Known, I found the cause on Wednesday. It's a Northgate-only problem. Fixing it right "
                                   "after the Bluestem tables, so early next week."),
        Post(GH_THEO, "09-15 16:20", "Lupe hit three more of these today. Can this jump the queue?"),
        Post(GH_ME, "09-16 12:55", "Fixed in #8. Re-run the March batch and these come out right."),
    ], state="closed", closed="09-16 12:56", labels=("bug",)))

    s.add(codex("cop-codex-bluestem", "09-14 13:05", 70, REPO, """
U: bluestem BOLs: the line item table comes out empty for anything longer than one page. samples/BS-BOL-558201.pdf is a two pager. fix the table extraction
T exec_command: uv run python -m copperline.cli extract samples/BS-BOL-558201.pdf --debug-tables
R: page 1: 1 table, 14 rows
page 2: 1 table, 9 rows
merged: 0 rows (header mismatch)
T exec_command: sed -n 1,60p src/copperline/extract/tables.py
R: def line_items(pdf, settings):
    tables = [t for p in pdf.pages for t in p.extract_tables(settings)]
    if len(tables) != 1:
        return []
A: The Bluestem line-item table runs across both pages and repeats its header row at the top of page 2. `line_items` only accepts a single table, so a two-page BOL returns nothing. I'll join the per-page tables when their header rows match and drop the repeated header from every page after the first. Bluestem's tables have ruled lines, so I'm also setting `vertical_strategy: lines` for that carrier; the text strategy splits the description column in two.
T apply_patch: *** Begin Patch
*** Update File: src/copperline/extract/tables.py
@@
-    if len(tables) != 1:
-        return []
+    if not tables:
+        return []
+    header, rows = tables[0][0], list(tables[0][1:])
+    for table in tables[1:]:
+        rows += table[1:] if table[0] == header else table
*** End Patch
R: Success. Updated the following files:
M src/copperline/extract/tables.py
T exec_command: uv run pytest -q tests/test_bluestem.py
R: .....
5 passed in 0.92s
U: also their accessorial codes. LFTG, RESI, INSD. map them to the rosebud names
A: Added an `accessorials` map to the Bluestem block in `config/carriers.yaml`: `LFTG` is liftgate service, `RESI` is residential delivery, `INSD` is inside delivery. Each maps to the matching Rosebud accessorial name. Unknown codes pass through unchanged and get logged, so we see new ones instead of losing them.
T exec_command: uv run pytest -q
R: ......................
22 passed, 1 xfailed in 2.41s
"""))

    s.add(commit("cop-commit-bluestem", REPO, "09-14 14:30", """extract: join Bluestem BOL tables across pages

Bluestem repeats the header row at the top of page 2. Join per-page tables
whose header matches and drop the repeats. Map LFTG/RESI/INSD accessorials.""",
                 ["M\tconfig/carriers.yaml", "M\tsrc/copperline/extract/tables.py", "A\ttests/test_bluestem.py"]))

    s.add(hermes("cop-hermes-fy", "09-15 12:10", 6, "Notes from Theo's call", """
U: just got off the phone with Theo from Copperline. save this: their fiscal year ends October 31, so Renata wants phase 2 invoiced before then. Theo wants the phase 2 quote by Sep 25. phase 2 is the OCR for Ridgeway plus pushing straight into Rosebud instead of CSV
A: Saved. Copperline notes from your call with Theo:
- Copperline's fiscal year ends October 31. Renata wants phase 2 invoiced before then.
- Theo wants the phase 2 quote by September 25.
- Phase 2 scope: OCR for Ridgeway's scanned invoices, and pushing straight into Rosebud instead of a CSV.
Want a reminder on September 23 to write the quote?
U: yes 9am
T cronjob: create "Write Copperline phase 2 quote (due to Theo Sep 25)" at 2026-09-23 09:00
R: created job 6f2a1c
A: Done. I'll remind you on September 23 at 9:00.
"""))

    s.add(codex("cop-codex-northgate", "09-16 09:40", 95, REPO, """
U: theo is asking about the northgate dates again (#7). fix it today. I think claude figured out the cause last week but I don't have that session handy, check the repo
T exec_command: rg -n "northgate" -i docs src config
R: docs/notes/northgate-dates.md:1:# Northgate dates parse into the wrong month
config/carriers.yaml:22:northgate:
T exec_command: cat docs/notes/northgate-dates.md
R: # Northgate dates parse into the wrong month
Northgate prints dates day-first (DD/MM/YYYY). parse_date uses dateutil, which reads ambiguous dates month-first, so days 1-12 swap day and month. Fix: per-carrier date_format in config/carriers.yaml, parse with strptime ...
A: There's a note in `docs/notes/northgate-dates.md` that explains it: Northgate prints day-first dates, and `parse_date` uses dateutil's month-first default, so any invoice dated on day 1 to 12 gets day and month swapped. The note proposes a per-carrier `date_format`. I'll do that:

1. Add `date_format` to every carrier in `config/carriers.yaml`. Northgate is `"%d/%m/%Y"`. Bluestem, Pell & Sons and Lone Cedar are `"%m/%d/%Y"`. Kettle Creek prints ISO dates, so `"%Y-%m-%d"`.
2. Replace the dateutil call with `datetime.strptime(raw, carrier.date_format)`, and make a mismatch an extraction error instead of a guess.
3. Add `currency: CAD` for Northgate, as the note says, and carry it to the CSV.
T apply_patch: *** Begin Patch
*** Update File: src/copperline/extract/fields.py
@@
-from dateutil import parser as dateparser
+from datetime import datetime
@@
-def parse_date(raw: str) -> date:
-    return dateparser.parse(raw).date()
+def parse_date(raw: str, fmt: str) -> date:
+    return datetime.strptime(raw.strip(), fmt).date()
*** End Patch
R: Success. Updated the following files:
M src/copperline/extract/fields.py
T exec_command: uv run pytest -q
E: FAILED tests/test_samples.py::test_header[KC-2026-08-1182.pdf] - ValueError: time data '2026-08-21' does not match format '%m/%d/%Y'
1 failed, 23 passed, 1 xfailed in 2.66s
A: Kettle Creek still had the US format in my first pass; I set it to ISO as planned.
T exec_command: uv run pytest -q
R: .........................
25 passed, 1 xfailed in 2.58s
T exec_command: uv run python -m copperline.cli extract samples/ --carrier northgate --format table
R: file                    invoice_date  currency  total
NG-20260304-118.pdf     2026-03-04    CAD       2525.20
NG-20260311-204.pdf     2026-03-11    CAD       1980.00
NG-20260317-311.pdf     2026-03-17    CAD       2210.45
NG-20260402-019.pdf     2026-04-02    CAD       2655.10
T exec_command: git checkout -b fix/carrier-date-formats && git commit -am "parse invoice dates with per-carrier formats" && gh pr create --fill
R: https://github.com/marlow-works/copperline-docs/pull/8
A: All four Northgate samples now have the right dates and carry CAD. PR #8 is open. I left the note in `docs/notes/` since it explains why the format is explicit.
""", branch="fix/carrier-date-formats"))

    s.add(commit("cop-commit-dates", REPO, "09-16 11:12", """parse invoice dates with per-carrier formats

Northgate Haulage prints day-first dates (04/03/2026 is 4 March), and dateutil
read them month-first, so invoices dated on days 1-12 landed in the wrong month.
Each carrier now declares date_format in config/carriers.yaml and parsing uses
strptime. Northgate also carries currency: CAD.

Fixes #7""", ["M\tconfig/carriers.yaml", "M\tsrc/copperline/extract/fields.py", "M\tsrc/copperline/export/csv.py",
              "A\ttests/test_dates.py"]))

    s.add(thread("cop-gh-8", REPO, 8, "PR", "Parse invoice dates with per-carrier formats", GH_ME, "09-16 11:20", """
Fixes #7.

Northgate prints day-first dates and we parsed them month-first, so any invoice dated on days 1 to 12 swapped day
and month. Each carrier now sets `date_format` in `config/carriers.yaml` and we parse with `strptime`, so a wrong
format fails loudly instead of guessing. Northgate also gets `currency: CAD`, which now goes to the CSV.
""", [Post(GH_THEO, "09-16 12:40", "Lupe re-ran March, all Northgate dates match the PDFs now. Thanks!")],
        state="merged", merged="09-16 12:54", branches="fix/carrier-date-formats -> main"))

    s.add(note("cop-checkin-0917", "Clients/Copperline/2026-09-17 Copperline check-in.md", """# Copperline check-in

Theo, Lupe, me.

- Northgate dates fixed (#8). Lupe re-ran March, all good.
- Bluestem two-page BOLs work now.
- Accuracy on the 212 March invoices: 96.8% of fields right on first pass.
- Lone Cedar labels the invoice number "Inv #" on some invoices and "Invoice No." on others. Added both.
- Lupe asked for a `needs_review` column: flag any row where a field's confidence is under 0.9, so she only checks
  those instead of everything. Agreed, doing it this week.
- Next week: Rosebud template + export.
"""))

    s.add(email("cop-paid", "Remittance advice: Marlow Works MW-1039", [
        Mail("Copperline AP <ap@copperline-freight.com>", JULES, "09-18 07:55", """Remittance advice

Payee: Marlow Works
Invoice: MW-1039
Amount: $3,250.00
Method: ACH
Payment date: 09/17/2026

Questions: ap@copperline-freight.com"""),
        Mail(JULES, RENATA, "09-18 09:02", "Received, thank you Renata. Jules"),
    ], labels=("INBOX",)))

    s.add(thread("cop-gh-9", REPO, 9, "issue", "Support Ridgeway Carriers invoices", GH_THEO, "09-21 10:15", """
Ridgeway is our 3rd biggest carrier by volume and they only send scans (fax quality, it's a TIFF inside the PDF).
Right now those all go to Lupe. Can we get them in?
""", [
        Post(GH_ME, "09-21 11:30", "Those need OCR, which is outside phase 1. I could add it now with ocrmypdf and "
                                   "Tesseract for about 6 extra hours, or fold it into phase 2 with the Rosebud API work. "
                                   "Your call."),
        Post(GH_THEO, "09-21 14:02", "Fold it into phase 2. Don't want to slip the demo."),
    ], labels=("enhancement", "phase-2")))

    s.add(email("cop-demo-move", "Phase 1 demo date", [
        Mail(THEO, JULES, "09-23 07:48", """Jules, bad news on timing: I'm at the Rosebud user conference in
Nashville September 29 through October 1, so the 30th won't work for the demo. Could we do Friday October 2 at
10am instead? Lupe and Renata will join.

Theo"""),
        Mail(JULES, THEO, "09-23 08:15", """Friday October 2 at 10am works. I'll send an invite for the demo with
Lupe and Renata. It also gives me two more days on the export.

Jules"""),
    ]))

    s.add(email("cop-phase2-quote", "Copperline phase 2 quote", [
        Mail(JULES, THEO, "09-24 16:40", """Theo, Renata,

Here's phase 2, as promised before the 25th:

- OCR for scanned carriers, starting with Ridgeway (issue #9).
- Push invoices straight into Rosebud through its API, so the CSV import step goes away.

Fixed price $5,200. Start October 5, done by October 30, so it can be invoiced inside your fiscal year.

Jules""", cc=RENATA, attachments=("Marlow Works - Copperline phase 2 quote.pdf",)),
        Mail(RENATA, JULES, "09-25 10:31", """Approved. Our PO number is PO-2291; please put it on the invoice.

Renata""", cc=THEO),
    ]))

    s.add(note("cop-checkin-0924", "Clients/Copperline/2026-09-24 Copperline check-in.md", """# Copperline check-in

- Lupe tested 150 invoices from August through the whole pipeline: 4 errors, all Lone Cedar Freight, all the
  fuel surcharge. Their PDF puts the surcharge in a footnote under the table. Theo will send 10 more Lone Cedar
  samples.
- `needs_review` column is in. Lupe likes it.
- Demo moved to Fri Oct 2 (Theo is in Nashville).
- Phase 2 quote goes out today.
"""))

    s.add(claude("cop-claude-export", "09-25 13:15", 64, REPO, "Rosebud CSV export rejected on import", """
U: lupe tried the export in rosebud's import screen and it rejected 31 of 150 rows. "column count mismatch". template is in samples/AP_Import_v3.csv
T Bash: head -3 samples/AP_Import_v3.csv
R: CARRIER,INV_NO,INV_DATE,PRO,BOL,ORIG_ZIP,DEST_ZIP,WEIGHT_LB,LINEHAUL,FSC,ACCESSORIALS,TOTAL,CURR
"Bluestem Logistics","558201","09/02/2026","4410982","CF-88102","80022","68110","12400","1840.00","312.80","LIFTGATE","2227.80","USD"
T Bash: uv run python -m copperline.cli export samples/ -o /tmp/out.csv && awk -F, 'NF!=13' /tmp/out.csv | head -3
R: Kettle Creek Transport,KC-2026-08-1182,2026-08-21,881234,CF-87710, Brighton,80601,80216,9800.5,...
A: The rejected rows have a comma inside a field. Kettle Creek and Pell & Sons put the city in the BOL reference ("CF-87710, Brighton"), and our writer only quotes fields when it has to, which Rosebud's importer doesn't handle: it splits on every comma. Their own template quotes every field. Three more differences from the template:

- `INV_DATE` must be MM/DD/YYYY. We write ISO dates.
- `WEIGHT_LB` must be whole pounds. We write decimals like 9800.5.
- Column order and names must match the template exactly, including `CURR` at the end.

I'll write with `csv.QUOTE_ALL`, format dates as `%m/%d/%Y`, round weights to whole pounds, and build the header from the template.
T Edit: /Users/jules/code/copperline-docs/src/copperline/export/csv.py
R: The file has been updated successfully.
T Bash: uv run pytest -q tests/test_export.py
R: ......
6 passed in 0.44s
T Bash: uv run python -m copperline.cli export samples/august-150/ -o /tmp/out.csv && awk -F'","' 'NF!=13' /tmp/out.csv | wc -l
R: 0
A: All 150 rows now have 13 quoted fields. Send Lupe `/tmp/out.csv` to try the import again.
U: she says all 150 imported. commit it
T Bash: git commit -am "export: quote every field for the Rosebud import" && git push
R: [main 4c1e9a2] export: quote every field for the Rosebud import
 3 files changed, 41 insertions(+), 12 deletions(-)
"""))

    s.add(commit("cop-commit-export", REPO, "09-25 14:20", """export: quote every field for the Rosebud import

Rosebud's importer splits on every comma, even inside a field, so rows with a
comma in the BOL reference were rejected. Quote all fields, write dates as
MM/DD/YYYY and weights as whole pounds, and take the header from AP_Import_v3.""",
                 ["M\tsrc/copperline/export/csv.py", "A\ttests/test_export.py", "A\tsamples/AP_Import_v3.csv"]))

    s.ask(
        Q("cop-01", "single", "Why were Northgate Haulage invoice dates coming out wrong in copperline-docs?",
          "Northgate prints dates day-first (DD/MM/YYYY) and the parser read them month-first with dateutil's "
          "default, so an invoice dated 04/03/2026 (4 March) became April 3.",
          ("cop-claude-dates", "cop-codex-northgate", "cop-commit-dates"), evidence_has=("day-first", "month-first")),
        Q("cop-02", "single", "Why did only some of the Northgate invoices land in the wrong month, not all of them?",
          "Dates with a day of 13 or more can't be read month-first, so dateutil fell back to day-first and got "
          "them right; only invoices dated on days 1 to 12 had day and month swapped.",
          ("cop-claude-dates",), evidence_has=("13 or more", "1 to 12")),
        Q("cop-03", "single", "Where do the carrier date formats live in copperline-docs, and how are dates parsed now?",
          "In config/carriers.yaml: each carrier has a date_format (\"%d/%m/%Y\" for Northgate), and dates are "
          "parsed with datetime.strptime, so a mismatch fails instead of guessing.",
          ("cop-claude-dates", "cop-codex-northgate", "cop-commit-dates", "cop-gh-8"),
          evidence_has=("date_format", "strptime", "fails loudly")),
        Q("cop-04", "single", "What date format does Kettle Creek Transport print on its invoices?",
          "ISO dates, YYYY-MM-DD (date_format \"%Y-%m-%d\").", ("cop-codex-northgate",), evidence_has=("%Y-%m-%d",)),
        Q("cop-05", "single", "What currency does Northgate Haulage bill Copperline in?",
          "Canadian dollars (CAD).", ("cop-claude-dates", "cop-kickoff"), evidence_has=("CAD",)),
        Q("cop-06", "single", "About how many carrier invoices does Copperline's AP team key by hand each month?",
          "About 600 a month.", ("cop-intro", "cop-kickoff"), evidence_has=("600",)),
        Q("cop-07", "single", "What's the price of phase 1 of the Copperline project?",
          "$6,500, fixed price.", ("cop-intro", "cop-sow"), evidence_has=("$6,500",)),
        Q("cop-08", "update", "When is the Copperline phase 1 demo?",
          "Friday October 2 at 10am. It moved from Wednesday September 30 because Theo is at the Rosebud user "
          "conference in Nashville September 29 to October 1.",
          ("cop-demo-move",), evidence_has=("October 2 at 10am",)),
        Q("cop-09", "single", "Who signs contracts at Copperline Freight?",
          "Renata Solis, the controller.", ("cop-intro",), evidence_has=("Renata Solis",)),
        Q("cop-10", "temporal", "On what date did Copperline sign the phase 1 statement of work?",
          "September 8, 2026, when Renata sent the signed copy.", ("cop-sow",), evidence_has=("Signed copy",)),
        Q("cop-11", "update", "What payment terms are we on with Copperline?",
          "Net-30, Copperline's vendor policy, which replaced Jules's standard net-15.", ("cop-sow",),
          evidence_has=("net-30",)),
        Q("cop-12", "single", "What was the Copperline deposit invoice number and amount?",
          "Invoice MW-1039 for $3,250 (50% of phase 1).", ("cop-sow", "cop-paid"), evidence_has=("MW-1039", "$3,250")),
        Q("cop-13", "temporal", "When did Copperline pay the deposit?",
          "By ACH on September 17, 2026 (remittance advice arrived September 18).", ("cop-paid",),
          evidence_has=("09/17/2026",)),
        Q("cop-14", "single", "Where should invoices to Copperline be sent?",
          "To ap@copperline-freight.com, not to Renata directly.", ("cop-sow",), evidence_has=("ap@copperline-freight.com",)),
        Q("cop-15", "single", "Which of Copperline's carriers sends scanned invoices that need OCR?",
          "Ridgeway Carriers (fax-quality scans, a TIFF inside the PDF).", ("cop-intro", "cop-gh-9"),
          evidence_has=("Ridgeway",)),
        Q("cop-16", "single", "What did we decide about supporting Ridgeway invoices in copperline-docs?",
          "Fold it into phase 2 instead of adding about 6 hours of OCR work now, so the demo doesn't slip.",
          ("cop-gh-9",), evidence_has=("Fold it into phase 2",)),
        Q("cop-17", "single", "When does Copperline's fiscal year end, and why does it matter for us?",
          "October 31; Renata wants phase 2 invoiced before then.", ("cop-hermes-fy",),
          evidence_has=("October 31",)),
        Q("cop-18", "single", "What's the price and PO number for Copperline phase 2?",
          "$5,200, PO number PO-2291.", ("cop-phase2-quote",), evidence_has=("$5,200", "PO-2291")),
        Q("cop-19", "single", "What's in Copperline phase 2, and when does it run?",
          "OCR for scanned carriers starting with Ridgeway, and pushing invoices straight into Rosebud's API "
          "instead of a CSV; October 5 to October 30.", ("cop-phase2-quote", "cop-hermes-fy"),
          evidence_has=("October 5", "October 30")),
        Q("cop-20", "single", "Why does the Rosebud export quote every field?",
          "Rows with a comma inside a field (the BOL reference) were rejected by Rosebud's importer, so the writer "
          "now quotes every field with csv.QUOTE_ALL.",
          ("cop-claude-export", "cop-commit-export"), evidence_has=("QUOTE_ALL",)),
        Q("cop-21", "single", "What date format and weight unit does Rosebud's import expect?",
          "Dates as MM/DD/YYYY and weights in whole pounds.", ("cop-claude-export", "cop-commit-export"),
          evidence_has=("MM/DD/YYYY", "whole pounds")),
        Q("cop-22", "single", "How do Bluestem's bills of lading break the table extraction, and what was the fix?",
          "The line-item table runs across two pages and repeats its header row on page 2; the fix joins "
          "per-page tables with matching headers and drops the repeated header. Bluestem also gets vertical_strategy: "
          "lines for its ruled tables, since the text strategy split the description column in two.",
          ("cop-codex-bluestem", "cop-commit-bluestem"), evidence_has=("repeat", "vertical_strategy: lines")),
        Q("cop-23", "single", "What does the Bluestem accessorial code LFTG mean?",
          "Liftgate service.", ("cop-codex-bluestem",), evidence_has=("liftgate",)),
        Q("cop-24", "single", "When is the weekly Copperline check-in?",
          "Thursdays at 10:00, 20 minutes, on video.", ("cop-kickoff",), evidence_has=("Thursdays at 10:00",)),
        Q("cop-25", "single", "How long does it take Lupe to key one carrier invoice by hand?",
          "About 6 minutes per invoice, more for multi-stop loads.", ("cop-kickoff",), evidence_has=("6 minutes",)),
        Q("cop-26", "single", "What did Lupe ask for so she doesn't have to check every row?",
          "A needs_review column that flags any row where a field's confidence is under 0.9.",
          ("cop-checkin-0917",), evidence_has=("needs_review", "0.9")),
        Q("cop-27", "multi", "Which carrier caused the errors in Lupe's August test run, and what went wrong?",
          "Lone Cedar Freight: all 4 errors in 150 invoices were its fuel surcharge, which its PDF puts in a "
          "footnote under the table.", ("cop-checkin-0924",), evidence_has=("Lone Cedar", "fuel surcharge")),
        Q("cop-28", "multi", "Which copperline-docs GitHub issues are still open at the end of September?",
          "Only #9, Ridgeway Carriers support, which was folded into phase 2. #7 (Northgate dates) was closed "
          "by PR #8 on September 16.", ("cop-gh-7", "cop-gh-8", "cop-gh-9"), evidence_has=("issue open", "issue closed")),
        Q("cop-29", "single", "Which PDF library does copperline-docs use, and why no OCR in phase 1?",
          "pdfplumber; the phase 1 PDFs have a real text layer, so no OCR is needed (only Ridgeway's scans would).",
          ("cop-claude-setup",), evidence_has=("pdfplumber", "text-based")),
        Q("cop-30", "single", "What was the first-pass field accuracy on Copperline's March invoices?",
          "96.8% of fields right on the 212 March invoices.", ("cop-checkin-0917",), evidence_has=("96.8%",)),
    )
    return s
