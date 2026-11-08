"""Temporary probe for #62: times the masking tests' inputs and candidate reference workloads, to pick the one whose
time tracks the masking's from machine to machine. Not part of the suite."""
import importlib.util
import json
import os
import platform
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kb.mask import mask  # noqa: E402
from tests import speed  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures")
PASSWORD = "Zq7vRt2LmX9k"


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(FIXTURES, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


old = load("main_mask")
from kb import mask as current  # noqa: E402
LINES = [row[0] for row in current._COMMANDS]
today = load("main_mask_68506d9")


def escaped(n):
    text = f'keytool -alias "team cert" -storepass {PASSWORD}'
    for _ in range(n):
        text = json.dumps(text)
    return text


SMALL, LARGE = escaped(10), escaped(12)
RUN = "keytool -storepass " + "\\" * 16000 + "\"x"
COLOUR = "\\" * 16000 + "u001b[31m"

_QUOTE = r"(?:\\{0,7}[\"']|\\{1,7}u00(?:22|27)|%(?:25)?2[27]|&(?:quot|apos|#0{0,3}3[49]|#[xX]0{0,3}2[27]);)"
_AFTER = r"(?:(?<![A-Za-z0-9])|(?<=\\[nrtbf])|(?<=\\u[0-9A-Fa-f]{4})|(?<=%[0-9A-Fa-f]{2}))"
PATTERNS = [re.compile(p) for p in (
    rf"(?i){_AFTER}(?:keytool|jarsigner|mongosh|mongo|twine|sshpass|redis-cli|az\s+login)(?![A-Za-z0-9_.-])",
    rf"\s{_QUOTE}?-(?:src|dest|new)?(?:storepass|keypass){_QUOTE}?[ \t]+{_QUOTE}?([^\s\\\"']+)",
    rf"{_AFTER}(?:password|passwd|secret|token){_QUOTE}?\s*[:=]\s*{_QUOTE}?([^\s\\\"']{{4,}})",
    r"(?:\\(?:[nrtbf\"\\]|u[0-9A-Fa-f]{4})){1,4}(?::?[A-Za-z0-9_.-]+[\"']?[ \t]*(?:[:=]|=>)|-{1,2}[A-Za-z])",
    r"(?<![\\])(?:\\\\)*\\\"",
)]


def regex_work():
    count = 0
    for text in (LARGE, RUN, COLOUR, SMALL) * 10:
        for pattern in PATTERNS:
            for m in pattern.finditer(text):
                count += m.end() - m.start()
    return count


def python_work():
    """A char-by-char reading like a shell's: quotes, escapes, words."""
    count = 0
    for text in (LARGE, SMALL, RUN[:4000]) * 250:
        words, quote, escape, start = [], None, False, 0
        for i, c in enumerate(text):
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif quote:
                if c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c in " \t\n;|":
                if i > start:
                    words.append(text[start:i])
                start = i + 1
        count += len(words)
    return count


def frozen_old_work():
    return [len(old.mask(t)) for t in (LARGE, LARGE, RUN, COLOUR) * 2]


def old_large_work():
    return [len(old.mask(t)) for t in (LARGE,) * 6]


def old_run_work():
    return [len(old.mask(t)) for t in (RUN, RUN, COLOUR, COLOUR)]


def lines_work():
    count = 0
    for text in (LARGE, RUN) * 9:
        for pattern in LINES:
            for m in pattern.finditer(text):
                count += 1
    return count


def old_plus_lines_work():
    count = [len(old.mask(t)) for t in (LARGE, RUN, COLOUR)]
    for text in (LARGE, RUN) * 5:
        for pattern in LINES:
            for m in pattern.finditer(text):
                count.append(m.start())
    return count


def frozen_today_work():
    return [len(today.mask(t)) for t in (LARGE, RUN)]


def best(fn, runs=5):
    spent = []
    for _ in range(runs):
        started = time.process_time()
        fn()
        spent.append(time.process_time() - started)
    return min(spent)


CASES = {
    "mask_large": lambda: mask(LARGE),
    "mask_run": lambda: mask(RUN),
    "mask_colour": lambda: mask(COLOUR),
    "mask_small": lambda: mask(SMALL),
    "strings_ref": speed._workload,
    "frozen_old": frozen_old_work,
    "old_large": old_large_work,
    "old_run": old_run_work,
    "lines": lines_work,
    "old_plus_lines": old_plus_lines_work,
    "frozen_today": frozen_today_work,
}


def main():
    cpu = ""
    try:
        with open("/proc/cpuinfo") as f:
            cpu = next(line.split(":", 1)[1].strip() for line in f if line.startswith("model name"))
    except (OSError, StopIteration):
        pass
    print("probe", platform.python_version(), cpu, flush=True)
    for case in CASES.values():
        case()
    for rep in range(int(os.environ.get("PROBE_REPS", "3"))):
        print("rep", rep, " ".join(f"{name}={best(fn):.4f}" for name, fn in CASES.items()), flush=True)


if __name__ == "__main__":
    main()
