"""iMessage: one item per chat per local calendar day, read from the Messages database (chat.db) on a Mac.

Contact names come from the macOS AddressBook files under the `contacts` folder. Tapbacks, empty rows, group-rename
events and auth-code texts are dropped. Reading chat.db needs Full Disk Access for the Python that runs kb.

`run.excludes` is called once per chat with each of these keys, and the chat is skipped when any matches: every
participant's handle as stored (a phone number like "+15550100123" or an email), the chat's own name when a group has
one, and every participant's contact name. Nothing is excluded by default.
"""
import glob
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, NamedTuple
from urllib.parse import quote

from kb import mask
from kb.sources import Item, Progress, SourceType

APPLE_EPOCH = 978307200
OBJECT_REPLACEMENT = "￼"
MAX_TITLE_NAMES = 5
GROUP_STYLE = 43


@dataclass(frozen=True)
class Options:
    db: str = "~/Library/Messages/chat.db"
    # Searched for AddressBook-v22.abcddb files, at any depth.
    contacts: str = "~/Library/Application Support/AddressBook"
    window_days: int = 365
    # The label on the user's own lines.
    me: str = "Me"


class Chat(NamedTuple):
    guid: str
    group: bool
    display: str
    service: str
    handles: List[str]      # as stored: a phone number or an email
    participants: List[str]


def decode_attributed(blob):
    """Text of an NSAttributedString typedstream: the length-prefixed UTF-8 after the NSString class marker."""
    if not blob:
        return None
    start = blob.find(b"NSString")
    if start < 0:
        return None
    plus = blob.find(b"+", start + 8, start + 24)
    if plus < 0:
        return None
    pos = plus + 1
    if pos >= len(blob):
        return None
    first = blob[pos]
    if first == 0x81:
        size, pos = int.from_bytes(blob[pos + 1:pos + 3], "little"), pos + 3
    elif first == 0x82:
        size, pos = int.from_bytes(blob[pos + 1:pos + 5], "little"), pos + 5
    else:
        size, pos = first, pos + 1
    raw = blob[pos:pos + size]
    return raw.decode("utf-8", errors="replace") if len(raw) == size else None


def _phone_key(number):
    digits = re.sub(r"\D", "", number or "")
    return digits[-10:] if digits else None


def _readonly(path):
    return sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True)


def load_contacts(folder):
    """Handle key (last 10 digits of a phone, or a lowercase email) -> display name."""
    names = {}
    for path in glob.glob(os.path.join(glob.escape(os.path.expanduser(folder)), "**", "AddressBook-v22.abcddb"),
                          recursive=True):
        try:
            db = _readonly(path)
            try:
                people = {}
                for pk, first, last, nick, org in db.execute(
                        "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZNICKNAME, ZORGANIZATION FROM ZABCDRECORD"):
                    name = " ".join(p for p in (first, last) if p) or nick or org
                    if name:
                        people[pk] = name
                for owner, number in db.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                    if owner in people and _phone_key(number):
                        names.setdefault(_phone_key(number), people[owner])
                for owner, address in db.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
                    if owner in people and address:
                        names.setdefault(address.strip().lower(), people[owner])
            finally:
                db.close()
        except sqlite3.Error:
            continue
    return names


class Names:
    def __init__(self, book):
        self.book = book

    def __call__(self, handle):
        key = handle.strip().lower()
        if "@" not in key:
            key = _phone_key(key) or key
        return self.book.get(key, handle)


def _unix(apple_ns):
    return apple_ns / 1e9 + APPLE_EPOCH


def _apple_ns(unix):
    return int((unix - APPLE_EPOCH) * 1e9)


def _day_bounds(day):
    start = datetime(day.year, day.month, day.day)
    end = start + timedelta(days=1)
    return _apple_ns(start.timestamp()), _apple_ns(end.timestamp())


def _local(apple_ns):
    return datetime.fromtimestamp(_unix(apple_ns))


def _chats(db, names):
    raw = {}
    for rowid, guid, style, display, service, ident in db.execute(
            "SELECT ROWID, guid, style, display_name, service_name, chat_identifier FROM chat"):
        raw[rowid] = (guid, style == GROUP_STYLE, display or "", service, ident, [])
    for chat_id, handle in db.execute(
            "SELECT j.chat_id, h.id FROM chat_handle_join j JOIN handle h ON h.ROWID = j.handle_id"):
        if chat_id in raw:
            raw[chat_id][5].append(handle)
    chats = {}
    for rowid, (guid, group, display, service, ident, handles) in raw.items():
        handles = handles or [ident]
        chats[rowid] = Chat(guid, group, display, service, handles, [names(h) for h in handles])
    return chats


def _excluded(chat, excludes):
    keys = chat.handles + chat.participants + ([chat.display] if chat.display else [])
    return any(excludes(key) for key in keys)


def _attachments(db, message_ids):
    found = {}
    for mid in message_ids:
        for name, path in db.execute(
                "SELECT a.transfer_name, a.filename FROM message_attachment_join j "
                "JOIN attachment a ON a.ROWID = j.attachment_id WHERE j.message_id = ? ORDER BY a.ROWID", (mid,)):
            name = name or os.path.basename(path or "")
            if name and not name.endswith(".pluginPayloadAttachment"):
                found.setdefault(mid, []).append(name)
    return found


def _day_lines(db, chat_id, day, names, me):
    lo, hi = _day_bounds(day)
    rows = db.execute(
        "SELECT m.ROWID, m.date, m.is_from_me, m.text, m.attributedBody, m.cache_has_attachments, h.id "
        "FROM chat_message_join j JOIN message m ON m.ROWID = j.message_id "
        "LEFT JOIN handle h ON h.ROWID = m.handle_id "
        "WHERE j.chat_id = ? AND j.message_date >= ? AND j.message_date < ? "
        "AND m.associated_message_type = 0 AND m.item_type = 0 ORDER BY j.message_date, m.ROWID",
        (chat_id, lo, hi)).fetchall()
    files = _attachments(db, [r[0] for r in rows if r[5]])
    lines, last = [], None
    for rowid, date, from_me, text, body, _has_files, handle in rows:
        text = text if text else decode_attributed(body)
        text = re.sub(r"\s+", " ", (text or "").replace(OBJECT_REPLACEMENT, " ")).strip()
        if text and mask.is_auth_noise(text):
            continue
        parts = [text] if text else []
        parts += [f"[attachment: {n}]" for n in files.get(rowid, [])]
        if not parts:
            continue
        who = me if from_me else (names(handle) if handle else "Unknown")
        lines.append(f"{_local(date):%H:%M} {who}: {' '.join(parts)}")
        last = date
    return lines, last


def _touched(db, since, cutoff):
    if since is None:
        rows = db.execute(
            "SELECT j.chat_id, j.message_date, j.message_id FROM chat_message_join j WHERE j.message_date >= ?",
            (_apple_ns(cutoff),))
    else:
        rows = db.execute(
            "SELECT j.chat_id, j.message_date, j.message_id FROM chat_message_join j WHERE j.message_id > ?",
            (int(since),))
    pairs, top = set(), None
    for chat_id, date, rowid in rows:
        pairs.add((chat_id, _local(date).date()))
        top = rowid if top is None else max(top, rowid)
    return pairs, top


def _open(path):
    try:
        return _readonly(path)
    except sqlite3.OperationalError as exc:
        # Without Full Disk Access, macOS makes chat.db look unopenable or unreadable rather than refusing by name.
        try:
            os.stat(path)
        except FileNotFoundError:
            raise exc from None
        except PermissionError:
            pass
        raise PermissionError("macOS blocked Messages; give Full Disk Access to "
                              f"{os.path.realpath(sys.executable)}") from exc


def _label(chat):
    people = chat.participants
    return chat.display or ", ".join(people[:MAX_TITLE_NAMES]) + (
        f" and {len(people) - MAX_TITLE_NAMES} more" if len(people) > MAX_TITLE_NAMES else "")


def _rowids_restarted(db, since):
    """Whether chat.db was rebuilt, so its message ids start again below the watermark."""
    return (db.execute("SELECT MAX(ROWID) FROM message").fetchone()[0] or 0) < since


def extract(run):
    options = run.options
    path = os.path.expanduser(options.db)
    since = run.since
    cutoff = time.time() - options.window_days * 86400
    db = _open(path)
    try:
        db.execute("PRAGMA busy_timeout=30000")
        if since is not None and _rowids_restarted(db, since):
            since = None
        names = Names(load_contacts(options.contacts))
        chats = _chats(db, names)
        pairs, top = _touched(db, since, cutoff)
        for chat_id, day in sorted(pairs, key=lambda p: (p[1], p[0])):
            chat = chats.get(chat_id)
            if not chat or _excluded(chat, run.excludes):
                continue
            lines, last = _day_lines(db, chat_id, day, names, options.me)
            if not lines:
                continue
            meta = {"path": path, "chat": chat.guid, "participants": chat.participants}
            if not chat.group:
                scheme = "imessage://" if chat.service == "iMessage" else "sms:"
                meta["url"] = scheme + chat.handles[0]
            yield Item(
                f"{chat.guid}:{day.isoformat()}",
                datetime.fromtimestamp(_unix(last), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                f"iMessage with {_label(chat)} on {day.isoformat()}",
                "\n".join(lines), meta, top)
        if top is not None:
            yield Progress(top)
    finally:
        db.close()


TYPE = SourceType(Options, extract, summarize=True, others_wrote=True, platforms=frozenset({"darwin"}))
