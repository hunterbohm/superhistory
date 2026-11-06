"""The one chokepoint for what leaves this computer, and for what the CLI prints.

Every outbound payload passes `clean` right before it is serialized: the records of a Sync pack or a snapshot as they
are signed, every Cloud request, and every request to the app. It masks every string, keys included, reading it both
with its invisible characters removed and as written, and leaves it with them removed, so a secret split by an invisible
character, or glued to a word by one, is hidden.
The relay's own requests carry only ciphertext and key material, and pass as they are.
"""
import unicodedata

from kb import mask as kbmask
from kb.sync import keys

INVISIBLE = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")


def strip(text, keep="\n\t\r"):
    """`text` without control, format, separator, surrogate, private or unassigned characters, other than `keep`."""
    return "".join(ch for ch in text if ch in keep or unicodedata.category(ch) not in INVISIBLE)


PASSES = 8


def hide(text, keep="\n\t\r", headless=True):
    """`text` stripped, with every secret kb's mask finds masked, then Varve's own recovery and pairing codes, which kb
    does not know. kb's mask reads it two ways and hides what either finds. It reads the text stripped, so a key that
    invisible characters split, or characters it takes for spaces such as form feeds, is whole again, and a key split
    around a secret-shaped middle masks whole, not just its middle. It also reads the text as written, since it reads
    through invisible characters and takes one as a word's edge, so a key glued to a word by one is found too, which
    the stripped text glues past finding. kb's mask goes before the codes: a code-shaped run inside a longer secret,
    masked first, would break that secret into pieces kb no longer knows. All of it runs again until the text stops
    changing, since masking one secret can free another the pass before skipped, such as a token glued to a code, so
    hiding is then the same however often it runs. A text still changing after PASSES passes is hidden whole.
    `headless` also masks a recovery code written without its head, which only what leaves the computer does."""
    for _ in range(PASSES):
        hidden = keys.mask_codes(_masked(text, keep), headless)
        if hidden == text:
            return hidden
        text = hidden
    return "[masked]"


def _masked(text, keep):
    """`text` stripped, with what kb's mask finds in it masked, and what it finds in `text` as written masked too."""
    places = [i for i, ch in enumerate(text) if ch in keep or unicodedata.category(ch) not in INVISIBLE]
    if len(places) == len(text):
        return kbmask.mask(text)
    stripped = "".join(text[i] for i in places)
    masked, origin = kbmask.mask_with_origin(stripped)
    written, came = kbmask.mask_with_origin(text)
    found = set(range(len(text))) - {c for c in came if c >= 0}  # what the text as written masks
    if not found.intersection(places):
        return masked
    out, hiding = [], False
    for ch, o in zip(masked, origin):
        if o >= 0 and places[o] in found:
            if not hiding:
                out.append("[masked]")
            hiding = True
            continue
        hiding = False
        out.append(ch)
    return "".join(out)


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
