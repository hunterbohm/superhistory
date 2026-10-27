"""`kb try`: a made-up month in a database of its own, and the check run on its sample questions.

The month is `items.jsonl` (one item a line: source, source_id, ts, title, content, meta) and `questions.jsonl` (id,
question, answer, evidence as source ids, kind; kind "none" means the right result is to add nothing).
"""
import json
from pathlib import Path

import kb
from kb import ai, check, search, store

HOST = "demo"
SHOWN_HITS = 5


def month_dir():
    """The month that ships beside the package: demo/month in a checkout or an install."""
    return Path(kb.__file__).resolve().parent.parent / "demo" / "month"


def _lines(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load(db, folder):
    """Stores every item of the month; loading it again changes nothing. Returns the number of items."""
    items = _lines(Path(folder) / "items.jsonl")
    for item in items:
        store.store_item(db, item["source"], f"{HOST}:{item['source_id']}", item["ts"], item["title"],
                         item["content"], {**item.get("meta", {}), "host": HOST})
    store.set_watermark(db, store.STATE, "last_ingest", store.now_iso())
    db.commit()
    return len(items)


def questions(folder):
    return _lines(Path(folder) / "questions.jsonl")


def samples(all_questions, chosen=None):
    """The question asked for, or the first one with an answer and the first one where the right result is nothing."""
    if chosen:
        return [q for q in all_questions if q["id"] == chosen]
    answered = next((q for q in all_questions if q["kind"] != "none"), None)
    empty = next((q for q in all_questions if q["kind"] == "none"), None)
    return [q for q in (answered, empty) if q]


def show(db, question, config, model, out):
    out(f'\nQuestion: "{question["question"]}"')
    if question["kind"] == "none":
        out("Nothing in the month fits this one, so the right result is to add nothing.")
    if model is None:
        hits = search.search(db, question["question"], limit=SHOWN_HITS)
        out(f"Search finds {len(hits)} candidate record{'s' if len(hits) != 1 else ''}:" if hits else "Search finds nothing.")
        for h in hits:
            out(f"  {h['ts'][:10]}  {h['source']}  {h['title'][:80]}")
        out("The check would now ask your AI plug-in which of these fit, and add only those. "
            "Set one up with `kb setup` to see it.")
        return
    budget = config.check.budget_seconds or model.traits.check_budget
    try:
        found = check.lookup(db, question["question"], [], "", set(), model, budget, model.traits.hedge_after)
    except Exception as exc:
        out(f"The check failed with {type(exc).__name__}: {exc}")
        return
    out(f"The check searched for: {found.query}" if found.query else "The check saw nothing to search for.")
    out(f"Search found {len(found.candidates)} candidate{'s' if len(found.candidates) != 1 else ''}; "
        f"the check kept {len(found.hits)}.")
    out(check.render(found.hits, HOST) if found.hits else "Added nothing.")
    if question["evidence"]:
        expected = {f"{HOST}:{sid}" for sid in question["evidence"]}
        kept = {h["source_id"] for h in found.hits}
        out(f"Records that answer it: {len(expected & kept)} of {len(expected)} shown.")


def run(paths, config, folder=None, chosen=None, out=print):
    folder = Path(folder) if folder else month_dir()
    if not (folder / "items.jsonl").exists():
        raise FileNotFoundError(f"no made-up month at {folder}")
    trial = paths.for_try()
    db = store.connect(trial.db)
    try:
        count = load(db, folder)
        out(f"Loaded {count} made-up records into {trial.db}. Your own data is not touched.")
        model = None
        if config.ai:
            model = ai.load(config.ai)
            out(f"The check runs with {model.name} ({model.model}).")
        for question in samples(questions(folder), chosen):
            show(db, question, config, model, out)
    finally:
        db.close()
    out("\nSearch the made-up month yourself with `kb --try search \"words\"`.")
