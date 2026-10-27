from model import Q, Story

UNKNOWN = "Nothing in the records covers this, so the answer should say it isn't known rather than guess."

GENERAL = (
    ("Write a regex that matches a US ZIP code, with or without the +4 part.", r"^\d{5}(-\d{4})?$"),
    ("What's the difference between HTTP 401 and 403?",
     "401 means the request isn't authenticated (no or bad credentials); 403 means the server knows who you are "
     "but you aren't allowed."),
    ("How do I undo my last git commit but keep the changes staged?", "git reset --soft HEAD~1"),
    ("Convert 72 degrees Fahrenheit to Celsius.", "About 22.2 °C."),
    ("In Python, what's the difference between a list and a tuple, in two sentences?",
     "A list is mutable and a tuple is immutable. Tuples can be dictionary keys when their items are hashable."),
    ("Give me a cron expression for 6:30am on weekdays.", "30 6 * * 1-5"),
    ("What does set -euo pipefail do at the top of a bash script?",
     "Exit on any error (-e), treat unset variables as errors (-u), and make a pipeline fail if any command in it "
     "fails (pipefail)."),
    ("How many tablespoons are in a quarter cup?", "4 tablespoons."),
    ("Explain what an idempotency key is in a payments API.",
     "A unique key the client sends with a request so a retry of the same request is applied only once."),
    ("Write a SQL query that returns the second-highest salary from an employees table.",
     "SELECT MAX(salary) FROM employees WHERE salary < (SELECT MAX(salary) FROM employees);"),
    ("What's a polite way to decline a meeting without giving a reason?",
     "Thank them, say you can't make it this time, and offer an alternative such as an email update."),
    ("What is the capital of Australia?", "Canberra."),
    ("How do I make a Python dataclass immutable?", "Use @dataclass(frozen=True)."),
    ("What's the time complexity of binary search?", "O(log n)."),
    ("Rename every .jpeg file in this folder to .jpg with one shell command.",
     "for f in *.jpeg; do mv -- \"$f\" \"${f%.jpeg}.jpg\"; done"),
    ("What does the Retry-After HTTP header mean?",
     "How long the client should wait before retrying, in seconds or as an HTTP date, sent with 429 or 503."),
    ("Explain the difference between git merge and git rebase in plain words.",
     "Merge joins two histories with a merge commit; rebase replays your commits on top of the other branch, "
     "giving a straight history."),
    ("Suggest a name for a function that removes duplicate items from a list but keeps their order.",
     "Something like dedupe_preserving_order or unique_in_order."),
)

ABSENT = (
    ("What did the Hollow Oak Brewing owner say about their taproom menu boards?", ("Hollow Oak",)),
    ("When is the Lakeview Pharmacy refill-reminder pilot supposed to start?", ("Lakeview Pharmacy",)),
    ("What was Greta Lindqvist's feedback on the logo concepts?", ("Greta Lindqvist",)),
    ("Did Summit Roofing ever sign the estimate for the lead-intake bot?", ("Summit Roofing",)),
    ("What rate limit did we hit with the Kestrel Insurance quotes integration?", ("Kestrel",)),
    ("What did we decide about the Ostara Yoga class waitlist texts?", ("Ostara",)),
    ("How much did Bramble Florist pay for the wedding-order form?", ("Bramble",)),
    ("What's the Wi-Fi password at the Juniper Vet Clinic front desk?", ("Juniper Vet",)),
    ("Which Shopify theme did Marigold Tea Co pick?", ("Marigold Tea",)),
    ("When did Atlas Moving's dispatcher say the new trucks arrive?", ("Atlas Moving",)),
    ("What did the vet say about Biscuit's allergy?", ("Biscuit",)),
    ("Remind me what we agreed with Ridgeline Chiropractic about HIPAA logging.", ("Ridgeline Chiropractic",)),
    ("What did Tomas at the coworking space say about the new desk prices?", ("Tomas",)),
    ("Which flight did I book for the conference in Austin?", ("Austin",)),
    ("What's the status of the Halcyon Hotels chatbot proposal?", ("Halcyon",)),
    ("What did Oren say about moving the client apps to Kubernetes?", ("Oren",)),
    ("How did the Pinewood Dental reminder pilot go?", ("Pinewood",)),
    ("What did Fernbrook's accountant say about sales tax on catering orders?", ("catering",)),
    ("How big is Copperline's warehouse in Pueblo?", ("Pueblo",)),
    ("What did Marisol say about the hygienist schedule change at Harbor Lane?", ("hygienist",)),
    ("When does Quill & Ledger's office move to the new building?", ("office move", "new building")),
    ("What did the Pinewood Dental office manager think of TextLoom's pricing?", ("Pinewood",)),
)


def build():
    s = Story()
    s.ask(*(Q(f"none-gen-{i:02d}", "none", q, a) for i, (q, a) in enumerate(GENERAL, 1)))
    s.ask(*(Q(f"none-abs-{i:02d}", "none", q, UNKNOWN, month_lacks=words) for i, (q, words) in enumerate(ABSENT, 1)))
    return s
