from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="photosorter", description="Sort photos from several sources into year folders. "
                                "Originals are never modified, moved or deleted: files are only copied into OUTPUT.")
    p.add_argument("--version", action="version", version=f"photosorter {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="analyse sources and (with --apply) copy into OUTPUT",
                       description="Default is a dry run: print a summary and write OUTPUT/manifest.csv.")
    r.add_argument("output", type=Path, help="output folder (created if missing)")
    r.add_argument("--source", action="append", required=True, metavar="SPEC",
                   help="local:/folder | takeout:/folder-or.zip | gphotos-picker (repeatable)")
    r.add_argument("--apply", action="store_true", help="copy files (default: dry run)")
    r.add_argument("--book", type=int, default=0, metavar="N", help="also pick the best N photos for a photo book")
    r.add_argument("--threshold", type=int, default=6, help="near-duplicate Hamming distance (default 6)")
    r.add_argument("--blur", type=float, default=100.0, help="blur score below this is a dud (default 100)")
    r.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="parallel workers")
    r.add_argument("-v", "--verbose", action="store_true")

    a = sub.add_parser("auth-google", help="one-time Google sign-in for the Photos Picker")
    a.add_argument("--credentials", type=Path, help="path to credentials.json from your Google Cloud project")

    s = sub.add_parser("synth", help="write ~200 synthetic test photos and a fake Takeout (for trying the tool)")
    s.add_argument("dest", type=Path)
    s.add_argument("--count", type=int, default=200)
    return p


def _setup_logging(output: Path, verbose: bool) -> None:
    output.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("photosorter")
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(output / "photosorter.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO if verbose else logging.ERROR)
    root.addHandler(ch)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "auth-google":
        from . import auth
        print(f"Signed in. Token stored at {auth.authorize(args.credentials)} (owner-only permissions).")
        return 0
    if args.cmd == "synth":
        from .synth import generate
        print(generate(args.dest, args.count))
        return 0

    from .pipeline import Config, run
    from .sources import parse_source
    out = args.output.expanduser().resolve()
    _setup_logging(out, args.verbose)
    cfg = Config(out, [parse_source(s, out) for s in args.source], args.apply, args.book,
                 args.threshold, args.blur, args.workers)
    try:
        _res, text = run(cfg)
    except KeyboardInterrupt:
        print("\nInterrupted. Staged files and the cache are kept: run the same command to resume.", file=sys.stderr)
        return 130
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
