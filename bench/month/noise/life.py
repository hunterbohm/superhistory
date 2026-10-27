import re

import cast
from model import Story, group, note, texts

CONTACTS = {
    "Sam": (cast.SAM_NAME, cast.SAM_PHONE),
    "Mom": (cast.MOM_NAME, cast.MOM_PHONE),
    "Pat": (cast.PAT_NAME, cast.PAT_PHONE),
    "Wren": (cast.WREN_NAME, cast.WREN_PHONE),
    "Cass": ("Cass Marlow", "+13035550174"),
    "Walt": ("Walt Ferreira", "+17205550188"),
    "Priya": ("Priya Nandakumar", "+16125550123"),
    "Marcus": ("Marcus Bell", "+13035550196"),
}
NAMES = {alias: name for alias, (name, _) in CONTACTS.items()}
NAMES.update({"Nora": "Nora Vance", "Eli": "Eli Brooks"})
SPEAKER = re.compile(r"^(\d\d:\d\d) (\w+):", re.M)

CHATS = []
NOTES = []

CHATS += [
    ("Sam", "09-01", """
09:41 Sam: [attachment: IMG_4402.HEIC]
09:41 Sam: taped to the fridge in our breakroom this morning
09:43 Jules: "please label your food. we are not animals."
09:43 Jules: and somebody wrote "we are tho" underneath
09:44 Sam: that's gareth's handwriting. he will deny it under oath
09:46 Jules: the confidence of the capital T
09:46 Sam: Laughed at “the confidence of the capital T”
12:18 Sam: ok Deepwater Station question. did you catch the thing with the radio in ep 3
12:30 Jules: the static? yeah i paused and went back
12:31 Jules: it's morse right
12:31 Sam: IT'S MORSE
12:32 Sam: i ran it through an online decoder and got "bring snacks"
12:33 Jules: so either the show is trolling us or your decoder is broken
"""),
    ("Sam", "09-03", """
15:52 Sam: ok so you know the vendor comparison deck i built
15:52 Sam: gareth just presented it at the all-hands. my slides. his name in the footer
15:54 Jules: wait what. did he change the footer or did you not have one
15:55 Sam: he swapped the template. footer now says "G. Prewitt, Strategy"
15:55 Sam: i don't even know what strategy is. it's a word he uses
15:57 Jules: did anyone say anything
15:58 Sam: roxanne said "great work gareth" and moved on
15:58 Sam: :)
16:01 Jules: ugh. that's grim. is the original file in the shared drive with your name on it
16:02 Sam: version history has everything
16:03 Jules: then you have the receipts. maybe just mention it in your 1:1 without making it a Thing
16:05 Sam: yeah. i'm going to be so calm about it
16:05 Sam: i'm going to be a lake
16:06 Jules: a very still lake with a body at the bottom
16:06 Sam: Loved “a very still lake with a body at the bottom”
"""),
    ("Sam", "09-05", """
11:20 Sam: ok you have to listen to this podcast i've been on. Small Systems. two guys talking about how boring infrastructure actually runs a city
11:20 Sam: the episode about the water treatment plant is wild
11:24 Jules: boring infrastructure is my love language
11:24 Jules: which one is the water one
11:25 Sam: "Ten Thousand Valves"
11:25 Sam: [attachment: IMG_4417.HEIC]
11:26 Sam: also this is pickle. i'm dog sitting for my sister. he has eaten one sock and one (1) oven mitt
11:28 Jules: that is a very guilty face
11:28 Jules: is he ok
11:29 Sam: the vet line said he'll pass it. he's fine, i'm the one who's traumatized
"""),
    ("Sam", "09-08", """
21:14 Sam: ok no spoilers if you haven't seen 5
21:14 Sam: but
21:15 Jules: i'm watching it right now, you're gonna ruin it
21:15 Sam: SAY NOTHING THEN
21:16 Jules: i'm like 20 min in
21:16 Sam: you're before the elevator scene. stop texting me and go
21:17 Jules: ok ok
22:03 Jules: ...
22:03 Jules: the captain's log thing
22:04 Sam: RIGHT?
22:04 Jules: i did not see that coming. i thought it was the engineer the whole time
22:05 Sam: everyone thinks it's the engineer. that's the point, they've been feeding us the engineer
22:07 Jules: you have a corkboard with string don't you
22:07 Sam: i have a spreadsheet, which is worse
22:08 Jules: of course you do
"""),
    ("Sam", "09-11", """
13:05 Sam: new thing at work, we now have a "sync of syncs"
13:05 Sam: it's a meeting where the people who run the weekly syncs sync
13:07 Jules: that's a meeting about meetings
13:07 Jules: who runs it
13:08 Sam: roxanne. she made a slide for it. the slide has a venn diagram
13:09 Jules: how many circles
13:09 Sam: four
13:10 Jules: four circle venn is a cry for help
13:10 Sam: and none of them overlap. it's just four circles that don't touch each other
13:12 Jules: so it's accurate
13:12 Sam: Laughed at “so it's accurate”
13:40 Sam: she also asked me to "own the narrative" for q4
13:41 Jules: what does that mean
13:41 Sam: nobody knows. i'm told it is "a mindset"
"""),
    ("Sam", "09-14", """
08:55 Jules: do you still have that ramen place bookmarked, the one near the stadium
08:58 Sam: Noodle Compass. yes. get the black garlic one. skip the spicy miso, it's spice with no personality
08:59 Jules: noted
09:00 Sam: what's the occasion
09:02 Jules: none, i've been eating the same three things for a week
09:03 Sam: same. i have rotated between one burrito and one different burrito
09:03 Jules: a cultured man
09:15 Sam: read anything good lately? my brain is just slides
09:17 Jules: just started Salt Road, a friend from college swore by it
09:18 Sam: is it sad
09:18 Jules: it's about a trucker and a lighthouse keeper so yes, probably
09:19 Sam: sold
"""),
    ("Sam", "09-16", """
12:47 Sam: someone microwaved fish. in the shared kitchen. at 12:45
12:47 Sam: it's been seven minutes and i can taste it
12:48 Jules: you can't prove it's fish
12:48 Sam: sir i can prove it's fish
12:50 Sam: [attachment: IMG_4431.HEIC]
12:50 Sam: there's now a post-it on the microwave. it says "kindly consider others"
12:51 Sam: and below it, in a different color: "kindly consider salmon"
12:52 Jules: this office has more drama than Deepwater Station
12:52 Sam: and about as well-resolved
"""),
    ("Sam", "09-18", """
18:30 Sam: [attachment: IMG_4436.HEIC]
18:30 Sam: this sandwich is the size of my head. no context. just wanted you to see
18:33 Jules: who hurt you
18:33 Jules: also where is this
18:34 Sam: a deli on the east side, Pioneer Deli. they cut it with a saw
18:34 Jules: i need this
"""),
    ("Sam", "09-21", """
10:14 Sam: update: #random is now #connection-and-culture
10:14 Sam: this was a decision
10:15 Jules: who decided
10:15 Sam: a committee. there was a survey
10:16 Jules: did the survey have a question about renaming the channel
10:16 Sam: it had a question about "how do you feel about our communication channels" and then somebody extrapolated
10:18 Jules: extrapolated into a whole new name
10:18 Sam: the memes are still going in there. today someone posted a cat in a suit. it has 14 reactions
10:19 Jules: peak culture and connection
10:19 Sam: Loved “peak culture and connection”
10:40 Sam: gareth added a :synergy: emoji. it's a handshake that's slightly on fire
10:41 Jules: that's the most honest emoji i've ever heard of
"""),
    ("Sam", "09-23", """
17:02 Jules: question. my laptop battery health says 81%
17:02 Jules: is that bad, do i care
17:05 Sam: eh, that's about normal for a couple years in. you start caring around 70
17:05 Sam: unless it's dying at 40%, then care now
17:06 Jules: it's fine, it just gets warm
17:06 Sam: is something running in the background. my old one was hot because of a backup thing spinning forever
17:08 Jules: probably. i have like nine dev servers open at any given time
17:08 Sam: there it is
17:09 Sam: close nine dev servers
17:09 Jules: no
"""),
    ("Sam", "09-24", """
12:12 Sam: the raccoon one is back
12:12 Sam: [attachment: IMG_4452.HEIC]
12:13 Jules: "me explaining why my side project needs a database"
12:13 Sam: it's right there in the picture
12:14 Jules: it's too accurate. i'm actually a raccoon
12:15 Sam: you pick up shiny things and wash them and then they dissolve
12:15 Jules: that's the saddest thing anyone's said about my repos
"""),
    ("Sam", "09-26", """
10:31 Sam: [attachment: IMG_4457.HEIC]
10:31 Sam: farmers market haul. the peaches are the last of the season apparently
10:33 Jules: those look unreal
10:33 Jules: is the guy with the hot sauce there. the one with the skull on the label
10:35 Sam: yes! i got the habanero mango one. fair warning it's rated "regret"
10:35 Jules: i respect honest packaging
10:36 Sam: also i bought a mushroom log kit. i don't know why
10:36 Sam: now i'm a person with a mushroom log
10:38 Jules: do you water it
10:38 Sam: it says "mist twice a day". i've just been staring at it
10:39 Jules: Laughed at “i've just been staring at it”
"""),
    ("Sam", "09-28", """
14:20 Sam: roxanne is introducing "fun friday"
14:20 Sam: it's mandatory
14:21 Jules: mandatory fun
14:21 Sam: it's a 30 minute block where we "unplug and build team spirit"
14:22 Jules: via what
14:22 Sam: first one is a trivia game on the company platform. it has leaderboards
14:23 Jules: you love trivia
14:23 Sam: i love trivia with friends. not trivia where gareth gets points for being loud
14:25 Jules: just answer every question with "42" and look serene
14:25 Sam: oh that's good. that's very good
14:26 Sam: Loved “look serene”
"""),
    ("Sam", "09-30", """
22:47 Sam: DEEPWATER STATION
22:47 Sam: i need a minute
22:48 Jules: i'm on the last scene. don't type
22:55 Jules: ok
22:55 Jules: ok ok ok
22:56 Sam: the hatch
22:56 Jules: the HATCH
22:57 Sam: there's a season 2 right
22:57 Jules: they renewed it before the finale even aired, apparently
22:58 Sam: good. i can't do an open question for two years
22:58 Jules: you literally have a spreadsheet of open questions
22:59 Sam: that's different, those are MY open questions
"""),
]

CHATS += [
    ("Mom", "09-01", """
08:12 Mom: Good morning honey. The tomatoes are going crazy. I picked a whole bucket yesterday.
08:12 Mom: [attachment: IMG_2190.HEIC]
08:30 Jules: whoa that's a lot of tomatoes
08:30 Jules: are those the big striped ones
08:34 Mom: Yes the Cherokee ones you liked. And the little yellow ones. I don't know what to do with all of them. I gave a bag to Barb next door and she gave me zucchini. It never ends.
08:35 Jules: the zucchini exchange program
08:36 Mom: Ha. Yes. Leon says if he sees one more zucchini he is changing his name.
08:37 Jules: you could make sauce and freeze it
08:41 Mom: I might. Do you have that recipe with the roasted garlic
08:42 Jules: i'll dig it up and send it
08:42 Mom: Thank you sweetie. Love you.
"""),
    ("Mom", "09-04", """
16:03 Mom: Honey the words on my phone got tiny. I didn't do anything.
16:05 Jules: go to Settings, then Display & Brightness, then Text Size
16:05 Jules: there's a slider
16:09 Mom: I see Display. I don't see the slider.
16:10 Jules: it should say Text Size under Brightness. if you tap it you can drag the dot to the right
16:14 Mom: Oh I found it. It's huge now.
16:14 Mom: [attachment: IMG_2203.HEIC]
16:15 Jules: that's a lot. slide it back left a little
16:19 Mom: Better. I can read it without my glasses now. Thank you.
16:19 Mom: Who taught you all this?
16:20 Jules: you did, you gave me the first computer
16:22 Mom: That was a hand-me-down from your uncle. It had a floppy.
16:22 Jules: it had a modem that sounded like a cat in a blender
16:24 Mom: Ha. I remember. It was a long time ago.
"""),
    ("Mom", "09-06", """
19:48 Mom: Are you watching Orchard Lane?
19:48 Mom: Margaret just found the letter
19:50 Jules: i haven't started it yet, no spoilers!
19:51 Mom: Oh sorry. I'll tell you nothing.
19:51 Mom: But you would like the vicar.
19:53 Jules: the vicar is the best part of every village show
19:53 Mom: This one has a very good vicar.
19:54 Mom: And a goat.
19:55 Jules: ok now i have to watch it
"""),
    ("Mom", "09-10", """
10:04 Mom: The deer got into the hostas. All of them. They look like sticks.
10:04 Mom: [attachment: IMG_2218.HEIC]
10:20 Jules: oh no. those were so big
10:21 Mom: Leon says to spray them with something that smells like rotten eggs. Barb says to hang bars of soap on strings.
10:22 Mom: I think they are both pulling my leg.
10:23 Jules: actually the soap thing is real, people say it works. the egg spray too, it just smells awful
10:24 Mom: Of course it does.
10:24 Mom: I'll try the soap. Smells nicer.
10:25 Jules: the deer will smell like lavender
10:26 Mom: They deserve nothing.
"""),
    ("Mom", "09-12", """
14:40 Mom: Honey is this real? I got a text that says my package is held and I need to pay a fee.
14:40 Mom: [attachment: IMG_2226.HEIC]
14:44 Jules: no that's a scam. don't click the link
14:44 Jules: you can just delete it
14:46 Mom: I didn't click. It looked funny. The link had a lot of letters.
14:46 Jules: good instinct. real delivery companies don't ask for a fee by text
14:48 Mom: And I haven't ordered anything. Except the bulbs.
14:49 Jules: did the bulbs come already
14:51 Mom: No, they say they ship later. Tulips and the little blue ones.
14:51 Jules: then it's not about the bulbs either. you can forward it to 7726 and it reports it
14:55 Mom: I'll do that. 7726 is easy to remember.
14:55 Mom: Thank you honey.
"""),
    ("Mom", "09-15", """
09:12 Mom: Joanne says the squash bugs this year are the worst she has seen
09:13 Mom: I found the little orange eggs under the leaves. Dozens.
09:20 Jules: ugh. you can scrape them off with tape
09:20 Jules: or drop them in soapy water
09:22 Mom: That is a lot of tape.
09:22 Jules: it's very satisfying though
09:25 Mom: I'm too old for satisfying. I'll get the soapy bucket.
09:26 Jules: you are not too old
09:30 Mom: Tell that to my knees.
"""),
    ("Mom", "09-17", """
15:22 Mom: Leon is painting his fence again. Same color. I don't know why he does it every year.
15:22 Mom: [attachment: IMG_2240.HEIC]
15:31 Jules: it looks great though
15:32 Mom: It looks exactly the same.
15:32 Mom: Barb says he just likes standing outside with a brush.
15:34 Jules: honestly that's a good hobby
15:35 Mom: He had the radio on all day. Some game.
15:36 Jules: good for him
15:36 Mom: Did you eat lunch? You always forget.
15:38 Jules: i had a sandwich
15:39 Mom: A real one or a granola bar
15:41 Jules: ...a real one
15:41 Mom: I can tell when you pause.
"""),
    ("Mom", "09-19", """
11:03 Mom: I tried your green chile stew from the notes you sent last year. Added the zucchini.
11:03 Mom: [attachment: IMG_2251.HEIC]
11:08 Jules: that looks so good
11:09 Jules: did you use the roasted ones or canned
11:11 Mom: I roasted them on the grill. Leon helped. He doesn't trust a stovetop.
11:12 Mom: It needed more salt but otherwise very good.
11:13 Jules: it always needs more salt, i keep forgetting to write that in the note
11:14 Mom: Write it in the note.
11:14 Jules: ok ok
11:25 Jules: updated
11:26 Mom: Thank you. That's what I like to see.
"""),
    ("Mom", "09-23", """
16:47 Mom: We had hail! Pea sized for five minutes and then it stopped.
16:47 Mom: [attachment: IMG_2259.HEIC]
16:50 Jules: oh no, the garden?
16:58 Mom: Went out to look. The beans are shredded. The tomatoes are fine, they're tougher than they look.
16:59 Mom: Barb's greenhouse has a cracked panel.
17:01 Jules: that's a bummer. glad you're all ok
17:03 Mom: Leon is already out there with a shop vac cleaning it up. He loves a project.
17:04 Jules: he and the fence
17:04 Mom: Don't tell him. He'll start another one.
"""),
    ("Mom", "09-25", """
13:15 Mom: Joanne's granddaughter learned to ride a bike. Joanne sent me 11 videos.
13:15 Mom: I've watched them all. Twice.
13:18 Jules: that's the correct amount
13:19 Mom: She wobbled a lot but did not fall.
13:19 Mom: Joanne cried.
13:21 Jules: i would also cry
"""),
    ("Mom", "09-28", """
07:58 Mom: jjjjjjjjjj
07:58 Mom: Sorry honey. That was my pocket.
08:03 Jules: haha good morning
08:03 Jules: pocket texts are the best texts
08:05 Mom: I was trying to turn the flashlight off.
08:05 Mom: Then I looked at the screen for a long time
08:07 Jules: on the lock screen the flashlight is the little button bottom left. press and hold to turn it off
08:09 Mom: I did and it worked. I feel very powerful.
08:10 Mom: Orchard Lane is back on tonight. The vicar has a secret.
08:11 Jules: i'm on episode 2, no spoilers!
08:11 Mom: My lips are sealed.
"""),
    ("Mom", "09-30", """
12:44 Mom: Look who came to the new feeder
12:44 Mom: [attachment: IMG_2271.HEIC]
12:44 Mom: [attachment: IMG_2272.HEIC]
12:50 Jules: is that a woodpecker?
12:51 Mom: Downy. Joanne looked it up in her book. He is very polite.
12:52 Jules: the other one is a finch?
12:55 Mom: House finch. They fight over the sunflower seeds.
12:56 Jules: nature documentary in your backyard
12:57 Mom: Better than television.
12:57 Mom: Except Orchard Lane.
"""),
]

CHATS += [
    ("Pat", "09-02", """
11:27 Pat: Hi Jules, Pat here. There was a box with your name on it left at the door of 1B downstairs. I moved it to your doormat so it doesn't walk off.
11:42 Jules: oh thank you! i wondered where that went, the tracking said delivered yesterday
11:43 Pat: The carrier has been mixing up the doors lately. I'll mention it to the office.
11:44 Jules: appreciate it
11:44 Pat: No trouble.
"""),
    ("Pat", "09-08", """
19:05 Jules: hi Pat, the dishwasher stopped draining and there's standing water at the bottom. it made a weird humming sound and then nothing
19:22 Pat: Thanks for telling me. Did you try the drain cover at the bottom, under the lower rack?
19:30 Jules: yeah i pulled it out and cleaned it. there was a bit of gunk but it didn't help
19:31 Jules: still hums and doesn't drain
19:40 Pat: Ok. Don't run it again until someone looks at it. I'll send a repair person, he'll call you to set up a time.
19:41 Jules: sounds good, thanks
19:41 Pat: Sorry about that. It's a pretty old unit.
"""),
    ("Pat", "09-14", """
13:12 Pat: Jules, the repair guy says he may need to order a pump for the dishwasher. He'll call you when he has it.
13:30 Jules: ok. in the meantime i'll just hand wash
13:31 Pat: Thanks for being patient.
13:31 Jules: no worries
"""),
    ("Pat", "09-21", """
08:44 Pat: Morning all. Reminder to everyone in the building, trash goes in the dumpster and recycling in the blue bins. Someone left a mattress by the side gate.
08:50 Jules: not me :)
08:51 Pat: Ha, I know. Mass text, sorry. Hope you're well otherwise.
08:52 Jules: all good here
"""),
    ("Pat", "09-26", """
10:16 Pat: Quick one. The pump came in for the dishwasher, he'll be in touch about installing it.
10:22 Jules: great, thanks for chasing that
10:22 Pat: Of course. Let me know if he doesn't call you.
10:23 Jules: will do
"""),
    ("Pat", "09-30", """
17:03 Pat: Hi, just checking the dishwasher is running ok now?
17:20 Jules: yep, working fine. quieter than before even
17:21 Pat: Glad to hear it. Thanks for your patience.
17:21 Jules: thanks for sorting it
17:25 Pat: Also the hallway light on your floor should be fixed now. Let me know if it flickers again.
17:40 Jules: will do
"""),
]

TRAIL = [
    ("09-02", """
20:15 Eli: who's watching Deepwater Station tonight
20:15 Nora: me. i have wine and no spoilers
20:16 Sam: ep 4? already seen it, i'm not saying anything
20:16 Sam: except. no. nothing
20:17 Eli: this is already a spoiler
20:17 Nora: Sam i will mute you
20:18 Jules: i'm a couple episodes behind, don't ruin it
20:18 Sam: i'm a vault
20:19 Eli: you are a leaky bucket
21:42 Nora: ok that was so good
21:42 Nora: the radio
21:43 Eli: the RADIO
21:44 Jules: i'm covering my eyes
21:44 Sam: Laughed at “i'm covering my eyes”
"""),
    ("09-05", """
10:02 Nora: [attachment: IMG_7731.HEIC]
10:02 Nora: this is Waffles. she has decided my laptop is hers
10:03 Eli: she looks like she runs a small empire
10:04 Jules: she's the CEO and you're the intern
10:04 Nora: she IS the boss. I just live here
10:06 Sam: does she accept payment in treats
10:06 Nora: exclusively
10:10 Eli: [attachment: IMG_5560.HEIC]
10:10 Eli: this is a screenshot of my screen time report
10:11 Eli: 7 hours daily average. i'm not sorry
10:12 Jules: the app that tells you you're sad
10:12 Sam: mine is 9. i didn't open the report
"""),
    ("09-09", """
17:21 Eli: ok recs please. i need new headphones for working, noise cancelling, nothing crazy expensive
17:22 Nora: I love my Cairn ones. battery lasts forever
17:22 Sam: avoid anything with a touch panel. you will pause your music by scratching your ear
17:23 Eli: lol this happened to me today
17:24 Jules: i've had wired ones with a boom mic for years and honestly the sound is great, you just look like an air traffic controller
17:24 Eli: honestly not the worst look
17:25 Nora: the Cairn ones have physical buttons, which Sam would approve of
17:26 Sam: i do approve
17:26 Eli: ok adding to cart. thank you committee
17:26 Nora: you're welcome :)
"""),
    ("09-11", """
19:03 Eli: my fantasy team is cursed
19:03 Eli: i drafted a kicker in round 6. i don't know what came over me
19:04 Sam: you can't do that. there's a law
19:04 Nora: the commissioner is Eli's cousin, he's fine
19:05 Eli: no she hates me. she told me i should be ashamed
19:06 Jules: i don't even know who is on my team. i set it up in week 1 and haven't looked since
19:06 Sam: you have no team. it's an empty shell
19:07 Jules: i have Kevin and i think he's injured
19:07 Eli: who's Kevin
19:08 Jules: a guy. he was in the thing
"""),
    ("09-13", """
11:30 Nora: [attachment: IMG_7790.HEIC]
11:30 Nora: say hello to my sourdough. she's a brick.
11:31 Eli: it looks like a hockey puck
11:32 Nora: I proofed it for the recommended time. The recipe lied.
11:32 Jules: what's your starter doing, is it bubbly
11:33 Nora: it was bubbly at 8am. By noon it was a sad puddle
11:34 Jules: sounds underfed. feed it, wait til it doubles, then use it right at the peak
11:35 Nora: you sound like a sourdough guy
11:35 Jules: i've read way too many forum threads
11:36 Sam: nobody tell Nora about hydration percentages
11:36 Nora: what are those
11:37 Sam: ...
11:37 Sam: forget i said anything
11:40 Eli: Nora's brick would be good for a doorstop
11:41 Nora: Disliked “Nora's brick would be good for a doorstop”
"""),
    ("09-14", """
16:18 Eli: [attachment: IMG_5571.HEIC]
16:18 Eli: thrift store find. 1978 encyclopedia, volume K only
16:19 Sam: why only K
16:19 Eli: because they want me to buy the other 25 volumes separately, apparently
16:20 Nora: K is for kayak
16:20 Jules: and kelp
16:20 Eli: and kerning
16:21 Jules: kerning is a good word
16:22 Eli: it has a whole entry on kites. 11 pages
16:23 Nora: is this just the kite entry now
16:23 Eli: i will be reading it aloud. no one is safe
16:24 Sam: Liked “i will be reading it aloud. no one is safe”
"""),
    ("09-16", """
20:30 Nora: does anyone know a good board game for two people that isn't chess
20:31 Eli: Tidepool. it's cute and nobody gets mad
20:32 Sam: Ravenmoor is better but you will fight about the rules
20:32 Nora: I do like fighting about rules
20:33 Sam: then Ravenmoor
20:34 Jules: seconding Tidepool. it takes 20 minutes and you feel like a marine biologist
20:35 Nora: a marine biologist is a fun thing to feel like
20:35 Eli: I'll lend you mine
20:36 Nora: you're a hero
"""),
    ("09-19", """
09:44 Sam: [attachment: IMG_4470.HEIC]
09:44 Sam: this is the most accurate thing i've seen about work this month
09:46 Eli: "my job is mostly moving numbers between two spreadsheets and pretending it's strategy"
09:46 Nora: lmao that's every job
09:47 Jules: that is literally a thing i've been paid to automate
09:48 Eli: you automate boredom, Jules
09:48 Jules: boredom is a renewable resource, it keeps coming back
09:49 Sam: Loved “boredom is a renewable resource, it keeps coming back”
"""),
    ("09-22", """
21:50 Sam: SPOILER ZONE for Deepwater ep 8, if you're not there yet, close this
21:50 Sam: ...
21:51 Nora: I'm there. go
21:51 Sam: the captain's sister. she's been in every scene as a background character
21:52 Eli: no she hasn't
21:52 Sam: i made a timeline. she's behind the monitor in ep 1, 3, 5, 6
21:53 Eli: ok that's actually unsettling
21:54 Nora: Sam your spreadsheet strikes again
21:55 Jules: i'm on 7, i'll look at the timeline later
21:55 Sam: it's color coded
21:56 Eli: of course it is
"""),
    ("09-24", """
18:12 Eli: i dropped my phone on the sidewalk and now there's a crack across the corner
18:12 Eli: [attachment: IMG_5603.HEIC]
18:13 Sam: that's called a character crack
18:14 Nora: I have the same one. It gets worse every month and then it stabilizes
18:14 Jules: do you have the case with the lip, that helps a lot
18:15 Eli: the case is why it survived. the case also has a crack
18:16 Nora: you have a cracked case on a cracked phone
18:16 Eli: structural integrity is a state of mind
"""),
    ("09-27", """
13:05 Nora: Dying for a good burrito. Recs?
13:06 Sam: Casa Verdin on Federal. get the smothered one
13:06 Eli: no, La Esquina. there's a line out the door for a reason
13:07 Sam: the line is for the tamales, the burritos are fine
13:07 Nora: Great, a feud
13:08 Jules: there's a place in my neighborhood with a handwritten menu and a lime tree in the window. no idea what it's called, I just go there
13:08 Eli: that's the best kind of rec
13:09 Nora: please find out the name
13:09 Jules: i will investigate
13:30 Sam: [attachment: IMG_4488.HEIC]
13:30 Sam: pickle says hi
13:31 Nora: Oh he's so fluffy
13:31 Eli: is that the sock guy
13:32 Sam: that's the sock guy
"""),
    ("09-29", """
18:40 Eli: finale tomorrow night, who's watching live
18:41 Sam: me. i'm making snacks
18:41 Nora: Same. Phone off
18:42 Jules: i'll watch it as soon as i can, don't spoil
18:42 Eli: you have to watch it live Jules, otherwise this chat will ruin it
18:43 Jules: this chat has ruined everything so far
18:43 Sam: that's what we're here for
18:44 Nora: This group has one purpose and it's spoilers
"""),
]
CHATS += [("Trailheads", day, script) for day, script in TRAIL]

CHATS += [
    ("Wren", "09-06", """
15:12 Wren: [attachment: IMG_3321.HEIC]
15:12 Wren: spotted on my walk. look at the kerning on "FRESH PRODUCE"
15:13 Jules: the E is hugging the S
15:13 Wren: they are in love and it is not working
15:14 Jules: i would've guessed that was a hand-painted sign
15:14 Wren: it IS hand painted, which makes it worse because someone had to line up all those letters
15:16 Jules: some person took a ruler to it and still lost
15:16 Wren: Laughed at “some person took a ruler to it and still lost”
"""),
    ("Wren", "09-10", """
11:08 Wren: finally got a lamp for my desk. thrifted, 1960s, ugly green, i love it
11:08 Wren: [attachment: IMG_3340.HEIC]
11:15 Jules: that is a great green
11:16 Wren: Pantone calls it "toad"
11:16 Jules: does it
11:17 Wren: no. i named it that
11:17 Jules: toad it is
11:18 Wren: my cat has already knocked it over twice
11:19 Jules: does the cat respect the toad
11:19 Wren: the cat respects nothing
"""),
    ("Wren", "09-15", """
08:51 Wren: have you heard the podcast Edge of the Map? it's a guy who reads old mapmakers' letters
08:52 Jules: no? that sounds weirdly right up my alley
08:53 Wren: the episode about the cartographer who hid fake towns on his maps to catch copycats. they're called paper towns, or trap streets
08:54 Jules: wait that's real?
08:54 Wren: very real
08:55 Jules: adding to my queue immediately
08:55 Wren: you'll thank me on a long walk
08:56 Jules: my walks are mostly to the coffee shop
08:56 Wren: that's a medium walk. it counts
"""),
    ("Wren", "09-18", """
14:30 Wren: [attachment: IMG_3359.HEIC]
14:30 Wren: riso print test. the pink and the blue both landed 3mm off
14:31 Jules: that's a cool effect actually? looks like old comics
14:32 Wren: it's called misregistration and everyone calls it "charm" when it's on purpose
14:32 Jules: so you're saying it's charm if you say it's charm
14:33 Wren: that is the entire business model of art
14:34 Jules: ...i'm stealing that
14:34 Wren: Liked “...i'm stealing that”
"""),
    ("Wren", "09-22", """
17:44 Wren: how do you keep a pothos alive. mine is dropping leaves like it's being paid by the leaf
17:45 Jules: i have a pothos that looks like it's been through a war and still keeps growing, so i'm not the one to ask
17:45 Wren: that is also the correct pothos experience
17:46 Jules: is it near a window? mine does best with indirect light and when i forget about it
17:47 Wren: it's in a window with a lot of sun. maybe too much
17:47 Jules: they scorch. pull it back a few feet
17:48 Wren: ok i'll move it and give it a pep talk
17:49 Jules: pep talk is half the care routine
"""),
    ("Wren", "09-29", """
21:03 Wren: made a playlist for when i'm printing. very slow, very synthy
21:03 Wren: https://music.example/p/slow-synth-print-night
21:09 Jules: oh this is nice. it's the first thing i've heard all week without lyrics about heartbreak
21:10 Wren: that's the intended use
21:11 Jules: okay playing it while i fix tests
21:11 Wren: tests with synth. very cyberpunk
"""),
    ("Cass", "09-04", """
19:10 Cass: so mom called me about the font size
19:10 Cass: apparently you fixed it
19:12 Jules: she texted me and i walked her through it, she found the slider
19:12 Cass: she told me you "know all the secrets"
19:13 Jules: i know where the settings are. that's all the secrets
19:13 Cass: she then asked me to explain voicemail
19:14 Jules: god. what did you say
19:14 Cass: that it's a robot that holds your messages
19:15 Jules: perfect
19:15 Cass: she said "that sounds like a trap"
"""),
    ("Cass", "09-07", """
10:20 Cass: [attachment: IMG_6102.HEIC]
10:20 Cass: Moose vs. the sprinkler. Moose lost
10:23 Jules: look at that face. total betrayal
10:23 Cass: he keeps going back for more
10:24 Jules: same energy as me and a flaky test
10:25 Cass: ha. how's work
10:27 Jules: busy, in a good way. lots of different small things
10:27 Cass: that's the dream. unlike me and my 40 open tabs of the same insurance form
10:28 Jules: what form
10:29 Cass: it's a long story involving a parking permit and a very rude website
10:31 Jules: i can look at it with you if you want
10:31 Cass: no I'll survive. thanks though
"""),
    ("Cass", "09-13", """
17:55 Cass: need an audiobook for painting the spare room
17:55 Cass: not scary, not sad
17:57 Jules: Salt Road is sad
17:57 Jules: try The Quiet Machinery. nonfiction about how automatic looms changed everything
17:58 Cass: ooh. is the narrator any good
17:59 Jules: sounds like a nature documentary, in a good way
17:59 Cass: perfect, downloading
"""),
    ("Cass", "09-20", """
12:11 Cass: Ravi built the bookshelf. It's leaning
12:11 Cass: [attachment: IMG_6144.HEIC]
12:15 Jules: that's... a lean, yes
12:15 Cass: he says it's within tolerance
12:16 Jules: what is the tolerance
12:16 Cass: "a feeling"
12:17 Jules: put a book under one leg
12:18 Cass: he's outside with a level. the level says he's wrong. he doesn't believe it
"""),
    ("Cass", "09-24", """
20:02 Cass: random question. how do I make a google sheet add up only the rows where column C says "paid"
20:04 Jules: =SUMIF(C:C, "paid", D:D)
20:04 Jules: C is the thing to check, D is the numbers to add
20:06 Cass: you are a wizard
20:06 Cass: it worked first try, which has never happened to me
20:07 Jules: sumif has a good heart
20:08 Cass: I'm going to put "sumif has a good heart" on a mug
"""),
    ("Walt", "09-09", """
08:15 Walt: Hey Jules, Walt from 1B. I got a magazine of yours in my box, something about web design?
08:20 Jules: ha, that's mine. thanks, just leave it by my door if you don't mind
08:21 Walt: Will do. By the way my dog Cooper is going to bark at your door, he's decided you're the mail carrier
08:22 Jules: he's welcome to
08:22 Walt: Appreciate it.
"""),
    ("Walt", "09-17", """
18:34 Walt: Do you have a stud finder by any chance? I'm trying to hang a shelf and I've already put four holes in the wall looking for the studs
18:36 Jules: i do, hold on
18:37 Jules: i'll bring it down
18:40 Walt: You're a lifesaver. Also the hallway light on our floor is out again
18:41 Jules: oh i'll mention it to Pat
18:41 Walt: Thanks, I told the office but who knows
18:55 Walt: Found a stud on the fifth try. Thank you!!
18:56 Jules: nice. keep it as long as you need
"""),
    ("Walt", "09-25", """
07:48 Walt: Sorry about Cooper this morning. He saw a squirrel through the door
07:50 Jules: haha no worries, didn't hear a thing
07:50 Walt: You are kind. He is going to the groomer today so he'll be mad at me later
07:52 Jules: he'll forgive you for a treat
07:52 Walt: He always does.
"""),
    ("Priya", "09-07", """
09:12 Priya: found this in a box
09:12 Priya: [attachment: IMG_8801.HEIC]
09:14 Jules: WHAT
09:14 Jules: the elevator in Harlan Hall!! that sign!!
09:15 Priya: "Out of order. Please use stairs. Again." someone wrote "again" in marker
09:16 Jules: that elevator was out of order more than it was in order
09:16 Priya: remember Dev getting stuck in it with the pizza guy
09:17 Jules: 40 minutes. and they became friends
09:18 Priya: the pizza guy came to Dev's graduation barbecue
09:19 Jules: i'd forgotten that
09:20 Priya: I forget nothing. It's a curse
09:21 Jules: tell me more embarrassing things about me
09:22 Priya: the fire alarm and the rice cooker. I rest my case
09:23 Jules: that was a design flaw in the dorm
09:24 Priya: it was a design flaw in YOU
"""),
    ("Priya", "09-12", """
16:40 Priya: have you started Salt Road yet?
16:42 Jules: it's on my nightstand looking at me
16:42 Priya: it's slow for the first fifty pages and then you're destroyed
16:43 Jules: that's a terrible pitch
16:43 Priya: it's an honest one
16:44 Priya: the lighthouse keeper's letters are the best part
16:44 Jules: no spoilers
16:45 Priya: those aren't spoilers, they're a flavor
16:46 Jules: ok flavor accepted
16:47 Priya: how's the freelance life? still good?
16:49 Jules: yeah it's good. busy week
16:50 Priya: love that. I would implode working alone
16:51 Jules: some days i also implode, quietly
"""),
    ("Priya", "09-20", """
20:15 Priya: ok job update, I now have a manager whose only management skill is saying "let's circle back"
20:16 Jules: circle back to what
20:16 Priya: nothing. it's just a sound he makes
20:17 Jules: how many times per meeting
20:17 Priya: I kept count in a meeting last week. 11
20:18 Jules: i'd pay money to see that tally
20:19 Priya: I'll send you the notebook
20:19 Priya: Dev says I should start a podcast about it
20:20 Jules: Circling Back. i'd listen
20:21 Priya: Loved “Circling Back. i'd listen”
"""),
    ("Priya", "09-27", """
18:22 Priya: our trivia team needs a name
18:22 Priya: current candidates: Quizzly Bears, Les Quizerables, The Fact Checkers
18:24 Jules: Les Quizerables is classic
18:24 Jules: but The Fact Checkers has a menace to it
18:25 Priya: Dev wants "Tim"
18:26 Jules: just Tim?
18:26 Priya: just Tim. he thinks the other teams will be confused
18:27 Jules: that's actually a power move
18:27 Priya: we're going with Tim
"""),
    ("Marcus", "09-03", """
10:05 Marcus: Hey Jules, Marcus at Brightside Barbers. We have an online booking page now if that's easier than texting me: brightsidebarbers.example/book
10:20 Jules: haha thanks, texting is easier for me
10:21 Marcus: Fair enough. You were overdue the last time I checked :)
10:22 Jules: i know i know
10:22 Marcus: No pressure. Door's open
"""),
    ("Marcus", "09-18", """
12:40 Marcus: [attachment: IMG_9012.HEIC]
12:40 Marcus: shop mascot. her name is Gus, she's a girl, don't ask
12:42 Jules: Gus is a great name for a shop dog
12:42 Marcus: she thinks she's the receptionist
12:43 Jules: does she take messages
12:44 Marcus: she takes treats and loses messages
12:45 Marcus: also that beard oil you asked about, it's the cedar one from Alder & Birch. smells like a campfire but nicer
12:46 Jules: thanks! i'll check it out
"""),
    ("Marcus", "09-29", """
15:30 Marcus: Hey, new chair got installed. It's so comfy that people are falling asleep mid-cut
15:30 Marcus: [attachment: IMG_9031.HEIC]
15:35 Jules: that looks like a throne
15:36 Marcus: It's called the Regent. It was a splurge
15:37 Jules: worth it for the nap factor
15:38 Marcus: Come by when you can
"""),
]

NOTES += [
    ("Daily/2026-09-02.md", "09-02 18:40", """
---
date: 2026-09-02
tags: [daily]
---
# Wed

## Today
- [ ] Copperline: carriers config
- [ ] reply to Dana
- [ ] invoice
- [x] coffee, walk
- [ ] groceries

## Thoughts
Slow morning. Email first thing is a trap. Try writing the plan before opening the inbox.
"""),
    ("Daily/2026-09-03.md", "09-03 17:55", """
# 2026-09-03

- [x] triage pinch issues
- [ ] Harbor Lane: test run
- [ ] marlowworks-site: RSS feed looks off
- [ ] laundry

Keep adding a "small" feature before lunch and losing the afternoon to it. Stop doing that. See [[Weekly review]].
"""),
    ("Daily/2026-09-04.md", "09-04 17:20", """
---
date: 2026-09-04
tags: [daily, friday]
---
## Checklist
- [x] reply to Dana
- [ ] Fernbrook: look at the kitchen sheet columns
- [ ] dotfiles: zsh startup is slow again
- [ ] call Cass back

## End of week
Got through about two thirds of the list. The last third is the same last third as last week.
"""),
    ("Daily/2026-09-08.md", "09-08 08:30", """
# 2026-09-08

Back after the long weekend.

- [ ] clear inbox (only the real ones)
- [ ] Copperline: PDF edge cases, the weird scans
- [ ] Quill & Ledger: reread my notes
- [ ] invoice
- [ ] stretch

The weird scans are always the ones that come in sideways. Rotate first, then OCR. Obvious in hindsight.
"""),
    ("Daily/2026-09-09.md", "09-09 18:05", """
---
date: 2026-09-09
tags: [daily]
---
- [x] Harbor Lane: test run
- [ ] Fernbrook: webhook retries
- [ ] review the outside PR on pinch
- [ ] buy printer paper (does anyone still print)

Rain all afternoon, got more done than I expected. Maybe the weather is the real productivity tool.
"""),
    ("Daily/2026-09-10.md", "09-10 19:12", """
# 2026-09-10

## To do
- [ ] Copperline: carriers config
- [ ] site: blog draft, first pass only
- [x] water the plants
- [ ] reply to Dana

Tests first, then the fix. I keep doing it the other way round and then wondering why the diff is huge.
"""),
    ("Daily/2026-09-11.md", "09-11 16:48", """
---
date: 2026-09-11
tags: [daily, friday]
---
## Checklist
- [ ] invoice
- [x] Fernbrook: tidy the logs
- [ ] check the backups actually restore
- [ ] weekly review, see [[Weekly review]]

Friday. Too many tabs open again. Closing everything that is not a tab I would miss.
"""),
    ("Daily/2026-09-14.md", "09-14 08:20", """
# 2026-09-14

Monday plan, in order:

- [ ] Harbor Lane: look at the reminder wording
- [ ] marlowworks-site: case study outline, see [[Case study outline]]
- [ ] text Cass back
- [ ] ramen?

Starting with the one I least want to do. We'll see how long that lasts.
"""),
    ("Daily/2026-09-15.md", "09-15 18:30", """
---
date: 2026-09-15
tags: [daily]
---
- [ ] Copperline: new sample PDFs
- [x] pinch: reply to the issue about Windows paths
- [ ] Fernbrook: kitchen sheet
- [ ] dishes (by hand, for now)

Wren pointed me at a podcast about old maps this morning and I am already hooked. Need to look up from the screen more.
"""),
    ("Daily/2026-09-16.md", "09-16 17:45", """
# 2026-09-16

## Today
- [x] Fernbrook: tidy the parser prompt
- [ ] reply to Dana
- [ ] pinch: write release notes
- [ ] laundry

A quiet one. Wrote a failing test for a bug I have not even found yet, which felt productive in a suspicious way.
"""),
    ("Daily/2026-09-17.md", "09-17 19:00", """
---
date: 2026-09-17
tags: [daily]
---
- [ ] Harbor Lane: test run
- [x] dependency bumps across the repos
- [ ] stretch
- [ ] bring the stud finder down to 1B

Dependency bumps are the dishes of software. Nobody wants to do them and everything is worse if you don't.
"""),
    ("Daily/2026-09-18.md", "09-18 16:40", """
# 2026-09-18

- [ ] invoice
- [x] Copperline: carriers config
- [ ] weekly review ([[Weekly review]])
- [ ] pick up the beard oil Marcus mentioned

Better week than it felt like while I was in it.
"""),
    ("Daily/2026-09-21.md", "09-21 08:10", """
---
date: 2026-09-21
tags: [daily]
---
## Monday
- [ ] Copperline: carriers config
- [ ] marlowworks-site: case study outline
- [ ] groceries
- [ ] reply to Dana

Keeping the list to four things. Any more and the list becomes the work.
"""),
    ("Daily/2026-09-22.md", "09-22 18:15", """
# 2026-09-22

- [x] Fernbrook: kitchen sheet
- [ ] pinch: triage new issues
- [ ] laundry
- [ ] read a chapter of [[Salt Road]]

Gray morning, then sun. Walked to the coffee shop twice instead of once. No regrets.
"""),
    ("Daily/2026-09-23.md", "09-23 17:50", """
---
date: 2026-09-23
tags: [daily]
---
- [ ] Harbor Lane: test run
- [ ] Quill & Ledger: reread notes
- [x] reply to Dana
- [ ] water plants

Hard to focus. Switched to paper for an hour and the problem made more sense. Annoyingly low-tech.
"""),
    ("Daily/2026-09-24.md", "09-24 19:25", """
# 2026-09-24

## Checklist
- [ ] Copperline: edge cases from the scans
- [x] dotfiles: fix the zsh startup time
- [ ] invoice

The zsh fix was a plugin loading twice. Forty minutes to find, four seconds to fix.
"""),
    ("Daily/2026-09-25.md", "09-25 16:30", """
---
date: 2026-09-25
tags: [daily, friday]
---
- [x] tidy repos
- [ ] check backups
- [ ] weekly review, see [[Weekly review]]
- [ ] email the person who sent that long, kind issue about pinch

Friday again. The month is going faster than the to-do list.
"""),
    ("Daily/2026-09-28.md", "09-28 08:35", """
# 2026-09-28

- [ ] plan the week
- [ ] Fernbrook: webhook retries
- [ ] site: blog draft, second pass
- [ ] stretch

Start the week by writing down the one thing that has to be done. Everything else is a bonus.
"""),
    ("Daily/2026-09-29.md", "09-29 18:20", """
---
date: 2026-09-29
tags: [daily]
---
- [x] Harbor Lane: test run
- [ ] Copperline: carriers config
- [ ] reply to Dana
- [ ] laundry

Third test run this month and I still find something new each time. Good sign or bad sign, unclear.
"""),
    ("Daily/2026-09-30.md", "09-30 17:40", """
# 2026-09-30

## End of the month
- [ ] invoice
- [ ] archive finished notes
- [x] tidy the desktop
- [ ] look at [[Pricing thoughts]] with fresh eyes

Month is done. I think the main thing I learned is that small steady days beat heroic ones.
"""),
]

NOTES += [
    ("Reading/The Quiet Machinery.md", "09-17 21:30", """
---
title: The Quiet Machinery
author: Edda Rowan
type: book
status: finished
tags: [reading, nonfiction, history]
---
# The Quiet Machinery

A history of automatic looms and the people who worked beside them. Sounds dry. Isn't.

## What stuck
- Every big change in the mill started as a small fix to one annoying step.
- The operators who understood how the machine failed were worth more than the machine.
- Nobody noticed the loom was "automatic" until it stopped. Same with most software.

## Quote
> A machine that never fails teaches no one anything about itself.

## To think about
- Upkeep as invisible work. Could be a post, see [[Blog post ideas]].
- The chapter on the night shift reads like an on-call story.
"""),
    ("Reading/Salt Road.md", "09-24 22:05", """
---
title: Salt Road
author: Tobin Vale
type: book
status: reading
tags: [reading, fiction]
---
# Salt Road

A long-haul trucker and a lighthouse keeper who only ever talk over a crackling radio. Recommended by Priya, who says it is slow for the first fifty pages and then devastating.

## Notes so far
- The radio chatter is the best writing in it.
- The keeper's letters are folded into the chapters. Good device.
- Too early to say how it ends. Not asking anyone.

Next: finish part two before starting anything else.
"""),
    ("Reading/Articles to read.md", "09-26 20:15", """
# Articles to read

Saved from newsletters. Link rot is real, so skim the first paragraph before I forget why I saved them.

- [ ] Why OCR still fails on rotated scans, and the cheap fix. https://ocrweekly.example/rotated-scans
- [ ] A field guide to SMS reminders: opt-outs, throttling, carrier filtering. https://textlane.example/field-guide
- [ ] How a three-store bakery rethought its order sheet. https://bakerybrief.example/order-sheet
- [ ] State of freight invoice automation. https://freightdesk.example/invoice-automation
- [ ] Date parsing is hard: a short history of off-by-one-day bugs. https://tinyfailures.example/dates
- [ ] Retries, idempotency keys, and the double-charged customer. https://backendnotes.example/idempotency
- [x] Writing a CLI that people actually keep installed. https://clicraft.example/keep-installed

> [!note]
> Nothing here is urgent. If I do not read it in a month, delete the list.
"""),
    ("Recipes/Green chile stew.md", "09-19 11:25", """
---
tags: [recipe, stew]
serves: 6
---
# Green chile stew

## Ingredients
- 2 lb pork shoulder, cubed
- 10 roasted green chiles, peeled and chopped
- 1 white onion, diced
- 4 cloves garlic
- 4 tomatillos, quartered
- 2 medium potatoes, cubed
- 1 tsp cumin, 1 tsp oregano
- 4 cups chicken stock
- Salt (more than you think)

## Steps
1. Brown the pork in batches. Don't crowd the pot.
2. Soften the onion and garlic in the same pot.
3. Add chiles, tomatillos, spices and stock. Simmer, covered, until the pork gives up.
4. Add potatoes for the last 25 minutes.
5. Taste. Add salt. Taste again.

Note to self: it always needs more salt.
"""),
    ("Recipes/Weeknight dal.md", "09-06 19:40", """
---
tags: [recipe, vegetarian]
---
# Weeknight dal

Red lentils, one pot, about thirty minutes.

- 1 cup red lentils, rinsed
- 3 cups water
- 1 onion, 3 cloves garlic, thumb of ginger
- 1 tsp turmeric, 1 tsp cumin seeds, 1 tsp garam masala
- 1 can diced tomatoes
- Lemon, cilantro

Toast the cumin in oil, then onion, garlic and ginger. Add lentils, water, turmeric, tomatoes. Simmer until it falls apart. Finish with garam masala and lemon.

Good over rice or just with a spoon.
"""),
    ("Recipes/Sourdough loaf.md", "09-13 12:10", """
---
tags: [recipe, bread]
---
# Sourdough loaf

## Starter
Feed 1:5:5 (starter, flour, water). Use it when it has doubled and just started to dome. If it has collapsed, it is late.

## Dough
- 500 g bread flour
- 350 g water
- 100 g active starter
- 10 g salt

## Method
1. Mix flour and water, rest 30 minutes (autolyse).
2. Add starter and salt. Fold every 30 minutes, four times.
3. Bulk until about 50 percent bigger. Shape. Fridge overnight.
4. Bake in a preheated Dutch oven, lid on, then lid off to finish.

## Notes
- Hydration is the lever. Too wet and the loaf spreads, too dry and it's dense.
- Underfed starter makes a brick. Learned that the hard way.

## Troubleshooting
- Dense crumb: starter was late or bulk was short.
- Gummy middle: cut it too soon. Wait an hour.
- Flat loaf: dough was too slack, add a fold or two next time.
- Pale crust: bake the second half with the lid off a little longer.
"""),
    ("Recipes/Shakshuka.md", "09-27 10:05", """
# Shakshuka

Eggs poached in spicy tomato sauce.

- Olive oil, 1 onion, 1 red pepper
- 3 cloves garlic
- 1 tsp each cumin and paprika, pinch of chili flakes
- 1 can crushed tomatoes
- 4 eggs, feta, parsley

Cook the onion and pepper until soft, add garlic and spices, then tomatoes. Simmer ten minutes. Make wells, crack in the eggs, cover until the whites set. Top with feta and parsley.

Eat with torn bread from the pan.
"""),
    ("Gear/Climbing rack.md", "09-09 20:25", """
---
tags: [gear, climbing]
---
# Climbing rack

## Rack
- Cams: 0.3 to 3, single set
- Nuts: one set of 10 on a wire loop
- Quickdraws: 12 (6 long, 6 short)
- Alpine draws: 4
- Slings: 2 x 60 cm, 2 x 120 cm
- Locking carabiners: 4
- Belay device, plus a backup tube
- Helmet
- Rope: 70 m dynamic, retire when the sheath shows core

## To check
- Webbing on the slings looks faded. Replace before it matters.
- Chalk bag is a mess.
- Wish list: a lighter cam set.
"""),
    ("Gear/Camera bag.md", "09-14 21:20", """
---
tags: [gear, photography]
---
# Camera bag

Everything that lives in the grey sling bag.

- Mirrorless body, two spare batteries
- 35 mm prime (the one that stays on)
- 85 mm prime for portraits
- Wide zoom, rarely used
- Lens cloth, blower, spare memory cards
- Small tripod that fits in the side pocket

## Notes
- Shoot RAW, cull the same day. Waiting makes it worse.
- Stop carrying the wide zoom unless there is a reason.
"""),
    ("Gear/Home office.md", "09-21 19:50", """
---
tags: [gear, desk]
---
# Home office

## Current setup
- MacBook on a stand, external monitor
- Mechanical keyboard with brown switches
- Sit-stand desk
- Wired headset with a boom mic
- Task lamp, warm bulb
- Plant on the left that is surviving on spite

## Problems
- Cable mess behind the desk.
- Chair is fine for two hours, not for six.

## Ideas
- Cable tray under the desk.
- Try a footrest.
- Link this to [[Tools I use]] so both lists stay current.
"""),
    ("Ideas/Blog post ideas.md", "09-16 20:40", """
---
tags: [ideas, blog]
---
# Blog post ideas

For [[Tools I use]] type posts and the occasional write-up for the site.

## Maybe
- A log trimmer that fits in your pocket (the story behind pinch)
- Why SMS reminders need a stop word
- What a nightly CSV export taught me about other people's software
- OCR on messy PDFs: the stuff the tutorials skip
- A webhook handler that survives retries
- Small automations I would skip building
- Reading carrier invoices at scale, without the scale

## Pitches, in a sentence each
- Pocket log trimmer: nobody wants to paste 4,000 lines of stack trace into a chat, and nobody wants to paste a secret either.
- Stop word: the smallest reply a reminder text can handle, and why you want it from day one.
- CSV export: it is a contract, even if nobody wrote it down.

## Format thoughts
- Short. Under 900 words.
- One code block that actually runs.
- End with what I would do differently.

Rank them after a week. Anything that still sounds good then goes on the site.
"""),
    ("Ideas/pinch ideas.md", "09-22 21:05", """
---
tags: [ideas, pinch, oss]
---
# pinch ideas

Things people ask for, or that I wish it did.

- Mask email addresses by default
- Redact bearer tokens and API keys, with a few known patterns
- Keep only the last N lines of a long log
- Pipe mode: `cat app.log | pinch | pbcopy`
- `--dry-run` to show what would be redacted
- A config file per project
- Windows path handling

## Non-goals
- Not a general log viewer
- Not a secret scanner

Keep the README short. People paste, they do not read.
"""),
    ("Areas/Tools I use.md", "09-18 17:10", """
---
tags: [area, tools]
---
# Tools I use

## Writing code
- Claude Code and Codex for the heavy lifting, with a human in the loop
- Hermes on Telegram for quick questions from the phone
- Python: FastAPI, pytest, ruff, uv
- Astro for the site
- Postgres and SQLite

## Documents and data
- pdfplumber and Tesseract for PDFs and scans
- Google Sheets, because everybody already has it

## Habits
- Write the plan in a note before opening an editor.
- Run the tests before and after every change, not only after.
- Commit small. A big diff is a future apology.

## Want to try
- A proper snippet manager
- A nicer terminal multiplexer setup (see dotfiles)
- Something better than a sticky note for the "waiting on" list

## Everything else
- Obsidian for notes ([[Weekly review]])
- GitHub for repos and issues
- Telegram for chat

See also [[Home office]].
"""),
    ("Areas/Workouts.md", "09-10 07:50", """
---
tags: [area, health]
---
# Workouts

## Strength A
- Squat, overhead press, rows
- Finish with a plank

## Strength B
- Deadlift, push-ups, pull-ups on the doorway bar

## Runs
- Easy pace, conversational
- Short and boring is fine

## Mobility
- Ten minutes: hips, shoulders, ankles

## Notes
- Do the thing at 80 percent, not zero.
- Sleep is the real training plan.
"""),
    ("Areas/Business/Pricing thoughts.md", "09-15 21:45", """
---
tags: [business, pricing]
---
# Pricing thoughts

No numbers here, just ways of thinking.

## Value pricing, in general
- Price follows what the outcome is worth to the buyer, not hours spent.
- A tool that saves a small team a few hours of repetitive work every week has a value that has little to do with how long it took to write.
- The hardest part is learning what the problem costs them now. Ask.

## Questions to ask
- What happens if nothing changes?
- Who feels the pain: the owner, the manager, or the person doing the clicking?
- What would "done" look like in one sentence?

## Things to be careful about
- Fixed fees punish scope creep unless scope is written down.
- Hourly rewards slow work.
- Discounts teach buyers to ask for them. Trading scope is cleaner.

## Framing
- Describe the result first and the work second.
- Offer two or three shapes of the same thing (small, standard, thorough) so the conversation is about scope, not about whether to say yes.
- Say the number plainly, then stop talking.

## What I notice
- The projects people are proudest of are the ones where the owner could say what the problem cost them.
- Vague goals produce vague invoices and vague feelings about them.
- Cheap work attracts the most opinions about how it should be done.

## Open
- When is a retainer better than a project?
- How do you talk about ongoing upkeep without it sounding like a tax?

See [[Client onboarding checklist]].
"""),
    ("Areas/Business/Client onboarding checklist.md", "09-11 15:20", """
---
tags: [business, template]
---
# Client onboarding checklist

A template. Copy into the client note and tick as you go.

## Before the first call
- [ ] Read whatever they sent
- [ ] Write down what I think the problem is, in one sentence

## First call
- [ ] Who decides, who uses it, who pays
- [ ] What "done" looks like
- [ ] What they do today, step by step
- [ ] What goes wrong most often

## Access and data
- [ ] Sample of real data (with their say-so)
- [ ] Accounts and permissions, written down
- [ ] Where to send questions

## Ground rules
- [ ] How change requests arrive
- [ ] Who to call if it breaks
- [ ] Where the code and docs live

## After
- [ ] Short recap email the same day
- [ ] Add to [[Weekly review]]

## Red flags worth noticing early
- Nobody can say who decides.
- The data lives only in one person's head.
- Everything is urgent, all the time.
- "It should be simple" with no example attached.

## Tone
Plain words, no jargon, one question at a time. People tell you more when they are not being quizzed.
"""),
    ("Areas/Business/Weekly review.md", "09-25 16:50", """
---
tags: [business, template]
---
# Weekly review

Fifteen minutes, Friday if possible.

## Look back
- What did I finish?
- What took longer than it should have?
- What did I say yes to that I should not have?

## Look forward
- The one thing that has to happen next week
- Anything waiting on someone else
- Anything I am avoiding

## Housekeeping
- [ ] Inbox to zero (or close)
- [ ] Open PRs and issues
- [ ] Backups
- [ ] Invoices out
- [ ] Notes filed

## Rule
Keep it short. A review that takes an hour does not get done.
"""),
    ("Areas/Business/Case study outline.md", "09-14 14:30", """
---
tags: [business, writing]
---
# Case study outline

A shape for the site, not a script.

1. The situation, in two sentences
2. What was painful
3. What we built, in plain language
4. What it looks like in daily use
5. What changed for the people doing the work
6. What I would do differently

## Style
- Plain words. No "leveraged".
- One screenshot or diagram.
- Ask the client to read it before it goes up.

See [[Blog post ideas]] for the shorter format.
"""),
]


def speakers(script):
    return SPEAKER.sub(lambda m: f"{m[1]} {NAMES.get(m[2], m[2])}:", script)


def build():
    s = Story()
    for n, (who, day, script) in enumerate(sorted(CHATS, key=lambda c: c[1]), 1):
        if who == "Trailheads":
            item = group(f"nl-t{n:02d}", who, cast.TRAILHEADS, day, speakers(script))
        else:
            name, phone = CONTACTS[who]
            item = texts(f"nl-t{n:02d}", name, phone, day, speakers(script))
        s.add(item)
    for n, (path, edited, body) in enumerate(NOTES, 1):
        s.add(note(f"nl-n{n:02d}", path, body, edited))
    return s
