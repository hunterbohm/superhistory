"""Every `hide` the chokepoint has had, frozen as it was (`at_<commit>.py`, egress.py at that commit), each with the places
in a text it hid. The bar the chokepoint holds grows with this list: it hides every character any of them hid as part of
a secret. When `hide` changes, freeze the old one here and add it to EARLIER; never take one out.

For the first three, `hidden_places` replays the frozen `hide` pass by pass, carrying where each character came from, and
`replay` gives its text, which the bar's test checks equals the frozen `hide`. The later ones say where each character
came from themselves (`hide_with_origin`)."""
import unicodedata

from kb import mask as kbmask
from kb.sync import keys

from . import at_0ba866c, at_2630b52, at_75483cf, at_922fb6b, at_ad58e34, at_b39f746, at_b6bb622

INVISIBLE = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")
PASSES = 8


def _invisible(ch, keep):
    return ch not in keep and unicodedata.category(ch) in INVISIBLE


def _strip(text, came, keep):
    kept = [(ch, o) for ch, o in zip(text, came) if not _invisible(ch, keep)]
    return "".join(ch for ch, _ in kept), [o for _, o in kept]


def _kb(text, origin):
    """kb's mask of `text` and where each character came from, or None where mask() hid what still changed whole."""
    masked, came = kbmask.mask_with_origin(text)
    if masked != kbmask.mask(text):
        return None
    return masked, [origin[c] if c >= 0 else -1 for c in came]


def _codes(text, came, headless):
    for start, end in reversed(keys.code_mask_spans(text, headless)):
        text, came = text[:start] + "[masked]" + text[end:], came[:start] + [-1] * 8 + came[end:]
    return text, came


def _until_stable(current, origin, size, one_pass):
    """(the text, the places of the first text hidden) once `one_pass` stops changing it; all hidden if it never does."""
    for _ in range(PASSES):
        step = one_pass(current, origin)
        if step is None:
            return "[masked]", set(range(size))
        hidden, came = step
        if hidden == current:
            return hidden, set(range(size)) - {c for c in came if c >= 0}
        current, origin = hidden, came
    return "[masked]", set(range(size))


def _strip_then_mask(text, keep, headless):  # 922fb6b
    stripped, origin = _strip(text, list(range(len(text))), keep)
    return _until_stable(stripped, origin, len(text),
                         lambda cur, org: (lambda r: None if r is None else _codes(*r, headless))(_kb(cur, org)))


def _mask_then_strip(text, keep, headless):  # b39f746
    def one(cur, org):
        r = _kb(cur, org)
        return None if r is None else _strip(*_codes(*r, headless), keep)
    return _until_stable(text, list(range(len(text))), len(text), one)


def _two_readings(text, keep, headless):  # 75483cf
    def one(cur, org):
        places = [i for i, ch in enumerate(cur) if not _invisible(ch, keep)]
        if len(places) == len(cur):
            r = _kb(cur, org)
            return None if r is None else _codes(*r, headless)
        stripped = "".join(cur[i] for i in places)
        masked, so = kbmask.mask_with_origin(stripped)
        if masked != kbmask.mask(stripped):
            return None
        written, wc = kbmask.mask_with_origin(cur)
        found = set(range(len(cur))) - {c for c in wc if c >= 0}
        if not found.intersection(places):
            return _codes(masked, [org[places[o]] if o >= 0 else -1 for o in so], headless)
        out, came, hiding = [], [], False
        for ch, o in zip(masked, so):
            if o >= 0 and places[o] in found:
                if not hiding:
                    out.append("[masked]")
                    came.extend([-1] * 8)
                hiding = True
                continue
            hiding = False
            out.append(ch)
            came.append(org[places[o]] if o >= 0 else -1)
        return _codes("".join(out), came, headless)
    return _until_stable(text, list(range(len(text))), len(text), one)


def _with_origin(module):
    def run(text, keep, headless):
        out, origin = module.hide_with_origin(text, keep, headless)
        return out, set(range(len(text))) - {o for o in origin if o >= 0}
    return run


class Earlier:
    def __init__(self, name, module, run):
        self.name, self.module, self._run = name, module, run

    def hide(self, text, keep="\n\t\r", headless=True):
        return self.module.hide(text, keep, headless)

    def replay(self, text, keep="\n\t\r", headless=True):
        return self._run(text, keep, headless)[0]

    def hidden_places(self, text, keep="\n\t\r", headless=True):
        return self._run(text, keep, headless)[1]


EARLIER = (
    Earlier("922fb6b strip then mask", at_922fb6b, _strip_then_mask),
    Earlier("b39f746 mask then strip, again until stable", at_b39f746, _mask_then_strip),
    Earlier("75483cf two readings", at_75483cf, _two_readings),
    Earlier("ad58e34 three readings, once", at_ad58e34, _with_origin(at_ad58e34)),
    Earlier("b6bb622 three readings, each until stable", at_b6bb622, _with_origin(at_b6bb622)),
    Earlier("2630b52 joint fixpoint of four readings", at_2630b52, _with_origin(at_2630b52)),
    Earlier("0ba866c union of three orders", at_0ba866c, _with_origin(at_0ba866c)),
)
