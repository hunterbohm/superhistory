"""The one chokepoint for what leaves this computer, and for what the CLI prints.

Every outbound payload passes `clean` right before it is serialized: the records of a Sync pack or a snapshot as they
are signed, every Cloud request, and every request to the app. Every string, keys included, leaves without the invisible
characters kb's mask reads through and with every other invisible or control character as a space, so nothing that
leaves joins two pieces kb's mask read apart. It is masked as `hide` says, read every way and in every order `hide`
names, so a secret split by invisible characters, or glued to a word by one, is hidden.
The relay's own requests carry only ciphertext and key material, and pass as they are.
"""
import json
import unicodedata

from . import keys
from ..mask import INVISIBLE as MASK_INVISIBLE, mask, mask_with_origin

INVISIBLE = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")


def _kind(ch, keep):
    """0 for a character that leaves as it is; 1 for an invisible one kb's mask reads through, which leaves out; 2 for
    any other invisible or control character, which kb's mask reads as a break and which leaves as a space; and 3 for a
    mark kb's mask reads through that is neither, such as the variation selector after an emoji, a combining grapheme
    joiner or a Hangul filler. A mark leaves as it is, so an emoji keeps its look, but the readings that leave out
    invisible characters leave it out too, since a secret with one inside is whole to anyone who deletes it."""
    if ch in keep:
        return 0
    through = MASK_INVISIBLE.fullmatch(ch)
    if unicodedata.category(ch) not in INVISIBLE:
        return 3 if through else 0
    return 1 if through else 2


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
    that round always comes), so hiding a secret in one reading gives the next secret its edge in all of them. Since
    hiding one secret can also hide the name another needs, what round 1's order hides (`_mask_then_strip`, which says
    why) is hidden too, and the rounds then go on from both. All of it runs again until the text stops changing, since
    masking one secret can free another the pass before skipped, such as a token glued to a code, so hiding is then the
    same however often it runs. A text that needs more than PASSES rounds, or still changes after PASSES passes, is
    hidden whole. `headless` also masks a recovery code written without its head, which only what leaves the computer
    does."""
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
        masked = mask(text)
        came = None
        if track:
            again, came = mask_with_origin(text)
            if again != masked:  # mask() hid what still changed at its last pass whole; count none of it shown
                came = [-1] * len(masked)
        return _codes(masked, came, headless)
    whole = "[masked]", [-1] * len("[masked]") if track else None
    joint = _settled(text, keep, headless, set())
    if joint is None:
        return whole
    hidden = set(range(len(text))) - {o for o in joint[1] if o >= 0}
    union = hidden | _strip_then_mask(text, keep, headless) | _mask_then_strip(text, keep, headless)
    if union != hidden:
        joint = _settled(text, keep, headless, union)
        if joint is None:
            return whole
    current, origin = joint
    out, came = [], []
    for ch, o in zip(current, origin):
        k = _kind(ch, keep)
        if k != 1:
            out.append(" " if k == 2 else ch)
            came.append(o)
    return "".join(out), came if track else None


def _settled(text, keep, headless, hidden):
    """The last of `_rounds` from `hidden`, or None when it takes more than PASSES rounds."""
    for n, last in enumerate(_rounds(text, keep, headless, hidden)):
        if n > PASSES:
            return None
    return last


def _strip_then_mask(text, keep, headless):
    """The places of `text` that strip-then-mask, the first order, hides: every invisible or control character left
    out, the marks kept as it kept them, then kb's mask and Sync's codes until the text stops changing."""
    places = [i for i, ch in enumerate(text) if _kind(ch, keep) in (0, 3)]
    return _found("".join(text[i] for i in places), places, headless)


def _mask_then_strip(text, keep, headless):
    """The places of `text` that round 1's order hides, as part of a secret: kb's mask and then Sync's codes on the
    text as written, then every invisible or control character left out, the marks kept as it kept them, all of it
    again until the text stops changing, every place if it never does. Leaving the invisible characters out drops them
    all, so only the others it hid count, with the invisible ones between two of them, which sit inside what it hid.

    `hide` hides what each of three orders of reading hides, and then runs the joint fixpoint from there: strip-then-
    mask (`_strip_then_mask`), this one, and the joint fixpoint. Masking is not monotone, since hiding one secret can
    hide the name another needs: in `pass<FF>word=1234`, a code and `<FF>tok<FF>en: <token>`, with <FF> a form feed,
    read with the form feeds left out kb's mask takes `token:` into the password's value, and then no reading sees a
    token's name, while this order hides the code first, which ends the value before `token:`. So no one order holds
    what each of the others hides, and their union does."""
    current, origin = text, list(range(len(text)))
    for _ in range(PASSES):
        masked, came = mask_with_origin(current)
        if masked != mask(current):  # mask() hid what still changed at its last pass whole
            return set(range(len(text)))
        masked, came = _replaced(masked, [origin[c] if c >= 0 else -1 for c in came],
                                 keys.code_mask_spans(masked, headless))
        kept = [j for j, ch in enumerate(masked) if _kind(ch, keep) in (0, 3)]
        hidden, came = "".join(masked[j] for j in kept), [came[j] for j in kept]
        if hidden == current:
            break
        current, origin = hidden, came
    else:
        return set(range(len(text)))
    shown = {o for o in origin if o >= 0}
    hid, last = set(), None
    for i, ch in enumerate(text):
        if _kind(ch, keep) in (1, 2):
            continue
        if i not in shown:
            if last is not None:
                hid.update(range(last + 1, i))
            hid.add(i)
            last = i
        else:
            last = None
    return hid


def _rounds(text, keep, headless, hidden=()):
    """The joint fixpoint, round by round, from the places `hidden`: (the text, and where each character of it came
    from in `text`, -1 for a placeholder's), first with only those hidden. Each round reads the last one's text every
    way `_readings` names, and hides in `text` each place any reading found, a run of them as one `[masked]`, so every
    reading then sees what the others hid. It stops after a round that finds nothing new. That round always comes: a
    round only adds places to hide and never takes one back, so each round before it hides at least one more of
    `text`'s characters, and there are len(text) of them."""
    hidden = set(hidden)
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
    found too; with every invisible character and mark left out, so a key split around a secret-shaped middle masks
    whole; as it leaves, as `visible` gives it, which is exactly what a receiver reads; and as it leaves with its marks
    left out, as a receiver who deletes them reads it, or read as spaces, since a filler shows as a blank but kb's mask
    takes it for a letter, so a token behind one is found."""
    unbroken = [i for i, k in enumerate(kinds) if k != 2]
    plain = [i for i, k in enumerate(kinds) if k == 0]
    leaving = [i for i, k in enumerate(kinds) if k != 1]
    bare = [i for i, k in enumerate(kinds) if k in (0, 2)]
    seen = set()
    for reading, places in ((text, range(len(text))), ("".join(text[i] for i in unbroken), unbroken),
                            ("".join(text[i] for i in plain), plain),
                            ("".join(" " if kinds[i] == 2 else text[i] for i in leaving), leaving),
                            ("".join(" " if kinds[i] == 2 else text[i] for i in bare), bare),
                            ("".join(" " if kinds[i] in (2, 3) else text[i] for i in leaving), leaving)):
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
        masked, came = mask_with_origin(current)
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
_JOINS = ("=", ": ")


def _secret_name(key):
    """Whether the mask reads `key` as the name of a secret, as it reads `password` in `password=...` or `Cookie` in
    `Cookie: ...`: a name is tried both ways, since a header is only ever written with a colon."""
    return isinstance(key, str) and any(hide(f"{key}{join}{_PROBE}") != f"{hide(key)}{join}{_PROBE}" for join in _JOINS)


def _all_hidden(value, depth=0):
    """Every leaf under a key that names a secret, a number or a list's items too, masked; past DEPTH, all of it."""
    if isinstance(value, (list, tuple, dict)) and depth >= DEPTH:
        return "[masked]"
    if isinstance(value, (list, tuple)):
        return [_all_hidden(v, depth + 1) for v in value]
    if isinstance(value, dict):
        return {_clean(k, (), depth): _all_hidden(v, depth + 1) for k, v in value.items()}
    return value if value is None or isinstance(value, bool) else "[masked]"


def _value(name, value, passing, depth, plain=None):
    """`value` as it leaves when `name` is its name: all of it masked when the name is a secret's; masked whole when it
    holds a secret only its name names, as in {"password": "..."}, which written after its name, as text would hold
    it, either way `_secret_name` tries, the mask hides more of than it hides in either alone; and otherwise cleaned
    like any other value, `plain` when the caller has cleaned it already. A value is cleaned once whatever its name,
    so a value nested under names takes time in step with its size."""
    if _secret_name(name):
        return _all_hidden(value, depth)
    if plain is None:
        plain = _clean(value, passing, depth)
    if not isinstance(value, str):
        return plain
    head = hide(name)
    if any(hide(f"{name}{join}{value}") != f"{head}{join}{plain}" for join in _JOINS):
        return "[masked]"
    return plain


def _named_by(names, value, passing, depth):
    """`value` as it leaves beside every name in `names`: as `_value` leaves it for the first name that hides more of it
    than cleaning alone does, since an object can hold its name twice, as `name` and `key`, and either can be the
    secret's. A secret's name hides the value before anything cleans it, so a value nested under such names is walked
    once."""
    if any(_secret_name(name) for name in names):
        return _all_hidden(value, depth)
    plain = _clean(value, passing, depth)
    for name in names:
        named = _value(name, value, passing, depth, plain)
        if named != plain:
            return named
    return plain


DEPTH = 64


def clean(value, passing=()):
    """`value` with every string in it hidden, dict keys included. Values under a key in `passing` stay as they are. A
    tuple is walked like a list, since JSON sends it as one. A name and its value held apart, as captured requests,
    API clients and cloud tags hold headers, credentials and variables, are read together like a key and its value:
    each pair in a list of nothing but pairs of strings, and an object with a string under `name` or `key`, in any case,
    beside a `value`, which either naming a secret masks. A list of two strings alone, such as two tags, is not a pair.
    A list or object more than DEPTH levels down is not walked: it is written out as JSON and hidden as one text, which
    reads no name beside its value but finds whatever the masks find in text, and one too deep or too odd to write out
    is hidden whole; so the work, and Python's own recursion, never grow past DEPTH levels. All of it runs again until
    it stops changing, since a field's name with an invisible character in it reads as `value` only once cleaned, so
    cleaning is then the same however often it runs; a value still changing after PASSES passes is hidden whole."""
    for _ in range(PASSES):
        cleaned = _clean(value, passing, 0)
        if cleaned == value:
            return cleaned
        value = cleaned
    return _all_hidden(value)


_NAMES, _VALUES = ("name", "key"), ("value",)


def _too_deep(value):
    """A list or object past DEPTH, as one text hidden: written out as JSON, or `[masked]` if it can't be."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (RecursionError, ValueError, TypeError):
        return "[masked]"
    return hide(text)


def _clean(value, passing, depth):
    """One pass of `clean`, `depth` levels down."""
    if isinstance(value, str):
        return hide(value)
    if isinstance(value, (list, tuple, dict)) and depth >= DEPTH:
        return _too_deep(value)
    if isinstance(value, (list, tuple)):
        if value and all(isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, str) for x in v)
                         for v in value):
            return [[_clean(name, passing, depth), _value(name, v, passing, depth + 1)] for name, v in value]
        return [_clean(v, passing, depth + 1) for v in value]
    if isinstance(value, dict):
        fields = {k: _clean(k, (), depth) for k in value}
        names = [v for k, v in value.items() if isinstance(v, str) and str(fields[k]).lower() in _NAMES]
        out = {}
        for k, v in value.items():
            if k in passing:
                out[fields[k]] = v
            elif names and str(fields[k]).lower() in _VALUES:
                out[fields[k]] = _named_by(names, v, passing, depth + 1)
            else:
                out[fields[k]] = _value(k, v, passing, depth + 1)
        return out
    return value
