import json
import sys
import time

from kb import ai, store, summarize
from kb.sources import Excludes, Gone, Item, Progress, Run

COMMIT_EVERY = 200


class LocalReader:
    def __init__(self, model_factory=None):
        self._model_factory = model_factory
        self._model = None

    def _run(self, source, since):
        model = None
        if source.type.needs_model:
            if self._model is None:
                self._model = (self._model_factory or (lambda: None))()
            model = self._model
        return Run(source.options, Excludes(source.exclude), since, model)

    def events(self, source, since):
        if sys.platform not in source.type.platforms:
            raise RuntimeError(f"the {source.type_name} source runs only on {', '.join(sorted(source.type.platforms))}")
        return source.type.extract(self._run(source, since))

    def live_ids(self, source):
        return list(source.type.live_ids(self._run(source, None)))


def apply(db, source, host, event):
    """Stores the event under the id "<host>:<source_id>". Applying an event twice changes nothing the second time.
    Returns True when rows changed."""
    if isinstance(event, Progress):
        return False
    sid = f"{host}:{event.source_id}"
    if isinstance(event, Gone):
        return store.delete_item(db, source, sid) > 0
    return store.store_item(db, source, sid, event.ts, event.title, event.content, {**event.meta, "host": host})


def load_mark(db, source, host):
    raw = store.get_watermark(db, source, host)
    return json.loads(raw) if raw is not None else None


def run_unit(db, source, host, reader, log):
    """Read one source on one host: store each event, move the watermark to the largest mark seen, and prune items
    whose files are gone when the type lists them."""
    since = load_mark(db, source.name, host)
    seen = changed = 0
    mark = None
    started = time.time()
    for event in reader.events(source, since):
        mark = event.mark if mark is None else max(mark, event.mark)
        if isinstance(event, Progress):
            continue
        try:
            if not isinstance(event, (Item, Gone)):
                raise TypeError(f"not an event: {event!r}")
            changed += apply(db, source.name, host, event)
        except Exception as exc:
            log.warning("%s@%s: skipped %s: %s", source.name, host, getattr(event, "source_id", "?"), exc)
        seen += 1
        if seen % COMMIT_EVERY == 0:
            db.commit()
    if mark is not None:
        store.set_watermark(db, source.name, host, json.dumps(mark))
    pruned = 0
    if source.type.live_ids:
        db.commit()
        live = {f"{host}:{sid}" for sid in reader.live_ids(source)}
        stored = [] if not live else [r[0] for r in db.execute(
            "SELECT DISTINCT source_id FROM docs WHERE source=? AND source_id LIKE ?", (source.name, f"{host}:%"))]
        for sid in stored:
            if sid not in live:
                store.delete_item(db, source.name, sid)
                pruned += 1
    store.record_ok(db, f"{source.name}@{host}")
    store.clear_away(db, host)
    db.commit()
    log.info("%s@%s: %d items read, %d changed, %d pruned in %.1fs", source.name, host, seen, changed, pruned,
             time.time() - started)


def run(db, config, log, only=(), with_summaries=True, model_factory=None):
    """Every configured source, or those named in `only`, then summaries for new long items. Returns the number of
    failures. The caller holds the database lock."""
    model_factory = model_factory or (lambda: ai.load(config.ai))
    reader = LocalReader(model_factory)
    failures = 0
    for source in config.sources:
        if only and source.name not in only:
            continue
        try:
            run_unit(db, source, config.machine, reader, log)
        except Exception as exc:
            log.exception("%s@%s failed", source.name, config.machine)
            store.record_failure(db, f"{source.name}@{config.machine}", exc)
            failures += 1
    store.set_watermark(db, store.STATE, "last_ingest", store.now_iso())
    db.commit()
    if with_summaries and config.summarize.enabled and config.ai:
        try:
            summarize.run(db, config, model_factory(), log)
        except Exception as exc:
            log.exception("summarize failed")
            store.record_failure(db, "summarize", exc)
            failures += 1
        else:
            store.record_ok(db, "summarize")
            db.commit()
    log.info("ingest done: %d failures", failures)
    return failures
