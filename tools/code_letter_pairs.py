"""Print kb/mask.py's _CODE_LETTER_PAIRS: how often each small letter follows another in the words of code's names.

The words are tools/code_words.txt: each word of the names in Python 3.12.3's standard library (its tests, idlelib,
site-packages and the modules Python 3.13 removed left out), split as camel case and snake case split them
(_LABEL_WORD), two letters or longer, in small letters, each listed once however often it is used. Each word is read
with a mark for its start ("^") and its end ("$"). Each row of the table is a letter or the start, each column a
letter or the end, and each cell the cost of that step, -10 ln P(next | this) rounded, with half a count added to every
pair, so a pair never seen costs about as much as the rarest seen. kb/mask.py holds what this prints, as a test checks:

    python3 -I tools/code_letter_pairs.py

The word list itself comes from the running Python's standard library, which differs from one Python to the next, so
it is kept in the repository and the table is printed from it alone:

    python3 -I tools/code_letter_pairs.py --collect > tools/code_words.txt
"""
import io
import math
import os
import re
import sys
import sysconfig
import tokenize

LETTERS = "abcdefghijklmnopqrstuvwxyz"
WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+")  # kb.mask._LABEL_WORD's letters
# The modules Python 3.13 removed (PEP 594, and lib2to3), whose words are no longer the library's.
REMOVED = frozenset("""aifc audioop cgi cgitb chunk crypt imghdr lib2to3 mailcap msilib nis nntplib ossaudiodev pipes
sndhdr spwd sunau telnetlib uu xdrlib""".split())
WORDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "code_words.txt")


def names(root):
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in ("test", "tests", "idlelib", "site-packages", "__pycache__")
                         and d not in REMOVED)
        for name in sorted(files):
            if not name.endswith(".py") or name[:-3] in REMOVED:
                continue
            try:
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    for token in tokenize.generate_tokens(io.StringIO(f.read()).readline):
                        if token.type == tokenize.NAME:
                            yield token.string
            except (SyntaxError, UnicodeDecodeError, tokenize.TokenError):
                continue


def collect():
    """The distinct words of the running Python's standard library's names, sorted, one a line."""
    return sorted({word.lower() for name in set(names(sysconfig.get_paths()["stdlib"]))
                   for word in WORD.findall(name) if len(word) >= 2})


def table(words):
    """The rows of _CODE_LETTER_PAIRS for `words`."""
    pairs, firsts = {}, {}
    for word in words:
        word = "^" + word + "$"
        for a, b in zip(word, word[1:]):
            pairs[a, b] = pairs.get((a, b), 0) + 1
            firsts[a] = firsts.get(a, 0) + 1
    return ["".join("%02d" % min(99, round(-10 * math.log((pairs.get((a, b), 0) + 0.5) / (firsts.get(a, 0) + 13.5))))
                    for b in LETTERS + "$")
            for a in "^" + LETTERS]


def main():
    if sys.argv[1:] == ["--collect"]:
        print("\n".join(collect()))
        return
    with open(WORDS, encoding="ascii") as f:
        words = f.read().split()
    assert words == sorted(set(words)) and all(re.fullmatch("[a-z]{2,}", word) for word in words), WORDS
    print("\n".join(table(words)))


if __name__ == "__main__":
    main()
