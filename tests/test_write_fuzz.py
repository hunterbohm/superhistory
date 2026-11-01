"""A seeded fuzz of write sequences. Rounds 8 and 9 each found writes that, in some order, left a row marked current
though its item was not masked whole. Each sequence here mixes the writes that ingest, Sync, Cloud, remask and an
older kb make, on a few items whose text holds canaries split across chunk and call boundaries. After every sequence:
- no reader shows a piece of a canary: search's hits, their render, the lookup's prompt, `kb get`, and the
  summarizer's input, each as kb sends or prints it;
- no row reads as current unless its whole item masks clean, and no current item's row records a hash of other text
  than its stored title and text.

KB_FUZZ_SEQUENCES and KB_FUZZ_SEED change the run. A failure names its sequence, which reruns on its own with
KB_FUZZ_ONLY.
"""
import contextlib
import functools
import io
import json
import os
import pathlib
import random
import re
import shutil
import string
import tempfile
import types
import unittest
from unittest import mock

from kb import ai, check, cli, ingest, search, store, summarize
from kb.mask import INVISIBLE, mask, mask_all
from kb.sources import Item
from tests import legacy_store

SEQUENCES = int(os.environ.get("KB_FUZZ_SEQUENCES", "2500"))
SEED = int(os.environ.get("KB_FUZZ_SEED", "20261027"))
ONLY = os.environ.get("KB_FUZZ_ONLY")
CHUNK = 120  # small chunks, so a few hundred characters make a long item and canaries cross chunk boundaries
IDS = ("a.md", "b.md", "c.md")
TS = "2026-10-01T10:00:00Z"
KEY_KINDS = ("PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "DSA PRIVATE KEY", "OPENSSH PRIVATE KEY",
             "ENCRYPTED PRIVATE KEY")
BASE64 = string.ascii_letters + string.digits + "+/"
# How each kind's body starts, as real keys' do: DER for all but OpenSSH's (openssl genpkey, genrsa, ecparam and
# ssh-keygen, every size and curve). An encrypted key's body after its DEK-Info field is ciphertext, so random.
KEY_STARTS = {"PRIVATE KEY": ("MIIEvQIBADANBgkqhkiG9w0BAQEFAASC", "MC4CAQAwBQYDK2VwBCIEI", "MIGHAgEAMBMGByqGSM49"),
              "RSA PRIVATE KEY": ("MIIEpAIBAAKCAQEA", "MIICXAIBAAKBgQ"), "EC PRIVATE KEY": ("MHcCAQEEI", "MIGkAgEBBDA"),
              "DSA PRIVATE KEY": ("MIIBuwIBAAKBgQ",), "OPENSSH PRIVATE KEY": ("b3BlbnNzaC1rZXktdjEAAAAA",),
              "ENCRYPTED PRIVATE KEY": ("MIIFLTBXBgkqhkiG9w0BBQ0w",)}
FILLER = ("walrus", "ledger", "deploy", "notes", "harbor", "the", "plan", "moved", "today", "approved")


class Interrupted(Exception):
    """A crash or kill partway through a write."""


class Planted:
    """One sequence's canaries, and the pieces of them no reader may show: each canary and its first and last 12
    characters, since a cut before masking keeps a start or an end too short to mask."""

    def __init__(self, r, seq):
        self.r, self.seq, self.n, self.pieces, self.items, self.bodies, self.narrowest = r, seq, 0, set(), {}, [], 1
        self.codes = []

    def _value(self, shape):
        self.n += 1
        value = shape.format(f"{self.seq:05d}{self.n:03d}")
        self.pieces.update((value, value[:12], value[-12:]))
        return value

    def token(self):
        token = self._value("sk-ant-api03-fz{}" + "Q" * 20)
        if self.r.random() < 0.2:  # split by a character a reader does not see
            cut = self.r.randint(1, len(token) - 1)
            token = token[:cut] + self.r.choice(("\u200b", "\u200d", "\u2060", "\u00ad", "\ufeff")) + token[cut:]
        return token

    def keyed(self):
        return self.r.choice(("password=", "api_key: ", "secret_token=", "PASSWORD = ")) + self._value("fz{}Kq12345678")

    def pem(self, whole=False):
        """A private key: whole, long enough that a middle chunk holds only body lines, cut off before its footer, or
        as an older kb's row-by-row remask leaves it, its placeholder and then the rest of the body."""
        line = ("MIIEvQIBAD" + self._value("Fz{}xPemBodyLine" + "PemBodyLine" * 5))[:64]
        self.pieces.update((line, line[:12]))
        body = "\n".join([line] * self.r.randint(2, 10))
        shape = 1 if whole else self.r.random()
        if shape < 0.2:
            return "-----BEGIN PRIVATE KEY-----\n" + body
        if shape < 0.4:
            # main's key pattern takes the line break too, so what follows its placeholder starts glued to it
            rest = body[self.r.randint(0, 70):].lstrip("\n")
            return "[masked]" + rest + self.r.choice(("\n-----END PRIVATE KEY-----", ""))
        return "-----BEGIN PRIVATE KEY-----\n" + body + "\n-----END PRIVATE KEY-----"

    def key(self):
        """A key block as keys come: any header kind, an encrypted key's header fields, lines of 1 to 100 characters,
        LF, CRLF or escaped line breaks, whole or cut at a random point, and sometimes a secret's name and value right
        after it. Its body is checked through any line breaks."""
        kind, brk, width = self.r.choice(KEY_KINDS), self.r.choice(("\n", "\r\n", "\\n")), self.r.randint(self.narrowest, 100)
        body = "".join(self.r.choice(BASE64) for _ in range(self.r.randint(24, 400))) + "=" * self.r.randint(0, 2)
        head = [f"-----BEGIN {kind}-----"]
        if not (kind in KEY_KINDS[1:4] and self.r.random() < 0.4):
            body = self.r.choice(KEY_STARTS[kind]) + body
        else:
            iv = "".join(self.r.choice("0123456789ABCDEF") for _ in range(32))
            head += ["Proc-Type: 4,ENCRYPTED", f"DEK-Info: AES-256-CBC,{iv}", ""]
        block = brk.join(head + [body[i:i + width] for i in range(0, len(body), width)] + [f"-----END {kind}-----"])
        if self.r.random() < 0.4:
            block = block[:self.r.randint(len(head[0]) + 1, len(block))]
        self.bodies.append(body)
        if self.r.random() < 0.4:
            name = self.r.choice(("clientSecretToken: ", "password=", "api_key = ", "secret_token: "))
            block += self.r.choice((brk, " ")) + name + self._value("fz{}Kq12345678")
        return block

    def code(self):
        """A sync recovery or pairing code as its client writes it or a person types it: in either case, with I, L or O
        for 1 and 0, with dashes or spaces, unbroken, a character at a time, wrapped, or cut short, and at times of
        letters only. Its body is checked as the client's parser reads it."""
        recovery = self.r.random() < 0.5
        alphabet = "ABCDEFGHJKMNPQRSTVWXYZ" if self.r.random() < 0.3 else "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
        body = "".join(self.r.choice(alphabet) for _ in range(32 if recovery else 26))
        if self.r.random() < 0.3:
            body = body[:self.r.randint(20, len(body))]
        self.codes.append(body)
        typed = "".join(self.r.choice(("I", "L", "l", "\u0131")) if c == "1" and self.r.random() < 0.3
                        else "O" if c == "0" and self.r.random() < 0.3 else c for c in body)
        head, groups = ("VRC" if recovery else "VP") + str(self.r.randint(1, 9)), [typed[i:i + 4] for i in range(0, len(typed), 4)]
        form = self.r.random()
        if form < 0.3:
            return head + "-" + "-".join(groups)
        if form < 0.5:
            return f"{head} {' '.join(groups)}".lower()
        if form < 0.6:
            return head + typed
        if form < 0.7:
            return " ".join(head + typed)
        if form < 0.8:
            return " ".join(head) + "--" + "--".join(groups)
        wrap = self.r.randint(1, len(groups) - 1)
        return f"{head}-{'-'.join(groups[:wrap])}-{self.r.choice(('\n', '\\n', '\\r\\n'))}{'-'.join(groups[wrap:])}"

    def text(self):
        if self.r.random() < 0.25:
            return "walrus ledger " + " ".join(self.r.choice(FILLER) for _ in range(self.r.randint(5, 80)))
        parts = []
        for _ in range(self.r.randint(2, 6)):
            pick = self.r.random()
            if pick < 0.4:
                parts.append(" ".join(self.r.choice(FILLER) for _ in range(self.r.randint(3, 30))))
            else:
                parts.append((self.token, self.keyed, self.pem, self.key, self.code)[int(pick * 10) % 5]())
        if self.r.random() < 0.15:  # a key cut off by a size limit, anywhere in a line, ends the text
            parts.append(self.pem(whole=True)[:self.r.randint(40, 600)])
        # Parts meet at a space or a line break, or at an escape as JSON text writes one, which a secret after it
        # reads as glued to.
        return "walrus ledger " + self.r.choice((" ", "\n", " \n", "\\n", "\\r\\n", "\\t", "\\u00a0")).join(parts)

    def title(self):
        return self.r.choice(("Walrus notes", f"Notes {self.token()}", f"Plan {self.keyed()}"))

    def meta(self, sid):
        extra = self.r.choice(({}, {"password": self._value("fz{}Kq12345678")},
                               {"url": f"https://x.example/?token={self._value('fz{}Kq12345678')}"}))
        return {"path": f"/notes/{sid}", **extra}


def op_ingest(db, r, p):
    """kb's own write, as ingest makes it."""
    sid, title, text = r.choice(IDS), p.title(), p.text()
    p.items[sid] = (title, text)
    ingest.apply(db, "notes", "desk", Item(sid, TS, title, text, p.meta(sid), 0))


def op_moved(db, r, p):
    """The same text under new metadata, which store_item gives the rows without rewriting them."""
    sid = r.choice(IDS)
    if sid in p.items:
        ingest.apply(db, "notes", "desk", Item(sid, TS, *p.items[sid], p.meta(sid), 0))


def _rows(r, p, sid, text):
    parts = legacy_store.chunk(text, CHUNK)
    kind = "raw" if len(parts) > 1 else "doc"
    return [{"kind": kind, "ts": TS, "title": p.title(), "content": part,
             "metadata": {**p.meta(sid), "chunk": i, "chunks": len(parts)}} for i, part in enumerate(parts)]


def op_sync_item(db, r, p):
    """An item another store sent, cut into chunks before it was masked."""
    sid = r.choice(IDS)
    store.replace_rows(db, "notes", f"desk:{sid}", _rows(r, p, sid, p.text()))


def op_sync_chunks(db, r, p):
    """An item's chunks sent one write at a time, as a client sending pieces might."""
    sid = r.choice(IDS)
    for row in _rows(r, p, sid, p.text()):
        store.replace_rows(db, "notes", f"desk:{sid}", [{**row, "kind": "raw"}], r.choice(((), ("doc",))))


def op_sync_summary(db, r, p):
    """Summary rows, whole or appended, whose question ends where a secret's name is and whose summary starts with
    it."""
    keyed = p.keyed()
    cut = keyed.index("=") + 1 if "=" in keyed else keyed.index(":") + 1
    row = {"kind": "segment", "ts": TS, "title": "t", "content": f"Which? {keyed[:cut]}\n{keyed[cut:]} walrus ledger",
           "metadata": {}}
    store.replace_rows(db, "notes", f"desk:{r.choice(IDS)}", [row], r.choice((store.SUMMARY_KINDS, ())))


def op_sync_secret_id(db, r, p):
    """An item whose id holds a token, which no write may store."""
    store.replace_rows(db, "notes", f"desk:key-{p.token()}.md", _rows(r, p, "x", p.text()))


def op_old_kb(db, r, p):
    """An older kb, whose masking misses these canaries: its store_item, and its _repoint for text it already has."""
    sid = r.choice(IDS) if r.random() < 0.9 else f"key-{p.token()}.md"
    title, text = p.items[sid] if sid in p.items and r.random() < 0.6 else (p.title(), p.text())
    legacy_store.store_item(db, "notes", f"desk:{sid}", TS, title, text, p.meta(sid), CHUNK)


def op_old_remask(db, r, p):
    """An older kb's remask, whose mask knows only token shapes and masks each row by itself."""
    legacy_store.remask(db, legacy_store.token_mask)


def op_remask(db, r, p):
    store.remask(db)


def op_remask_timed(db, r, p):
    store.remask(db, seconds=0)


def op_remask_cut(db, r, p):
    """remask killed after a random number of items."""
    left, real = r.randint(0, 3), store._remask_item

    def item(*args):
        nonlocal left
        if left == 0:
            raise Interrupted
        left -= 1
        return real(*args)

    with mock.patch.object(store, "_remask_item", item):
        store.remask(db)


def op_rebuild_cut(db, r, p):
    """remask killed in its final rebuild, after its last batch committed."""
    if hasattr(store, "_rebuild"):
        with mock.patch.object(store, "_rebuild", side_effect=Interrupted):
            store.remask(db)


def op_delete(db, r, p):
    store.delete_item(db, "notes", f"desk:{r.choice(IDS)}")


def op_supersede(db, r, p):
    ids = [i for (i,) in db.execute("SELECT id FROM docs")]
    if len(ids) > 1:
        db.execute("UPDATE docs SET superseded_by=? WHERE id=?", tuple(r.sample(ids, 2)))


OPERATIONS = {name[3:]: fn for name, fn in globals().items() if name.startswith("op_")}


class Recorder:
    """A model that scores the first record useful and keeps what it was sent."""

    def __init__(self):
        self.sent = []

    def ask(self, prompt, timeout):
        self.sent += [prompt.system, prompt.user]
        return '{"scores": {"1": 2}}'


def not_masked_whole(db):
    """Rows that read as current though their whole item does not mask clean."""
    rows = db.execute("SELECT id, kind, source, source_id, ts, title, content, metadata FROM docs").fetchall()
    fresh = store.current(db, [row[0] for row in rows])
    problems = []
    for rid, kind, source, sid, ts, title, content, meta in rows:
        if rid not in fresh:
            continue
        item_title = title
        if kind == "raw":
            chunks = db.execute("SELECT title, content FROM docs WHERE source=? AND source_id=? AND kind='raw' "
                                "ORDER BY json_extract(metadata, '$.chunk')", (source, sid)).fetchall()
            item_title, content = chunks[0][0], "".join(c for _, c in chunks)
        if (mask(content), mask(title), mask(ts), mask_all(json.loads(meta))) != (content, title, ts, json.loads(meta)):
            problems.append(f"row {rid} ({kind} {sid}) reads as current, but its item does not mask clean")
        if kind in store.ITEM_KINDS and json.loads(meta).get("hash") != store.item_hash(item_title, content):
            problems.append(f"row {rid} ({kind} {sid}) reads as current, but records a hash of other text")
    return problems


def shown(db, path):
    """Everything a reader shows or sends, as kb shows or sends it."""
    out = []
    for query in ("walrus ledger", "deploy notes", "harbor plan"):
        hits = search.search(db, query, limit=10)
        for hit in hits:
            out += [hit["title"], hit["snippet"], hit["excerpt"], hit["content"], hit["source_id"],
                    json.dumps(hit["meta"])]
        out.append(check.render(hits, "desk"))
        if hits:
            model = Recorder()
            with contextlib.suppress(Exception):
                check.pick("What does the walrus ledger say?", [], hits, model, 5)
            out += model.sent
    paths, config = types.SimpleNamespace(db=pathlib.Path(path)), types.SimpleNamespace(machine="desk")
    for (rid,) in db.execute("SELECT min(id) FROM docs GROUP BY source, source_id, kind"):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.suppress(cli.Fail):
            cli.cmd_get(paths, config, types.SimpleNamespace(id=rid, offset=None))
        out.append(printed.getvalue())
    for source, sid, title in db.execute("SELECT source, source_id, max(title) FROM docs WHERE kind='raw' "
                                         "GROUP BY source, source_id").fetchall():
        item = types.SimpleNamespace(source=source, source_id=sid, title=title)
        out.append(ai.masked(summarize.request(item, summarize.cap(summarize.item_text(db, item)), ["fact"])).user)
    return out


def run_sequence(seq, root, ops=None):
    """One seeded sequence of writes and the problems it leaves. Returns (the operations, problems)."""
    r = random.Random(f"{SEED}:{seq}")
    path = os.path.join(root, f"{seq}.sqlite")
    db = store.connect(path)
    db.execute("PRAGMA synchronous=OFF")  # a scratch file: no write here needs to survive a power cut
    planted, done = Planted(r, seq), []
    names = [r.choice(ops or sorted(OPERATIONS)) for _ in range(r.randint(3, 9))]
    # An older kb only ever stored real keys, wrapped at 64 to 76 characters; narrower ones come from kb's own writes.
    planted.narrowest = 16 if {"old_kb", "old_remask"} & set(names) else 1
    try:
        for name in names:
            done.append(name)
            try:
                OPERATIONS[name](db, r, planted)
                db.commit()
            except (ValueError, Interrupted):  # a refused write, or a killed one
                done[-1] += " (stopped)"
                db.rollback()
        problems = not_masked_whole(db)
        text = INVISIBLE.sub("", "\n".join(shown(db, path)))  # as a reader sees it
        problems += [f"a reader shows {piece!r}" for piece in sorted(planted.pieces) if piece in text]
        squeezed = re.sub(r"\s+|\\[nr]", "", text)  # a key's body read through its line breaks
        parsed = re.sub(r"[- \r\n]|\\[nr]", "", text).upper().translate(str.maketrans("ILO", "110"))  # as the parser
        problems += [f"a reader shows {code[i:i + 12]!r} of a code" for code in planted.codes
                     for i in range(0, len(code) - 11, 4) if code[i:i + 12] in parsed][:3]
        problems += [f"a reader shows {body[i:i + 12]!r} of a key" for body in planted.bodies
                     for i in range(0, len(body) - 11, 4) if body[i:i + 12] in squeezed][:3]
    finally:
        db.close()
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(path + suffix)
    return done, problems


def run(sequences=SEQUENCES, ops=None):
    """Every sequence's failure, as (sequence, operations, problems)."""
    # Memory-backed where there is such a folder, since a few thousand small databases would otherwise wait on disk.
    root = tempfile.mkdtemp(dir="/dev/shm" if os.path.isdir("/dev/shm") else None)
    try:
        with mock.patch.object(store, "chunk", functools.partial(store.chunk, size=CHUNK)):
            picked = [int(ONLY)] if ONLY else range(sequences)
            return [(seq, *found) for seq in picked for found in [run_sequence(seq, root, ops)] if found[1]]
    finally:
        shutil.rmtree(root)


class WriteSequenceFuzz(unittest.TestCase):
    def test_no_write_sequence_leaves_a_secret_for_a_reader_or_a_false_current_mark(self):
        failures = run()
        if failures:
            seq, done, problems = failures[0]
            self.fail(f"{len(failures)} of {SEQUENCES} sequences fail; the first, KB_FUZZ_ONLY={seq} "
                      f"KB_FUZZ_SEED={SEED}: {' -> '.join(done)}: {problems[:3]}")


if __name__ == "__main__":
    unittest.main()
