from cast import JULES, MOM_NAME, MOM_PHONE, SAM_NAME, SAM_PHONE, TRAILHEADS
from model import Mail, Q, Story, email, group, hermes, note, texts

ODETTE_NAME, ODETTE_PHONE = "Odette Rinaldi", "+17205550126"
CYCLES_NAME, CYCLES_PHONE = "Alder Street Cycles", "+17205550109"
TALLOW = "Tallow & Thyme <reservations@tallowandthyme.example>"
CLINIC = "Bellcourt Family Medicine <noreply@bellcourtfm.example>"


def build():
    s = Story()
    s.add(texts("b6-mom-0903", MOM_NAME, MOM_PHONE, "09-03", """
09:02 Mom: Morning honey. Leon and I talked and we'd like to come down for my birthday weekend if you're free. Saturday the 26th, home Sunday after lunch.
09:15 Jules: yes!! absolutely. staying at my place or a hotel?
09:17 Mom: We booked the Inn at Wazee Street, one night. Your couch is not good for Leon's back.
09:18 Jules: fair. the couch has hurt better people than leon
09:21 Mom: Ha. We'll leave after lunch and be at your place around 3.
09:22 Jules: perfect. i'll book dinner for saturday night. anything you two want?
09:26 Mom: Somewhere quiet please. Leon can't hear a thing in those loud places, he just nods.
09:26 Mom: And no stairs. His knee still isn't right since the surgery in August. Ground floor only.
09:28 Jules: quiet, ground floor. got it
09:29 Mom: You don't have to do anything fancy.
09:30 Jules: it's your birthday, i'm doing something a little fancy
09:31 Mom: Love you sweetie.
"""))

    s.add(email("b6-physical", "Your appointment has been rescheduled", [
        Mail(CLINIC, JULES, "09-08 10:12", """Hello Jules,

Your appointment at Bellcourt Family Medicine has been rescheduled.

Previous: Tuesday, September 15, 2026 at 8:00 AM
New:      Tuesday, September 29, 2026 at 8:40 AM
Provider: Dr. Imani Castellano
Visit:    Annual physical with fasting bloodwork

Dr. Castellano is out of the office on September 15. We're sorry for the change.

Before your visit:
- Fasting labs: no food for 10 hours before your appointment. Water is fine, and you can take your usual
  medications with water.
- Bring your insurance card and a photo ID.
- Please arrive 10 minutes early to check in at the front desk, 2nd floor.

To cancel or change this appointment, reply in the patient portal or call us at (303) 555-0147.

Bellcourt Family Medicine
This mailbox is not monitored."""),
    ], labels=("INBOX",)))

    s.add(hermes("b6-hermes-dinner", "09-09 12:25", 9, "Quiet step-free dinner spot for Mom's birthday", """
U: need a dinner spot for saturday sep 26, party of 4. me, my mom, her partner leon, my friend sam. has to be quiet and ground floor with no stairs. somewhere around lohi or highland ideally
T web_search: quiet restaurants Highland LoHi Denver ground floor step-free
R: 1. Tallow & Thyme, Highland. Seasonal American, small dining room, carpeted, single level. 2. Ferro, LoHi. Wood-fired Italian, ground floor. 3. Wildflower Cantina, LoHi. Rooftop, very loud on weekends.
A: A few that fit:

1. **Tallow & Thyme** (Highland). Small single-level dining room with carpet and soft lighting, so it stays quiet. Good for a birthday.
2. **Ferro** (LoHi). Ground floor, step-free entrance. Livelier than Tallow & Thyme, but the back room is calmer.
3. Wildflower Cantina is out. It's a rooftop and it's loud on weekends.

I'd try Tallow & Thyme first for quiet, Ferro second.
U: ferro only has 8:45 left that night, too late for leon. tallow & thyme had 6:15 so I grabbed it
A: Good call. 6:15 at Tallow & Thyme on Saturday the 26th, party of 4. Want me to remind you to tell your mom the time?
U: nah I'll text her. thanks
A: OK. Enjoy the dinner.
"""))

    s.add(email("b6-tallow", "Your reservation at Tallow & Thyme", [
        Mail(TALLOW, JULES, "09-09 12:41", """Hi Jules,

You're booked!

Tallow & Thyme
Saturday, September 26, 2026 at 6:15 PM
Party of 4
Confirmation: TT-48213

Requests we noted: ground-floor table near the back, quiet corner, celebrating a birthday.

Our dining room is all on one level with a step-free entrance on 32nd Avenue.

Cancellation policy: please cancel or change at least 24 hours before your reservation. Cancellations within 24
hours, and no-shows, are charged $25 per person to the card on file.

See you soon,
The Tallow & Thyme team
3216 W 32nd Ave, Denver"""),
    ], labels=("INBOX",)))

    s.add(texts("b6-sam-0910", SAM_NAME, SAM_PHONE, "09-10", """
12:04 Jules: are you free saturday the 26th for dinner? my mom and leon are down for her birthday. tallow & thyme, 6:15
12:09 Sam: your mom who sends you tomato photos? obviously yes
12:10 Sam: one catch, i fly to chicago at 6am sunday for the offsite. i have to leave by 8:30
12:11 Jules: that's fine, leon will be asleep by 9 anyway
12:12 Sam: should i bring anything
12:14 Jules: just you. maybe don't bring up deepwater station, mom will want the whole plot
12:15 Sam: i will be a lake
12:15 Jules: a calm lake
12:16 Sam: Loved “a calm lake”
"""))

    s.add(group("b6-trail-0910", "Trailheads", TRAILHEADS, "09-10", """
18:02 Nora Vance: saturday hike still on? Red Hawk Ridge loop
18:03 Eli Brooks: yes. 8am at Nora's like we said?
18:05 Sam Ito: forecast says storms after 1. that loop is all exposed up top
18:06 Nora Vance: ugh you're right. let's meet at 6:45 instead and be off the ridge by noon
18:07 Jules: 6:45 works. who's driving
18:07 Nora Vance: me, the outback fits four. meet at my place
18:08 Eli Brooks: 6:45 is a crime but fine
18:09 Sam Ito: i'll bring the good trail mix. the one with the chocolate
18:10 Jules: i'll bring coffee for everyone so eli survives
18:11 Eli Brooks: Loved “i'll bring coffee for everyone so eli survives”
"""))

    s.add(texts("b6-odette-0914", ODETTE_NAME, ODETTE_PHONE, "09-14", """
10:21 Odette Rinaldi: Hi Jules, it's Odette from 2C. Could you still feed Mochi while I'm at my sister's? I leave Wednesday the 16th early and fly back Tuesday the 22nd.
10:34 Jules: yes of course. same as june?
10:36 Odette Rinaldi: Almost. In June she was only on dry food. Now the vet has her on a thyroid pill, so mornings are different.
10:37 Odette Rinaldi: Morning around 7: half a can of the salmon wet food, and the pill tucked inside a Pill Pocket treat. She eats it if it's in the treat, she spits it out if it's in the food.
10:38 Odette Rinaldi: Evening: one scoop of dry food. Fresh water once a day.
10:40 Jules: pill in the pill pocket, not the food. got it
10:41 Odette Rinaldi: I'll leave the spare key in your mailbox Tuesday night.
10:42 Jules: sounds good. i'll send you mochi photos
10:43 Odette Rinaldi: Thank you, you're the best neighbor.
"""))

    s.add(texts("b6-bike-0917", CYCLES_NAME, CYCLES_PHONE, "09-17", """
14:48 Alder Street Cycles: Hi Jules, this is Alder Street Cycles. Your bike is ready. You mentioned the front brake squeaking, but the squeak was actually the rear pads, they were worn to the metal.
14:49 Alder Street Cycles: We replaced the chain and the rear brake pads and trued the back wheel. Total is $112.50. We're open until 6 today, closed Mondays.
15:20 Jules: great, thanks. i'll grab it tomorrow after lunch
15:22 Alder Street Cycles: Sounds good, it'll be on the rack by the counter.
"""))

    s.add(note("b6-errands", "Personal/September errands.md", """---
tags: [personal, errands]
---
# September errands

- [x] Passport renewal. Mailed Sep 4 with the old passport and photos. They say 6 to 8 weeks.
- [x] Car registration. The mailed notice said $201.15, but that includes a $13.75 mail-in fee. Paid online Sep 18,
  $187.40. Sticker should come in about 2 weeks.
- [x] Bike tune-up at Alder Street Cycles. Picked up Sep 18.
- [x] Dinner for Mom's birthday. Tallow & Thyme, Sat 26th, 6:15.
- [ ] Mom's gift. Photo book from the Ouray trip, ordered Sep 12, should arrive Sep 24.
- [ ] Feed Mochi for Odette, Sep 16 to 22. Pill in the Pill Pocket.
""", "09-20 09:15"))

    s.ask(
        Q("b6-01", "single", "What's the fee if we cancel the birthday dinner reservation late?",
          "$25 per person if cancelled within 24 hours of the reservation, or for a no-show.", ("b6-tallow",),
          evidence_has=("$25 per person", "24 hours")),
        Q("b6-02", "single", "Why did Mom want a restaurant with no stairs?",
          "Leon's knee still isn't right since his surgery in August, so ground floor only.", ("b6-mom-0903",),
          evidence_has=("knee", "surgery in August")),
        Q("b6-03", "single", "What time are Mom and Leon getting to my place on the 26th?",
          "Around 3 in the afternoon; they leave after lunch.", ("b6-mom-0903",),
          evidence_has=("be at your place around 3",)),
        Q("b6-04", "multi", "What time do I need to be at the restaurant for Mom's birthday, and when does Sam have to head out?",
          "Dinner is at 6:15 PM at Tallow & Thyme on Saturday Sep 26; Sam has to leave by 8:30 for a 6am Sunday flight to Chicago.",
          ("b6-tallow", "b6-hermes-dinner", "b6-sam-0910", "b6-errands"), evidence_has=("6:15", "leave by 8:30")),
        Q("b6-05", "single", "When and where did we meet for the Red Hawk Ridge hike, and who drove?",
          "6:45 at Nora's place (moved up from 8am because of afternoon storms); Nora drove.", ("b6-trail-0910",),
          evidence_has=("meet at 6:45", "meet at my place")),
        Q("b6-06", "single", "Who was bringing snacks on the Trailheads hike?",
          "Sam was bringing trail mix (the one with chocolate); Jules was bringing coffee.", ("b6-trail-0910",),
          evidence_has=("good trail mix",)),
        Q("b6-07", "single", "How do I get Odette's cat to take her medicine?",
          "Tuck the thyroid pill inside a Pill Pocket treat in the morning; she spits it out if it's mixed into the food.",
          ("b6-odette-0914", "b6-errands"), evidence_has=("Pill Pocket",)),
        Q("b6-08", "temporal", "How many nights is Odette away this time?",
          "6 nights: she leaves Wednesday Sep 16 and flies back Tuesday Sep 22.", ("b6-odette-0914", "b6-errands"),
          evidence_has=("Wednesday the 16th", "Tuesday the 22nd")),
        Q("b6-09", "single", "When's my physical now?",
          "Tuesday Sep 29 at 8:40 AM with Dr. Imani Castellano at Bellcourt Family Medicine (moved from Sep 15).",
          ("b6-physical",), evidence_has=("September 29, 2026 at 8:40 AM",)),
        Q("b6-10", "single", "Can I have breakfast before the physical?",
          "No. It has fasting bloodwork: no food for 10 hours before; water and usual medications are fine.",
          ("b6-physical",), evidence_has=("no food for 10 hours",)),
        Q("b6-11", "single", "How much did I end up paying for the car registration?",
          "$187.40, paid online on Sep 18 (the mailed notice's $201.15 included a $13.75 mail-in fee).",
          ("b6-errands",), evidence_has=("$187.40",)),
        Q("b6-12", "single", "When did I send in the passport renewal?",
          "September 4; they quoted 6 to 8 weeks.", ("b6-errands",), evidence_has=("Mailed Sep 4",)),
        Q("b6-13", "single", "What did the bike shop actually fix when I brought my bike in for the squeaky brake?",
          "Replaced the chain and the rear brake pads and trued the back wheel, for $112.50. The squeak was the rear pads, not the front brake.",
          ("b6-bike-0917",), evidence_has=("replaced the chain and the rear brake pads", "$112.50")),
    )
    return s
