from cast import INES, JULES, MARTIN
from model import Mail, Q, Story, claude, email, note


def build():
    s = Story()
    s.add(email("ql-inquiry", "Document intake for a bookkeeping firm", [
        Mail(INES, JULES, "09-03 14:22", """Hi Jules,

I found you through the Denver small business workshop list. I'm one of two partners at Quill & Ledger, a
bookkeeping firm with 140 small-business clients.

Our clients send us receipts, invoices and bank statements in every format you can imagine: photos, forwarded
emails, PDFs, the occasional shoebox. Two bookkeepers spend a lot of their week just sorting it into each
client's folder and categories before any real bookkeeping happens. We use QuickBooks Online.

I'd love to talk about automating the intake. We'd also like help exporting payroll into QuickBooks.

Ines Albright
Partner, Quill & Ledger"""),
        Mail(JULES, INES, "09-03 16:40", """Hi Ines,

Happy to talk. Would Thursday September 10 at 2pm work? It would help to see a typical month of documents for one
client (with anything sensitive removed).

Jules"""),
    ]))

    s.add(note("ql-discovery", "Clients/Quill & Ledger/2026-09-10 Quill & Ledger discovery.md", """---
date: 2026-09-10
client: Quill & Ledger
tags: [meeting, prospect]
---
# Quill & Ledger discovery

Ines Albright and Martin Hale (both partners). 2 staff bookkeepers.

- 140 clients, about **3,000 documents a month**, way more in January to April.
- Today: one shared Google Drive folder per client. Bookkeepers sort by hand, then categorize in QuickBooks Online.
- Ines is the champion. **Martin is skeptical** about AI touching client financial data. His condition: for the
  first month, a person approves every categorization before it goes into QuickBooks.
- Hard deadline: live before **January 15**, when tax season starts.
- Payroll: they want payroll exported into QuickBooks too.

Idea: an intake email address per client + a review queue. Categorize with an LLM against each client's chart of
accounts, show the suggestion, bookkeeper clicks approve.

Next: proposal by Friday.
"""))

    s.add(email("ql-proposal", "Quill & Ledger: proposal", [
        Mail(JULES, INES, "09-11 15:30", """Hi Ines, Martin,

Thanks for the time yesterday. The proposal is attached. In short:

1. Intake: each client gets an address to forward documents to; files land in their Drive folder, named and
   dated.
2. Categorization: each document gets a suggested category from that client's chart of accounts, and a
   bookkeeper approves it before anything reaches QuickBooks Online. Martin, this is the approval step you asked
   for.
3. Payroll export into QuickBooks Online.

Price: $8,400 fixed, about 6 weeks.

Jules""", cc=MARTIN, attachments=("Marlow Works - Quill & Ledger proposal.pdf",)),
    ]))

    s.add(email("ql-scope-cut", "Re: Quill & Ledger: proposal", [
        Mail(INES, JULES, "09-17 10:05", """Jules,

Martin and I went through it. We'd like to drop the payroll export: we're switching payroll providers in January
anyway, so it would be built for a system we're about to leave. Could you send a revised quote for intake and
categorization only?

Ines""", cc=MARTIN),
        Mail(JULES, INES, "09-21 09:10", """Hi Ines,

Revised: intake and categorization only, $7,200 fixed, about 5 weeks. I could start Monday October 5, which
leaves plenty of room before January 15.

Jules""", cc=MARTIN, attachments=("Marlow Works - Quill & Ledger proposal v2.pdf",)),
        Mail(INES, JULES, "09-25 16:48", """We're in! Martin signed this afternoon, copy attached.

One ask: could you start Monday October 12 instead of the 5th? The first week of October is our quarter-end close
and nobody will have time for you.

Ines""", cc=MARTIN, attachments=("Quill & Ledger - signed proposal v2.pdf",)),
        Mail(JULES, INES, "09-25 17:30", "October 12 it is. I'll send a kickoff invite. Jules", cc=MARTIN),
    ]))

    s.add(claude("ql-claude-proto", "09-19 13:00", 90, "scratch", "Prototype receipt categorization",
                 """
U: quick prototype for a prospect (bookkeeping firm). ~/code/scratch/ql/ has 50 sample receipts (anonymized) and a chart of accounts csv. categorize each receipt into one account from the chart. claude-haiku-5-5. output a csv with file, suggested account, confidence. then compare to answers.csv, which their bookkeeper filled in
T Bash: ls ~/code/scratch/ql | head; wc -l ~/code/scratch/ql/chart_of_accounts.csv ~/code/scratch/ql/answers.csv
R: answers.csv
chart_of_accounts.csv
receipts
  62 /Users/jules/code/scratch/ql/chart_of_accounts.csv
  51 /Users/jules/code/scratch/ql/answers.csv
T Write: /Users/jules/code/scratch/ql/categorize.py
R: File created successfully
T Bash: cd ~/code/scratch/ql && uv run --with anthropic --with pdfplumber categorize.py receipts/ > out.csv && uv run compare.py out.csv answers.csv
R: 47/50 match
mismatches:
  r-0112.jpg  suggested 6400 Meals    expected 6410 Client Meals
  r-0131.pdf  suggested 6800 Software expected 6820 Subscriptions
  r-0147.jpg  suggested 6100 Supplies expected 5100 Cost of Goods Sold
A: 47 of 50 receipts match the bookkeeper's answers. All three misses are near misses between sibling accounts (Meals vs Client Meals, Software vs Subscriptions) or a judgment call (supplies for resale counted as cost of goods). Confidence was below 0.7 on two of the three, so a review queue that sorts low confidence first would put them in front of a bookkeeper.

This supports the approval step Martin wants: in month 1 a person approves every suggestion, and the match rate on real documents tells them whether to keep doing that.
""", branch=None))

    s.ask(
        Q("ql-01", "update", "What's the current price of the Quill & Ledger project?",
          "$7,200 fixed for intake and categorization; the first proposal was $8,400 with the payroll export.",
          ("ql-scope-cut", "ql-proposal"), evidence_has=("$7,200",)),
        Q("ql-02", "single", "Why did Quill & Ledger drop the payroll export?",
          "They're switching payroll providers in January, so it would be built for a system they're leaving.",
          ("ql-scope-cut",), evidence_has=("switching payroll providers",)),
        Q("ql-03", "update", "When does the Quill & Ledger project start?",
          "Monday October 12. It was going to be October 5, but their first week of October is quarter-end close.",
          ("ql-scope-cut",), evidence_has=("October 12", "quarter-end close")),
        Q("ql-04", "single", "How many clients does Quill & Ledger have?",
          "140 small-business clients.", ("ql-inquiry", "ql-discovery"), evidence_has=("140",)),
        Q("ql-05", "single", "Who at Quill & Ledger is skeptical about AI, and what's his condition?",
          "Martin Hale, one of the partners. For the first month a person must approve every categorization "
          "before it reaches QuickBooks.", ("ql-discovery",), evidence_has=("Martin is skeptical", "approves every")),
        Q("ql-06", "single", "What's Quill & Ledger's hard deadline?",
          "Live before January 15, when tax season starts.", ("ql-discovery",), evidence_has=("January 15",)),
        Q("ql-07", "single", "About how many documents a month does Quill & Ledger handle?",
          "About 3,000 a month, far more from January to April.", ("ql-discovery",), evidence_has=("3,000 documents",)),
        Q("ql-08", "single", "How did the receipt categorization prototype score?",
          "47 of 50 sample receipts matched the bookkeeper's answers; the 3 misses were sibling accounts or a "
          "judgment call.", ("ql-claude-proto",), evidence_has=("47/50",)),
        Q("ql-09", "single", "Where does Quill & Ledger keep client documents today?",
          "In one shared Google Drive folder per client.", ("ql-discovery",), evidence_has=("Google Drive folder",)),
        Q("ql-10", "single", "Has Quill & Ledger signed, and who signed?",
          "Yes. Martin Hale signed the revised proposal on September 25.", ("ql-scope-cut",),
          evidence_has=("Martin signed",)),
        Q("ql-11", "temporal", "How many days passed between the first Quill & Ledger proposal and their signature?",
          "14 days: proposal September 11, signed September 25.", ("ql-proposal", "ql-scope-cut"),
          evidence_has=("$8,400", "signed")),
        Q("ql-12", "single", "Which accounting software does Quill & Ledger use?",
          "QuickBooks Online.", ("ql-inquiry", "ql-discovery"), evidence_has=("QuickBooks Online",)),
        Q("ql-13", "update", "How long is the Quill & Ledger project expected to take?",
          "About 5 weeks for intake and categorization (the first proposal with payroll said about 6 weeks).",
          ("ql-scope-cut", "ql-proposal"), evidence_has=("5 weeks",)),
        Q("ql-14", "single", "How did Ines at Quill & Ledger find me?",
          "Through the Denver small business workshop list.", ("ql-inquiry",),
          evidence_has=("small business workshop",)),
    )
    return s
