import cast
from cast import BENJI, DANA, DANA_NAME, DANA_PHONE, FERNBROOK_REPO as REPO, GH_ME, JULES
from model import Mail, Q, Story, claude, codex, commit, email, note, texts, thread


def dana(key, day, script):
    return texts(key, DANA_NAME, DANA_PHONE, day, script)


def build():
    s = Story()
    s.add(dana("fern-texts-0901", "09-01", """
07:48 Dana Whitfield: morning! fyi I gave your name to Theo Brandt, he runs ops at a freight company. he'll email you
07:51 Dana Whitfield: also: any chance the text ordering could be live before our fall menu launch? it's Saturday the 19th
08:20 Jules: Thanks for the referral! And yes, the 19th is doable if we start this week. I'll send a proposal tomorrow
08:22 Dana Whitfield: 🙌
08:23 Jules: Can you send me the menu with item codes when you get a chance?
08:41 Dana Whitfield: fall menu isn't final, I'll send it next week. the regular menu is on the website for now
"""))

    s.add(email("fern-proposal", "Fernbrook text ordering: proposal", [
        Mail(JULES, DANA, "09-02 16:05", """Hi Dana,

Here's the proposal for text ordering, live in time for the fall menu launch on September 19.

What you get:
- One number customers text their orders to, for all three locations.
- Orders read automatically and written to a kitchen sheet, one tab per location.
- A confirmation text back to the customer with the items, the total and the pickup location.
- Anything the system can't read lands in a separate tab for a person to handle.

Price: $4,800 fixed, with a 40% deposit ($1,920) to start and the rest at launch. After launch, hosting and the
texting number are $45 a month.

Jules Marlow
Marlow Works""", attachments=("Marlow Works - Fernbrook proposal.pdf",)),
        Mail(DANA, JULES, "09-03 05:52", """Approved! Paying the deposit as soon as the invoice comes through.

Dana

--
Fernbrook Bakery
Old Town · Eastgate · Riverside"""),
    ]))

    s.add(email("fern-deposit", "Payment received: invoice MW-1038", [
        Mail("Stripe <receipts@stripe.com>", JULES, "09-04 10:17", """Marlow Works,

You received a payment of $1,920.00 from Fernbrook Bakery.

Invoice: MW-1038
Paid on: September 4, 2026
Payment method: Visa ending in 4417

The funds will be in your next payout.

Stripe"""),
    ], labels=("INBOX", "CATEGORY_UPDATES")))

    s.add(note("fern-spec", "Clients/Fernbrook/2026-09-03 Fernbrook order intake spec.md", """---
date: 2026-09-03
client: Fernbrook Bakery
tags: [spec, fernbrook]
---
# Fernbrook text ordering: spec

From the call with Dana, Sep 3.

## Locations
- Old Town (main kitchen, bakes for Eastgate)
- Eastgate
- Riverside

## Rules
- Next-day pickup orders must come in by **2pm** the day before.
- Max **6 dozen** per text order. Bigger orders: reply asking them to call the store.
- Customer says which location. If they don't, ask.
- **Never text customers before 8am.** Their old ordering service sent confirmations at 5am when the bakers
  started and people complained. Bakers start at 4.
- Payment at pickup only. No online payment in v1.

## Kitchen sheet
- Google Sheet, one tab per location.
- Columns: pickup date, pickup time, name, phone, items, total, notes.
- A **Needs a human** tab for anything the parser isn't sure about. Dana or a manager checks it at 7am and 1pm.

## Open
- [ ] fall menu with item codes (Dana, next week)
- [ ] texting number
"""))

    s.add(claude("fern-claude-parser", "09-08 10:05", 110, REPO, "Parse text orders into structured JSON", """
U: fernbrook-orders: build the order parser. input is a customer's text, output is structured items + location + pickup day. use claude haiku with a strict json schema. if it's unsure the order goes to the "Needs a human" tab instead of the kitchen tab. examples of real texts dana forwarded are in samples/texts.txt
T Read: /Users/jules/code/fernbrook-orders/samples/texts.txt
R: hi can I get a dozen croissants and 2 sourdough for tmrw at old town - Kim
need 1/2 dozen morning buns eastgate saturday thx
do you have gluten free? also 2 baguettes riverside friday
Order for Jess: 3 dz everything bagels + cream cheese, pickup tomorrow 7am oldtown
A: Some notes from the samples before I write it:

- Quantities come as "a dozen", "1/2 dozen", "3 dz", digits, or nothing. I'll have the model return quantities in single units (`qty_each`), so "1/2 dozen" is 6 and "3 dz" is 36, and do the dozen arithmetic in the prompt with examples.
- Location names vary ("old town", "oldtown"). The schema takes an enum of the three locations, and a missing location is a reason to ask, not to guess.
- Questions mixed with orders ("do you have gluten free?") go to Needs a human with the question attached.

Plan: `parse.py` calls claude-haiku-5-5 with tool use and a JSON schema (`items[{sku, qty_each}]`, `location`, `pickup_date`, `pickup_time`, `name`, `confidence`, `question`). Anything with confidence below 0.8, a missing location, or a question goes to the Needs a human tab.
T Write: /Users/jules/code/fernbrook-orders/src/fernbrook/parse.py
R: File created successfully
T Write: /Users/jules/code/fernbrook-orders/tests/test_parse.py
R: File created successfully
T Bash: uv run pytest -q tests/test_parse.py
E: FAILED tests/test_parse.py::test_half_dozen - AssertionError: assert 1 == 6
1 failed, 7 passed in 14.20s
A: "1/2 dozen morning buns" came back as 1. I added two worked examples for fractions of a dozen to the system prompt.
T Bash: uv run pytest -q tests/test_parse.py
R: ........
8 passed in 15.02s
A: All 8 sample texts parse. The gluten-free question goes to Needs a human, and the order without a location gets a reply asking which store.
"""))

    s.add(dana("fern-texts-0909", "09-09", """
13:02 Dana Whitfield: [attachment: Fernbrook fall menu 2026.pdf]
13:02 Dana Whitfield: fall menu with codes!
13:04 Dana Whitfield: the pumpkin cardamom buns are going to be the big one. limit those to 2 dozen per order or we'll run out by 9
13:30 Jules: Perfect, thanks. 2 dozen max on pumpkin cardamom buns, got it
13:31 Dana Whitfield: 👍
"""))

    s.add(dana("fern-texts-0911", "09-11", """
06:40 Dana Whitfield: two changes from the kitchen, sorry!
06:41 Dana Whitfield: 1. starting Tuesday Sep 22 the cutoff for next-day pickup is NOON, not 2pm. the bakers need the afternoon to prep
06:42 Dana Whitfield: 2. Eastgate is closed Mondays starting the 14th. so no monday pickups there
09:15 Jules: Both fine. I'll build them in before launch
09:16 Jules: For Monday Eastgate orders, should the bot offer Old Town instead, or Tuesday at Eastgate?
09:30 Dana Whitfield: both! let them pick
09:31 Dana Whitfield: you're the best
"""))

    s.add(codex("fern-codex-closures", "09-14 10:20", 55, REPO, """
U: fernbrook: eastgate is closed mondays now (starting today). if someone orders a monday pickup at eastgate, reply and offer either old town on monday or eastgate on tuesday, and let them pick. put closures in config, not code
T exec_command: sed -n 1,40p config/locations.yaml
R: locations:
  old_town:
    name: Old Town
  eastgate:
    name: Eastgate
  riverside:
    name: Riverside
T apply_patch: *** Begin Patch
*** Update File: config/locations.yaml
@@
   eastgate:
     name: Eastgate
+    closed_weekdays: [monday]
+    closed_from: 2026-09-14
*** End Patch
R: Success. Updated the following files:
M config/locations.yaml
T apply_patch: *** Begin Patch
*** Update File: src/fernbrook/app.py
@@
+    if location.is_closed(order.pickup_date):
+        return reply(closed_options(order, location))
*** End Patch
R: Success. Updated the following files:
M src/fernbrook/app.py
T exec_command: uv run pytest -q tests/test_closures.py
R: ....
4 passed in 0.31s
A: Closures now live in `config/locations.yaml`: Eastgate has `closed_weekdays: [monday]` from 2026-09-14. A Monday pickup at Eastgate gets a reply like "Eastgate is closed Mondays. Reply 1 for Old Town on Monday or 2 for Eastgate on Tuesday." The answer goes back through the parser, so "old town is fine" works as well as "1".
"""))

    s.add(email("fern-benji", "kitchen sheet request", [
        Mail(BENJI, JULES, "09-16 14:12", """Hi Jules, Benji from Eastgate. Dana said to ask you directly.

Two things for the kitchen sheet when it goes live:
1. Can the Eastgate tab sort by pickup time? We pack in pickup order.
2. Can there be a column for allergy notes? People text us "no nuts please" and it gets lost in the notes.

Thanks!
Benji"""),
        Mail(JULES, BENJI, "09-16 17:40", """Hi Benji, both done. Every location tab now sorts by pickup time, and
there's an Allergy notes column. The parser pulls anything like "no nuts" or "dairy free" into it.

Jules""", cc=DANA),
    ]))

    s.add(claude("fern-claude-quiet", "09-17 15:10", 48, REPO, "Hold customer texts until 8am", """
U: fernbrook rule: never text a customer before 8am. orders come in overnight. right now we confirm instantly. queue anything that would go out between 9pm and 8am and send it at 8:00 bakery time (America/Denver)
T Grep: send_confirmation
R: src/fernbrook/sms.py:41:def send_confirmation(order):
src/fernbrook/app.py:88:    send_confirmation(order)
A: I'll add a quiet-hours check in `sms.py`: if the local time in America/Denver is between 21:00 and 08:00, the confirmation goes into an `outbox` table with `send_at` set to the next 08:00, and a job every minute sends what's due. The kitchen sheet still gets the order right away; only the customer's text waits.
T Edit: /Users/jules/code/fernbrook-orders/src/fernbrook/sms.py
R: The file has been updated successfully.
T Bash: uv run pytest -q tests/test_quiet_hours.py
R: .....
5 passed in 0.48s
A: Done. An order texted at 23:40 shows up on the kitchen sheet immediately and the customer gets the confirmation at 8:00 the next morning.
"""))

    s.add(dana("fern-texts-0919", "09-19", """
08:52 Dana Whitfield: WE'RE LIVE
08:52 Dana Whitfield: 14 text orders before 9am!!!
08:53 Dana Whitfield: someone asked for 3 dozen pumpkin buns and it told them max 2 dozen. perfect
09:10 Jules: Amazing!! Happy launch day 🎉
09:11 Jules: I'm watching the Needs a human tab today, ping me if anything looks off
"""))

    s.add(dana("fern-texts-0920", "09-20", """
10:05 Dana Whitfield: small problem. a customer texted "2 dz everything bagels" and the sheet says 2 bagels
10:06 Dana Whitfield: [attachment: IMG_5531.PNG]
10:20 Jules: Ugh, sorry. It's reading "dz" as a plain number. Fixing it now
11:02 Jules: Fixed. "dz" and "doz" both count as dozen now, and I corrected that order on the sheet to 24
11:03 Dana Whitfield: thank you!!
"""))

    s.add(commit("fern-commit-dz", REPO, "09-20 10:58", """parse: read "dz" and "doz" as dozen

A Saturday order for "2 dz everything bagels" became 2 bagels.""",
                 ["M\tsrc/fernbrook/parse.py", "M\ttests/test_parse.py"]))

    s.add(codex("fern-codex-cutoff", "09-21 14:30", 40, REPO, """
U: change the next-day pickup cutoff. it's noon now, starting tomorrow (tue sep 22). dana texted it a while ago, I almost forgot
T exec_command: rg -n "cutoff" config src
R: config/locations.yaml:2:next_day_cutoff: "14:00"
src/fernbrook/app.py:61:    if order.placed_at.time() > CUTOFF:
A: The cutoff is a single setting today, `next_day_cutoff: "14:00"`. Since the change starts on a date, I'll make it a dated list so today's orders keep the 2pm rule and orders from Tuesday on use noon:

```yaml
next_day_cutoff:
  - from: 2026-01-01
    time: "14:00"
  - from: 2026-09-22
    time: "12:00"
```
T apply_patch: *** Begin Patch
*** Update File: config/locations.yaml
@@
-next_day_cutoff: "14:00"
+next_day_cutoff:
+  - from: 2026-01-01
+    time: "14:00"
+  - from: 2026-09-22
+    time: "12:00"
*** End Patch
R: Success. Updated the following files:
M config/locations.yaml
T exec_command: uv run pytest -q
R: ...............................
31 passed in 16.88s
A: Done. From Tuesday September 22, next-day pickup orders have to arrive by 12:00 noon; later ones get a reply offering the day after. The late reply says "Orders for tomorrow close at noon."
"""))

    s.add(commit("fern-commit-cutoff", REPO, "09-21 15:05", """orders: next-day cutoff is noon from Sep 22

The cutoff is now a dated list in config/locations.yaml, so orders placed
before the change keep the 2pm rule.""", ["M\tconfig/locations.yaml", "M\tsrc/fernbrook/app.py",
                                            "M\ttests/test_cutoff.py"]))

    s.add(dana("fern-texts-0924", "09-24", """
12:10 Jules: Personal order! Could I get a lemon olive oil cake for my mom's birthday? Pickup Saturday the 26th at Old Town, around 2
12:31 Dana Whitfield: for you? on the house. I'll write happy birthday on it. what's her name
12:33 Jules: Ruth. Thank you Dana, that's so kind
12:34 Dana Whitfield: 💛
"""))

    s.add(email("fern-wholesale", "Wholesale?", [
        Mail(DANA, JULES, "09-24 15:20", """Jules,

Two cafés asked about buying from us wholesale: Little Fox Coffee and Sprocket Café. They'd want standing
weekly orders they can change online, and an invoice each month. Could the system you built handle that, or is it a
new thing? What would it cost?

Ray (my husband, he does the books) will want to see a number.

Dana"""),
        Mail(JULES, DANA, "09-25 09:40", """Hi Dana,

It's a new piece, but it can reuse the kitchen sheet and the menu. My estimate for a wholesale portal (each café
logs in, sets a standing weekly order, changes it until the cutoff, and gets one invoice a month) is $3,200 and
about 3 weeks. Wren Castillo, the designer I work with, would design the portal.

No rush. Talk it over with Ray and let me know.

Jules""", cc=cast.WREN),
    ]))

    s.add(dana("fern-texts-0926", "09-26", """
08:15 Dana Whitfield: Riverside finally got its own phone line for orders: (720) 555-0186
08:16 Dana Whitfield: can texts to THAT number go straight to the riverside tab? people who text it obviously mean riverside
08:40 Jules: Yes, easy. I'll route by the number they text. Monday ok?
08:41 Dana Whitfield: perfect
14:05 Jules: Picked up the cake, it's gorgeous. Thank you!!
14:20 Dana Whitfield: happy birthday to Ruth!
"""))

    s.add(claude("fern-claude-routing", "09-27 11:00", 42, REPO, "Route Riverside order line to its tab", """
U: fernbrook: riverside has its own order number now, +17205550186. anything texted to that number goes to the riverside tab, skip the location question. the main number keeps working like now
T Read: /Users/jules/code/fernbrook-orders/src/fernbrook/app.py
R: @app.post("/sms")
async def sms(request: Request):
    form = await request.form()
    order = await parse_order(form["Body"])
A: The webhook ignores the `To` field today. I'll add `numbers:` to `config/locations.yaml` mapping `+17205550186` to Riverside. When a text arrives on a mapped number, the parser gets the location as a fixed value and never asks for it.
T Edit: /Users/jules/code/fernbrook-orders/src/fernbrook/app.py
R: The file has been updated successfully.
T Bash: uv run pytest -q tests/test_routing.py
R: ...
3 passed in 0.27s
A: Done. Texts to (720) 555-0186 go to the Riverside tab; the main number still asks for a location when the customer leaves it out.
"""))

    s.add(note("fern-numbers", "Clients/Fernbrook/Fernbrook launch numbers.md", """# Fernbrook launch numbers

First week of text ordering (Sat Sep 19 to Fri Sep 25):

| | |
|---|---|
| Text orders | 212 |
| Sent to Needs a human | 9 (4%) |
| Average order | $38 |
| No-shows | 3 |

Needs a human reasons: 4 questions mixed into orders, 3 no location (before Riverside got its own number), 2 the
"dz" bug on day two.

For the case study. Ask Dana if we can use the name.
""", "09-28 09:30"))

    s.add(email("fern-final", "Invoice MW-1041 from Marlow Works", [
        Mail(JULES, DANA, "09-28 10:02", """Hi Dana,

Here's the final invoice for text ordering: MW-1041, $2,880 (the remaining 60%). Hosting at $45 a month starts in
October.

Also, would you be OK with me writing up the project as a case study on my website, with Fernbrook's name?

Jules""", attachments=("MW-1041.pdf",)),
        Mail(DANA, JULES, "09-29 06:15", """Paid this morning! And yes, use our name. Benji says the kitchen sheet is
a lifesaver, you can quote him on that.

Dana"""),
    ]))

    s.add(thread("fern-gh-4", REPO, 4, "issue", "Holiday closures", GH_ME, "09-29 16:30", """
Dana wants closures she can set ahead of time, not only weekly ones. First one: all three locations are closed on
Thanksgiving, Thursday November 26. Orders for that day should get the same "we're closed" reply with the next
open day.

Probably a `closed_dates` list per location in `config/locations.yaml`.
""", labels=("enhancement",)))

    s.ask(
        Q("fern-01", "single", "When did Fernbrook's fall menu launch, the day text ordering had to be live?",
          "Saturday, September 19.", ("fern-texts-0901", "fern-proposal"), evidence_has=("Saturday the 19th",)),
        Q("fern-02", "update", "What's the cutoff for next-day pickup orders at Fernbrook now?",
          "Noon (12:00) the day before, since Tuesday September 22; it used to be 2pm.",
          ("fern-texts-0911", "fern-codex-cutoff", "fern-commit-cutoff"), evidence_has=("noon", "Sep 22")),
        Q("fern-03", "single", "Which Fernbrook location is closed on Mondays, and since when?",
          "Eastgate, starting Monday September 14.", ("fern-texts-0911", "fern-codex-closures"),
          evidence_has=("Eastgate is closed Mondays", "14")),
        Q("fern-04", "single", "What should the Fernbrook bot do with an order for Monday pickup at Eastgate?",
          "Reply and let the customer pick: Old Town on Monday, or Eastgate on Tuesday.",
          ("fern-texts-0911", "fern-codex-closures"), evidence_has=("Old Town on Monday", "Eastgate on Tuesday")),
        Q("fern-05", "single", "What's the largest order Fernbrook takes by text?",
          "6 dozen; bigger orders get a reply asking them to call the store.", ("fern-spec",),
          evidence_has=("6 dozen",)),
        Q("fern-06", "single", "Is there a per-order limit on Fernbrook's pumpkin cardamom buns?",
          "Yes, 2 dozen per order.", ("fern-texts-0909", "fern-texts-0919"), evidence_has=("2 dozen",)),
        Q("fern-07", "single", "What time can the Fernbrook system start texting customers?",
          "Not before 8am Denver time; confirmations for orders between 9pm and 8am wait in a queue and go out at "
          "8:00.", ("fern-spec", "fern-claude-quiet"), evidence_has=("8am", "08:00")),
        Q("fern-08", "single", "Why does Fernbrook never text customers before 8am?",
          "Their old ordering service sent confirmations at 5am when the bakers started, and customers complained.",
          ("fern-spec",), evidence_has=("complained",)),
        Q("fern-09", "single", "What's the price of the Fernbrook text-ordering project, and how big was the deposit?",
          "$4,800 fixed; the deposit was 40%, $1,920.", ("fern-proposal", "fern-deposit"),
          evidence_has=("$4,800", "$1,920")),
        Q("fern-10", "single", "What does Fernbrook pay each month after launch?",
          "$45 a month for hosting and the texting number.", ("fern-proposal", "fern-final"), evidence_has=("$45 a month",)),
        Q("fern-11", "temporal", "When did Fernbrook pay its deposit, and how?",
          "September 4, 2026, by card through Stripe (invoice MW-1038).", ("fern-deposit",),
          evidence_has=("September 4, 2026", "MW-1038")),
        Q("fern-12", "single", "Where do Fernbrook orders go when the parser isn't sure about them?",
          "To the Needs a human tab of the kitchen sheet (confidence below 0.8, a missing location, or a question "
          "in the text).", ("fern-spec", "fern-claude-parser"), evidence_has=("Needs a human",)),
        Q("fern-13", "single", "Which model reads Fernbrook's text orders, and how are quantities stored?",
          "claude-haiku-5-5 with a strict JSON schema; quantities are stored as single units (qty_each), so "
          "half a dozen is 6.", ("fern-claude-parser",), evidence_has=("claude-haiku-5-5", "qty_each")),
        Q("fern-14", "single", "How many text orders did Fernbrook get on launch morning?",
          "14 text orders before 9am on Saturday September 19.", ("fern-texts-0919",), evidence_has=("14 text orders",)),
        Q("fern-15", "single", "What was the dozen bug at Fernbrook after launch?",
          "\"2 dz everything bagels\" was read as 2 bagels; since September 20, \"dz\" and \"doz\" count as dozen "
          "and that order was corrected to 24.", ("fern-texts-0920", "fern-commit-dz"), evidence_has=("dz", "doz")),
        Q("fern-16", "single", "What did Benji from Eastgate ask for in the kitchen sheet?",
          "Sort the Eastgate tab by pickup time, and add an allergy notes column.", ("fern-benji",),
          evidence_has=("pickup time", "allergy notes")),
        Q("fern-17", "single", "What's the phone number of Fernbrook Riverside's new order line?",
          "(720) 555-0186.", ("fern-texts-0926", "fern-claude-routing"), evidence_has=("555-0186",)),
        Q("fern-18", "single", "How are texts to Fernbrook Riverside's own number handled?",
          "They're routed by the number texted straight to the Riverside tab, with no location question.",
          ("fern-texts-0926", "fern-claude-routing"), evidence_has=("Riverside tab",)),
        Q("fern-19", "single", "What did I estimate for Fernbrook's wholesale portal?",
          "$3,200 and about 3 weeks, with Wren Castillo designing the portal.", ("fern-wholesale",),
          evidence_has=("$3,200", "3 weeks")),
        Q("fern-20", "single", "Which cafés want to buy wholesale from Fernbrook?",
          "Little Fox Coffee and Sprocket Café.", ("fern-wholesale",), evidence_has=("Little Fox Coffee", "Sprocket")),
        Q("fern-21", "single", "What are Fernbrook Bakery's three locations?",
          "Old Town, Eastgate and Riverside.", ("fern-spec",), evidence_has=("Old Town", "Eastgate", "Riverside")),
        Q("fern-22", "single", "How do Fernbrook customers pay for text orders?",
          "At pickup; there's no online payment in version 1.", ("fern-spec",), evidence_has=("Payment at pickup",)),
        Q("fern-23", "multi", "How many text orders did Fernbrook get in its first week, and how many needed a person?",
          "212 orders from September 19 to 25; 9 of them (4%) went to Needs a human.", ("fern-numbers",),
          evidence_has=("212", "9 (4%)")),
        Q("fern-24", "single", "What's the average text order at Fernbrook?",
          "$38.", ("fern-numbers",), evidence_has=("$38",)),
        Q("fern-25", "single", "What was Fernbrook's final invoice, and is it paid?",
          "MW-1041 for $2,880, the remaining 60%; Dana paid it on the morning of September 29.", ("fern-final",),
          evidence_has=("MW-1041", "$2,880", "Paid")),
        Q("fern-26", "single", "Can I use Fernbrook's name in a case study on my website?",
          "Yes. Dana agreed on September 29 and said I can quote Benji calling the kitchen sheet a lifesaver.",
          ("fern-final",), evidence_has=("use our name", "lifesaver")),
        Q("fern-27", "single", "Are Fernbrook's locations open on Thanksgiving?",
          "No, all three are closed on Thursday November 26; it's tracked in issue #4.", ("fern-gh-4",),
          evidence_has=("November 26",)),
        Q("fern-28", "multi", "Who referred Theo Brandt from Copperline to me?",
          "Dana Whitfield of Fernbrook Bakery.", ("fern-texts-0901", "cop-intro"), evidence_has=("Theo Brandt", "Dana")),
        Q("fern-29", "temporal", "How many days after Fernbrook paid the deposit did text ordering go live?",
          "15 days: the deposit was paid September 4 and ordering went live September 19.",
          ("fern-deposit", "fern-texts-0919"), evidence_has=("September 4", "LIVE")),
        Q("fern-30", "single", "Who does the books at Fernbrook Bakery?",
          "Ray, Dana's husband.", ("fern-wholesale",), evidence_has=("Ray",)),
        Q("fern-31", "single", "How does the Fernbrook bot handle an order that says nothing about which store?",
          "It replies asking which location; it never guesses (texts to the Riverside line are the exception).",
          ("fern-claude-parser", "fern-spec", "fern-claude-routing"), evidence_has=("ask", "skip the location question")),
    )
    return s
