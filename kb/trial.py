"""The made-up month is `items.jsonl` (one item a line: source, source_id, ts, title, content, meta) and
`questions.jsonl` (id, question, answer, evidence as source ids, kind; kind "none" means the right result is to add
nothing).
"""
import json
from pathlib import Path

import kb
from kb import COMMAND, ai, check, search, store
from kb.mask import holds_secret, mask, secret_in_id

HOST = "demo"
SHOWN_HITS = 5


def month_dir():
    """The Benchmarks track's month when it ships beside the package (demo/month), else the small one inside it."""
    beside = Path(kb.__file__).resolve().parent.parent / "demo" / "month"
    return beside if (beside / "items.jsonl").exists() else Path(kb.__file__).resolve().parent / "sample_month"


def _lines(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load(db, folder):
    # A month someone else made can name a record after a secret; such a record is skipped, as ingest skips one.
    items = [item for item in _lines(Path(folder) / "items.jsonl")
             if not holds_secret(str(item["source"])) and not secret_in_id(str(item["source_id"]))]
    for item in items:
        store.store_item(db, item["source"], f"{HOST}:{item['source_id']}", item["ts"], item["title"],
                         item["content"], {**item.get("meta", {}), "host": HOST})
    store.set_watermark(db, store.STATE, "last_ingest", store.now_iso())
    db.commit()
    return len(items)


def questions(folder):
    return _lines(Path(folder) / "questions.jsonl")


def samples(all_questions, chosen=None):
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
            out(f"  {h['ts'][:10]}  {h['source']}  {mask(h['title'])[:80]}")
        out("The check would now ask your AI plug-in which of these fit, and add only those. "
            f"Set one up with `{COMMAND} setup` to see it.")
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
    out(f"\nSearch the made-up month yourself with `{COMMAND} --try search \"words\"`.")
