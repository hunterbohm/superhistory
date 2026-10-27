"""Reads every configured source on every host it names and stores what changed. A failing source is recorded and
skipped. A reader reads one source on one host: LocalReader on this computer, SshReader on another, and the Sync
client's reader for a computer whose rows arrive through Sync."""
import dataclasses
import hashlib
import json
import sys
import time

from kb import ai, store, summarize
from kb.mask import mask, masked_log
from kb.remote import HostAway, SshReader
from kb.sources import Excludes, Gone, Item, Progress, Run

COMMIT_EVERY = 200


_UNBUILT = object()


class LocalReader:
    def __init__(self, model_factory=None):
        self._model_factory = model_factory or (lambda: None)
        self._built = _UNBUILT

    def _model(self):
        """The AI plug-in, built once a run, or None when none is set up."""
        if self._built is _UNBUILT:
            try:
                self._built = self._model_factory()
            except ai.NotReady:
                self._built = None
        return self._built

    def _run(self, source, since):
        return Run(source.options, Excludes(source.exclude), since, self._model() if source.type.needs_model else None)

    def events(self, source, since):
        if sys.platform not in source.type.platforms:
            raise RuntimeError(f"the {source.type_name} source runs only on {', '.join(sorted(source.type.platforms))}")
        return source.type.extract(self._run(source, since))

    def live_ids(self, source):
        return list(source.type.live_ids(self._run(source, None)))


def apply(db, source, host, event):
    """Stores the event under the id "<host>:<source_id>", masked like the text: an id such as a file name can hold a
    token. Applying an event twice changes nothing the second time. Returns True when rows changed."""
    if isinstance(event, Progress):
        return False
    sid = f"{host}:{mask(event.source_id)}"
    if isinstance(event, Gone):
        return store.delete_item(db, source, sid) > 0
    return store.store_item(db, source, sid, event.ts, event.title, event.content, {**event.meta, "host": host})


def load_mark(db, source, host):
    raw = store.get_watermark(db, source, host)
    return json.loads(raw) if raw is not None else None


def settings_digest(source):
    """A fingerprint of what decides which items a source reads: its type, options and excludes."""
    text = json.dumps({"type": source.type_name, "options": dataclasses.asdict(source.options),
                       "exclude": list(source.exclude)}, sort_keys=True, default=str)
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _forget_mark_if_settings_changed(db, source, host):
    """A watermark taken under other settings would skip items the new settings include, such as an older folder the
    source now points at, so the source starts over."""
    digest, key = settings_digest(source), f"{host}:settings"
    if store.get_watermark(db, source.name, key) != digest:
        db.execute("DELETE FROM ingest_state WHERE source=? AND key=?", (source.name, host))
        store.set_watermark(db, source.name, key, digest)


def run_unit(db, source, host, reader, log):
    """Read one source on one host: store each event, move the watermark to the largest mark seen, and prune items
    whose files are gone when the type lists them."""
    _forget_mark_if_settings_changed(db, source, host)
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
                raise TypeError(mask(f"not an event: {event!r}"))
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
        live = {f"{host}:{mask(sid)}" for sid in reader.live_ids(source)}
        stored = [] if not live else [r[0] for r in db.execute(
            "SELECT DISTINCT source_id FROM docs WHERE source=? AND substr(source_id, 1, ?) = ?",
            (source.name, len(host) + 1, f"{host}:"))]
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
    log = masked_log(log)
    model_factory = model_factory or (lambda: ai.load(config.ai))
    local = LocalReader(model_factory)
    remote = {h.name: SshReader(h) for h in config.hosts}
    away = set()
    failures = 0
    for source in config.sources:
        if only and source.name not in only:
            continue
        for host in source.hosts:
            if host in away:
                continue
            unit = f"{source.name}@{host}"
            try:
                run_unit(db, source, host, local if host == config.machine else remote[host], log)
            except HostAway:
                db.rollback()
                away.add(host)
                store.record_away(db, host)
                db.commit()
                log.info("%s is unreachable, probably asleep; its sources wait until it is back", host)
            except Exception as exc:
                log.exception("%s failed", unit)
                store.record_failure(db, unit, exc)
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
