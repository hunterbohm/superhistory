"""Reads every configured source on every host it names and stores what changed. A failing source is recorded and
skipped. A reader reads one source on one host: LocalReader on this computer, SshReader on another, and the Sync
client's reader for a computer whose rows arrive through Sync."""
import dataclasses
import importlib
import hashlib
import json
import sys
import time

from kb import ai, ids, mask as mask_module, store, summarize
from kb.mask import holds_secret, id_names, mask, masked_log, secret_in_id
from kb.remote import HostAway, SshReader
from kb.sources import Excludes, Gone, Item, Progress, Run

COMMIT_EVERY = 200
# How long one ingest spends masking older rows again; the rest wait for the next ingest, read masked meanwhile.
REMASK_SECONDS = 60


_UNBUILT = object()


class LocalReader:
    def __init__(self, model_factory=None, plugins_dir=""):
        self._model_factory = model_factory or (lambda: None)
        self._plugins_dir = plugins_dir
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
        return Run(source.options, Excludes(source.exclude), since, self._model() if source.type.needs_model else None,
                   self._plugins_dir)

    def events(self, source, since):
        if sys.platform not in source.type.platforms:
            raise RuntimeError(f"the {source.type_name} source runs only on {', '.join(sorted(source.type.platforms))}")
        return source.type.extract(self._run(source, since))

    def live_ids(self, source):
        return list(source.type.live_ids(self._run(source, None)))


def apply(db, source, prefix, event, host):
    """Stores the event under the id "<prefix><source_id>", the prefix of the install that read it (ids.prefix). An
    item whose id looks like it holds a secret, such as a file named after a token, is not stored. Applying an event
    twice changes nothing the second time. Returns True when rows changed."""
    if holds_secret(source) or holds_secret(host):
        raise ValueError("a source or host name that looks like a secret is not stored")
    if isinstance(event, Progress) or secret_in_id(event.source_id, id_names(getattr(event, "meta", None))):
        return False
    sid = prefix + event.source_id
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


def run_unit(db, source, host, reader, log, prefix):
    """Read one source on one host: store each event under `prefix`, move the watermark to the largest mark seen, and
    prune items whose files are gone when the type lists them. A prune deletes only under `prefix`."""
    _forget_mark_if_settings_changed(db, source, host)
    since = load_mark(db, source.name, host)
    seen = changed = 0
    mark = None
    started = time.time()
    for event in reader.events(source, since):
        if holds_secret(json.dumps(event.mark, default=str)):
            log.warning("%s@%s: skipped an event whose position looks like it holds a secret", source.name, host)
            continue
        mark = event.mark if mark is None else max(mark, event.mark)
        if isinstance(event, Progress):
            continue
        try:
            if not isinstance(event, (Item, Gone)):
                raise TypeError(f"not an event: a {type(event).__name__}")
            if not isinstance(event, Progress) and secret_in_id(event.source_id,
                                                                 id_names(getattr(event, "meta", None))):
                if source.type.all_or_nothing:
                    raise ValueError("an item's id looks like it holds a secret; give it an id that does not")
                log.warning("%s@%s: skipped %s, whose name looks like it holds a secret; rename it to index it",
                            source.name, host, mask(event.source_id))
            changed += apply(db, source.name, prefix, event, host)
        except Exception as exc:
            if source.type.all_or_nothing:
                raise RuntimeError(f"the store refused an item ({type(exc).__name__}: {mask(str(exc))[:100]}), so "
                                   "nothing of this run was kept and its watermark stays") from None
            log.warning("%s@%s: skipped %s: %s", source.name, host, getattr(event, "source_id", "?"), exc)
        seen += 1
        if seen % COMMIT_EVERY == 0 and not source.type.all_or_nothing:
            db.commit()
    if mark is not None:
        store.set_watermark(db, source.name, host, json.dumps(mark))
    pruned = 0
    if source.type.live_ids:
        db.commit()
        live = {prefix + sid for sid in reader.live_ids(source) if not secret_in_id(sid)}
        stored = [] if not live else [r[0] for r in db.execute(
            "SELECT DISTINCT source_id FROM docs WHERE source=? AND substr(source_id, 1, ?) = ?",
            (source.name, len(prefix), prefix))]
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
    capped = mask_module.capped
    model_factory = model_factory or (lambda: ai.load(config.ai))
    store.clean_state(db)
    db.commit()
    local = LocalReader(model_factory, config.plugins_dir)
    remote = {h.name: SshReader(h) for h in config.hosts}
    away = set()
    failures = 0
    for source in config.sources:
        if only and source.name not in only:
            continue
        for host in source.hosts:
            if host in away:
                continue
            if holds_secret(source.name) or holds_secret(host):
                # A config built without config.parse can hold such a name; it goes into every id and health key.
                log.warning("a source or host name looks like a secret; skipped it without storing anything")
                failures += 1
                continue
            unit = f"{source.name}@{host}"
            try:
                if host == config.machine:
                    run_unit(db, source, host, local, log, ids.prefix(config.owner))
                else:
                    run_unit(db, source, host, remote[host], log, ids.prefix(config.owner, host))
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
    if store.old_rows(db) or store.rebuild_owed(db):
        changed, left = store.remask(db, REMASK_SECONDS, log)
        log.info("remask: %d rows masked again, %d older rows left for the next ingest", changed, left)
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
    if mask_module.capped > capped:
        total = int(store.get_watermark(db, store.STATE, "mask_capped") or 0) + mask_module.capped - capped
        store.set_watermark(db, store.STATE, "mask_capped", total)
        db.commit()
        log.warning("masking stopped at its pass limit %d times and masked those regions whole",
                    mask_module.capped - capped)
    log.info("ingest done: %d failures", failures)
    return failures


def after(paths, config, db, log):
    """Calls kb.sync.after_ingest(paths, config, db, log) when [sync] or [cloud] is in the config. A failure is
    recorded as the "sync" unit. Returns the number of failures."""
    if "sync" not in config.units():
        return 0
    try:
        hook = importlib.import_module("kb.sync").after_ingest
        hook(paths, config, db, log)
    except Exception as exc:
        log.exception("sync after ingest failed")
        store.record_failure(db, "sync", exc)
        return 1
    store.record_ok(db, "sync")
    db.commit()
    return 0
