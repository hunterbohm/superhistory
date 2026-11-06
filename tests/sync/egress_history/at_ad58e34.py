"""The one chokepoint for what leaves this computer, and for what the CLI prints.

Every outbound payload passes `clean` right before it is serialized: the records of a Sync pack or a snapshot as they
are signed, every Cloud request, and every request to the app. Every string, keys included, leaves without the invisible
characters kb's mask reads through and with every other invisible or control character as a space, so nothing that
leaves joins two pieces kb's mask read apart. It is masked as `hide` says, reading it three ways, so a secret split by
invisible characters, or glued to a word by one, is hidden.
The relay's own requests carry only ciphertext and key material, and pass as they are.
"""
import unicodedata

from kb import mask as kbmask
from kb.sync import keys

INVISIBLE = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")


def _kind(ch, keep):
    """0 for a character that leaves as it is, 1 for an invisible one kb's mask reads through, which leaves out, and 2
    for any other invisible or control character, which kb's mask reads as a break and which leaves as a space."""
    if ch in keep or unicodedata.category(ch) not in INVISIBLE:
        return 0
    return 1 if kbmask.INVISIBLE.fullmatch(ch) else 2


def visible(text, keep="\n\t\r"):
    """`text` as it leaves, before masking: what kb's mask reads through left out, and every other invisible or control
    character a space, so leaving them out never joins two pieces kb's mask read apart."""
    return "".join(" " if k == 2 else ch for ch in text if (k := _kind(ch, keep)) != 1)


PASSES = 8


def hide(text, keep="\n\t\r", headless=True):
    """`text` as `visible` leaves it, with every secret kb's mask and then Varve's own recovery and pairing codes find
    masked. kb's mask goes before the codes: a code-shaped run inside a longer secret, masked first, would break that
    secret into pieces kb no longer knows. Both read the text three ways, and what any finds is hidden: as written, since
    kb's mask reads through invisible characters and takes one as a word's edge, so a key glued to a word by one is
    found; with what it reads as breaks left out, so a key form feeds split, glued to a word by an invisible, is found
    too; and with every invisible character left out, so a key split around a secret-shaped middle masks whole. All of
    it runs again until the text stops changing, since masking one secret can free another the pass before skipped,
    such as a token glued to a code, so hiding is then the same however often it runs. A text still changing after
    PASSES passes is hidden whole. `headless` also masks a recovery code written without its head, which only what
    leaves the computer does."""
    return _hide(text, keep, headless, False)[0]


def hide_with_origin(text, keep="\n\t\r", headless=True):
    """hide(), and where each character of its result came from in `text`, -1 for a placeholder's. A character `visible`
    turns into a space keeps its place."""
    return _hide(text, keep, headless, True)


def _hide(text, keep, headless, track):
    origin = list(range(len(text))) if track else None
    for _ in range(PASSES):
        hidden, came = _masked(text, keep, headless, track)
        if hidden == text:
            return hidden, origin
        text = hidden
        if track:
            origin = [origin[c] if c >= 0 else -1 for c in came]
    return "[masked]", [-1] * len("[masked]") if track else None


def _masked(text, keep, headless, track):
    """One pass: (the text, and with `track` where each character of it came from in `text`)."""
    kinds = [_kind(ch, keep) for ch in text]
    if not any(kinds):
        masked = kbmask.mask(text)
        came = None
        if track:
            again, came = kbmask.mask_with_origin(text)
            if again != masked:  # mask() hid what still changed at its last pass whole; count none of it shown
                came = [-1] * len(masked)
        return _codes(masked, came, headless)
    found = _found(text, range(len(text)), headless)
    for places in ([i for i, k in enumerate(kinds) if k != 2], [i for i, k in enumerate(kinds) if k == 0]):
        if len(places) != len(text):
            found |= _found("".join(text[i] for i in places), places, headless)
    out, came, hiding = [], [], False
    for i, ch in enumerate(text):
        if kinds[i] == 1:
            continue
        if i in found:
            if not hiding:
                out.append("[masked]")
                came.extend([-1] * len("[masked]"))
            hiding = True
            continue
        hiding = False
        out.append(" " if kinds[i] == 2 else ch)
        came.append(i)
    return _codes("".join(out), came if track else None, headless)


def _codes(text, came, headless):
    """`text` with Varve's codes masked, as keys.mask_codes masks them, and `came` carried along when given."""
    for start, end in reversed(keys.code_mask_spans(text, headless)):
        text = text[:start] + "[masked]" + text[end:]
        if came is not None:
            came = came[:start] + [-1] * len("[masked]") + came[end:]
    return text, came


def _found(reading, places, headless):
    """The places in the text, `places[i]` for `reading`'s i-th character, that kb's mask and then Varve's codes hide
    when they read `reading`, with any the reading left out between two of them, which sit inside what is hidden."""
    masked, origin = kbmask.mask_with_origin(reading)
    found = set(places) - {places[o] for o in origin if o >= 0}
    for start, end in keys.code_spans(masked) + (keys.headless_spans(masked) if headless else []):
        found.update(places[origin[j]] for j in range(start, end) if origin[j] >= 0)
    for here, after in zip(places, places[1:]):
        if after > here + 1 and here in found and after in found:
            found.update(range(here + 1, after))
    return found


_PROBE = "Ab3dE5fG7hJ9kL2m"


def _secret_name(key):
    """Whether the mask reads `key` as the name of a secret, as it reads `password` in `password=...`."""
    return isinstance(key, str) and hide(f"{key}={_PROBE}") != f"{hide(key)}={_PROBE}"


def _all_hidden(value):
    """Every leaf under a key that names a secret, a number or a list's items too, masked."""
    if isinstance(value, (list, tuple)):
        return [_all_hidden(v) for v in value]
    if isinstance(value, dict):
        return {clean(k): _all_hidden(v) for k, v in value.items()}
    return value if value is None or isinstance(value, bool) else "[masked]"


def _named(key, value):
    """Whether `value` holds a secret only its key names, as in {"password": "..."}: written after its key, as text
    would hold it, the mask hides more than it hides in either alone."""
    return hide(f"{key}={value}") != f"{hide(key)}={hide(value)}"


def clean(value, passing=()):
    """`value` with every string in it hidden, dict keys included. Values under a key in `passing` stay as they are. A
    tuple is walked like a list, since JSON sends it as one."""
    if isinstance(value, str):
        return hide(value)
    if isinstance(value, (list, tuple)):
        return [clean(v, passing) for v in value]
    if isinstance(value, dict):
        return {clean(k): v if k in passing else _all_hidden(v) if _secret_name(k) else
                "[masked]" if isinstance(v, str) and _named(k, v) else clean(v, passing) for k, v in value.items()}
    return value
