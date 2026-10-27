#!/usr/bin/env python3
"""Draw the month's head-to-head as a PNG in the launch palette.

    python3 bench/chart.py [--out bench/results/month/chart.png]

Two panels, one scale each: the share of questions with an answer that each tool's agent got right, and the share
of questions where nothing fits on which the tool added nothing. Rendered with headless Chrome.
"""
import argparse
import html
import os
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import product
import run

PAPER, INK, GREEN, MUTED, RULE = "#F2EEE5", "#17150F", "#1F6B4C", "#9A9488", "#D9D3C7"
# Tool, label. The product's own row is drawn in green; every other row in the muted ink.
ROWS = (("check", product.NAME), ("mem0", "Mem0 (open source)"), ("supermemory", "Supermemory (self-hosted)"),
        ("claude-mem", "claude-mem"), ("none", "No lookup"))
WIDTH, HEIGHT = 1600, 900
FONTS = {"Newsreader": "ofl/newsreader/Newsreader%5Bopsz,wght%5D.ttf", "Inter": "ofl/inter/Inter%5Bopsz,wght%5D.ttf",
         "JetBrains Mono": "ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf"}


def shares(results):
    found = {}
    for tool, label in ROWS:
        path = os.path.join(results, f"{tool}.jsonl")
        if not os.path.exists(path):
            continue
        rows = run.read_jsonl(path)
        answerable = [r for r in rows if r["kind"] != "none"]
        none = [r for r in rows if r["kind"] == "none"]
        found[tool] = (label, sum(r["correct"] for r in answerable) / len(answerable),
                       sum(not r["context"] for r in none) / len(none), len(answerable), len(none))
    return found


def panel(x, title, note, values, key):
    bar_left, bar_width, top, step = x + 300, 320, 250, 96
    parts = [f'<text x="{x}" y="170" class="h">{html.escape(title)}</text>',
             f'<text x="{x}" y="206" class="n">{html.escape(note)}</text>']
    for i, (tool, row) in enumerate(values):
        y = top + i * step
        value = row[key]
        color = GREEN if tool == "check" else MUTED
        weight = "600" if tool == "check" else "400"
        parts += [
            f'<text x="{bar_left - 24}" y="{y + 30}" class="l" text-anchor="end" font-weight="{weight}">'
            f'{html.escape(row[0])}</text>',
            f'<rect x="{bar_left}" y="{y + 6}" width="{bar_width}" height="36" fill="{RULE}" rx="4"/>',
            f'<rect x="{bar_left}" y="{y + 6}" width="{max(bar_width * value, 4):.1f}" height="36" fill="{color}" '
            f'rx="4"/>',
            f'<text x="{bar_left + bar_width + 20}" y="{y + 32}" class="v" font-weight="{weight}">'
            f'{100 * value:.0f}%</text>']
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
    values = [(tool, found[tool]) for tool, _ in ROWS if tool in found]
    answerable, none = values[0][1][3], values[0][1][4]
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>
{faces}
body {{ margin: 0; background: {PAPER}; }}
.t {{ font: 500 60px Newsreader, serif; fill: {INK}; }}
.s {{ font: 400 26px Inter, sans-serif; fill: {INK}; opacity: .72; }}
.h {{ font: 600 30px Inter, sans-serif; fill: {INK}; }}
.n {{ font: 400 22px Inter, sans-serif; fill: {INK}; opacity: .65; }}
.l {{ font: 400 26px Inter, sans-serif; fill: {INK}; }}
.v {{ font: 400 28px "JetBrains Mono", monospace; fill: {INK}; }}
.f {{ font: 400 20px "JetBrains Mono", monospace; fill: {INK}; opacity: .55; }}
</style></head><body>
<svg width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" xmlns="http://www.w3.org/2000/svg">
<rect width="{WIDTH}" height="{HEIGHT}" fill="{PAPER}"/>
<text x="80" y="100" class="t">One made-up month, asked {answerable + none} times</text>
{panel(80, "Answered right", f"{answerable} questions with an answer in the month", values, 1)}
{panel(840, "Added nothing when nothing fit", f"{none} questions with no answer in the month", values, 2)}
<text x="80" y="{HEIGHT - 50}" class="f">Same answer model and judge for every tool. Method, versions and every row: bench/README.md</text>
</svg></body></html>"""


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--results", default=run.RESULTS)
    p.add_argument("--out", default=os.path.join(run.RESULTS, "chart.png"))
    p.add_argument("--chrome", default="google-chrome")
    p.add_argument("--fonts", default=os.path.expanduser("~/.cache/kb-bench/fonts"))
    args = p.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "chart.html")
        with open(source, "w", encoding="utf-8") as fh:
            fh.write(page(shares(args.results), fonts(args.fonts)))
        subprocess.run([args.chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                        "--no-default-browser-check", f"--user-data-dir={os.path.join(tmp, 'profile')}",
                        f"--window-size={WIDTH},{HEIGHT}",
                        f"--screenshot={os.path.abspath(args.out)}", source],
                       check=True, capture_output=True, timeout=120)
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
