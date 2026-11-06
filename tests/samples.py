"""Strings a regular expression matches, drawn at random from its parsed form, as gitleaks draws the examples its own
rules are tested on (secrets.NewSecret): each literal as written, each class's member at random, each repeat a random
count from its range, each alternative at random. Anchors and lookarounds add nothing; a sample is kept only where
the pattern matches it."""
import random
import string

try:
    from re import _parser as sre_parse, _constants as sre
except ImportError:  # Python 3.9 and 3.10
    import sre_constants as sre
    import sre_parse

_CATEGORIES = {sre.CATEGORY_DIGIT: string.digits, sre.CATEGORY_WORD: string.ascii_letters + string.digits + "_",
               sre.CATEGORY_SPACE: " ", sre.CATEGORY_NOT_DIGIT: string.ascii_letters,
               sre.CATEGORY_NOT_WORD: "-", sre.CATEGORY_NOT_SPACE: string.ascii_letters + string.digits}
_ANY = string.ascii_letters + string.digits
# The longest a repeat with no upper bound runs past its least.
_OPEN = 24


def _members(items):
    chars, negate = set(), False
    for op, arg in items:
        if op == sre.NEGATE:
            negate = True
        elif op == sre.LITERAL:
            chars.add(chr(arg))
        elif op == sre.RANGE:
            chars.update(chr(c) for c in range(arg[0], arg[1] + 1))
        elif op == sre.CATEGORY:
            chars.update(_CATEGORIES[arg])
    if negate:
        chars = set(string.ascii_letters + string.digits + string.punctuation + " ") - chars
    return sorted(c for c in chars if c.isprintable() and c not in "\r\n") or sorted(chars)


def _draw(r, items, groups):
    out = []
    for op, arg in items:
        if op == sre.LITERAL:
            out.append(chr(arg))
        elif op == sre.NOT_LITERAL:
            out.append(r.choice([c for c in _ANY if ord(c) != arg]))
        elif op == sre.ANY:
            out.append(r.choice(_ANY))
        elif op == sre.IN:
            out.append(r.choice(_members(arg)))
        elif op in (sre.MAX_REPEAT, sre.MIN_REPEAT, getattr(sre, "POSSESSIVE_REPEAT", None)):
            least, most, sub = arg
            most = least + _OPEN if most == sre.MAXREPEAT else min(most, least + 3 * _OPEN)
            for _ in range(r.randint(least, most)):
                out.append(_draw(r, sub, groups))
        elif op == sre.SUBPATTERN:
            group, sub = arg[0], arg[-1]
            drawn = _draw(r, sub, groups)
            if group:
                groups[group] = drawn
            out.append(drawn)
        elif op == getattr(sre, "ATOMIC_GROUP", None):
            out.append(_draw(r, arg, groups))
        elif op == sre.BRANCH:
            out.append(_draw(r, r.choice(arg[1]), groups))
        elif op == sre.GROUPREF:
            out.append(groups.get(arg, ""))
        elif op == sre.CATEGORY:
            out.append(r.choice(_CATEGORIES[arg]))
        # AT (anchors), ASSERT and ASSERT_NOT (lookarounds) add nothing.
    return "".join(out)


def samples(pattern, n, seed):
    """Up to `n` distinct strings the compiled `pattern` matches whole, drawn with `seed`."""
    parsed = sre_parse.parse(pattern.pattern, pattern.flags)
    r, out = random.Random(seed), []
    for _ in range(n * 20):
        drawn = _draw(r, parsed, {})
        if pattern.fullmatch(drawn) and drawn not in out:
            out.append(drawn)
            if len(out) == n:
                break
    return out
