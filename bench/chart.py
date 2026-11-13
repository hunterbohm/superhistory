#!/usr/bin/env python3
"""Draw the month's head-to-head as a PNG in the launch palette.

    python3 bench/chart.py [--out bench/results/month/chart.png]

Four panels, one scale each, from the month's result rows: questions with an answer that the agent got right,
questions with nothing to find on which the tool added nothing, tokens each lookup added to the agent's context,
and seconds per lookup. Rendered with headless Chrome and the launch fonts.
"""
import argparse
import html
import os
import statistics
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import product
import run

PAPER, INK, GREEN, MUTED, RULE = "#F2EEE5", "#17150F", "#1F6B4C", "#9A9488", "#D9D3C7"
# The product's own row is green; every other row is the muted ink.
ROWS = (("check", product.NAME), ("supermemory", "Supermemory"), ("mem0", "Mem0"), ("claude-mem", "claude-mem"))
WIDTH, HEIGHT = 1600, 900
FONTS = {"Newsreader": "ofl/newsreader/Newsreader%5Bopsz,wght%5D.ttf", "Inter": "ofl/inter/Inter%5Bopsz,wght%5D.ttf",
         "JetBrains Mono": "ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf"}


def lookup_seconds(results, tool, rows):
    """The median of the tool's lookups timed alone (latency.jsonl), or of its month run when it has no such run, and
    whether it was timed alone. README's table reads the same numbers."""
    path = os.path.join(results, "latency.jsonl")
    alone = [r["seconds"] for r in run.read_jsonl(path) if r["tool"] == tool] if os.path.exists(path) else []
    return statistics.median(alone or [r["lookup_seconds"] for r in rows]), bool(alone)


def seconds_note(found):
    """Says where the seconds panel's numbers come from, so a month run's timings never read as timed alone."""
    from_run = [f["label"] for f in found.values() if not f["alone"]]
    if not from_run:
        return "median of lookups timed alone"
    if len(from_run) == len(found):
        return "median of each tool's lookups during its month run"
    whose = "its month run" if len(from_run) == 1 else "their month runs"
    return f"median of lookups timed alone ({', '.join(from_run)}: {whose})"


def measures(results):
    """Per tool: answered right, added nothing when nothing fit, mean tokens added, median lookup seconds."""
    found = {}
    for tool, label in ROWS:
        rows = run.read_jsonl(os.path.join(results, f"{tool}.jsonl"))
        answerable = [r for r in rows if r["kind"] != "none"]
        none = [r for r in rows if r["kind"] == "none"]
        found[tool] = {"label": label,
                       "right": sum(r["correct"] for r in answerable) / len(answerable),
                       "quiet": sum(not r["context"] for r in none) / len(none),
                       "tokens": statistics.mean(r["context_tokens"] + r["tool_tokens"] for r in rows),
                       **dict(zip(("seconds", "alone"), lookup_seconds(results, tool, rows))),
                       "answerable": len(answerable), "none": len(none)}
    return found


def panel(x, y, title, note, found, key, scale, show):
    label_right, bar_left, bar_width, step = x + 190, x + 214, 330, 62
    parts = [f'<text x="{x}" y="{y}" class="h">{html.escape(title)}</text>',
             f'<text x="{x}" y="{y + 32}" class="n">{html.escape(note)}</text>']
    for i, (tool, _) in enumerate(ROWS):
        top = y + 62 + i * step
        value = found[tool][key]
        mine = tool == "check"
        weight = "600" if mine else "400"
        parts += [
            f'<text x="{label_right}" y="{top + 23}" class="l" text-anchor="end" font-weight="{weight}">'
            f'{html.escape(found[tool]["label"])}</text>',
            f'<rect x="{bar_left}" y="{top}" width="{bar_width}" height="32" fill="{RULE}" rx="4"/>',
            f'<rect x="{bar_left}" y="{top}" width="{max(bar_width * value / scale, 4):.1f}" height="32" '
            f'fill="{GREEN if mine else MUTED}" rx="4"/>',
            f'<text x="{bar_left + bar_width + 18}" y="{top + 24}" class="v" font-weight="{weight}">{show(value)}</text>']
    return "\n".join(parts)


def fonts(folder):
    """The launch fonts (SIL Open Font License), fetched from the google/fonts repository on first use."""
    os.makedirs(folder, exist_ok=True)
    faces = []
    for family, path in FONTS.items():
        local = os.path.join(folder, family.replace(" ", "") + ".ttf")
        if not os.path.exists(local):
            urllib.request.urlretrieve("https://raw.githubusercontent.com/google/fonts/main/" + path, local)
        faces.append(f'@font-face {{ font-family: "{family}"; src: url("file://{local}"); font-weight: 100 900; }}')
    return "\n".join(faces)


def page(found, faces):
    first = found["check"]
    tokens = max(f["tokens"] for f in found.values())
    seconds = max(f["seconds"] for f in found.values())
    pct = lambda v: f"{100 * v:.0f}%"
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
{faces}
body {{ margin: 0; background: {PAPER}; }}
.t {{ font: 500 54px Newsreader, serif; fill: {INK}; }}
.h {{ font: 600 28px Inter, sans-serif; fill: {INK}; }}
.n {{ font: 400 20px Inter, sans-serif; fill: {INK}; opacity: .65; }}
.l {{ font: 400 24px Inter, sans-serif; fill: {INK}; }}
.v {{ font: 400 24px "JetBrains Mono", monospace; fill: {INK}; }}
.f {{ font: 400 18px "JetBrains Mono", monospace; fill: {INK}; opacity: .55; }}
</style></head><body>
<svg width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" xmlns="http://www.w3.org/2000/svg">
<rect width="{WIDTH}" height="{HEIGHT}" fill="{PAPER}"/>
<text x="80" y="92" class="t">One made-up month, {first["answerable"] + first["none"]} questions, four tools</text>
{panel(80, 170, "Answered right", f"{first['answerable']} questions with an answer in the month", found, "right", 1, pct)}
{panel(840, 170, "Added nothing when nothing fit", f"{first['none']} questions with nothing to find", found, "quiet", 1, pct)}
{panel(80, 520, "Tokens added per question", "mean, lookup plus what the agent read; fewer is cheaper", found, "tokens",
       tokens, lambda v: f"{v:,.0f}")}
{panel(840, 520, "Seconds per lookup", seconds_note(found), found, "seconds", seconds,
       lambda v: f"{v:.2f} s")}
<text x="80" y="{HEIGHT - 34}" class="f">Same answer model and judge for every tool; rivals self-hosted. Method and every row: bench/README.md</text>
</svg></body></html>"""


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--results", default=run.RESULTS)
    p.add_argument("--out", default=os.path.join(run.RESULTS, "chart.png"))
    p.add_argument("--chrome", default="google-chrome")
    p.add_argument("--fonts", default=os.path.expanduser("~/.cache/superhistory-bench/fonts"))
    args = p.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "chart.html")
        with open(source, "w", encoding="utf-8") as fh:
            fh.write(page(measures(args.results), fonts(args.fonts)))
        subprocess.run([args.chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                        "--no-default-browser-check", f"--user-data-dir={os.path.join(tmp, 'profile')}",
                        f"--window-size={WIDTH},{HEIGHT}", f"--screenshot={os.path.abspath(args.out)}", source],
                       check=True, capture_output=True, timeout=120)
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
