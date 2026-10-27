"""On 2026-10-27 these domains returned NXDOMAIN and these GitHub logins did not exist."""
from cast import GH_ME, JULES
from model import Mail, Post, Q, Story, claude, codex, commit, email, hermes, note, texts, thread

REPO = f"{GH_ME}/hookbox"

NOOR = "Noor Halvorsen <noor@halvorsenwrites.com>"
NOOR_NAME, NOOR_PHONE = "Noor Halvorsen", "+17205550175"
GH_NOOR = "noorhalvorsen"

PRIYA = "Priya Desrosiers <priya@milehighautomators.org>"

GH_KALLWEIT = "kallweit-j"
GH_SUNNYBRAE = "sunnybrae-dev"
GH_LHOANG = "lhoang-ops"


def build():
    s = Story()
    s.add(note("b5-roadmap", "Projects/hookbox/hookbox roadmap.md", """# hookbox roadmap

Small local inbox for webhooks: `hookbox serve` records every request into SQLite, `hookbox replay <id>` sends it
again to localhost. ~200 stars, a handful of people filing issues.

## Now (0.8.x)
- keep the issue queue under 5 open
- answer issues within 2 working days, even if it's just "confirmed"

## Later / maybe 1.0
- prune by count? (keep last 10,000 events per source) — nobody has asked
- split the db per source
- web UI: dark mode, search by header value
- Windows: never tested. `serve` uses signals, probably breaks.
- proper docs site. Current README quickstart is from 0.2 and wrong in places. Ask Noor?

## Not doing
- hosted version
- auth on the web UI (it binds to 127.0.0.1 only)
""", "09-02 08:50"))

    s.add(email("b5-noor-scope", "hookbox docs + a workshop handout?", [
        Mail(JULES, NOOR, "09-02 10:05", """Hi Noor,

Hope the summer treated you well. I have a small paid job if you have room in September.

1. hookbox (my webhook inbox tool) needs real docs. The README quickstart is from version 0.2 and people keep
   tripping on it. I'd like a new quickstart plus two recipes: Stripe and Shopify.
2. I'm running a hands-on workshop at the end of the month and need a one-page handout attendees can follow
   with a laptop.

Same setup as the booking-form copy last spring: you invoice Marlow Works when it's done.

Jules"""),
        Mail(NOOR, JULES, "09-02 16:40", """Hey Jules,

Yes, I can fit that in. My rate went up this year, it's $70 an hour now. Rough guess: 10 hours for the docs
(quickstart + two recipes, I'll need to actually run everything) and 4 hours for the handout.

I'll send the docs as a PR. My GitHub is noorhalvorsen.

When do you need the handout final? I'd want a few days to print.

Noor

--
Noor Halvorsen
Technical writing and docs
halvorsenwrites.com"""),
        Mail(JULES, NOOR, "09-03 08:15", """$70 is fine. Let's cap it at 16 hours total for both pieces; if it looks like
it'll go over, tell me before it does. Invoice when done, net 15.

The workshop is Tuesday the 29th, so the handout needs to be final by Friday the 25th.

Thanks!
Jules"""),
    ]))

    s.add(email("b5-priya-invite", "Workshop at Mile High Automators?", [
        Mail(PRIYA, JULES, "09-03 11:20", """Hi Jules,

I'm one of the organizers of Mile High Automators. A few members use hookbox and said you live in Denver. Would
you run a hands-on workshop for us? Something practical about debugging webhooks: people wiring up Stripe, Zapier,
Shopify and so on and not knowing why nothing arrives.

Our slot is Tuesday, September 29, 6:30 to 8:00 PM, at Ironwick Commons in the second-floor classroom. It seats
36. Attendees bring their own laptops. The projector is HDMI only, so bring an adapter if your laptop needs one.

We can't pay speakers, but there's a $50 gift card and as much pizza as you can eat.

Priya Desrosiers
Mile High Automators"""),
        Mail(JULES, PRIYA, "09-03 14:02", """Hi Priya,

I'd love to. Two title ideas, pick whichever reads better for your crowd:

- "Webhooks without tears"
- "Catch every webhook: debugging integrations locally with hookbox"

Plan would be a short intro, then two exercises: capture a Stripe test event and inspect it, then replay it into
a broken handler and fix the handler.

Jules"""),
        Mail(PRIYA, JULES, "09-04 09:10", """The second one. Our members search the event page for tool names, so having
"hookbox" in the title helps. It's up: "Catch every webhook: debugging integrations locally with hookbox".

Priya"""),
        Mail(PRIYA, JULES, "09-23 12:30", """Hi Jules,

Quick update: we're at 34 RSVPs, so we'll be close to the room's 36. I'll keep a waitlist.

People will be on the Ironwick guest wifi. See you on the 29th!

Priya"""),
    ]))

    s.add(thread("b5-gh-41", REPO, 41, "issue", "Replayed Stripe events fail signature verification", GH_KALLWEIT,
                 "09-04 10:12", """
hookbox 0.8.0, Python 3.12, macOS.

I record Stripe test events with `hookbox serve` and replay them into my Flask app:

```
hookbox replay 118 --to http://localhost:5000/stripe/webhook
```

The app answers 400 with `No signatures found matching the expected signature for payload`. The original delivery
from Stripe passed. Comparing the two requests in my app's log, the replayed one has no `Stripe-Signature` header at
all.
""", [
        Post(GH_ME, "09-04 13:05", """Confirmed, thanks for the clear report. `replay` only forwards `Content-Type` and
`User-Agent`. That allowlist dates from 0.3, when I only cared about JSON bodies. The fix is to forward every
recorded header except `Host` and `Content-Length`, which the client sets itself.

That alone won't fix old events, though: Stripe's signature includes a timestamp and the libraries reject anything
older than 5 minutes by default. I'll add a flag that re-signs the payload with a fresh timestamp using your
endpoint secret."""),
        Post(GH_KALLWEIT, "09-04 14:30", "Re-signing would be great, I usually replay events from days ago."),
        Post(GH_ME, "09-09 14:20", """Released in 0.8.1. `replay` now forwards the recorded headers, and
`hookbox replay 118 --to ... --stripe-secret whsec_...` re-signs the event with the current time so old events pass
verification. Closing, reopen if it misbehaves."""),
    ], state="closed", closed="09-09 14:20", labels=("bug",)))

    s.add(claude("b5-claude-replay", "09-08 09:40", 70, REPO, "Forward recorded headers on replay, add --stripe-secret",
                 """
U: hookbox issue 41. replay drops Stripe-Signature because we only forward content-type and user-agent. forward all recorded headers except host and content-length. also add --stripe-secret that re-signs stripe events with a fresh timestamp so old events pass the 5 minute tolerance
T Grep: ALLOWED_HEADERS in /Users/jules/code/hookbox/src
R: /Users/jules/code/hookbox/src/hookbox/replay.py:9:ALLOWED_HEADERS = ("content-type", "user-agent")
/Users/jules/code/hookbox/src/hookbox/replay.py:31:    headers = {k: v for k, v in event.headers.items() if k.lower() in ALLOWED_HEADERS}
T Read: /Users/jules/code/hookbox/src/hookbox/replay.py
R: import httpx

from hookbox.store import Event

ALLOWED_HEADERS = ("content-type", "user-agent")


def replay(event: Event, target: str, timeout: float = 10.0) -> httpx.Response:
    headers = {k: v for k, v in event.headers.items() if k.lower() in ALLOWED_HEADERS}
    return httpx.post(target, content=event.body, headers=headers, timeout=timeout)
A: Two changes in `replay.py`:

1. Swap the allowlist for a small denylist. `Host` has to go because the target differs from the original, and `Content-Length` because httpx computes it from the body. I'll also drop hop-by-hop headers (`Connection`, `Transfer-Encoding`, `Keep-Alive`), which describe the original connection, not the request.
2. `--stripe-secret`: Stripe's header reads `t=<unix time>,v1=<hex HMAC-SHA256 of "t.body">`. With a secret, I'll compute a new `t` from the current time and a new `v1`, and replace the header. Without the flag, the recorded header is sent as is, so a replay within 5 minutes of capture still verifies with the original signature.

The body has to be sent byte for byte as recorded, which it already is (`content=event.body`).
T Edit: /Users/jules/code/hookbox/src/hookbox/replay.py
R: The file has been updated successfully.
T Read: /Users/jules/code/hookbox/src/hookbox/cli.py
R: @app.command()
def replay(event_id: int, to: str = typer.Option(..., "--to"), timeout: float = 10.0):
    event = store.get(event_id)
T Edit: /Users/jules/code/hookbox/src/hookbox/cli.py
R: The file has been updated successfully.
T Write: /Users/jules/code/hookbox/tests/test_replay_stripe.py
R: File created successfully at: /Users/jules/code/hookbox/tests/test_replay_stripe.py
T Bash: cd /Users/jules/code/hookbox && uv run pytest -q
R: ..............................
30 passed in 1.12s
A: All 30 pass. The new test records a signed event, replays it with `--stripe-secret` an hour "later" (frozen clock), and checks it with `stripe.Webhook.construct_event`, the same call kallweit-j's app makes. Another test checks that `Host` and `Content-Length` are never forwarded and everything else is.
U: commit it, I'll cut 0.8.1 tomorrow
T Bash: git add -A && git commit -qm "replay: forward recorded headers, add --stripe-secret (fixes #41)" && git log --oneline -1
R: 7c1e9a4 replay: forward recorded headers, add --stripe-secret (fixes #41)
""", branch="replay-headers"))

    s.add(commit("b5-commit-replay", REPO, "09-08 10:50", """replay: forward recorded headers, add --stripe-secret (fixes #41)

Replay sent only Content-Type and User-Agent, so Stripe-Signature was lost and
verification failed. Forward every recorded header except Host, Content-Length
and hop-by-hop headers. --stripe-secret re-signs a Stripe event with the current
timestamp, so events older than Stripe's 5 minute tolerance still verify.""",
                     ["M\tsrc/hookbox/replay.py", "M\tsrc/hookbox/cli.py", "A\ttests/test_replay_stripe.py",
                      "M\tCHANGELOG.md"]))

    s.add(thread("b5-gh-43", REPO, 43, "issue", "Database grows forever: option to delete old events?", GH_SUNNYBRAE,
                 "09-10 07:55", """
I leave `hookbox serve` running on a small box that records a Shopify store's webhooks for debugging. After about
four months `hookbox.db` is 2.3 GB and the web UI takes several seconds to list events.

Could hookbox delete old events automatically? I only ever look at the last week or two.
""", [
        Post(GH_ME, "09-11 08:30", """Fair, this should have existed already. Plan:

- `hookbox serve --keep-days N` deletes events older than N days, at startup and then every hour.
- Default 14 days. `--keep-days 0` keeps everything, like today.
- `hookbox pin <id>` marks an event to keep forever, for the one weird payload you want to hold on to.

I'll try to get it into 0.9.0."""),
        Post("tovah-r", "09-11 10:02", "Could it be by size instead, e.g. keep the db under 500 MB?"),
        Post(GH_ME, "09-11 11:15", """Not for now. Days are easier to reason about ("is last Tuesday's event still
there?"), and size limits mean deciding which source loses events first."""),
        Post(GH_ME, "09-25 15:10", "Shipped in 0.9.0. Closing."),
    ], state="closed", closed="09-25 15:10", labels=("enhancement",)))

    s.add(codex("b5-codex-prune", "09-15 13:30", 85, REPO, """
U: hookbox #43: add --keep-days to serve. default 14 days, 0 = keep everything. prune at startup and hourly. add `hookbox pin <id>` so pinned events are never pruned. also the db file should actually shrink after pruning
T exec_command: sed -n 1,40p src/hookbox/store.py
R: SCHEMA = '''
create table if not exists events (
  id integer primary key,
  source text not null,
  received_at text not null,
  method text not null,
  path text not null,
  headers text not null,
  body blob not null
);
'''

def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db
A: Plan:

- Add a `pinned integer not null default 0` column through a migration, and an index on `received_at`.
- `prune(db, keep_days, now)` runs `delete from events where pinned = 0 and received_at < ?`. `keep_days == 0` returns early.
- `serve` calls `prune` at startup and from a background task every hour.

On shrinking the file: SQLite doesn't give pages back to the OS after a delete unless the database uses `auto_vacuum`. I'll set `PRAGMA auto_vacuum = INCREMENTAL` for new databases and run `PRAGMA incremental_vacuum` after each prune. The catch is that `auto_vacuum` can only change on an existing database through a full `VACUUM`, so databases created before 0.9.0 won't shrink until they're vacuumed once. I'll add `hookbox vacuum` for that and say so in the changelog.
T apply_patch: *** Begin Patch
*** Update File: src/hookbox/store.py
@@
-def connect(path: Path) -> sqlite3.Connection:
-    db = sqlite3.connect(path)
+def connect(path: Path) -> sqlite3.Connection:
+    new = not path.exists()
+    db = sqlite3.connect(path)
+    if new:
+        db.execute("pragma auto_vacuum = incremental")
     db.executescript(SCHEMA)
+    migrate(db)
     return db
+
+
+def prune(db: sqlite3.Connection, keep_days: int, now: datetime) -> int:
+    if keep_days == 0:
+        return 0
+    cutoff = (now - timedelta(days=keep_days)).isoformat()
+    n = db.execute("delete from events where pinned = 0 and received_at < ?", (cutoff,)).rowcount
+    db.execute("pragma incremental_vacuum")
+    db.commit()
+    return n
*** End Patch
R: Success. Updated the following files:
M src/hookbox/store.py
T apply_patch: *** Begin Patch
*** Update File: src/hookbox/cli.py
@@
-def serve(port: int = 8787):
+def serve(port: int = 8787, keep_days: int = typer.Option(14, "--keep-days", min=0)):
*** End Patch
R: Success. Updated the following files:
M src/hookbox/cli.py
T exec_command: uv run pytest -q tests/test_prune.py tests/test_cli.py
R: .........
9 passed in 0.66s
T exec_command: cp ~/scratch/hookbox-big.db /tmp/hb.db && ls -lh /tmp/hb.db && uv run hookbox vacuum --db /tmp/hb.db && uv run hookbox serve --db /tmp/hb.db --keep-days 14 --once && ls -lh /tmp/hb.db
R: -rw-r--r--  1 jules  wheel   612M Sep 15 14:31 /tmp/hb.db
vacuum: auto_vacuum set to incremental (was none)
pruned 418,233 events older than 14 days (12 pinned kept)
-rw-r--r--  1 jules  wheel    41M Sep 15 14:33 /tmp/hb.db
A: Done. On a copy of your 612 MB test database, `hookbox vacuum` switched it to incremental auto_vacuum, then a 14-day prune removed 418,233 events and the file went to 41 MB. Without the vacuum step, the same prune left the file at 612 MB. `--keep-days 0` is covered by a test and deletes nothing, and pinned events survive any prune.
""", branch="keep-days", title="hookbox: --keep-days pruning and pin"))

    s.add(thread("b5-gh-44", REPO, 44, "PR", "docs: new quickstart, Stripe and Shopify recipes", GH_NOOR, "09-17 10:20",
                 """
Replaces the 0.2-era quickstart in the README with `docs/quickstart.md`, and adds two recipes:

- `docs/recipes/stripe.md`: record test events with the Stripe CLI, replay with `--stripe-secret`
- `docs/recipes/shopify.md`: record an order webhook, verify the HMAC in a small Flask handler

I ran every command on a clean macOS user and on Ubuntu 24.04. README now links to the docs instead of repeating them.
""", [
        Post(GH_ME, "09-17 15:45", """This is so much better than what was there. One fix in the Shopify recipe: the
handler compares against `hexdigest()`, but Shopify sends `X-Shopify-Hmac-Sha256` as base64. Use
`base64.b64encode(digest)` and `hmac.compare_digest`.""", verdict="CHANGES_REQUESTED"),
        Post(GH_NOOR, "09-18 09:05", "Good catch, fixed and re-ran the recipe against a dev store. It verifies now."),
        Post(GH_ME, "09-18 09:40", "Thanks Noor!", verdict="APPROVED"),
    ], state="merged", merged="09-18 09:42", branches="noorhalvorsen:docs-rewrite -> main", labels=("docs",)))

    s.add(note("b5-workshop-plan", "Talks/Mile High Automators workshop.md", """# Catch every webhook: debugging integrations locally with hookbox

Mile High Automators, Tuesday September 29, 6:30–8:00 PM
Ironwick Commons, second-floor classroom (36 seats). Projector is HDMI only: bring the USB-C to HDMI adapter.

## Before
- release 0.9.0 by Friday so everyone installs the same version
- attendees: Python 3.10+ and pipx. Install line on the handout: `pipx install hookbox`
- Noor: handout final by Fri 25th

## Agenda
- 6:30 intro: what a webhook is, why "nothing arrived" is the most common bug (10 min)
- 6:40 install + `hookbox serve` + `hookbox tunnel` so Stripe can reach the laptop (10 min)
- 6:50 exercise 1: `stripe trigger payment_intent.succeeded`, inspect it in the web UI (20 min)
- 7:10 exercise 2: replay into the broken Flask handler from the repo, find the bug, fix it (25 min)
- 7:35 Q&A (15 min)

## If the network fails
Venue wifi is a guest network, so tunnels may not work. Backup: everyone loads the recorded events offline with
`hookbox load fixtures/workshop.jsonl` (12 Stripe events, two of them deliberately malformed) and does both
exercises from there. Put the command on the handout.
""", "09-22 10:30"))

    s.add(texts("b5-noor-texts", NOOR_NAME, NOOR_PHONE, "09-24", """
10:12 Noor Halvorsen: handout v3 is in the shared folder. I moved the offline fixtures command to a box at the top so nobody misses it
10:15 Jules: perfect. Priya says 34 RSVPs
10:16 Noor Halvorsen: so print 40? I can do it at the shop by my place
10:18 Jules: 40 copies, yes. put the printing on your invoice
10:19 Noor Halvorsen: 👍 I'll drop them at your place Monday
"""))

    s.add(commit("b5-commit-090", REPO, "09-25 14:40", """release 0.9.0

- serve --keep-days N deletes events older than N days at startup and hourly.
  Default 14 days; --keep-days 0 keeps everything (#43).
- hookbox pin <id>: pinned events are never deleted.
- hookbox vacuum: databases created before 0.9.0 have auto_vacuum off and
  won't shrink after pruning until you run it once.
- Python 3.10 is now the minimum; 3.9 is no longer supported.
- New docs: quickstart, Stripe and Shopify recipes (#44, thanks @noorhalvorsen).""",
                     ["M\tCHANGELOG.md", "M\tpyproject.toml", "M\tsrc/hookbox/__init__.py",
                      "M\t.github/workflows/test.yml"]))

    s.add(thread("b5-gh-46", REPO, 46, "issue", "serve crashes on Windows: module 'signal' has no attribute 'SIGHUP'",
                 GH_LHOANG, "09-26 11:05", """
hookbox 0.9.0, Python 3.12, Windows 11.

```
> hookbox serve
Traceback (most recent call last):
  File "C:\\Users\\lh\\pipx\\venvs\\hookbox\\Lib\\site-packages\\hookbox\\cli.py", line 88, in serve
    signal.signal(signal.SIGHUP, _reload)
AttributeError: module 'signal' has no attribute 'SIGHUP'
```
""", [
        Post(GH_ME, "09-26 16:20", """Thanks. Windows has never been supported or tested, and SIGHUP doesn't exist
there. Guarding that line is easy, but I don't have a Windows machine to test the rest, so I'm labeling this help
wanted. Until then, hookbox runs fine under WSL."""),
    ], labels=("help wanted", "windows")))

    s.add(email("b5-noor-invoice", "Invoice NH-2026-031: hookbox docs and workshop handout", [
        Mail(NOOR, JULES, "09-28 10:30", """Hi Jules,

Invoice attached for September:

- hookbox docs (quickstart, Stripe and Shopify recipes, review fixes): 9.5 hours
- workshop handout (three drafts): 3.5 hours
- 13 hours at $70/hour: $910.00
- printing, 40 copies of the handout: $22.00

Total: $932.00, due in 15 days.

Good luck tomorrow!

Noor

--
Noor Halvorsen
Technical writing and docs
halvorsenwrites.com""", attachments=("NH-2026-031.pdf",)),
        Mail(JULES, NOOR, "09-28 11:05", """Thanks Noor, paying it this week. Under the estimate, too.

Jules"""),
    ]))

    s.add(hermes("b5-hermes-debrief", "09-29 21:35", 6, "Workshop debrief", """
U: log this for me. mile high automators workshop is done. 29 showed up out of 34 rsvps. about 15 min in the ironwick guest wifi started killing the tunnels so everyone switched to the fixtures file and both exercises still worked. 4 people on windows, 3 got going in WSL, 1 just watched. priya wants me back in january for a part 2 on retries and idempotency
A: Logged. Mile High Automators workshop, Tuesday September 29:
- 29 showed up out of 34 RSVPs.
- About 15 minutes in, the guest wifi started cutting the tunnels. Everyone switched to the offline fixtures file and finished both exercises.
- 4 attendees were on Windows. 3 got hookbox running in WSL, 1 followed along without it.
- Priya wants you back in January for a part 2 on retries and idempotency.
Want me to remind you to reply to Priya about January?
U: yes, next monday morning
A: Done. I'll remind you Monday October 5 at 9:00 to reply to Priya about a January part 2.
"""))

    s.ask(
        Q("b5-01", "single", "Why were replayed Stripe events failing in hookbox?",
          "Replay forwarded only Content-Type and User-Agent, so the Stripe-Signature header was dropped. It now "
          "forwards every recorded header except Host, Content-Length and the hop-by-hop headers Connection, "
          "Transfer-Encoding and Keep-Alive (fixed in 0.8.1).",
          ("b5-gh-41", "b5-claude-replay", "b5-commit-replay"), evidence_has=("Stripe-Signature", "User-Agent")),
        Q("b5-02", "single", "How do I replay an old Stripe event without it failing the timestamp check?",
          "Pass the endpoint secret: `hookbox replay <id> --to ... --stripe-secret whsec_...` re-signs the event with "
          "the current time, so it passes Stripe's 5-minute tolerance.",
          ("b5-gh-41", "b5-claude-replay", "b5-commit-replay"), evidence_has=("--stripe-secret", "5 minute")),
        Q("b5-03", "single", "How long does hookbox keep events by default now?",
          "14 days (`serve --keep-days`, pruned at startup and hourly); `--keep-days 0` keeps everything and pinned "
          "events are never deleted.", ("b5-gh-43", "b5-codex-prune", "b5-commit-090"),
          evidence_has=("--keep-days", "14 days")),
        Q("b5-04", "single", "Someone upgraded hookbox and pruning didn't shrink their db file. What do they run?",
          "`hookbox vacuum`, once. Databases created before 0.9.0 have auto_vacuum off, so the file won't shrink "
          "after pruning until it's vacuumed.", ("b5-codex-prune", "b5-commit-090"),
          evidence_has=("hookbox vacuum", "auto_vacuum")),
        Q("b5-05", "single", "What's the oldest Python version hookbox supports?",
          "Python 3.10; 3.9 was dropped in 0.9.0.", ("b5-commit-090", "b5-workshop-plan"),
          evidence_has=("3.10",)),
        Q("b5-06", "single", "Does hookbox run on Windows?",
          "Not yet. `hookbox serve` crashes because signal.SIGHUP doesn't exist on Windows; issue #46 is open as "
          "help wanted, and WSL works in the meantime.", ("b5-gh-46",), evidence_has=("SIGHUP", "WSL")),
        Q("b5-07", "single", "What's Noor's hourly rate?",
          "$70 an hour.", ("b5-noor-scope", "b5-noor-invoice"), evidence_has=("$70",)),
        Q("b5-08", "single", "When and where is my Mile High Automators workshop?",
          "Tuesday September 29, 6:30 to 8:00 PM, in the second-floor classroom at Ironwick Commons.",
          ("b5-priya-invite", "b5-workshop-plan"), evidence_has=("September 29", "Ironwick Commons")),
        Q("b5-09", "single", "Which title did we go with for the meetup workshop?",
          "\"Catch every webhook: debugging integrations locally with hookbox\" (not \"Webhooks without tears\").",
          ("b5-priya-invite", "b5-workshop-plan"), evidence_has=("Catch every webhook",)),
        Q("b5-10", "single", "How many people came to the workshop?",
          "29, out of 34 RSVPs.", ("b5-hermes-debrief",), evidence_has=("29 showed up",)),
        Q("b5-11", "single", "How many handouts did we print?",
          "40 copies.", ("b5-noor-texts", "b5-noor-invoice"), evidence_has=("40 copies",)),
        Q("b5-12", "single", "Who rewrote the hookbox docs, and what's in them?",
          "Noor Halvorsen, in PR #44: a new quickstart plus Stripe and Shopify recipes, merged September 18.",
          ("b5-gh-44", "b5-commit-090"), evidence_has=("quickstart", "Shopify")),
        Q("b5-13", "single", "What did I push back on in Noor's docs PR?",
          "The Shopify recipe compared a hex digest, but Shopify sends X-Shopify-Hmac-Sha256 as base64; she fixed it.",
          ("b5-gh-44",), evidence_has=("base64", "hexdigest")),
        Q("b5-14", "single", "What does Priya from Mile High Automators want me to do next?",
          "Come back in January for a part 2 workshop on retries and idempotency.", ("b5-hermes-debrief",),
          evidence_has=("January", "retries and idempotency")),
        Q("b5-15", "single", "What do I need to plug my laptop into the projector at Ironwick?",
          "The projector is HDMI only, so a USB-C to HDMI adapter.", ("b5-priya-invite", "b5-workshop-plan"),
          evidence_has=("HDMI only",)),
        Q("b5-16", "single", "How big had the hookbox database gotten for the person who asked for auto-delete?",
          "2.3 GB after about four months recording a Shopify store's webhooks.", ("b5-gh-43",),
          evidence_has=("2.3 GB",)),
        Q("b5-17", "multi", "Did Noor stay under the hours cap I gave her?",
          "Yes. The cap was 16 hours for the docs and the handout; she billed 13 hours (9.5 docs, 3.5 handout).",
          ("b5-noor-scope", "b5-noor-invoice"), evidence_has=("16 hours", "13 hours")),
        Q("b5-18", "multi", "What was my backup if the network failed at the workshop, and did I need it?",
          "Loading recorded events offline with `hookbox load fixtures/workshop.jsonl`. Yes: about 15 minutes in, "
          "the guest wifi cut the tunnels, everyone switched to the fixtures file, and both exercises still worked.",
          ("b5-workshop-plan", "b5-hermes-debrief"), evidence_has=("fixtures/workshop.jsonl", "fixtures file")),
        Q("b5-19", "multi", "Which hookbox fixes or features this month came from outside issues, and in which release?",
          "Forwarding recorded headers on replay plus --stripe-secret (#41) shipped in 0.8.1; --keep-days pruning "
          "and `hookbox pin` (#43) shipped in 0.9.0. The Windows crash (#46) is still open.",
          ("b5-gh-41", "b5-gh-43", "b5-commit-090", "b5-gh-46"), evidence_has=("0.8.1", "0.9.0", "help wanted")),
        Q("b5-20", "temporal", "How many days was the Stripe signature bug in hookbox open?",
          "5 days: opened September 4 and closed September 9.", ("b5-gh-41",),
          evidence_has=("created 2026-09-04", "closed 2026-09-09")),
    )
    return s
