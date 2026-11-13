"""How fast this machine is at the masking tests' kind of work, for the test caps that are absolute seconds.

A cap such as "under 1.5 s" was measured on one machine. `scaled(1.5)` is that cap times how much slower this machine
runs a fixed reference workload than the reference machine did. Ratio checks ("6 * small + 0.3") need none of this:
both sides slow down together.

The workload is masking, done by tests/fixtures/main_mask.py, main's mask frozen at 3be191e (a test pins its bytes),
on fixed inputs shaped like the timed tests': a command line escaped twelve times over and runs of 16,000
backslashes. It calls no code of kb's, so a slowdown in kb cannot raise its own cap. It replaces plain string and
regex work, which did not track (#62): a GitHub runner took 1.04 times as long as the reference machine at that, and
1.6 times as long at the masking tests. On twelve runners of four kinds of CPU, the frozen mask's time tracked the
masking tests' within about 15%.

It is timed in CPU time, the clock the caps use, best of several runs so noise does not swing the factor.
`python -m tests.speed` prints the factor, as CI does before the suite.
"""
import importlib.util
import json
import os
import sys
import time

# CPU seconds `measure()` takes on the reference machine, a Linux desktop, best of 5 runs, under each Python CI runs.
# Fifty runs of each there gave 0.161 to 0.188 under 3.12 and 0.178 to 0.202 under 3.9; each time here is the one
# nine runs in ten came in under, so the factor there is 1 but on a rare slow run, and the caps are as strict as they
# were set. That machine's 3.9 is the slower at this work, and at the masking tests too (15 to 20%), while GitHub's
# two run alike, so each version is held to its own time. Any other version is held to the larger, which keeps its
# caps the stricter. The factor is this machine's time over that, and never below 1, so a faster machine is held to
# the caps as they were set.
REFERENCE_SECONDS = {(3, 9): 0.194, (3, 12): 0.173}

_FROZEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "main_mask.py")
_RUNS = 5
_factor = None


def _frozen_mask():
    spec = importlib.util.spec_from_file_location("speed_reference_mask", _FROZEN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.mask


def _inputs():
    """The timed tests' shapes: a password's command line escaped twelve times, and 16,000 backslashes before a quote
    and before a colour code."""
    escaped = 'keytool -alias "team cert" -storepass Zq7vRt2LmX9k'
    for _ in range(12):
        escaped = json.dumps(escaped)
    run = "\\" * 16000
    return (escaped, escaped, f'keytool -storepass {run}"x', run + "u001b[31m") * 2


def measure():
    """CPU seconds the reference workload takes here, best of several runs."""
    mask, texts = _frozen_mask(), _inputs()
    best = None
    for _ in range(_RUNS):
        started = time.process_time()
        for text in texts:
            mask(text)
        spent = time.process_time() - started
        best = spent if best is None else min(best, spent)
    return best


def reference():
    """CPU seconds the reference workload took on the reference machine, under this Python."""
    return REFERENCE_SECONDS.get(sys.version_info[:2], max(REFERENCE_SECONDS.values()))


def _factor_of(spent):
    return max(1.0, spent / reference())


def factor():
    """How many times slower this machine is than the reference machine, 1.0 at the least. Measured once per process."""
    global _factor
    if _factor is None:
        _factor = _factor_of(measure())
    return _factor


class _Cap(float):
    """A scaled cap. A failed assertion shows it with the cap it came from and the factor."""

    def __new__(cls, seconds, factor):
        cap = super().__new__(cls, seconds * factor)
        cap.seconds, cap.factor = seconds, factor
        return cap

    def __repr__(self):
        return f"{float(self)!r} (a {self.seconds} s cap times speed factor {self.factor:.3f})"


def scaled(seconds):
    """A cap of `seconds` on the reference machine, as it comes out here."""
    return _Cap(seconds, factor())


if __name__ == "__main__":
    spent = measure()
    print(f"speed factor {_factor_of(spent):.3f}: the reference workload took {spent:.3f} CPU seconds here, "
          f"{reference()} on the reference machine")
