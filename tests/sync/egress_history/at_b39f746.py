"""The one chokepoint for what leaves this computer, and for what the CLI prints.

Every outbound payload passes `clean` right before it is serialized: the records of a Sync pack or a snapshot as they
are signed, every Cloud request, and every request to the app. It removes invisible characters from every string, keys
included, and then masks the string, so a secret split by an invisible character is whole again when the mask reads it.
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
    """`text` with kb's secrets masked, then Varve's own recovery and pairing codes, which kb does not know, and then
    stripped. It masks before it strips, since kb's mask reads through invisible characters and takes one as a word's
    edge, so a key glued to a word by one is still found. kb's mask goes before the codes: a code-shaped run inside a
    longer secret, masked first, would break that secret into pieces kb no longer knows. All of it runs again until the
    text stops changing. Stripping can join a key that characters kb reads as spaces had split, such as form feeds,
    and masking one secret can free another the pass before skipped, such as a token glued to a code, so hiding is then
    the same however often it runs. A text still changing after PASSES passes is hidden whole. `headless` also masks a
    recovery code written without its head, which only what leaves the computer does."""
    for _ in range(PASSES):
        hidden = strip(keys.mask_codes(kbmask.mask(text), headless), keep)
        if hidden == text:
            return hidden
        text = hidden
    return "[masked]"


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
