from cast import MOM_NAME, MOM_PHONE, PAT_NAME, PAT_PHONE, SAM_NAME, SAM_PHONE, TRAILHEADS
from model import Q, Story, group, hermes, texts


def chat(name, phone):
    return lambda key, day, script: texts(key, name, phone, day, script)


mom, sam, pat = chat(MOM_NAME, MOM_PHONE), chat(SAM_NAME, SAM_PHONE), chat(PAT_NAME, PAT_PHONE)


def build():
    s = Story()
    s.add(mom("pe-mom-0908", "09-08", """
18:02 Mom: Booked my birthday dinner! Saturday the 26th, 6pm at Lucia's in Fort Collins
18:03 Mom: reservation for 5 under Marlow. Cass is driving up from Colorado Springs!
18:10 Jules: Yay! I'll be there. I'll bring a cake
18:11 Mom: you don't have to!!
18:11 Jules: I want to 🙂
"""))

    s.add(sam("pe-sam-0902", "09-02", """
17:40 Sam Ito: still on for climbing tonight?
17:41 Jules: yes! 6:30 at Granite Room like always
17:42 Sam Ito: tuesdays and thursdays forever
17:42 Jules: forever
21:15 Sam Ito: you can keep my grigri till thursday, I have the other one
21:16 Jules: thanks, I'll bring it back
"""))

    s.add(group("pe-trail-0908", "Trailheads", TRAILHEADS, "09-08", """
19:02 Nora Vance: ok Saturday the 12th: Mount Falcon, Castle Trail loop. meet at my place at 7am, I'll drive
19:04 Sam Ito: in
19:05 Jules: in! I'll bring snacks
19:20 Eli Brooks: out, sorry, cousin's wedding that weekend 😭
19:21 Nora Vance: boo. next time
"""))

    s.add(pat("pe-pat-0917", "09-17", """
11:30 Pat Keane: Hi Jules, the furnace service is booked for Tuesday the 22nd between 9 and 11. Can you be home to let them in?
11:52 Jules: Yes, I'll be home. Thanks Pat
"""))

    s.add(hermes("pe-hermes-gift", "09-20 20:10", 12, "Birthday gift for Mom", """
U: help me pick a birthday gift for my mom, she's really into gardening. under $60. need it by saturday the 26th
T web_search: best gardening gifts under $60 2026
R: Hori hori knives, goatskin gloves, kneeling pads, seed subscriptions, pruners ...
A: A few ideas that a serious gardener actually uses:
1. A hori hori knife (Japanese soil knife for weeding, digging and dividing), about $30 to $40.
2. Goatskin gardening gloves: thorn-resistant and they soften with use, about $20 to $25.
3. Felco-style bypass pruners, about $55 to $60.
4. A kneeling pad that converts to a seat, about $45.
The hori hori and the gloves together come in under $60 and arrive in 2 days with standard shipping.
U: do the hori hori and the gloves. size medium gloves
A: Good choice. Order a hori hori knife and a pair of medium goatskin gloves; with 2-day shipping they arrive by Wednesday the 23rd, in time for Saturday.
U: ordered
A: 🎁 Nice. She'll use both.
"""))

    s.add(pat("pe-pat-0920", "09-20", """
14:05 Pat Keane: Lease renewal is in your email. Rent goes from $1,850 to $1,925 starting November 1. Please sign by September 30
14:30 Jules: Got it, thanks for keeping it reasonable. I'll sign this week
"""))

    s.add(pat("pe-pat-0928", "09-28", """
09:12 Jules: Signed the lease renewal and sent it back this morning
09:40 Pat Keane: Got it, thanks!
"""))

    s.ask(
        Q("pe-01", "single", "When and where is Mom's birthday dinner?",
          "Saturday September 26 at 6pm at Lucia's in Fort Collins, a reservation for 5 under Marlow.",
          ("pe-mom-0908",), evidence_has=("Lucia's", "6pm")),
        Q("pe-02", "multi", "Where and when am I picking up Mom's birthday cake, and what kind is it?",
          "A lemon olive oil cake from Fernbrook's Old Town location, pickup Saturday September 26 around 2pm; "
          "Dana made it on the house.", ("fern-texts-0924",), evidence_has=("lemon olive oil", "Old Town")),
        Q("pe-03", "single", "What's my mom's first name?",
          "Ruth.", ("fern-texts-0924",), evidence_has=("Ruth",)),
        Q("pe-04", "single", "When and where do Sam and I go climbing?",
          "Tuesdays and Thursdays at 6:30pm at Granite Room.", ("pe-sam-0902",),
          evidence_has=("Granite Room", "tuesdays and thursdays")),
        Q("pe-05", "single", "Whose belay device do I have?",
          "Sam's GriGri; Sam said to keep it until Thursday.", ("pe-sam-0902",), evidence_has=("grigri",)),
        Q("pe-06", "single", "Where did the Trailheads meet for the September 12 hike, and who drove?",
          "At Nora's place at 7am; Nora drove.", ("pe-trail-0908",), evidence_has=("my place at 7am",)),
        Q("pe-07", "single", "Who missed the Trailheads hike on September 12, and why?",
          "Eli Brooks, because of a cousin's wedding.", ("pe-trail-0908",), evidence_has=("wedding",)),
        Q("pe-08", "single", "Which trail did the Trailheads hike on September 12?",
          "The Castle Trail loop at Mount Falcon.", ("pe-trail-0908",), evidence_has=("Castle Trail",)),
        Q("pe-09", "single", "When is the furnace service at my place?",
          "Tuesday September 22, between 9 and 11.", ("pe-pat-0917",), evidence_has=("between 9 and 11",)),
        Q("pe-10", "single", "What will my rent be after the lease renewal, and from when?",
          "$1,925 a month from November 1, up $75 from $1,850.", ("pe-pat-0920",), evidence_has=("$1,925", "November 1")),
        Q("pe-11", "temporal", "Did I sign the lease renewal before Pat's deadline?",
          "Yes: signed and sent back on September 28; the deadline was September 30.",
          ("pe-pat-0920", "pe-pat-0928"), evidence_has=("September 30", "Signed the lease renewal")),
        Q("pe-12", "single", "What did I buy Mom for her birthday?",
          "A hori hori garden knife and a pair of medium goatskin gardening gloves.",
          ("pe-hermes-gift",), evidence_has=("hori hori", "goatskin")),
        Q("pe-13", "single", "Is Cass coming to Mom's birthday dinner, and from where?",
          "Yes, Cass is driving up from Colorado Springs.", ("pe-mom-0908",), evidence_has=("Colorado Springs",)),
    )
    return s
