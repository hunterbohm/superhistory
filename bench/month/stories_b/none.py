from model import Q, Story


def absent(id, question, *names):
    return Q(id, "none", question, f"Nothing in the records mentions {' or '.join(names)}.", month_lacks=names)


def build():
    s = Story()
    s.ask(
        Q("b7-01", "none", "How do I undo my last git commit but keep the changes staged?",
          "git reset --soft HEAD~1"),
        Q("b7-02", "none", "SQL to find duplicate emails in a users table?",
          "SELECT email, COUNT(*) FROM users GROUP BY email HAVING COUNT(*) > 1;"),
        Q("b7-03", "none", "What does an HTTP 304 response mean?",
          "Not Modified: the cached copy is still current, so the server sends no body."),
        Q("b7-04", "none", "How do I see what's listening on port 3000 on my Mac?",
          "lsof -nP -iTCP:3000 -sTCP:LISTEN"),
        Q("b7-05", "none", "Cleanest way to merge two dicts in Python?",
          "a | b on Python 3.9+, or {**a, **b}; keys in b win."),
        Q("b7-06", "none", "What's the difference between a 401 and a 403?",
          "401 means not authenticated (no or bad credentials); 403 means authenticated but not allowed."),
        Q("b7-07", "none", "Cron line for every weekday at 7:30 in the morning?", "30 7 * * 1-5"),
        Q("b7-08", "none", "How many fluid ounces are in a cup?", "8 US fluid ounces."),
        Q("b7-09", "none", "How do I get the size of each folder in the current directory?", "du -sh *"),
        Q("b7-10", "none", "How do I list all the tables in psql?", "\\dt"),
        Q("b7-11", "none", "What does set -euo pipefail actually do in a bash script?",
          "Exit on any failing command (-e), treat unset variables as errors (-u), and fail a pipeline if any "
          "command in it fails (pipefail)."),
        Q("b7-12", "none", "In JavaScript, why use === instead of ==?",
          "=== compares without type coercion, so 0 === '0' is false while 0 == '0' is true."),
        Q("b7-13", "none", "When are federal quarterly estimated taxes due?",
          "April 15, June 15, September 15, and January 15 of the next year (moved to the next business day "
          "when one falls on a weekend or holiday)."),
        Q("b7-14", "none", "What temperature does water boil at in Denver?",
          "About 202°F (roughly 94.5°C), lower than 212°F because of the altitude."),
        Q("b7-15", "none", "Regex for a US ZIP code, with or without the +4?", "^\\d{5}(-\\d{4})?$"),
        Q("b7-16", "none", "How do I rename the git branch I'm on?", "git branch -m new-name"),
        Q("b7-17", "none", "What port does Postgres listen on by default?", "5432"),
        Q("b7-18", "none", "Which header do I set when POSTing JSON?", "Content-Type: application/json"),
        absent("b7-19", "When's my next call with Odalys Ferreira about the Brindlewood Catering intake form?",
               "Odalys Ferreira", "Brindlewood"),
        absent("b7-20", "How much did I quote Thornbury Plumbing for the dispatch board?", "Thornbury"),
        absent("b7-21", "What did we decide about syncing memberships for Wexford Climbing?", "Wexford"),
        absent("b7-22", "What did Desmond Achterberg say about the Fiddlehead Florals invoice?",
               "Achterberg", "Fiddlehead"),
        absent("b7-23", "When's my appointment with Dr. Ruvalcaba?", "Ruvalcaba"),
        absent("b7-24", "What time is Philippa's recital?", "Philippa"),
        absent("b7-25", "Did Galloway Credit Union ever get back to me about the loan calculator?", "Galloway"),
        absent("b7-26", "What's the door code at Foundry Nine?", "Foundry Nine"),
        absent("b7-27", "Where did I leave off on the Chuparosa Taqueria menu sync?", "Chuparosa"),
        absent("b7-28", "What did Fenwick Osei want me to talk about at the meetup?", "Fenwick Osei"),
        absent("b7-29", "Which hotel did Marguerite and I book for Vail?", "Marguerite"),
        absent("b7-30", "Has Zephyr Courier paid their last invoice?", "Zephyr Courier"),
        absent("b7-31", "Where are the API keys for the Sorrel Lane Apartments maintenance portal?", "Sorrel Lane"),
        absent("b7-32", "When's the next Ironclad Bouldering league night?", "Ironclad"),
        absent("b7-33", "What did Ansel Pruitt say about subletting my desk?", "Ansel Pruitt"),
        absent("b7-34", "Which venue did the Thorvaldsens pick for the wedding?", "Thorvaldsen"),
        absent("b7-35", "What's the deadline on the grant application for Peregrine Pet Grooming?",
               "Peregrine Pet Grooming"),
        absent("b7-36", "What did the Kaleidoscope Kids director want changed on the waitlist form?", "Kaleidoscope"),
        absent("b7-37", "When is the Brightwater Brewing taproom launch?", "Brightwater"),
        absent("b7-38", "Am I signed up for the Cottonwood Ridge 50K?", "Cottonwood Ridge"),
        absent("b7-39", "What did Imogen Rask suggest about my pricing?", "Imogen Rask"),
        absent("b7-40", "Which Postgres host did we move the Tidewell Charters bookings to?", "Tidewell"),
    )
    return s
