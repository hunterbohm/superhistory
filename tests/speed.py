"""How fast this machine is, for the test caps that are absolute seconds.

A cap such as "under 1.5 s" was measured on one machine; GitHub's shared runners are about 1.5 times slower, so the
same code sat at its cap there (#62). `scaled(1.5)` is that cap times how much slower this machine runs a fixed
reference workload than the reference machine did. Ratio checks ("6 * small + 0.3") need none of this: both sides
slow down together.

The workload calls no code from this repo, so a slowdown in kb cannot raise its own cap. It is plain string and
regex work on fixed data, timed in CPU time (the same clock the caps use), best of several runs so noise does not
swing the factor.
"""
import re
import time

# CPU seconds `measure()` takes on the reference machine: a Linux desktop, Python 3.12, best of 5 runs. Measured
# six times there, it gave 0.203 to 0.215; this is the middle. The factor is this machine's time over that, and
# never below 1, so a faster machine is held to the caps as they were set on the reference machine.
REFERENCE_SECONDS = 0.21

_RUNS = 5
_factor = None


def _workload():
    """Fixed work of the kind the masks do: scan, substitute, split and join over repetitive text."""
    text = ("keytool -alias \"team cert\" -storepass letmein \\\\\\\" key=value; Bearer abcDEF123456 "
            "-----BEGIN KEY----- line\n") * 400
    words = re.compile(r"[A-Za-z0-9_]+")
    options = re.compile(r"(?:-{1,2}[a-z][a-z-]*)\s+(\S+)")
    count = 0
    for _ in range(150):
        count += len(words.findall(text))
        count += len(options.sub("-x [m]", text))
        count += len(text.upper().lower().replace("key", "k").split("\n"))
        count += sum(1 for line in text.splitlines() if "cert" in line and line.endswith("line"))
    return count


def measure():
    """CPU seconds the reference workload takes here, best of several runs."""
    best = None
    for _ in range(_RUNS):
        started = time.process_time()
        _workload()
        spent = time.process_time() - started
        best = spent if best is None else min(best, spent)
    return best


def factor():
    """How many times slower this machine is than the reference machine, 1.0 at the least. Measured once per process."""
    global _factor
    if _factor is None:
        _factor = max(1.0, measure() / REFERENCE_SECONDS)
    return _factor


def scaled(seconds):
    """A cap of `seconds` on the reference machine, as it comes out here."""
    return seconds * factor()
