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
    """`text` as `visible` leaves it, with every secret kb's mask and then Sync's own recovery and pairing codes find
    masked. kb's mask goes before the codes: a code-shaped run inside a longer secret, masked first, would break that
    secret into pieces kb no longer knows. Text with an invisible character is read every way `_readings` names, and
    what any reading finds is hidden before any reads again, until a round finds nothing new (`_rounds`, which says why
    that round always comes), so hiding a secret in one reading gives the next secret its edge in all of them. All of it
    runs again until the text stops changing, since masking one secret can free another the pass before skipped, such
    as a token glued to a code, so hiding is then the same however often it runs. A text that needs more than PASSES
    rounds, or still changes after PASSES passes, is hidden whole. `headless` also masks a recovery code written
    without its head, which only what leaves the computer does."""
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
    for n, (current, origin) in enumerate(_rounds(text, keep, headless)):
        if n > PASSES:
            return "[masked]", [-1] * len("[masked]") if track else None
    out, came = [], []
    for ch, o in zip(current, origin):
        k = _kind(ch, keep)
        if k != 1:
            out.append(" " if k == 2 else ch)
            came.append(o)
    return "".join(out), came if track else None


def _rounds(text, keep, headless):
    """The joint fixpoint, round by round: (the text, and where each character of it came from in `text`, -1 for a
    placeholder's), first as written. Each round reads the last one's text every way `_readings` names, and hides in
    `text` each place any reading found, a run of them as one `[masked]`, so every reading then sees what the others
    hid. It stops after a round that finds nothing new. That round always comes: a round only adds places to hide and
    never takes one back, so each round before it hides at least one more of `text`'s characters, and there are
    len(text) of them."""
    hidden = set()
    while True:
        current, origin, hiding = [], [], False
        for i, ch in enumerate(text):
            if i in hidden:
                if not hiding:
                    current.append("[masked]")
                    origin.extend([-1] * len("[masked]"))
                hiding = True
                continue
            hiding = False
            current.append(ch)
            origin.append(i)
        current = "".join(current)
        yield current, origin
        found = set()
        for reading, places in _readings(current, [_kind(ch, keep) for ch in current]):
            found |= _found(reading, places, headless)
        new = {origin[j] for j in found if origin[j] >= 0}  # a placeholder found is a run already hidden
        if not new:
            return
        hidden |= new


def _readings(text, kinds):
    """Each way a round reads `text`, as (the reading, the place in `text` of each of its characters): as written, since
    kb's mask reads through invisible characters and takes one as a word's edge, so a key glued to a word by one is
    found; with what kb's mask reads as breaks left out, so a key form feeds split, glued to a word by an invisible, is
    found too; with every invisible character left out, so a key split around a secret-shaped middle masks whole; and as
    it leaves, as `visible` gives it, which is exactly what a receiver reads."""
    unbroken = [i for i, k in enumerate(kinds) if k != 2]
    plain = [i for i, k in enumerate(kinds) if k == 0]
    leaving = [i for i, k in enumerate(kinds) if k != 1]
    seen = set()
    for reading, places in ((text, range(len(text))), ("".join(text[i] for i in unbroken), unbroken),
                            ("".join(text[i] for i in plain), plain),
                            ("".join(" " if kinds[i] == 2 else text[i] for i in leaving), leaving)):
        if (reading, tuple(places)) not in seen:
            seen.add((reading, tuple(places)))
            yield reading, places


def _codes(text, came, headless):
    """`text` with Sync's codes masked, as keys.mask_codes masks them, and `came` carried along when given."""
    return _replaced(text, came, keys.code_mask_spans(text, headless))


def _replaced(text, came, spans):
    """`text` with each of `spans`, in order and apart, as one `[masked]`, and `came` carried along when given. It is
    built in one pass, since cutting each span out of the whole text in turn takes time that grows with the square of
    the text's length when it holds many codes."""
    pieces, carried, last = [], [], 0
    for start, end in spans:
        pieces += (text[last:start], "[masked]")
        if came is not None:
            carried += came[last:start]
            carried += [-1] * len("[masked]")
        last = end
    pieces.append(text[last:])
    if came is None:
        return "".join(pieces), None
    return "".join(pieces), carried + came[last:]


def _found(reading, places, headless):
    """The places in the text, `places[i]` for `reading`'s i-th character, that kb's mask and then Sync's codes hide
    when they read `reading` until it stops changing, as strip-then-mask did, since hiding a code can free a key behind
    it; with any the reading left out between two of them, which sit inside what is hidden."""
    current, origin = reading, list(range(len(reading)))
    for _ in range(PASSES):
        masked, came = kbmask.mask_with_origin(current)
        came = [origin[c] if c >= 0 else -1 for c in came]
        masked, came = _replaced(masked, came, keys.code_mask_spans(masked, headless))
        if masked == current:
            break
        current, origin = masked, came
    else:
        return set(places)  # still changing: the reading is hidden whole, as strip-then-mask hid one
    found = set(places) - {places[c] for c in came if c >= 0}
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
