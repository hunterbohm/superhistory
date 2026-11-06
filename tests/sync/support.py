import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid

from kb import config as config_mod, store
from kb.mask import VERSION, clean
from kb.paths import Paths
from kb.sync import keys, login, pack, records
from kb.sync.client import SyncClient

from .fake_relay import FakeRelay

EMAIL = "person@example.com"


def connect(path):
    """kb's store, in autocommit as the Sync client opens it."""
    db = store.connect(path)
    db.isolation_level = None
    return db


def store_path(home):
    return Paths.under(home).db


def put_rows(db, source, source_id, ts, title, texts, meta):
    """An item's rows exactly as given, their text unmasked, with today's mask version stamped on them, as an older kb
    or a bug in one would leave them. Tests of what leaves a computer need such rows: kb's own writers mask at rest,
    and then the egress chokepoint would have nothing to do. One text is a `doc` row, several are `raw` chunks."""
    if len(texts) == 1:
        rows = [("doc", title, texts[0], meta)]
        kinds = store.ITEM_KINDS + store.SUMMARY_KINDS
    else:
        rows = [("raw", title, part, {**meta, "chunk": i, "chunks": len(texts)}) for i, part in enumerate(texts)]
        kinds = store.ITEM_KINDS
    db.execute(f"DELETE FROM docs WHERE source=? AND source_id=? AND kind IN ({','.join('?' * len(kinds))})",
               (source, source_id, *kinds))
    db.executemany("INSERT INTO docs(kind, source, source_id, ts, title, content, metadata, masked) VALUES (?,?,?,?,?,?,?,?)",
                   [(kind, source, source_id, ts, t, content, json.dumps(m), VERSION) for kind, t, content, m in rows])


def put_item(db, source, source_id, ts, title, content, meta):
    """put_rows for one text: a `doc` row, or `raw` chunks when long. Returns False when nothing changed."""
    title, content = clean(title or "")[:300], clean(content or "")
    digest = hashlib.sha1(f"{title}\0{content}".encode()).hexdigest()[:16]
    old = db.execute("SELECT json_extract(metadata, '$.hash') FROM docs WHERE source=? AND source_id=? "
                     "AND kind IN ('doc','raw') LIMIT 1", (source, source_id)).fetchone()
    if old and old[0] == digest:
        return False
    parts = store.chunk(content)
    put_rows(db, source, source_id, ts, title, parts, {**meta, "hash": digest})
    return True


def put_record(db, rec):
    """What `records.apply` writes for `rec`, by SQL, as put_rows does: unmasked, and with no refusal of an id kb would
    not store."""
    b = rec.body
    if rec.kind == "item":
        put_rows(db, rec.source, rec.source_id, b["ts"], b["title"], b["rows"], b["meta"])
    elif rec.kind == "summary":
        db.execute("DELETE FROM docs WHERE source=? AND source_id=? AND kind IN ('segment','atom')",
                   (rec.source, rec.source_id))
        db.executemany("INSERT INTO docs(kind, source, source_id, ts, title, content, metadata, masked) "
                       "VALUES (?,?,?,?,?,?,?,?)",
                       [(r["kind"], rec.source, rec.source_id, r["ts"], r["title"], r["content"], json.dumps(r["metadata"]),
                         VERSION) for r in b["rows"]])
    elif rec.kind == "supersede":
        new = b["new"] or [None, None]
        db.execute("INSERT OR REPLACE INTO supersedes(source, source_id, marked_by, new_source, new_source_id, at, v) "
                   "VALUES (?,?,?,?,?,?,?)", (rec.source, rec.source_id, b["by"], new[0], new[1], b["at"], rec.v))
    else:
        db.execute("DELETE FROM docs WHERE source=? AND source_id=?", (rec.source, rec.source_id))


def put_summary(db, source, source_id, of_hash, rows):
    """The summarizer's write, unmasked: `rows` are (kind, title, content) for one item, tagged with the hash they
    summarize."""
    ts = db.execute("SELECT ts FROM docs WHERE source=? AND source_id=? AND kind IN ('doc','raw') LIMIT 1",
                    (source, source_id)).fetchone()[0]
    db.execute("DELETE FROM docs WHERE source=? AND source_id=? AND kind IN ('segment','atom')", (source, source_id))
    db.executemany("INSERT INTO docs(kind, source, source_id, ts, title, content, metadata, masked) VALUES (?,?,?,?,?,?,?,?)",
                   [(kind, source, source_id, ts, title, content, json.dumps({"of_hash": of_hash}), VERSION)
                    for kind, title, content in rows])


def rows(db):
    return sorted(db.execute("SELECT kind, source, source_id, ts, title, content, metadata FROM docs").fetchall())


def marks(db):
    return sorted(db.execute("SELECT source, source_id, marked_by, new_source, new_source_id, at, v FROM supersedes"))


class Computer:
    def __init__(self, world, name):
        self.world, self.name = world, name
        self.offset = 0.0
        self.home = os.path.join(world.tmp, name)
        self.paths = Paths.under(self.home)
        self.config = self.home  # kb's config folder: config.toml, install-id and sync/ are in it
        self.store = os.fspath(self.paths.db)
        self.client = None
        self.accounts = {}

    def login(self, label=None):
        login.login(self.paths, self.world.fake, "https://app.test", "https://relay.test", label=label or self.name,
                    show=lambda uri, code: None, sleep=lambda s: None)
        return self.open()

    def open(self):
        if self.client:
            self.client.close()
        self.client = SyncClient.load(self.paths, transport=self.world.fake,
                                      clock=lambda: self.world.now + self.offset)
        self.client.accounts = self.accounts
        return self.client

    def reads(self, *sources):
        """The online accounts this computer's config names, from `[sources.X]` tables, as `Config.account_keys` says."""
        text = "\n".join(sources) + "\n[sync]\n"
        self.accounts = config_mod.parse(text).account_keys()
        if self.client:
            self.client.accounts = self.accounts
        return self.accounts

    @property
    def db(self):
        return self.client.db

    @property
    def prefix(self):
        return self.client.prefix

    def key(self, name):
        return f"{self.prefix}:{name}"

    def item(self, source, name, text, title=None, meta=None, ts="2026-10-01"):
        put_item(self.db, source, self.key(name), ts, title or name, text, meta or {})
        return self.key(name)

    def has(self, source, source_id):
        return records.item_exists(self.db, source, source_id)

    def text(self, source, source_id):
        return records.read_item(self.db, source, source_id)

    def sync(self):
        self.world.tick()
        return self.client.run_once()

    def lose_everything(self):
        self.client.close()
        self.client = None
        shutil.rmtree(self.home)


class World:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="superhistory-sync-test-")
        self.now = 1_790_000_000.0
        self.fake = FakeRelay(clock=self.clock)
        self.fake.sign_up(EMAIL)
        self.fake.browser_user = EMAIL
        self.computers = []

    def clock(self):
        return self.now

    def browser(self):
        """Where a computer shows a code for the person to approve. The fake's `browser_user` decides each one."""
        return lambda uri, code: None

    def tick(self, seconds=1.0):
        self.now += seconds

    def computer(self, name):
        c = Computer(self, name)
        self.computers.append(c)
        c.login()
        return c

    def pair(self, member, joiner):
        pairing = member.client.add_device()
        words = joiner.client.join(pairing.code)
        seen = []
        member.client.approve(pairing, lambda label, w: seen.append((label, w)) or True, sleep=lambda s: None)
        assert seen == [(joiner.name, words)], seen
        return joiner.client.finish_join(sleep=lambda s: None)

    def group(self, *names):
        """The first computer sets up the group and the rest join. Returns (computers, recovery code)."""
        first = self.computer(names[0])
        code, _ = first.client.init(self.browser())
        rest = [self.computer(n) for n in names[1:]]
        for c in rest:
            self.pair(first, c)
        self.setup_asks = len(self.fake.asks)  # the codes asked while setting up, which a test of a later change skips
        return [first] + rest, code

    def put_raw(self, blob, epoch, snapshot_id=None):
        """Store a blob as the next pack, as a malicious relay can."""
        group = self.fake.group(EMAIL)
        seq = group.next_seq
        group.next_seq += 1
        group.packs.append({"seq": seq, "pack_id": str(uuid.uuid4()), "epoch": epoch, "blob": blob,
                            "snapshot_id": snapshot_id, "day": time.strftime("%Y-%m-%d")})
        return seq

    def forge(self, author, recs, signer=None, header=None, seal_epoch=None, listed_epoch=None, confirm=True):
        """A change pack claiming `author` wrote it, continuing its chain, put on the relay. Returns (seq, plaintext).
        With `confirm`, the app records it as one of the author's packs, as the author's own upload would."""
        c = author.client
        d, h = c._signing_head()
        head = {"epoch": c.keyring.newest, "author": c.device_id, "dseq": d + 1, "prev": h,
                "roster_version": c.roster.version, "roster_hash": c.roster.hash, "snapshot": None}
        head.update(header or {})
        plain = pack.build(head, [records.to_json(r) for r in recs], (signer or author).client.paths.device_ssh)
        epoch = seal_epoch or c.keyring.newest
        seq = self.put_raw(pack.seal(plain, c.keyring.recipient(epoch)), listed_epoch or epoch)
        if confirm and head["author"] == c.device_id and self.fake.is_member(c.device_id) and isinstance(head["dseq"], int):
            self.fake.devices[c.device_id]["points"].setdefault(head["dseq"], hashlib.sha256(plain).hexdigest())
        return seq, plain

    def opened(self, reader):
        """Every pack on the relay that `reader`'s key ring opens, as (seq, header)."""
        out = []
        for p in self.fake.group(EMAIL).packs:
            try:
                padded = keys.open_with([reader.client.keyring.identity(p["epoch"])], p["blob"])
            except (keys.Refused, KeyError):
                continue
            out.append((p["seq"], pack.parse(padded).header))
        return out

    def close(self):
        for c in self.computers:
            if c.client:
                c.client.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
