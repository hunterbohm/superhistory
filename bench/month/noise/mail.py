import cast
import model
from model import Story, Mail, Post, claude, codex, hermes, email, texts, note, commit, thread

NEWS = ("INBOX", "CATEGORY_UPDATES")
PROMO = ("CATEGORY_PROMOTIONS",)
PERSONAL = ("INBOX", "IMPORTANT")


def foot(site, address):
    return (f"\n\n--\nYou are receiving this at {model.ACCOUNT} because you subscribed at https://{site}/.\n"
            f"Unsubscribe: https://{site}/unsubscribe?id=jm-4471\nPreferences: https://{site}/preferences\n{address}")


def issue(key, subject, sender, when, body, site, address, labels=NEWS):
    return email(key, subject, [Mail(sender, cast.JULES, when, body.strip() + foot(site, address))], labels=labels)


def single(key, subject, sender, when, body, labels=NEWS):
    return email(key, subject, [Mail(sender, cast.JULES, when, body)], labels=labels)


NET_THIRTY = ("Net Thirty <hello@netthirty.example>", "netthirty.example", "Net Thirty Media, PO Box 2210, Portland, OR 97208")
WORKFLOW = ("Quiet Workflow <weekly@quietworkflow.example>", "quietworkflow.example",
            "Quiet Workflow, 88 Mercer Row #4, Providence, RI 02903")
BACKHAUL = ("The Backhaul <desk@backhaulbrief.example>", "backhaulbrief.example",
            "The Backhaul, 1200 Commerce Way, Suite 310, Memphis, TN 38103")
WALRUS = ("Walrus Digest <editor@walrusdigest.example>", "walrusdigest.example", "Walrus Digest, PO Box 771, Madison, WI 53701")
RECALL = ("The Recall Letter <hello@recallletter.example>", "recallletter.example",
          "The Recall Letter, 415 Orchard Ave, Suite 2, Dayton, OH 45402")
PROOFING = ("The Proofing Drawer <crumbs@proofingdrawer.example>", "proofingdrawer.example",
            "The Proofing Drawer, 30 Millrace Lane, Burlington, VT 05401")


def build():
    s = Story()
    for item in (*_freelancer(), *_automation(), *_freight(), *_python(), *_dental(), *_bakery(),
                 *_tools(), *_pitches(), *_community(), *_personal(), *_leads()):
        s.add(item)
    return s


def _freelancer():
    return [
        issue("nm-001", "Net Thirty #58: The three-line late payment email", *NET_THIRTY[:1], "09-01 07:05", """
NET THIRTY
Money notes for people who invoice for a living
Issue 58

Good morning,

Most late payments are not refusals. They are an invoice sitting in somebody's inbox under forty other things. This issue is about making the follow-up so easy to act on that nobody has to think.

1. THE THREE-LINE FOLLOW-UP

Day 1 after the due date:
"Hi Jordan, invoice 1043 came due yesterday. Here is the payment link again in case it got buried. Thanks!"

Day 7:
"Hi Jordan, checking in on invoice 1043, now a week past due. If something on our side is blocking payment (a PO number, a vendor form, a different billing contact), tell me and I will fix it today."

Day 14:
"Hi Jordan, invoice 1043 is two weeks past due. Can you tell me the date it will be paid? I will pause new work until I have a date."

Notice the pattern. Each message names the invoice, names one next step, and offers a way out that is not a confrontation. Readers who switched to this sequence told us roughly a third of their "late" invoices were paid within a day of the first message.

2. READER QUESTION

"One client pays all vendors in a single batch on the 20th. Should I time my invoices to land just before it?"

Yes. Ask their accounts payable contact for the batch calendar once, then put the cutoff in your own calendar. A twelve-minute email now saves a month of waiting every time you miss the window by a day.

3. WORTH YOUR TIME

- Why we stopped saying "net 30" in proposals and started writing an actual date: https://netthirty.example/notes/write-the-date
- A one-page late fee clause in plain English: https://netthirty.example/templates/late-fee
- Interview: a bookkeeper on what she wishes freelancers put on invoices: https://netthirty.example/interviews/bookkeeper-checklist

Until the 15th,
Carmen
""", *NET_THIRTY[1:]),
        issue("nm-010", "Net Thirty #59: Deposits without the awkward", *NET_THIRTY[:1], "09-15 07:02", """
NET THIRTY
Issue 59

Hi again,

Last issue was about chasing money. This one is about not having to chase it, which starts before the project does.

1. THE DEPOSIT CONVERSATION

The awkward version: "I require a deposit." The easy version: "To hold a start date, the first payment is due when you sign. Everything after that is billed as we hit milestones."

Said that way, the deposit is a scheduling tool, not a trust test. Several readers told us clients stopped negotiating it once it was attached to a start date instead of to the client's credit.

2. THREE STRUCTURES READERS ACTUALLY USE

- Half up front, half on delivery. Simple, but leaves a long gap on projects over six weeks.
- Thirds: signing, midpoint review, handoff. The midpoint doubles as a forced check-in.
- Monthly in advance for ongoing work, billed on the first so it clears before you do the month's work.

Whichever you pick, put the due date of each payment in the contract as a calendar date or a named event. "Upon completion" is where disputes live.

3. WHEN A COMPANY SAYS THEY ONLY PAY AGAINST A PO

Ask for the PO number before you start, not before you invoice. One reader keeps a line at the top of every statement of work: "Work begins when a PO number or written approval is received." Nobody has objected to it yet.

4. LINKS

- A deposit clause you can paste into a statement of work: https://netthirty.example/templates/deposit
- Cash buffers for irregular income, with a worksheet: https://netthirty.example/guides/buffer
- Reader thread: do you bill expenses at cost? https://netthirty.example/community/expenses-at-cost

See you on the 29th.
Carmen
""", *NET_THIRTY[1:]),
        issue("nm-020", "Net Thirty #60: The 30-minute quarter-end money review", *NET_THIRTY[:1], "09-29 07:03", """
NET THIRTY
Issue 60

Hello,

Quarter-end is a good excuse for a thirty-minute review. Set a timer. Here is the order we use.

1. LIST EVERY SUBSCRIPTION YOU PAY FOR

Open your card statements for the last three months and write down each recurring charge. Domain, hosting, email, design tools, a scheduling link, that analytics thing you tried once. For each one, answer: did I use it in the last 30 days? Cancel what you did not. One reader found a hosting plan for a project she shipped two years ago.

2. AGE YOUR RECEIVABLES

Group unpaid invoices by how late they are: current, 1 to 15 days, 16 to 30, 31 plus. The goal is not shame. It is to see whether the late ones belong to one client (a process problem on their side) or to many (a process problem on yours).

3. CHECK YOUR CONCENTRATION

If one client is more than 40 percent of the quarter's revenue, write that number down and look at it again next quarter. No need to panic. You just want it to be a decision, not a surprise.

4. PAY YOURSELF ON PURPOSE

Move money from the business account to your personal account on a fixed day with a fixed rule, even if the rule is "whatever is left after the buffer." Irregular income gets easier when the transfer is boring.

5. WRITE DOWN ONE CHANGE

Only one. Last quarter's change for the editor was "invoice on acceptance, not on delivery." It moved her average payment day by about nine days.

LINKS

- The 30-minute review as a printable checklist: https://netthirty.example/templates/quarter-end
- Why we stopped using the word "retainer": https://netthirty.example/notes/retainers
- Spreadsheet: receivables aging in five columns: https://netthirty.example/templates/aging

Carmen
""", *NET_THIRTY[1:]),
    ]


def _automation():
    return [
        issue("nm-002", "Quiet Workflow 212: Three ways orders reach a spreadsheet", *WORKFLOW[:1], "09-02 06:30", """
QUIET WORKFLOW
Issue 212 | Automation for small teams without an IT department

Hi,

Four people wrote in this week with the same question: "Orders come in from three places and I type them into a spreadsheet. Now what?" So let's walk through it.

THREE WAYS ORDERS REACH A SPREADSHEET

1. A form that writes to Google Sheets. Boring, reliable, and the right answer more often than people want to admit. If you can send customers to a form, do that first.

2. Email parsing. Forward order emails to a mailbox a script reads, pull out the fields, append a row. Works when the senders are other businesses writing in a consistent template. Falls apart with humans.

3. Text messages or DMs. Customers love them and they are the messiest input. You get "two dozen of the usual, pick up sat" with no name attached. This is where people reach for a language model, and where you need a confirmation step (more on that in issue 215).

THE ONE THAT FAILS

Copy and paste by a person at the end of a long day. It works until the week somebody is out sick and the sheet is three days behind. If a human is the integration, schedule a replacement for the human.

CASE FILE: A GROOMING SALON IN TULSA

Waggle Grooming took bookings by phone, text and web form, and the receptionist copied everything into one calendar sheet. Before automating anything they added a "source" column and a "raw message" column. When a booking looked wrong, they could see exactly what the customer had written. Debugging time dropped, and so did the arguments.

TOOL OF THE WEEK

Apps Script time-driven triggers. A few lines of script, run every five minutes, can tidy new rows (trim whitespace, normalize phone numbers) without any extra service. Walkthrough: https://quietworkflow.example/tools/apps-script-triggers

Reply to this email if you have a workflow you'd like us to dissect.
Tobias
""", *WORKFLOW[1:]),
        issue("nm-006", "Quiet Workflow 213: When the automation breaks at 2 a.m.", *WORKFLOW[:1], "09-09 06:30", """
QUIET WORKFLOW
Issue 213

Hi,

Every automation fails eventually. The ones that hurt are the ones that fail quietly. This week: how to find out before your customers do.

1. A HEARTBEAT, NOT A HOPE

If a job should run every hour, have it write a timestamp somewhere (a cell, a file, a tiny status page). Then have a second, separate check that complains when the timestamp is more than two hours old. The second check is the one that saves you, because a dead job cannot report its own death.

2. A DEAD-LETTER TAB

When a row cannot be processed, do not skip it and do not crash. Move it to a tab called "needs a human" with the error message beside it. A florist we spoke to lost six orders over a month because her script quietly dropped any row with a blank delivery field. A dead-letter tab would have shown all six on day one.

3. MAKE REPLAYS SAFE

Sooner or later you will run the same batch twice. Give every record a stable ID (order number plus source, for example) and check for it before you write. If running the job twice gives the same result as running it once, you can retry without fear.

4. ONE LOUD ALERT BEATS TEN QUIET ONES

Alert fatigue is real. Pick one channel (a text, a Slack message to yourself), send only failures, and include what failed and what to do. "Order sync failed: 3 rows in needs-a-human, see sheet" is useful. "Error 500" is not.

READER CORNER

Rosalind from a bike repair shop in Ohio shared her checklist for new automations: who gets told when it fails, where the raw input lives, how to turn it off. Three lines. We printed it. https://quietworkflow.example/community/three-line-checklist

Tobias
""", *WORKFLOW[1:]),
        issue("nm-011", "Quiet Workflow 214: The spreadsheet is a fine database (until it isn't)", *WORKFLOW[:1], "09-16 06:30", """
QUIET WORKFLOW
Issue 214

Hi,

Whenever we run a piece on spreadsheets someone writes in to say "just use a real database." Sometimes they are right. Here is how to tell.

WHEN GOOGLE SHEETS IS ENOUGH

- A few thousand rows, a handful of editors, and the people who read the data are not developers.
- Your staff already live in it. Adoption is a feature.
- You want a human to be able to fix a bad row with a click and an undo.

SIGNS YOU'RE OUTGROWING IT

1. Quota errors. If your script starts seeing "429 Quota exceeded for quota metric 'Read requests'", you are reading cell by cell. Batch the reads (one call for the whole range) before you decide anything else.
2. Two things write to the same row at once and the last writer wins. This one is subtle and shows up as "sometimes a number is wrong."
3. You have formulas that reference formulas that reference another file. One broken link and half the sheet turns to #REF!.
4. Somebody asks "can we see this by customer over the last two years?" and the answer takes an afternoon.

THE MOVE, WHEN YOU MAKE IT

You do not have to throw the sheet away. A common pattern: keep the sheet as the friendly face for staff, put Postgres underneath for anything that needs history or reporting, and sync one direction. The sheet shows; the database remembers.

LINKS

- Batch reads and writes with the Sheets API: https://quietworkflow.example/tools/sheets-batch
- A reader's migration notes, sheet to Postgres in a weekend: https://quietworkflow.example/community/sheet-to-postgres
- Poll results: how many rows is too many? https://quietworkflow.example/polls/row-count

Tobias
""", *WORKFLOW[1:]),
        issue("nm-016", "Quiet Workflow 215: Letting a language model read messy messages, carefully", *WORKFLOW[:1], "09-23 06:30", """
QUIET WORKFLOW
Issue 215

Hi,

The most common request in our inbox now is some version of: "Can an AI read my customers' texts and put them in the right place?" Often, yes. Here is how to do it without waking up to a mess.

1. ASK FOR STRUCTURE, THEN CHECK IT

Have the model return a fixed shape: customer name, items, quantity, pickup day, anything unclear. Then validate in code. Is the quantity a number? Is the item on your actual menu? Does the date exist? Treat the model's output like form input from a stranger.

2. SAY WHAT YOU UNDERSTOOD

The single most effective safety step is a confirmation message back to the customer: "Got it, 2 dozen rolls, pickup Saturday. Reply if that's wrong." It turns a silent misread into a two-second fix.

3. KEEP THE RAW MESSAGE

Store exactly what came in, untouched, next to what you extracted. When something goes wrong (and it will, around the third week), the raw text tells you whether the model or the instructions were at fault.

4. HAVE A HUMAN LANE

Some messages should never be auto-handled: complaints, allergy questions, anything with the word "refund." Route them to a person and say so in the reply.

5. BUILD A SMALL TEST SET

Pull 50 real messages from your own history, write down what each one means, and run every change to your prompt against them. Fifty is enough to notice a regression. Most teams skip this step and regret it.

LINKS

- Our prompt template for order parsing: https://quietworkflow.example/templates/order-parsing
- A reader's test set (anonymized): https://quietworkflow.example/community/test-set
- Last year's piece, still true: https://quietworkflow.example/issues/150

Tobias
""", *WORKFLOW[1:]),
        issue("nm-021", "Quiet Workflow 216: 'Next Friday' and other date problems", *WORKFLOW[:1], "09-30 06:30", """
QUIET WORKFLOW
Issue 216

Hi,

Dates are the quiet villain of small automations. Last month's mailbag had five date bugs, no two the same. Here are the patterns.

THE RELATIVE DATE TRAP

"Next Friday" means different things to different people, and on a Wednesday it can mean two different days. When a message says "this Friday" or "next week," convert it using the date the message was sent, not the date your script happens to run. Store the original words too.

THE TIME ZONE TRAP

A reader's reminder script ran on a server set to UTC, so a "send at 9" job landed at 3 in the morning local time. Fix: keep the business's time zone as explicit configuration and convert once, at the edges. Inside the system, use UTC or timezone-aware values only.

THE DAYLIGHT SAVING TRAP

Twice a year, "same time tomorrow" is not 24 hours later. If you add 24 hours to a timestamp instead of adding a day to a local date, you will be an hour off for a week. Clocks in most of the U.S. change on the first Sunday of November. Put it on your calendar now and test before it arrives.

THE FORMAT TRAP

03/04 is March 4th in the U.S. and the 3rd of April nearly everywhere else. If your inputs come from a form, use a date picker. If they come from text, ask for the month spelled out.

READER BUG OF THE MONTH

A tutoring business exported schedules to a spreadsheet and the date column silently turned into text on every row that began with a zero. The reminder script skipped them. Nobody noticed for nine days. The fix was one format setting and a check that counts rows processed against rows exported.

LINKS

- A checklist for date handling in automations: https://quietworkflow.example/templates/date-checklist
- Time zones explained with a pizza shop: https://quietworkflow.example/issues/166
- Next issue: backups nobody tests.

Tobias
""", *WORKFLOW[1:]),
    ]


def _freight():
    return [
        issue("nm-005", "The Backhaul: Spot rates soften as peak season stalls", *BACKHAUL[:1], "09-04 06:00", """
THE BACKHAUL
Vol. 9, No. 33 | Freight and logistics news, Fridays

SPOT RATES SOFTEN AS PEAK SEASON STALLS

National dry van spot rates averaged $2.04 per mile this week, down three cents from the week before, according to the broker index we track. Reefer held roughly flat at $2.46. Flatbed was the outlier, up a nickel on construction demand in the Mountain West.

Brokers told us the usual early-fall surge is running late. Retail import volume is still arriving, but shippers are spacing out pickups instead of dumping them into one week. "It's a drip, not a flood," one Midwest broker said.

DETENTION: WHO GETS PAID, AND HOW

A survey of 400 carriers found 62 percent billed detention at least once last quarter, but only 38 percent were paid in full. The most common reason for a short pay was not the dwell time itself but missing timestamps. Carriers whose drivers photographed the check-in sheet and kept the bill of lading stamped with in and out times collected far more often.

Takeaway for small carriers: the paperwork is the claim. If the in-time is not on a document, the invoice will be negotiated.

PAPERWORK WATCH

Invoice mismatches remain the top reason for slow payment between carriers and shippers. The usual suspects are a rate confirmation that says one thing and an invoice that says another, a fuel surcharge figured on the wrong week's index, and lumper receipts that never made it into the packet. Several mid-size shippers now reject invoices automatically when the load number on the PDF does not match their TMS.

Meanwhile, more carriers are trying OCR to read invoices and bills of lading. Accuracy on clean scans is above 95 percent in vendor demos. On documents that were faxed and then scanned, nobody wants to quote a number.

IN BRIEF

- Tamarack Line Haul named a new CFO after eight months with the seat empty.
- The port at Savannah reported its busiest August on record.
- Redfern Freight is acquiring Alder & Cole Logistics, terms undisclosed.

That is the week. Forward this to a dispatcher you like.
The Backhaul desk
""", *BACKHAUL[1:]),
        issue("nm-008", "The Backhaul: Freight audit season, where invoice errors hide", *BACKHAUL[:1], "09-11 06:00", """
THE BACKHAUL
Vol. 9, No. 34

FREIGHT AUDIT SEASON: WHERE INVOICE ERRORS HIDE

With fourth-quarter budgets being set, shippers are taking a harder look at what they paid carriers in the first three quarters. Auditors we spoke to say a typical mid-size shipper overpays between one and three percent on freight invoices, mostly through errors that are tedious rather than clever.

The top five, in the order auditors say they find them:

1. Duplicate invoices, usually the same load billed once by the carrier and again after a rebill.
2. Accessorial charges (liftgate, residential delivery, detention) with no supporting proof of delivery.
3. Fuel surcharge calculated on the wrong index week.
4. Rate drift, where the invoiced linehaul does not match the contracted lane rate.
5. Weight or class corrections applied after the fact with no reweigh ticket.

THE DOCUMENT PROBLEM

Almost every audit stalls on the same thing. The invoice is a PDF, the bill of lading is a different PDF, the rate confirmation is an email, and the TMS record holds a fourth version. Someone has to line up four sources by hand. A logistics manager at a Texas shipper told us her team spends about two days a month on exactly this.

Software vendors are circling the problem. Most pitches combine OCR with field extraction and a match against the TMS. The ones that survive a pilot tend to do one boring thing well, such as pulling the load number, carrier name, invoice total and ship date into a clean row, and flagging what they could not read.

NUMBERS TO KNOW

- Average days to pay a carrier invoice (reported): 38
- Share of invoices needing one or more corrections: 14 percent
- Share of audits that found duplicate payments: 41 percent

Webinar next Thursday: 'Matching invoices to rate confirmations without losing your mind.' https://backhaulbrief.example/events/matching-webinar

The Backhaul desk
""", *BACKHAUL[1:]),
        issue("nm-014", "The Backhaul: The paperwork gap between brokers and carriers", *BACKHAUL[:1], "09-18 06:00", """
THE BACKHAUL
Vol. 9, No. 35

THE PAPERWORK GAP BETWEEN BROKERS AND CARRIERS

Ask a small carrier what slows its cash flow and the answer is rarely rates. It is paperwork. Ask a broker the same question and you hear the mirror image: carriers send documents in whatever form they have, and every load becomes a small data entry project.

WHAT BROKERS WANT IN THE PACKET

- Signed bill of lading, legible, with the receiver's stamp
- Invoice with the broker's load number, not just the carrier's own
- Lumper or scale receipts when charges are claimed
- Proof of delivery with a time

A broker survey found 22 percent of packets arrive missing at least one of the four, and each incomplete packet adds about six days to payment.

EDI VS PDF

Large carriers send electronic invoices (EDI 210) that drop straight into a payables system. Everyone else sends PDFs. The industry expected PDFs to fade out by now. They have not, and several vendors have pivoted from "replace the PDF" to "read the PDF," extracting fields into the CSV or API format the receiving system wants.

TMS NEWS

A midmarket TMS vendor announced a new document inbox that auto-matches uploaded invoices to open loads. Early adopters say it works well when load numbers are printed clearly and badly when they are handwritten. The vendor says handwriting support is coming.

MERGERS

Redfern Freight closed its purchase of Alder & Cole Logistics. Tamarack Line Haul says it will keep operating its regional fleet under its own name.

The Backhaul desk
""", *BACKHAUL[1:]),
        issue("nm-018", "The Backhaul: Diesel, drivers and a very busy port", *BACKHAUL[:1], "09-25 06:00", """
THE BACKHAUL
Vol. 9, No. 36

DIESEL, DRIVERS AND A VERY BUSY PORT

Diesel averaged $3.97 a gallon nationally, up 11 cents in two weeks. Fleets with fuel surcharge tables tied to the weekly index are largely protected. Owner-operators on flat per-mile deals are not.

DRIVER SUPPLY

New entrant-driver training enrollment is up 9 percent over last year, according to a trade association count, but carriers say the pipeline is still thin for team and hazmat work. Several mid-size fleets are raising sign-on bonuses; others are putting that money into lower-turnover tactics like predictable home time and faster pay.

One carrier we talked to now pays drivers within 48 hours of delivery, which depends on getting the bill of lading and proof of delivery back from the driver quickly. Their fix was a phone app that scans the paperwork at the dock. Pay-day questions to the office dropped by half.

PORT WATCH

The Port of Savannah handled a record container volume for August. Truckers reported average turn times of 62 minutes inside the gates, up from 48 in July. Dwell fees are a talking point again.

FINE PRINT

A new federal rule on electronic bills of lading takes effect next year. If you handle any paper BOLs, expect questions from your customers. A summary is at https://backhaulbrief.example/explainers/e-bol

Classifieds: https://backhaulbrief.example/classifieds

The Backhaul desk
""", *BACKHAUL[1:]),
    ]


def _python():
    return [
        issue("nm-004", "Walrus Digest #301: fixtures you overuse, uv in CI, table extraction", *WALRUS[:1], "09-03 07:00", """
WALRUS DIGEST
Issue 301 | A weekly roundup for people who write Python for a living

Morning,

Short issue this week. Three things worth your coffee.

1. FIXTURES YOU'RE OVERUSING

A pytest fixture that does five things is a hidden dependency. The piece by Maren Haugen makes the case for small fixtures that each build one object, composed explicitly in the test. Her rule of thumb: if you have to open conftest.py to understand a test, the fixture has too much in it. https://walrusdigest.example/links/small-fixtures

2. UV IN CI, ONE YEAR LATER

A team of eleven moved their pipelines from pip to uv and wrote up the numbers: cold install went from about 90 seconds to under 10, and cache hit rates stopped being a coin flip. The surprise was lock file diffs in code review. They became readable, so people actually read them. https://walrusdigest.example/links/uv-in-ci

3. PULLING TABLES OUT OF PDFS WITH PDFPLUMBER

A tutorial that goes past the README. It covers the difference between lines-based and text-based table strategies, how to crop to a region before extracting, and why you should print `page.extract_words()` before you decide anything is broken. The author processes carrier-style invoices and has a nice trick for stitching a table that wraps across pages. https://walrusdigest.example/links/pdfplumber-tables

QUICK LINKS

- Pydantic models at API boundaries, dataclasses inside: https://walrusdigest.example/links/models-at-boundaries
- A tiny CLI for diffing two CSVs by key: https://walrusdigest.example/links/csvdiff
- "I deleted my mocks and my tests got better": https://walrusdigest.example/links/fewer-mocks

ONE OPINION

If your project has a `utils.py` over 300 lines, it has a missing module in it. Name the thing.

Next week: FastAPI background tasks versus a real queue.

Until Thursday,
The Walrus Digest team
""", *WALRUS[1:]),
        issue("nm-007", "Walrus Digest #302: background tasks vs a queue, zoneinfo, OCR on scans", *WALRUS[:1], "09-10 07:00", """
WALRUS DIGEST
Issue 302

Morning,

1. FASTAPI BACKGROUND TASKS VS A REAL QUEUE

Background tasks are great for "send the email after you return the response." They are risky for anything you cannot afford to lose, because they live and die with the worker process. A deploy in the middle of a task drops the task. The post draws the line clearly: if a lost job means a lost customer order, use a queue (even a database table with a status column and a polling worker). If a lost job means a missed analytics ping, background tasks are fine. https://walrusdigest.example/links/background-vs-queue

2. ZONEINFO AND THE HOUR THAT DOES NOT EXIST

A walk through the standard library's zoneinfo, with a focus on the two bad hours a year: the one that doesn't exist in spring and the one that happens twice in fall. Includes a test helper that freezes time on both transitions. If you store local times without a zone, you will learn this lesson the hard way. https://walrusdigest.example/links/zoneinfo-dst

3. TESSERACT ON SCANNED INVOICES

Practical settings for getting usable text from low-quality scans: binarize and deskew first, set the page segmentation mode to match the layout, and restrict the character set when you know a field is digits only. The author also recommends cropping the invoice number region and OCR-ing it on its own. Accuracy on that field jumped. https://walrusdigest.example/links/tesseract-scans

RELEASES WORTH NOTICING

- A new minor release of Ruff adds rules around implicit string concatenation in collections.
- pytest's latest release tightens how it reports fixture teardown errors.
- A popular HTTP client library deprecated a long-standing retry argument.

SHORT TAKES

- Don't parse dates with a hand-written regex. Use a parser, then test it against your ugliest real input.
- `subprocess.run(..., check=True)` is a gift. Use it.
- Logging the full request body? Redact before you write it down.

Until next week,
The Walrus Digest team
""", *WALRUS[1:]),
        issue("nm-013", "Walrus Digest #303: typed boundaries, scrubbing logs, console scripts", *WALRUS[:1], "09-17 07:00", """
WALRUS DIGEST
Issue 303

Morning,

1. TYPEDDICT, DATACLASS, PYDANTIC: WHERE EACH GOES

A clear framework from a conference talk transcript. TypedDict describes dictionaries you do not control (JSON in, JSON out). Dataclasses are for plain values inside your code. Pydantic belongs at the edge, where untrusted input becomes trusted data, because validation is the whole point. Mixing them up produces either slow code or leaky code. https://walrusdigest.example/links/three-ways-to-model

2. SCRUBBING LOGS BEFORE THEY LEAVE YOUR MACHINE

Developers paste logs into chat tools and bug trackers all day. A short guide on stripping secrets, emails, IP addresses and tokens first, with a comparison of regex-based and entropy-based approaches. The conclusion: regex catches the known shapes, entropy catches the weird ones, and you want both. Also, always diff the output before you paste. https://walrusdigest.example/links/scrub-your-logs

3. CONSOLE SCRIPTS IN PYPROJECT.TOML

If your CLI still has a `bin/` script, here is how to replace it with an entry point, test it with `python -m`, and keep install times fast. Pairs well with this week's other article on shipping single-file tools. https://walrusdigest.example/links/console-scripts

QUICK LINKS

- A year of running FastAPI behind a small VPS: https://walrusdigest.example/links/fastapi-small-vps
- Webhook signature checks: the ten-line version: https://walrusdigest.example/links/webhook-signatures
- A beginner's tour of `pathlib`: https://walrusdigest.example/links/pathlib-tour

The Walrus Digest team
""", *WALRUS[1:]),
        issue("nm-017", "Walrus Digest #304: subprocess traps, date parsing, free-threading", *WALRUS[:1], "09-24 07:00", """
WALRUS DIGEST
Issue 304

Morning,

1. FIVE SUBPROCESS TRAPS

Shell=True with user input, forgetting a timeout, reading stdout and stderr in the wrong order, zombie children, and assuming `PATH` matches your terminal. Each has a two-line fix in the post. https://walrusdigest.example/links/subprocess-traps

2. DATE PARSING WITHOUT TEARS

The author compares dateutil, the standard library, and a dedicated natural-language date parser on 200 real messages ("tmrw 3pm", "the 5th", "next thurs"). No library parsed everything. The lesson: parse what you can, and when the result is ambiguous, ask the sender instead of guessing. One more thing worth stealing: log the raw string beside the parsed value so you can find the pattern later. https://walrusdigest.example/links/date-parsing-compared

3. FREE-THREADED PYTHON: WHERE THINGS STAND

A status report on the experimental no-GIL builds. Pure-Python code mostly just works, C extensions are catching up, and the performance picture depends on your workload. Not a reason to change anything this quarter. Good to understand before you have to. https://walrusdigest.example/links/free-threading-status

THE NEXT RELEASE

The next major Python release is in its release-candidate stage. Check your CI matrix and test against it now, while fixes are cheap.

QUICK LINKS

- Stop mocking the clock; use a fake one: https://walrusdigest.example/links/fake-clock
- Reading the traceback bottom to top: https://walrusdigest.example/links/read-tracebacks
- An argparse cheat sheet for people who prefer click: https://walrusdigest.example/links/argparse-cheats

The Walrus Digest team
""", *WALRUS[1:]),
    ]


def _dental():
    return [
        issue("nm-003", "The Recall Letter #77: No-shows by day of week, and what 140 practices reported", *RECALL[:1], "09-02 07:40", """
THE RECALL LETTER
Front-desk and practice management notes for dental offices | Issue 77

NO-SHOWS BY DAY OF WEEK: WHAT 140 PRACTICES REPORTED

Our spring survey asked practices to export 90 days of appointments and send back four numbers: scheduled, kept, cancelled, no-show. 140 practices replied. The median no-show rate was 6.1 percent, with a range from under 2 to over 15.

The weekday pattern was less dramatic than office folklore suggests. Mondays were worst at 7.4 percent, Thursdays best at 4.9. The bigger spread was by visit type. New patient visits were missed at roughly double the rate of recall cleanings.

WHAT CEDAR ROW DENTAL CHANGED

Cedar Row Dental, a four-chair practice in Columbus, Ohio, moved from a phone call two days out to a text with a one-tap confirm link. Office manager Lena Fisk told us: "The calls got answered about a third of the time. The texts get answered inside the hour." Their no-show rate went from 8.2 to 5.5 percent over two quarters with the same staff.

Her two caveats. One: texts to landline numbers fail silently, so they still call anyone whose number comes back flagged. Two: patients who reply with a question ("can I come at 3 instead?") need a person to see the reply the same day, or the text feels worse than a voicemail.

SCRIPT SWAP

Old: "This is a reminder that you have an appointment on Thursday. Please call if you cannot make it."
New: "Hi Maria, it's Cedar Row Dental. We have you down for Thursday afternoon. Tap here to confirm or pick a new time: [link]"

The second one asks for something specific and makes the easy answer one tap long.

WORTH A CLICK

- The full survey tables (members): https://recallletter.example/survey/spring
- A cancellation policy you can adapt: https://recallletter.example/templates/cancellation-policy
- Webinar replay, filling same-day openings from a waitlist: https://recallletter.example/webinars/waitlist

Until the 16th,
The Recall Letter team
""", *RECALL[1:]),
        issue("nm-012", "The Recall Letter #78: Reminder texts that get replies", *RECALL[:1], "09-16 07:40", """
THE RECALL LETTER
Issue 78

REMINDER TEXTS THAT GET REPLIES

We collected 31 reminder templates from member practices and read them all in one sitting. A few patterns separate the ones that get answered from the ones that get ignored.

1. THE PRACTICE NAME COMES FIRST. Patients get texts from numbers they do not know. "Lakemont Pediatric Dentistry: Ava's visit is Tuesday" gets read. "Reminder: appointment Tuesday" looks like spam.

2. ONE ASK PER MESSAGE. "Reply 1 to confirm or 2 to reschedule" outperformed templates that offered four options. Practices that offered a phone number as well saw more calls and fewer replies.

3. SAY WHO THE PATIENT IS. Parents get texts for several children. Put the child's first name in the message, or you will field a call that begins "which one?"

4. A SECOND TOUCH BEATS A BETTER FIRST ONE. Practices that sent a short follow-up to anyone who had not replied saw the biggest lift, more than any rewrite of the first message.

5. NEVER GO PAST THREE. Three touches in total was the ceiling before opt-outs climbed.

WHEN A PATIENT TEXTS BACK SOMETHING ODD

Sandpiper Orthodontics logged a month of replies. About 8 percent were not "yes" or "no": questions about parking, cost, whether a parent could bring a sibling, "wrong number." Their front desk now checks the reply inbox before each opening and again before close. Unanswered replies were the main source of one-star reviews.

TEMPLATE LIBRARY (members): https://recallletter.example/templates/reminders
Next webinar: Handling the angry no-show call. https://recallletter.example/webinars/angry-no-show

The Recall Letter team
""", *RECALL[1:]),
        issue("nm-022", "The Recall Letter #79: After the missed visit", *RECALL[:1], "09-30 07:40", """
THE RECALL LETTER
Issue 79

AFTER THE MISSED VISIT: WHAT PATIENTS ARE TOLD, AND WHAT WORKS

Most offices have a reminder process. Far fewer have a process for what happens after the patient does not show up. This issue is about that gap.

THE SAME-DAY FOLLOW-UP

Four practices we spoke to send a short note within two hours of a missed visit: "We missed you today. Want to find a new time? Reply here or call us." Rebooking within 14 days was roughly twice as likely as in offices that did nothing. The tone matters. Notes that mentioned a fee in the first line were answered less than half as often.

THE WAITLIST

A waitlist only works if it is short and current. Practices that texted a waitlist of five to ten people, first reply wins, filled about one in three same-day openings. Practices with a list of 80 people filled almost none, because half the numbers were stale.

SCRIPT: THE CALLBACK

"Hi, this is Ellis at the front desk of Elm & Fifth. We had you on the schedule this morning and wanted to make sure everything is okay. I can offer Tuesday at 8 or Thursday at 2."

PRACTICE SOFTWARE CORNER

Several readers asked about nightly exports from their practice software to feed other tools. Most systems can produce a CSV on a schedule. The common trap is date formats: the same export can use MM/DD/YYYY in one report and YYYY-MM-DD in another. Check a sample before wiring anything to it. https://recallletter.example/guides/exports

LINKS

- Cancellation language that patients accept: https://recallletter.example/templates/cancellation-policy
- Q4 scheduling checklist: https://recallletter.example/guides/q4-schedule
- Member Q&A, October: https://recallletter.example/events/member-qa

The Recall Letter team
""", *RECALL[1:]),
    ]


def _bakery():
    return [
        issue("nm-009", "The Proofing Drawer: Fall menu planning, apple, pear, and the pumpkin question", *PROOFING[:1], "09-14 06:45", """
THE PROOFING DRAWER
For independent bakeries | Fall menu issue

FALL MENU PLANNING: APPLE, PEAR, AND THE PUMPKIN QUESTION

Every September, bakeries split into two camps. One has been selling pumpkin since the 1st. The other refuses to touch it until the first cold morning. The data, from a poll of 212 shops: 58 percent launched a pumpkin item before the autumn equinox, and shops that did saw a 9 percent lift in morning sales for the first three weeks, then a plateau.

THE SHORT LIST THAT SELLS

- Apple: hand pies, cider-glazed donuts, galettes. Shops that rotated the apple variety weekly got repeat visits.
- Pear: ginger-pear scones outsold plain pear tarts two to one at three shops.
- Squash: savory focaccia with roasted squash and sage is the sleeper hit of the season.
- Spiced anything: chai and cardamom are easier to run than pumpkin pie spice, and customers think they are fancier.

MENU CARD TIPS

Rye Street Bakehouse in Duluth prints its menu card weekly and writes only the items that are guaranteed. A chalkboard "ask us" line holds the experiments. Their sold-out rate dropped, and so did staff stress at the counter.

SUPPLY WATCH

Butter prices are up about 6 percent over last year, with cultured butter up more. Flour contracts for the holiday season are being signed now. A mill in Kansas is offering fixed pricing through January to shops that commit by month's end.

Next week: pre-orders without the chaos.

Full menu planning worksheet: https://proofingdrawer.example/worksheets/fall-menu
Wholesale price list template: https://proofingdrawer.example/worksheets/wholesale-list

Warmly,
Nell at The Proofing Drawer
""", *PROOFING[1:], labels=PROMO),
        issue("nm-015", "The Proofing Drawer: Pre-orders without the chaos", *PROOFING[:1], "09-21 06:45", """
THE PROOFING DRAWER
For independent bakeries | Holiday prep issue

PRE-ORDERS WITHOUT THE CHAOS

The holidays are coming whether you are ready or not. If you plan to take pre-orders for pies, rolls and centerpiece cakes, the decisions you make now determine whether the week before is joyful or a scramble.

DECIDE THREE THINGS

1. How many can you actually bake? Count oven racks, not hope. Set a cap per item per day and refuse to sell past it.
2. When do pre-orders close? Practices vary. Rye Street closes the Sunday before. A Portland shop closes eight days ahead for pies and three for rolls, because rolls are simpler. The point is to write it down and say it everywhere.
3. How do people order? Online forms, a phone line, in person, and now text messages. Several shops told us texting is how regulars prefer to order, and it is also the least structured. The shops doing it well confirm every order by text back, with the item, quantity and pickup day, and keep a spreadsheet as the single source of truth.

PICKUP WINDOWS

Group customers into windows (morning, midday, late). A shop in Asheville says splitting the day into three windows cut the line at the door by half. Hand out numbered bags so the right order is easy to find.

DEPOSITS

Deposits for large orders reduce no-shows. Shops are split on small ones. The most common compromise: prepay for anything over a set size, pay at pickup for the rest.

WHAT TO PRINT

A single-page prep sheet per day with item, quantity, name, pickup window, and a checkbox. Production starts from that page, not from anyone's memory.

A free pre-order planner (spreadsheet): https://proofingdrawer.example/worksheets/preorder-planner
Last year's holiday issue: https://proofingdrawer.example/issues/holiday-prep

Warmly,
Nell
""", *PROOFING[1:], labels=PROMO),
        issue("nm-019", "The Proofing Drawer: How to say yes to the coffee shop (wholesale basics)", *PROOFING[:1], "09-28 06:45", """
THE PROOFING DRAWER
For independent bakeries | Wholesale issue

HOW TO SAY YES TO THE COFFEE SHOP

A neighborhood cafe asks if you can supply them with croissants, muffins and a sourdough loaf. The answer is usually yes. The question is how to say yes without wrecking your retail day.

START WITH A STANDING ORDER

A standing order (the same items on the same days) is easier to produce than one-off requests and easier to forecast. Ask for a small commitment: three days a week, set quantities, with changes by a certain time the day before.

KNOW YOUR PAR LEVELS

Ask the cafe what they sell on a normal day. Then ask for their par, the number they want on the shelf at open. Bake to the par, not to the average. Bakeries that skipped this step got angry calls the first Saturday.

DELIVERY, NOT PICKUP

If you can, deliver. Cafes that pick up on their own schedule create bottlenecks in your back door. A fixed delivery window is better for both sides. A bakery in Boulder told us the delivery day became the busiest of the week, and it is the only one they schedule a dedicated driver for.

TERMS AND PAPERWORK

- Invoice weekly, not per delivery. Net 15 is common for small accounts.
- Put a short wholesale agreement on one page: items, days, cutoff for changes, payment terms.
- Return and credit policy. Decide ahead of time whether you credit stale product.

PRICING

Wholesale is typically 40 to 50 percent below retail. That looks scary until you remember you are not paying for front-of-house, packaging, or a glass case.

Wholesale one-pager template: https://proofingdrawer.example/worksheets/wholesale-agreement
Poll: how many wholesale accounts do you have? https://proofingdrawer.example/polls/accounts

Warmly,
Nell
""", *PROOFING[1:], labels=PROMO),
    ]


GITHUB = "GitHub <notifications@github.com>"
CALENDLY = "Calendly <notifications@calendly.com>"


def _tools():
    return [
        single("nm-023", "Google Workspace: Your invoice for marlowworks.dev is available",
               "Google Workspace <payments-noreply@google.com>", "09-01 05:12", """
Hello Jules Marlow,

Your Google Workspace invoice is ready.

Invoice number: 5084417390
Billing period: Aug 1, 2026 - Aug 31, 2026
Domain: marlowworks.dev
Subscription: Business Starter (1 user)
Total: $8.40 USD
Payment method: Visa ending in 4417, charged Sep 1, 2026

You can view and download your invoice in the Admin console under Billing > Payment history:
https://admin.google.com/ac/billing/paymenthistory

Thank you for choosing Google Workspace.
The Google Workspace Team
"""),
        single("nm-024", "[marlow-works/marlowworks-site] Run failed: CI - main (7c1e9ab)", GITHUB, "09-03 14:22", """
Run failed: CI - main (7c1e9ab)

Repository: marlow-works/marlowworks-site
Workflow: CI
Branch: main
Commit: 7c1e9ab  Fix alt text on case study images
Triggered by: push by jules-marlow

Jobs
  check    failed     38s
  build    skipped

Annotations
  check: Process completed with exit code 1.
  astro check: 2 errors, 0 warnings, 1 hint
    src/pages/blog/[...slug].astro:14:9 - error ts(2339): Property 'readingTime' does not exist on type 'CollectionEntry<"blog">'.

View workflow run: https://github.com/marlow-works/marlowworks-site/actions/runs/10482930110

--
You are receiving this because this workflow ran on a branch you pushed to.
Manage your GitHub Actions notifications: https://github.com/settings/notifications
"""),
        single("nm-025", "New Event: Priya Raman - 11:00am Thu, Sep 10, 2026 - Intro call (20 min)", CALENDLY, "09-04 10:15", """
Hi Jules Marlow,

A new event has been scheduled.

Event Type:
Intro call (20 min)

Invitee:
Priya Raman

Invitee Email:
priya@ramancabinetry.example

Event Date/Time:
11:00am - Thursday, September 10, 2026 (Mountain Time - US & Canada)

Location:
This is a Google Meet web conference. Join: https://meet.google.com/qkt-vxhm-dre

Invitee Time Zone:
Mountain Time - US & Canada

What would you like to talk about?
We run a small cabinet shop and our quotes live in email threads. I'm wondering if there is a way to get them into one place without buying a big system.

View event details: https://calendly.com/app/scheduled_events/user/me
""", NEWS),
        single("nm-026", "[jules-marlow/pinch] Dependabot alert: urllib3 (dev dependency)", GITHUB, "09-05 09:40", """
Dependabot alert

Repository: jules-marlow/pinch
Package: urllib3 (pip, transitive via requests, development dependency)
Severity: Moderate
Advisory: GHSA-q9fw-2h7c-8mjx
Affected versions: < 2.2.3
Patched version: 2.2.3

Summary: Request body not stripped after redirect from 303 status changes request method to GET.

Dependabot will open a pull request to update the package if it can do so safely. You can also review the alert in the Security tab:
https://github.com/jules-marlow/pinch/security/dependabot

--
You are receiving this because you have security alerts turned on for this repository.
Manage your security notifications: https://github.com/settings/notifications
"""),
        single("nm-027", "[marlow-works/copperline-docs] Run failed: CI - dependabot/pip/pdfplumber (c4d8e02)", GITHUB, "09-08 17:44", """
Run failed: CI - dependabot/pip/pdfplumber (c4d8e02)

Repository: marlow-works/copperline-docs
Workflow: CI
Branch: dependabot/pip/pdfplumber
Commit: c4d8e02  Bump pdfplumber from 0.11.4 to 0.11.7
Triggered by: pull_request opened by dependabot[bot]

Jobs
  lint          passed     21s
  tests (3.12)  failed     1m 04s

Annotations
  tests (3.12): Process completed with exit code 1.
    FAILED tests/test_cli.py::test_version_flag - AssertionError
    1 failed, 58 passed in 14.22s

View workflow run: https://github.com/marlow-works/copperline-docs/actions/runs/10511640228

--
You are receiving this because you are watching this repository.
Manage your GitHub Actions notifications: https://github.com/settings/notifications
"""),
        single("nm-028", "[Skerry Cloud] Disk usage warning: denver-vps-01 at 82%",
               "Skerry Cloud <alerts@skerrycloud.example>", "09-10 03:30", """
Hello,

The volume attached to denver-vps-01 has crossed 80% of its capacity.

Server: denver-vps-01 (region: us-central)
Volume: /dev/vda1  mounted at /
Used: 41.2 GB of 50 GB (82%)
Largest directories (last scan): /var/log, /var/lib/docker, /home

We will send another alert at 90%. You can resize the volume or clean up files from the console:
https://console.skerrycloud.example/servers/denver-vps-01/volumes

Alerts are configurable under Settings > Notifications.

Skerry Cloud Monitoring
"""),
        single("nm-029", "Canceled: Dennis Okafor and Jules Marlow - 2:00pm Mon, Sep 14, 2026", CALENDLY, "09-11 16:02", """
Hi Jules Marlow,

Dennis Okafor canceled this event.

Event Type:
Intro call (20 min)

Event Date/Time:
2:00pm - Monday, September 14, 2026 (Mountain Time - US & Canada)

Invitee:
Dennis Okafor
dennis@okafor-heating.example

Reason for cancellation:
Something came up at a job site, I will rebook when things calm down. Sorry!

This event has been removed from your Google Calendar.
"""),
        single("nm-030", "Your domain marlowworks.dev renews in 7 days",
               "Namelark Domains <billing@namelark.example>", "09-05 09:02", """
Hi Jules,

This is a heads-up that your domain is set to renew automatically.

Domain: marlowworks.dev
Renewal date: September 12, 2026
Renewal term: 1 year
Amount: $14.98 USD
Payment method: Visa ending in 4417
Auto-renew: ON
Privacy protection: included

No action is needed. If you want to turn off auto-renew or change the payment method, you can do it from your account:
https://namelark.example/account/domains

Questions? Reply to this email or visit https://namelark.example/help

Namelark Domains
"""),
        single("nm-031", "[marlow-works/harbor-reminders] Run failed: CI - chore/ruff-config (e91b3f7)", GITHUB, "09-13 03:07", """
Run failed: CI - chore/ruff-config (e91b3f7)

Repository: marlow-works/harbor-reminders
Workflow: CI
Branch: chore/ruff-config
Commit: e91b3f7  Tighten ruff config
Triggered by: push by jules-marlow

Jobs
  lint          failed     17s
  tests (3.12)  skipped

Annotations
  lint: Process completed with exit code 1.
    src/harbor_reminders/cli.py:88:101: E501 Line too long (104 > 100)
    src/harbor_reminders/cli.py:131:101: E501 Line too long (112 > 100)
    Found 2 errors.

View workflow run: https://github.com/marlow-works/harbor-reminders/actions/runs/10529187704

--
You are receiving this because this workflow ran on a branch you pushed to.
Manage your GitHub Actions notifications: https://github.com/settings/notifications
"""),
        single("nm-032", "Mailtray usage: 82 of 100 emails used this month",
               "Mailtray <noreply@mailtray.example>", "09-17 13:10", """
Hi Jules,

You have used 82 of the 100 emails included in your Free plan this month.

Plan: Free (100 emails per month)
Message stream: transactional-default
Sent so far in September: 82
Bounced: 1
Resets on: October 1

If you reach the limit, new messages will be paused until the next billing cycle. To keep sending, you can upgrade at any time:
https://mailtray.example/account/plan

Mailtray
"""),
        single("nm-033", "[GitHub] Payment Receipt for marlow-works", "GitHub <noreply@github.com>", "09-18 04:15", """
Receipt for marlow-works

Plan: GitHub Team (1 seat, monthly)
Amount: $4.00 USD
Date: Sep 18, 2026
Billing period: Sep 18 - Oct 17, 2026
Payment method: Visa ending in 4417

View billing settings: https://github.com/organizations/marlow-works/settings/billing

Thanks,
The GitHub Team
"""),
        single("nm-034", "[marlow-works/marlowworks-site] Dependabot alert: postcss", GITHUB, "09-21 08:55", """
Dependabot alert

Repository: marlow-works/marlowworks-site
Package: postcss (npm, transitive via astro)
Severity: Moderate
Advisory: GHSA-hx4v-p3nr-q6c8
Affected versions: < 8.4.31
Patched version: 8.4.31

Summary: PostCSS line return parsing error.

A pull request to update this dependency has been opened by Dependabot. You can review the alert in the Security tab:
https://github.com/marlow-works/marlowworks-site/security/dependabot

--
You are receiving this because you have security alerts turned on for this repository.
"""),
        single("nm-035", "New Event: Hannah Berg - 3:30pm Fri, Sep 25, 2026 - Intro call (20 min)", CALENDLY, "09-22 12:30", """
Hi Jules Marlow,

A new event has been scheduled.

Event Type:
Intro call (20 min)

Invitee:
Hannah Berg

Invitee Email:
hannah@bergdaughters-tile.example

Event Date/Time:
3:30pm - Friday, September 25, 2026 (Mountain Time - US & Canada)

Location:
This is a Google Meet web conference. Join: https://meet.google.com/lpa-wzcn-hgb

What would you like to talk about?
My bookkeeper suggested you. Customers text us requests for estimates and some of them get lost between phones. Not sure if you do this kind of thing.

View event details: https://calendly.com/app/scheduled_events/user/me
"""),
        email("nm-036", "Monitor alert: marlowworks.dev", [
            Mail("Hawkwatch <alerts@hawkwatch.example>", cast.JULES, "09-27 02:14", """
ALERT: marlowworks.dev is DOWN

Monitor: marlowworks.dev (HTTPS, every 1 minute)
Started: Sun, Sep 27, 2026 at 02:14 AM (America/Denver)
Reason: Connection timeout after 30 seconds
Checked from: Chicago, Frankfurt

View incident: https://hawkwatch.example/incidents/8841207
"""),
            Mail("Hawkwatch <alerts@hawkwatch.example>", cast.JULES, "09-27 02:19", """
RESOLVED: marlowworks.dev is UP

Monitor: marlowworks.dev (HTTPS, every 1 minute)
Resolved: Sun, Sep 27, 2026 at 02:19 AM (America/Denver)
Duration: 5 minutes
Reason for outage: Connection timeout after 30 seconds

View incident: https://hawkwatch.example/incidents/8841207
"""),
        ], labels=NEWS),
    ]


def _pitches():
    j = cast.JULES
    return [
        single("nm-037", "Quick question about marlowworks.dev", "Derek Lund <derek@rankforge.example>", "09-02 11:20", """
Hi Jules,

I was looking at marlowworks.dev and noticed it doesn't show up on the first page for searches like "Denver automation consultant" or "small business workflow automation." Your blog has good content, it just isn't getting seen.

We help consultants and small agencies grow organic traffic with no long contracts. Could I send over a short audit of your site? It's free and takes me about ten minutes.

Worth a look?

Derek Lund
Growth Partner, RankForge
https://rankforge.example
""", PROMO),
        email("nm-038", "Senior Backend Engineer (Python), remote, Series B", [
            Mail("Nadia Farrow <nadia@stackwellsearch.example>", j, "09-03 15:05", """
Hi Jules,

I'm a talent partner at Stackwell Search and came across your GitHub profile. I'm working with a Series B payments company that is hiring a Senior Backend Engineer on a fully remote team. Python, FastAPI, Postgres, a small platform group, strong engineering culture.

Your background building automation tools looks like a great match. Would you be open to a 15 minute chat this week?

Best,
Nadia Farrow
Talent Partner, Stackwell Search
https://stackwellsearch.example
"""),
            Mail(j, "Nadia Farrow <nadia@stackwellsearch.example>", "09-03 16:20", """
Hi Nadia,

Thanks for thinking of me. I run my own consulting practice and I'm not looking for a full-time role, so I'll pass. Good luck with the search.

Jules

On Thu, Sep 3, 2026 at 3:05 PM Nadia Farrow <nadia@stackwellsearch.example> wrote:
> Your background building automation tools looks like a great match.
> Would you be open to a 15 minute chat this week?
"""),
        ], labels=PERSONAL),
        email("nm-039", "Dedicated developers for your projects", [
            Mail("Arjun Mehta <arjun.m@codeharvest.example>", j, "09-08 09:12", """
Hello Jules,

I hope this email finds you well. I'm Arjun from CodeHarvest Labs. We provide dedicated Python and React developers at very competitive rates, with a team of 120+ engineers ready to scale your projects. If you ever have more work than you can handle, we can be your extension.

Can we schedule a quick call to discuss how we can help?

Regards,
Arjun Mehta
Business Development, CodeHarvest Labs
"""),
            Mail("Arjun Mehta <arjun.m@codeharvest.example>", j, "09-15 09:30", """
Hi Jules,

Just bumping this to the top of your inbox. Let me know if you would like to see sample profiles.

Regards,
Arjun
"""),
        ], labels=PROMO),
        single("nm-040", "Podcast invite: Founders in Flannel", "Mia Calloway <booking@foundersinflannel.example>", "09-10 14:40", """
Hi Jules,

I'm a producer at Founders in Flannel, a podcast about people building small, scrappy companies. We loved your site and think our audience would too.

We'd love to have you on as a guest for a 30 minute conversation. Guests get a custom landing page, a short audio clip, and promotion across our channels as part of our $295 production package.

If you're interested I can send over a few open dates.

Thanks,
Mia Calloway
Guest Booking, Founders in Flannel
""", PROMO),
        email("nm-041", "White-label dev partnership", [
            Mail("Kofi Mensah <kofi@brightline-digital.example>", j, "09-14 10:05", """
Hi Jules,

I run Brightline Digital, a design and marketing agency in Atlanta. We have more website and app requests than our in-house team can handle, and we're looking for a trusted developer to partner with on a white-label basis, so our clients see our brand and you do the build.

Would you be open to a quick call this week to see if there is a fit?

Kofi Mensah
Founder, Brightline Digital
"""),
            Mail(j, "Kofi Mensah <kofi@brightline-digital.example>", "09-14 12:30", """
Hi Kofi,

Thanks, but I work directly with my clients and don't do white-label work. Appreciate the note.

Jules
"""),
        ], labels=PERSONAL),
        single("nm-042", "Guest post for marlowworks.dev?", "Elise Fournier <elise@growthstacks.example>", "09-12 08:15", """
Hello,

I read your post on small business automation and really enjoyed it. I'm a freelance writer and would love to contribute a guest post to your blog. Some topic ideas:

- 7 Workflow Automation Tools Every Small Business Needs in 2026
- How AI is Transforming Customer Service
- The Ultimate Guide to Digital Productivity

All content is original and I only ask for a single backlink to a client site.

Let me know which topic you like,
Elise Fournier
Content Strategist, GrowthStacks
""", PROMO),
        email("nm-043", "Contract opportunity: Python and automation, 6 months, logistics", [
            Mail("Greg Pellham <greg.pellham@corvidstaffing.example>", j, "09-23 11:20", """
Hi Jules,

Greg Pellham with Corvid Staffing. I have a six month contract, with strong chance of extension, at a national 3PL. They need a Python developer to build document processing tools: PDFs in, structured data out, into their TMS. Hybrid, with some on-site time in Denver.

Your profile looks like a strong fit. Do you have a current resume I can share with the hiring manager?

Thanks,
Greg Pellham
Senior Recruiter, Corvid Staffing
"""),
            Mail(j, "Greg Pellham <greg.pellham@corvidstaffing.example>", "09-23 14:05", """
Hi Greg,

Thanks for reaching out. I don't take contract placements through agencies, so I'll pass.

Jules
"""),
        ], labels=PERSONAL),
        single("nm-044", "Booking 10 to 15 sales calls a month for consultants", "Sofia Reyes <sofia@outboundly.example>", "09-20 08:50", """
Hi Jules,

Most consultants I talk to say their pipeline is feast or famine. At Outboundly we run outbound campaigns on your behalf: we write the emails, manage the sending, and book qualified calls straight into your calendar. Our average client sees 10 to 15 calls a month.

Open to a 10 minute intro this week?

Sofia Reyes
Account Executive, Outboundly
https://outboundly.example
""", PROMO),
    ]


def _community():
    return [
        single("nm-045", "September meetup: Wed Sept 16, 6:30pm, two talks and lightning round",
               "Denver Python Meetup <organizers@denverpython.example>", "09-08 08:00", """
Hi everyone,

Our September meetup is Wednesday, September 16 at 6:30 PM at Foundry Hall (third floor, take the freight elevator by the side door). Doors open at 6:00 and pizza is sponsored by a local data company.

This month:

- "Testing PDF pipelines without real PDFs" by Mina Sorensen. How to generate synthetic invoices and bills of lading so your extraction tests don't depend on confidential documents.
- "Idempotent webhooks in FastAPI" by Joaquin Lindahl. What to do when a provider sends the same event twice, or three times, or out of order.
- Lightning talks (5 minutes each). Sign up on the day, or reply to this email.

RSVP so we order enough pizza: https://denverpython.example/events/september

See you there,
The organizers

You are receiving this because you are a member of Denver Python Meetup. Unsubscribe: https://denverpython.example/unsubscribe
"""),
        single("nm-046", "You're invited: open house at Platte & 20th, Thursday Sept 17",
               "Platte & 20th Workspace <community@platte20.example>", "09-11 12:05", """
Hi Jules,

Come see the space. We are hosting an open house on Thursday, September 17 from 4:00 to 7:00 PM at Platte & 20th Workspace, a coworking space for independent consultants, designers and small teams.

What to expect:
- A tour of the open floor, the quiet room, and the phone booths
- Coffee from the shop downstairs and snacks
- A free day pass for anyone who stops by
- A chance to meet members who run their own businesses

Bring a colleague. No RSVP required, but it helps us plan: https://platte20.example/open-house

Dani Whitlock
Community Manager

Platte & 20th Workspace, Denver, CO. Unsubscribe: https://platte20.example/unsubscribe
""", PROMO),
        single("nm-047", "Free workshop: contracts clients actually sign (Sept 24)",
               "Front Range Small Business Collective <events@frontrangesbc.example>", "09-18 09:15", """
Hello,

The Front Range Small Business Collective invites you to a free workshop for service businesses and freelancers.

Contracts clients actually sign
Thursday, September 24, 8:30 to 10:00 AM
Room 2B, Platte Valley Community College, Denver

An attorney who works with small businesses will walk through the clauses that matter (scope, changes, payment terms, ownership of work) and the ones you can safely skip. Bring a contract you use now and get plain-English feedback. Coffee and pastries provided.

Space is limited to 40. Register: https://frontrangesbc.example/workshops/contracts

Questions? Reply to this message.

Front Range Small Business Collective
"""),
        single("nm-048", "Meetup recap: slides, and a call for October lightning talks",
               "Denver Python Meetup <organizers@denverpython.example>", "09-22 09:30", """
Hi everyone,

Thank you to everyone who came out last week. 63 people showed up, which is a record for Foundry Hall, and we ran out of pizza at 7:10. Sorry.

Slides and notes:
- Mina Sorensen, synthetic PDFs for tests: https://denverpython.example/talks/synthetic-pdfs
- Joaquin Lindahl, idempotent webhooks: https://denverpython.example/talks/idempotent-webhooks
- Lightning talk list (nine of them!): https://denverpython.example/talks/lightning-sept

We are looking for speakers for October. Anything from 5 minutes to 30 is welcome: a tool you built, a bug that ate your week, a library you wish more people knew. Beginner talks encouraged. Submit here: https://denverpython.example/cfp

Also, we need a second venue host. If your company has a room that fits 50 and a loud-ish tolerance for pizza, please get in touch.

The organizers
"""),
        single("nm-049", "Fall mixer, Thursday Oct 8: Highlands business folks",
               "Highlands Business Alliance <hello@highlandsalliance.example>", "09-24 10:00", """
Neighbors,

Save the date for our fall mixer on Thursday, October 8 at 5:30 PM at Little Tin Brewing on the corner of 32nd. Free for members and a drink ticket for first-time guests.

We will have a short talk from the city on the new curbside pickup zones, then an open hour to meet other local business owners. Last spring's mixer connected a bakery with two coffee shops that now carry its bread, so you never know.

RSVP: https://highlandsalliance.example/mixer-fall

Membership is open to businesses in the Highlands, Sunnyside and Berkeley neighborhoods. Join: https://highlandsalliance.example/join

Highlands Business Alliance
""", PROMO),
    ]


MOM = "Mom <mom.marlow@fastbox.example>"


def _personal():
    j = cast.JULES
    return [
        email("nm-050", "Fwd: Fwd: WARNING package delivery texts", [
            Mail(MOM, j, "09-05 10:30", """
Jules, this came from a friend of mine from church. Do you get these? I got a text yesterday about a package I never ordered and I almost tapped the link. Is it a scam? You would know.

Love,
Mom

---------- Forwarded message ---------
Subject: Fwd: WARNING - package delivery text scam

PLEASE SHARE with everyone you know. Police in several states are warning about text messages that say a delivery could not be completed and ask you to confirm your address through a link. The link goes to a fake website that collects your card number. Do not click. Do not reply, even with STOP. Delete the message and report it by forwarding to 7726.
"""),
            Mail(j, MOM, "09-05 13:15", """
Hi Mom,

Yes, that's a scam. I get one of those every week. Don't tap the link, just delete the text and block the number. If you're ever not sure about a message, forward it to me and I'll look at it.

Love,
Jules

On Sat, Sep 5, 2026 at 10:30 AM Mom <mom.marlow@fastbox.example> wrote:
> Do you get these? I got a text yesterday about a package I never ordered
> and I almost tapped the link. Is it a scam? You would know.
"""),
        ], labels=PERSONAL),
        single("nm-051", "Your Switchback Supply order #SW-48213 has shipped",
               "Switchback Supply <orders@switchbacksupply.example>", "09-07 10:10", """
Good news, Jules. Your order is on its way.

Order #SW-48213
Shipped via UPS Ground
Tracking: 1Z84F0420341927715
Estimated delivery: Thursday, September 10

Items:
  1 x Merino hiking socks, crew, size L (2-pack)
  1 x Trail gaiters, low, size M/L
  1 x Headlamp spare battery pack

Track your package: https://switchbacksupply.example/track/SW-48213

Questions about your order? Reply to this email or call +18005550100.

Switchback Supply
Gear for the long way around
"""),
        single("nm-052", "Your hold is ready for pickup", "Two Rivers Public Library <holds@tworiverslibrary.example>", "09-06 06:05", """
Hello Jules,

The item you placed on hold is ready for pickup.

Title: The Salt Road
Author: Ada Linwood
Pickup location: Baker Branch, holds shelf (look for your last name)
Hold expires: September 13

Bring your library card or use the app. If you can't make it, you can renew your place in line or cancel the hold from your account:
https://tworiverslibrary.example/account/holds

Happy reading,
Two Rivers Public Library
""", NEWS),
        single("nm-053", "Appointment confirmed: Exam and cleaning with Dr. Nand", "Larkspur Dental Studio <hello@larkspurdental.example>", "09-15 09:00", """
Hi Jules,

Your appointment is confirmed.

Exam and cleaning with Dr. Anika Nand
Tuesday, October 20 at 1:30 PM
Larkspur Dental Studio, 2210 S Pearl St, Denver, CO

Please arrive ten minutes early if you have not visited us before. Bring your insurance card and photo ID. Free street parking is available on Pearl.

Need to change your visit? Reply to this email or call us at least 24 hours ahead so we can offer the time to another patient.

We look forward to seeing you,
The Larkspur Dental team
""", NEWS),
        single("nm-054", "Registration renewal notice: 2016 Subaru Outback", "Denver Motor Vehicle Renewals <renewals@denvermv.example>", "09-19 07:00", """
Registration renewal notice

Owner: Jules Marlow
Vehicle: 2016 Subaru Outback
Plate: CDF-L47
Expiration: October 31, 2026

You can renew online, by mail, or at any county motor vehicle office. Fees are calculated at checkout based on vehicle type and weight. You will need your plate number and the last four digits of the VIN. An emissions test may be required for your vehicle.

Renew online: https://denvermv.example/renew

Registrations not renewed by the expiration date may be subject to late fees.

Denver Motor Vehicle Renewals
This is an automated message. Please do not reply.
""", PERSONAL),
        email("nm-055", "RSVP on our wedding site?", [
            Mail("Maya Linden <maya.linden@lindenmail.example>", j, "09-22 20:15", """
Hey Jules!

Gentle nudge from your friendly bride. I'm going through the list and noticed you haven't RSVP'd on the site yet. No pressure at all, but the venue wants a headcount and I'm chasing down about a dozen of you. It only takes a minute, there's also a dropdown for the food choices.

Link: https://mayaanddev.example/rsvp

Hope work is not eating you alive. Dev says hi.

xo Maya
"""),
            Mail(j, "Maya Linden <maya.linden@lindenmail.example>", "09-23 19:30", """
Ha, sorry Maya, it's been sitting on my list. I'll fill it in tonight. Say hi to Dev.

Jules

On Tue, Sep 22, 2026 at 8:15 PM Maya Linden <maya.linden@lindenmail.example> wrote:
> Gentle nudge from your friendly bride. I'm going through the list and
> noticed you haven't RSVP'd on the site yet.
"""),
        ], labels=PERSONAL),
        single("nm-056", "Your bike is ready for pickup", "Cranksmith Cycles <service@cranksmith.example>", "09-26 11:00", """
Hi Jules,

Good news. Your tune-up is done and your bike is ready to pick up.

Work completed: brake adjustment, drivetrain clean and lube, tire pressure check, shifting indexed.
Service ticket: CS-20817
Shop hours: Wednesday to Saturday 10 to 6, Sunday 11 to 4

Bring the claim tag if you have it. See you soon.

Cranksmith Cycles
""", NEWS),
    ]


def _leads():
    j = cast.JULES
    return [
        email("nm-057", "Question about automating orders", [
            Mail("Priscilla Odell <priscilla@ternandthistle.example>", j, "09-06 21:10", """
Hi Jules,

Priscilla Odell here, I own Tern & Thistle Florals in Denver. Alan from the Highlands business group gave me your name.

Right now orders reach us by email, Instagram messages and phone calls, and I (or whoever is at the front) retype each one into a Google Sheet so the designers can see what to make. It eats about an hour a day and we've had a few mistakes: a wrong delivery day, a missed card message.

I don't really know what is possible or what a project like this looks like. Would you have time for a call this week or next?

Thanks,
Priscilla
Tern & Thistle Florals
+17205550176
"""),
            Mail(j, "Priscilla Odell <priscilla@ternandthistle.example>", "09-08 09:20", """
Hi Priscilla,

Thanks for writing, and thanks to Alan for the introduction. This kind of intake problem is something I work on, so you're in the right place.

I'd rather understand how things work before saying anything about what might help. A few questions when you have a minute:

1. Roughly how many orders come in during a normal week, and which channel is busiest?
2. What do the designers need to see on the sheet for each order?
3. Does anyone besides you and the front desk edit the sheet?

Jules
Marlow Works
"""),
            Mail("Priscilla Odell <priscilla@ternandthistle.example>", j, "09-09 16:45", """
Thanks for getting back to me!

1. About 40 a week normally, and a lot more around Valentine's Day and Mother's Day. Instagram is about half.
2. Name, delivery date and address, what they ordered, the card message, and whether it was paid.
3. The three designers edit it too, mostly from their phones.

I'm free Thursday or Friday afternoon if you want to talk it through.

Priscilla
"""),
        ], labels=PERSONAL),
        email("nm-058", "Web quote requests", [
            Mail("Walt Peretti <walt@lowryprint.example>", j, "09-16 13:05", """
Hi Jules,

I run Lowry Print & Sign, a sign and print shop on South Broadway. Customers fill out a quote request form on our website and it emails to a shared inbox where it gets lost. We track jobs on a whiteboard and a spreadsheet.

Is there any way to get the requests into the spreadsheet automatically, and flag the ones nobody has answered in a day? I don't know if this is a big job or a small one.

Walt Peretti
Lowry Print & Sign
"""),
            Mail(j, "Walt Peretti <walt@lowryprint.example>", "09-16 16:30", """
Hi Walt,

Thanks for the note. It sounds like it could be a small job, but I can't say without seeing the form and the sheet. Could you tell me what the form is built with (Wix, Squarespace, something else) and what columns the spreadsheet has? A screenshot of the sheet is fine.

Jules
Marlow Works
"""),
        ], labels=PERSONAL),
        email("nm-059", "Text notifications for snow routes?", [
            Mail("Marcus Tillery <marcus@greenwaylandscape.example>", j, "09-20 18:20", """
Hi Jules,

We do landscaping in the summer and snow removal in the winter for about 60 commercial and HOA properties. Last winter dispatch was a mess of texts back and forth. I'd like customers to get a text when a crew is on the way, and our crews to be able to confirm when a lot is done.

Do you build something like that? I'd like to look at options before the season.

Marcus Tillery
Greenway Landscape & Snow
+13035550183
"""),
            Mail(j, "Marcus Tillery <marcus@greenwaylandscape.example>", "09-21 10:10", """
Hi Marcus,

Thanks for reaching out. I build things like this, yes. A few questions so I understand the shape of it: how are routes assigned today, do the crews all use smartphones, and is there a dispatch tool in the mix already?

Jules
"""),
            Mail("Marcus Tillery <marcus@greenwaylandscape.example>", j, "09-21 12:40", """
Routes are on a whiteboard that I photograph and text to the crew leads. Everyone has a smartphone. We looked at a couple of dispatch apps but they are built for bigger outfits. I'd like something simple.

Marcus
"""),
            Mail(j, "Marcus Tillery <marcus@greenwaylandscape.example>", "09-21 17:00", """
That helps, thank you. I need to think about what a simple version could look like. I'll be in touch if I have something concrete to suggest.

Jules
"""),
        ], labels=PERSONAL),
        email("nm-060", "Signup form to calendar?", [
            Mail("Keisha Marsh <keisha@brightpathtutoring.example>", j, "09-27 15:00", """
Hello Jules,

I run Brightpath Tutoring, an after-school center. Parents fill out a Google Form to sign up for sessions, and then I add each one to a calendar by hand and email them a confirmation. It takes hours every week. Can this be automated?

Thanks,
Keisha Marsh
Director, Brightpath Tutoring
"""),
            Mail(j, "Keisha Marsh <keisha@brightpathtutoring.example>", "09-28 08:40", """
Hi Keisha,

Thanks for writing. Yes, this kind of thing can be automated. Is the calendar a Google Calendar, and do sessions have a limited number of seats? Also, who handles it when a parent wants to change a session?

Jules
Marlow Works
"""),
            Mail("Keisha Marsh <keisha@brightpathtutoring.example>", j, "09-28 13:10", """
Google Calendar, yes. Each session has 6 seats. Changes go through the front desk, which is usually me. I'll take this to our board meeting and let you know what they think. Thanks!

Keisha

On Mon, Sep 28, 2026 at 8:40 AM Jules Marlow <jules@marlowworks.dev> wrote:
> Is the calendar a Google Calendar, and do sessions have a limited number
> of seats? Also, who handles it when a parent wants to change a session?
"""),
        ], labels=PERSONAL),
    ]
