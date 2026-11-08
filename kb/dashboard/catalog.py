"""What the store holds, grouped for the dashboard: items by source, by project and by person.

The grouping reads only what sources record, never an item's name or a list of known ones.

- An item's family follows the metadata its source writes, since a source's name is the user's to choose: `cwd` or
  `session_id` is an agent session, `account` a mail thread, `chat` a day of texts, `sha` a commit, `repo` with a
  `kind` a pull request or issue, and anything else a note.
- A project is a folder or a repository. A session belongs to the folder it ran in (its last path part, unless that is
  a home folder), a commit or pull request to its repository (the part after the owner), so a repository's sessions
  and commits meet on one page. Notes and messages name no project.
- A person is someone the user texted or mailed. A text lists its participants. A mail thread lists its addresses; the
  user's own are the accounts the threads were read from. An address is named from the "Name <address>" headers in
  the mail itself, and a name is the person's key, so one person texted and mailed is one page. A person known only by
  an address has the address as key. A correspondent whose threads all carry one of Gmail's category labels (updates,
  promotions, social, forums) is listed apart as a list or notification sender, with a page like anyone's.
"""
import collections
import json
import re
from email.utils import getaddresses
from typing import Dict, List, NamedTuple, Optional, Tuple

from kb import store
from kb.mask import mask, mask_all

HEADER_CHARS = 20000
HOME = re.compile(r"^(?:~|/(?:Users|home)/[^/]+|[A-Za-z]:[\\/]+Users[\\/]+[^\\/]+|/root)[\\/]*$")
CATEGORY = re.compile(r"^CATEGORY_(UPDATES|PROMOTIONS|SOCIAL|FORUMS)$")
FAMILIES = ("agent", "text", "mail", "commit", "pr", "note")
# What the dashboard calls each family, in the order it lists them.
FAMILY_LABEL = {"agent": "Agent sessions", "text": "Texts", "mail": "Email", "commit": "Commits",
                "pr": "Pull requests and issues", "note": "Notes"}
HEADER_LINE = re.compile(r"^(?:From|To|Cc): (.*)$", re.M)


class Entry(NamedTuple):
    id: int
    source: str
    source_id: str
    ts: str
    title: str
    family: str
    project: Optional[str]
    people: Tuple[str, ...]


class Group:
    """One project or person: the name to show, its items newest first, and what else is known."""

    def __init__(self, key, name):
        self.key, self.name = key, name
        self.entries: List[Entry] = []
        self.addresses = set()
        self.automated = True
        self._names = collections.Counter()

    def add(self, entry, name=None):
        self.entries.append(entry)
        if name:
            self._names[name] += 1

    def finish(self):
        self.entries.sort(key=lambda e: e.ts, reverse=True)
        if self._names:
            self.name = self._names.most_common(1)[0][0]

    @property
    def count(self):
        return len(self.entries)

    @property
    def last(self):
        return self.entries[0].ts if self.entries else ""

    def by_family(self):
        counts = collections.Counter(e.family for e in self.entries)
        return {f: counts[f] for f in FAMILIES if counts[f]}


class Catalog:
    def __init__(self):
        self.entries: List[Entry] = []
        self.by_id: Dict[int, Entry] = {}
        self.sources: Dict[str, List[Entry]] = {}
        self.projects: Dict[str, Group] = {}
        self.people: Dict[str, Group] = {}


def family_of(meta):
    if "account" in meta:
        return "mail"
    if "chat" in meta:
        return "text"
    if "cwd" in meta or "session_id" in meta:
        return "agent"
    if "sha" in meta:
        return "commit"
    if "repo" in meta and meta.get("kind") in ("pr", "issue"):
        return "pr"
    return "note"


def last_part(path):
    parts = [p for p in re.split(r"[\\/]+", str(path or "")) if p]
    return parts[-1] if parts else ""


def project_name(family, meta):
    """The folder or repository an item belongs to, or None."""
    if family == "agent":
        cwd = meta.get("cwd")
        if not cwd or cwd == "?" or HOME.match(str(cwd)):
            return None
        return last_part(cwd) or None
    if family in ("commit", "pr"):
        return last_part(meta.get("repo") or meta.get("path")) or None
    return None


def _clean(name):
    name = re.sub(r"\s+", " ", str(name or "")).strip().strip("\"'")
    return name


def address_names(texts):
    """{address: name} from the "Name <address>" headers in mail text; the name seen most often for an address."""
    seen = collections.defaultdict(collections.Counter)
    for text in texts:
        for value in HEADER_LINE.findall(text):
            for name, address in getaddresses([value]):
                address = address.strip().lower()
                if address and _clean(name) and _clean(name).lower() != address:
                    seen[address][_clean(name)] += 1
    return {a: names.most_common(1)[0][0] for a, names in seen.items()}


def _stale_items(db):
    """Items with a row masked by another version of mask(): shown masked again, as `get` shows them."""
    return set(db.execute("SELECT DISTINCT source, source_id FROM docs WHERE masked IS NOT ?", (store.VERSION,)))


def build(db):
    catalog = Catalog()
    stale = _stale_items(db)
    rows = []
    for rid, source, source_id, ts, title, meta in db.execute(
            "SELECT id, source, source_id, ts, title, metadata FROM docs WHERE kind = 'doc' "
            "OR (kind = 'raw' AND json_extract(metadata, '$.chunk') = 0) ORDER BY ts DESC, id"):
        if (source, source_id) in stale:
            if store.secret_id(source, source_id, store.item_names(db, source, source_id)):
                continue
            title = mask(title)
            meta = mask_all(json.loads(meta))
        else:
            meta = json.loads(meta)
        rows.append((rid, source, source_id, ts, title, meta, family_of(meta)))
    mail_text = {}
    for rid, source, source_id, text in db.execute(
            "SELECT id, source, source_id, substr(content, 1, ?) FROM docs WHERE "
            "json_extract(metadata, '$.account') IS NOT NULL AND "
            "(kind = 'doc' OR (kind = 'raw' AND json_extract(metadata, '$.chunk') = 0))", (HEADER_CHARS,)):
        if (source, source_id) in stale:
            if store.secret_id(source, source_id, store.item_names(db, source, source_id)):
                continue
            text = mask(text)
        mail_text[rid] = text
    names = address_names(mail_text.values())
    own = {str(r[5]["account"]).lower() for r in rows if r[6] == "mail" and r[5].get("account")}
    own_names = {names[a].casefold() for a in own if a in names}
    for rid, source, source_id, ts, title, meta, family in rows:
        project = project_name(family, meta)
        found, automated = {}, False  # person's key (case folded) -> (name to show, what each mention gave)
        if family == "text":
            for who in meta.get("participants") or []:
                who = _clean(who)
                if who and who.casefold() not in own_names:
                    found.setdefault(who.casefold(), (who, []))[1].append(who)
        elif family == "mail":
            labels = meta.get("labels") or []
            automated = any(CATEGORY.match(str(label)) for label in labels)
            for address in meta.get("participants") or []:
                address = str(address).strip().lower()
                if not address or address in own:
                    continue
                name = names.get(address)
                if name and name.casefold() in own_names:
                    continue
                who = name or address
                found.setdefault(who.casefold(), (who, []))[1].append((name, address))
        entry = Entry(rid, source, source_id, ts, title, family, project.casefold() if project else None, tuple(found))
        catalog.entries.append(entry)
        catalog.by_id[rid] = entry
        catalog.sources.setdefault(source, []).append(entry)
        if project:
            group = catalog.projects.setdefault(entry.project, Group(entry.project, project))
            group.add(entry, project)
        for key, (who, mentions) in found.items():
            group = catalog.people.setdefault(key, Group(key, who))
            if family == "mail":
                group.add(entry, mentions[0][0])  # once for the thread, however many of the person's addresses it names
                group.addresses.update(address for _, address in mentions)
                group.automated = group.automated and automated
            else:
                group.add(entry, who)
                group.automated = False
    for group in (*catalog.projects.values(), *catalog.people.values()):
        group.finish()
    return catalog
