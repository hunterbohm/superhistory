"""A stand-in for yt-dlp, for tests. It reads a made-up channel from state.json beside it and appends every call to
calls.jsonl. `--flat-playlist --print id URL/<tab>` prints the tab's video ids, or fails as yt-dlp does for a tab the
channel lacks. A read writes <id>.info.json and each caption file asked for by --sub-langs into the -o folder, and
skips a video it cannot read with an error, exiting 1 as yt-dlp does with --ignore-errors."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(args):
    with open(os.path.join(HERE, "calls.jsonl"), "a") as fh:
        fh.write(json.dumps(args) + "\n")
    with open(os.path.join(HERE, "state.json")) as fh:
        state = json.load(fh)
    if "--flat-playlist" in args:
        tab = args[-1].rstrip("/").rsplit("/", 1)[-1]
        if tab not in state["tabs"]:
            sys.stderr.write(f"ERROR: [youtube:tab] @sam: This channel does not have a {tab} tab\n")
            sys.exit(1)
        for vid in state["tabs"][tab]:
            print(vid)
        return
    folder = os.path.dirname(args[args.index("-o") + 1])
    wanted = args[args.index("--sub-langs") + 1].split(",") if "--sub-langs" in args else []
    failed = False
    for vid in args[args.index("--") + 1:]:
        video = state["videos"].get(vid)
        if video is None:
            sys.stderr.write(f"ERROR: [youtube] {vid}: Video unavailable\n")
            failed = True
            continue
        with open(os.path.join(folder, f"{vid}.info.json"), "w") as fh:
            json.dump(video["info"], fh)
        for lang, text in video.get("captions", {}).items():
            if lang in wanted:
                with open(os.path.join(folder, f"{vid}.{lang}.vtt"), "w") as fh:
                    fh.write(text)
    sys.exit(1 if failed else 0)


main(sys.argv[1:])
