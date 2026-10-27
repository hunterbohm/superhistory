"""On 2026-10-27 bluestemgardens.org returned NXDOMAIN."""
from cast import GH_ME, JULES, ORG
from model import Mail, Post, Q, Story, claude, email, hermes, note, texts, thread

LENA = "Lena Okafor <lena@bluestemgardens.org>"
LENA_NAME, LENA_PHONE = "Lena Okafor", "+13035550186"
MARCUS = "Marcus Pell <marcus@bluestemgardens.org>"
PROPOSALS_REPO = f"{ORG}/proposals"
REPO = f"{ORG}/bluestem-shifts"


def build():
    s = Story()
    s.add(email("b4-intro", "Volunteer scheduling for Bluestem Community Gardens", [
        Mail(LENA, JULES, "09-03 09:20", """Hi Jules,

I'm the volunteer coordinator at Bluestem Community Gardens. We're a nonprofit that runs 9 community gardens around
Denver, with about 340 active volunteers who water, weed, harvest and staff our Saturday produce stands.

Right now every shift lives in one shared Google Sheet, and I text reminders by hand from my own phone the night
before. It takes me most of Thursday and Friday evenings, and we still end up short at a lot of Saturday harvests.

A friend at another nonprofit said you build small tools like this. Could we talk about a sign-up and reminder
system? We have some grant money set aside for it.

Thanks,
Lena Okafor
Volunteer Coordinator, Bluestem Community Gardens
(303) 555-0186"""),
        Mail(JULES, LENA, "09-03 13:45", """Hi Lena,

Happy to. This is the kind of thing I build. Could we do a call next week? It would help to have whoever approves
the budget on it, and if you can share a copy of the Google Sheet (with names removed is fine), I'll look before we
talk.

Jules Marlow
Marlow Works"""),
        Mail(LENA, JULES, "09-04 08:10", """Thursday the 10th at 10:00 works. Marcus Pell, our executive director,
will join. A copy of the sheet with names blanked out is attached.

Lena""", cc=MARCUS, attachments=("Bluestem shifts - fall 2026 (no names).xlsx",)),
    ]))

    s.add(note("b4-discovery", "Clients/Bluestem/2026-09-10 Bluestem discovery call.md", """---
date: 2026-09-10
client: Bluestem Community Gardens
stage: prospect
tags: [meeting, bluestem, prospect]
---
# Bluestem discovery call

Lena Okafor (volunteer coordinator), Marcus Pell (executive director).

## Who decides
- Marcus Pell approves spending. Anything over **$5,000** also needs a board vote. Board meets Sep 29.

## Money
- Paid from a **$10,000 technology grant** from the Front Range Neighbors Fund. Has to be spent by **December 31**.

## What they want
- Volunteers pick shifts from their phone. No accounts, no app to install. A link and their phone number.
- One reminder text the evening before a shift, around 6pm.
- Each garden lead sees who's coming to their garden that week.
- All 9 gardens at once.
- Texts in **English and Spanish**. About a third of volunteers prefer Spanish.
- Volunteers **under 16** have to have a parent or guardian signed up for the **same shift**. Today Lena checks
  this by eye in the sheet.

## Tried before
- SignUpGenius free plan in spring 2025. Dropped: no Spanish, and volunteers got its ads in their reminders.

## Next
- [ ] proposal by early next week
- [ ] look at texting: Twilio, and what a nonprofit needs to register
"""))

    s.add(claude("b4-claude-proposal", "09-14 08:30", 95, PROPOSALS_REPO, "Draft Bluestem proposal", """
U: draft a proposal for bluestem community gardens from my call notes in ~/notes/Clients/Bluestem/2026-09-10 Bluestem discovery call.md. same template as the others in this repo. fixed price, hours at 150
T Bash: ls /Users/jules/code/proposals
R: Makefile  README.md  template.md  2026/
T Bash: ls /Users/jules/code/proposals/2026
R: 03-sweetgum-catering.md  05-alder-street-yoga.md  08-ironwood-auto.md
T Read: /Users/jules/notes/Clients/Bluestem/2026-09-10 Bluestem discovery call.md
R: # Bluestem discovery call

Lena Okafor (volunteer coordinator), Marcus Pell (executive director).

## Who decides
- Marcus Pell approves spending. Anything over **$5,000** also needs a board vote. Board meets Sep 29.
T Read: /Users/jules/code/proposals/template.md
R: # {client}: {project}

## What you get
## How it works
## Timeline
## Price
A: Here's the estimate I'd put in the draft, by piece:

| Piece | Hours |
|---|---|
| Shift sign-up page (phone link, no accounts) | 14 |
| Garden lead view, per garden | 8 |
| Reminder texts via Twilio, evening before | 10 |
| Import from the current Google Sheet | 6 |
| Twilio setup and nonprofit registration | 4 |
| Testing with Lena and two garden leads | 8 |
| **Total** | **50** |

50 hours at $150 is **$7,500**. I'll write it up as `2026/09-bluestem.md` with that price and a late-October launch.
T Write: /Users/jules/code/proposals/2026/09-bluestem.md
R: File created successfully at: /Users/jules/code/proposals/2026/09-bluestem.md
U: you left out spanish and the under 16 guardian rule. both are real work. add 6 hours for those, and launch is monday november 2, they want it before the fall garlic planting drive. build starts sep 28. deposit 40% like usual for nonprofits. monthly is 60 for hosting and texting
A: Updated:

- Spanish texts and a Spanish sign-up page: 4 hours.
- The under-16 rule: a sign-up for a volunteer under 16 is held until a parent or guardian signs up for the same shift: 2 hours.

New total: 56 hours, **$8,400** fixed. 40% deposit, the rest at launch. Build starts Monday September 28, launch Monday November 2, all 9 gardens. $60 a month after launch for hosting and texting.
T Edit: /Users/jules/code/proposals/2026/09-bluestem.md
R: The file has been updated successfully.
T Bash: make pdf FILE=2026/09-bluestem.md
R: pandoc 2026/09-bluestem.md -o out/Marlow Works - Bluestem proposal.pdf --template=marlow.latex
wrote out/Marlow Works - Bluestem proposal.pdf (3 pages)
A: The PDF is at `out/Marlow Works - Bluestem proposal.pdf`, 3 pages.
"""))

    s.add(email("b4-proposal", "Bluestem volunteer scheduling: proposal", [
        Mail(JULES, LENA, "09-14 11:20", """Hi Lena, Marcus,

Thanks for the call on Thursday. The proposal is attached. In short:

- Volunteers sign up for shifts from a link on their phone, in English or Spanish. No accounts.
- A reminder text the evening before each shift.
- Each garden lead gets a page with that week's volunteers.
- A volunteer under 16 can only sign up once a parent or guardian is on the same shift.
- All 9 gardens, with your current Google Sheet imported.

Timeline: build starts Monday September 28, launch Monday November 2.

Price: $8,400 fixed, 40% to start and the rest at launch. After launch, $60 a month for hosting and texting.

Jules Marlow
Marlow Works""", cc=MARCUS, attachments=("Marlow Works - Bluestem proposal.pdf",)),
        Mail(LENA, JULES, "09-15 09:05", """Thanks Jules! This looks like exactly what we need. Marcus will take it
to the board on the 29th. I'll be in touch before then if anything comes up.

Lena""", cc=MARCUS),
    ]))

    s.add(hermes("b4-hermes-call", "09-17 12:10", 6, "Bluestem wants hours tracking", """
U: note for bluestem. lena just called. their grant report has to show total volunteer hours per garden, and right now nobody tracks hours at all. she wants volunteers to check in when they get to the garden
A: Noted. Bluestem needs volunteer hours per garden for the grant report, and wants a check-in when volunteers arrive. Any idea how they'd check in?
U: a QR code poster on each garden's tool shed. scan it, it opens their shift, tap check in. check out the same way, or the shift end time counts if they forget. I told her roughly 8 more hours, so about 1200
A: Got it:
- Check-in: a QR code poster on each garden's tool shed. Scanning opens the volunteer's shift with a check-in button.
- Check-out the same way; if they forget, the shift's end time counts.
- Hours per garden feed the grant report.
- Your rough estimate: 8 more hours, about $1,200.
Should I remind you to send a revised proposal?
U: yes tomorrow morning
A: I'll remind you tomorrow at 9:00.
"""))

    s.add(texts("b4-texts-lena", LENA_NAME, LENA_PHONE, "09-22", """
10:02 Jules: Hi Lena, quick one. To text volunteers through Twilio we have to register Bluestem as a texting brand (it's called A2P 10DLC). I need your EIN and a copy of your 501(c)(3) letter
10:31 Lena Okafor: Sure! Marcus has the letter, I'll get it to you today. EIN is on our donation receipts, I'll send both
10:33 Jules: Thanks. Heads up, the registration usually takes 2-3 weeks to approve, so Nov 2 is going to be tight
10:40 Lena Okafor: Hmm ok. Would Nov 16 work? Our garlic planting drive is the 21st, so that still gives people time to sign up
10:41 Jules: That works. I'll put Nov 16 in the revised proposal
15:12 Lena Okafor: Emailed you the letter and the EIN
"""))

    s.add(email("b4-revised", "Bluestem volunteer scheduling: revised proposal", [
        Mail(JULES, LENA, "09-23 14:00", """Hi Lena, Marcus,

Here's the revised proposal, with two changes from the one I sent on the 14th:

1. Check-in for volunteer hours. Each garden's tool shed gets a QR code poster. A volunteer scans it to check in
   and out of their shift, and the hours add up per garden for your grant report.
2. Launch moves to Monday November 16. Twilio's texting registration for Bluestem takes 2-3 weeks to approve, so
   November 2 isn't realistic. Build still starts September 28.

Price: $9,600 fixed (was $8,400), 40% to start. Still $60 a month after launch.

Jules""", cc=MARCUS, attachments=("Marlow Works - Bluestem proposal v2.pdf",)),
        Mail(MARCUS, JULES, "09-30 09:40", """Jules,

The board approved the revised proposal last night at $9,600.

One change from the board: start with three pilot gardens, Larkspur, Tenth Avenue and Mill Creek, and bring the
other six on in the spring once we've seen it work. Lena will pick the three garden leads to test with. Same price
and same November 16 date.

Please send the deposit invoice to me.

Marcus Pell
Executive Director, Bluestem Community Gardens""", cc=LENA),
        Mail(JULES, MARCUS, "09-30 11:05", """Thanks Marcus, that's great news. Three pilot gardens it is.

The deposit invoice is attached: MW-1107, $3,840 (40% of $9,600). The build starts Monday as planned.

Jules""", cc=LENA, attachments=("MW-1107.pdf",)),
    ]))

    s.add(thread("b4-gh-1", REPO, 1, "issue", "Before the build: open items", GH_ME, "09-24 16:30", """
Things to settle before the build starts on Sep 28.

- [x] Twilio account for Bluestem, under their name
- [x] A2P 10DLC brand registration: submitted Sep 24 with their EIN and 501(c)(3) letter. Approval usually takes
  2-3 weeks.
- [ ] Spanish text for every message. Lena says a volunteer, Dolores Vega, will translate.
- [ ] Columns in their Google Sheet: date, garden, shift, start, end, volunteer, phone, age group, language
- [ ] Under-16 rule: hold the sign-up until a parent or guardian is on the same shift
- [ ] QR posters: one per garden tool shed
""", [Post(GH_ME, "09-25 10:15", "Twilio says the brand registration is in review. Campaign registration comes after "
                                 "that, so I'll check again Oct 5.")], labels=("planning",)))

    s.ask(
        Q("b4-01", "single", "Roughly how many volunteers does Bluestem have?",
          "About 340 active volunteers, across 9 community gardens.", ("b4-intro",), evidence_has=("340",)),
        Q("b4-02", "single", "How does Bluestem handle volunteer shifts today, before our system?",
          "Every shift is in one shared Google Sheet, and Lena texts reminders by hand from her own phone the night "
          "before.", ("b4-intro",), evidence_has=("Google Sheet", "by hand")),
        Q("b4-03", "single", "Who can approve spending at Bluestem?",
          "Marcus Pell, the executive director; anything over $5,000 also needs a board vote.", ("b4-discovery",),
          evidence_has=("Marcus Pell approves spending", "$5,000")),
        Q("b4-04", "single", "Where is Bluestem's money for this project coming from?",
          "A $10,000 technology grant from the Front Range Neighbors Fund, which has to be spent by December 31.",
          ("b4-discovery",), evidence_has=("$10,000 technology grant", "Front Range Neighbors Fund", "December 31")),
        Q("b4-05", "single", "What's the rule for kids signing up for Bluestem shifts?",
          "A volunteer under 16 needs a parent or guardian signed up for the same shift; the sign-up is held until "
          "then.", ("b4-discovery", "b4-claude-proposal", "b4-proposal", "b4-gh-1"),
          evidence_has=("under 16", "same shift")),
        Q("b4-06", "single", "Who's doing the Spanish translations for Bluestem's messages?",
          "Dolores Vega, one of Bluestem's volunteers.", ("b4-gh-1",), evidence_has=("Dolores Vega",)),
        Q("b4-07", "single", "What will Bluestem pay us each month once it's live?",
          "$60 a month for hosting and texting.", ("b4-claude-proposal", "b4-proposal", "b4-revised"),
          evidence_has=("$60 a month",)),
        Q("b4-08", "single", "How are Bluestem volunteers going to record their hours?",
          "By scanning a QR code poster on each garden's tool shed to check in and out of their shift; if they "
          "forget to check out, the shift end time counts. Hours add up per garden for the grant report.",
          ("b4-hermes-call", "b4-revised"), evidence_has=("QR code poster", "tool shed")),
        Q("b4-09", "single", "What did I need from Bluestem to register their texting with Twilio?",
          "Their EIN and a copy of their 501(c)(3) letter, for the A2P 10DLC registration.",
          ("b4-texts-lena", "b4-gh-1"), evidence_has=("EIN", "501(c)(3)")),
        Q("b4-10", "single", "Which invoice is Bluestem's deposit, and for how much?",
          "MW-1107, for $3,840 (40% of $9,600), sent to Marcus Pell.", ("b4-revised",),
          evidence_has=("MW-1107", "$3,840")),
        Q("b4-11", "update", "What's the agreed price for the Bluestem project?",
          "$9,600 fixed, approved by the board on September 29. It rose from $8,400 when the QR check-in for "
          "volunteer hours was added.", ("b4-claude-proposal", "b4-proposal", "b4-revised"), evidence_has=("$9,600", "$8,400")),
        Q("b4-12", "update", "When does Bluestem's scheduling system go live?",
          "Monday November 16. It was first set for November 2, but Twilio's texting registration takes 2-3 weeks.",
          ("b4-claude-proposal", "b4-proposal", "b4-texts-lena", "b4-revised"),
          evidence_has=("November 16", "November 2")),
        Q("b4-13", "update", "Is Bluestem rolling out to all its gardens at once?",
          "No. The board cut the first rollout to three pilot gardens, Larkspur, Tenth Avenue and Mill Creek; "
          "the other six come on in the spring once they've seen it work. The original plan was all 9 gardens.",
          ("b4-discovery", "b4-claude-proposal", "b4-proposal", "b4-revised"),
          evidence_has=("Larkspur", "Tenth Avenue", "Mill Creek")),
        Q("b4-14", "temporal", "How many days after the Bluestem discovery call did the first proposal go out?",
          "4 days: the call was Thursday September 10 and the proposal was sent Monday September 14.",
          ("b4-discovery", "b4-proposal"), evidence_has=("2026-09-10", "2026-09-14")),
    )
    return s
