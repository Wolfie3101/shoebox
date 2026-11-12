# AGENTS.md

Instructions for coding agents (Codex etc.). Same rules as `CLAUDE.md`, minus Claude-specific tooling.

## What this is
Shoebox (`photosorter` CLI): sorts photos from local folders, Google Takeout and the Google Photos Picker into
year folders; sets aside duplicates, screenshots/receipts and duds; optionally picks the best N for a photo book.
Read `README.md` first for usage and the decision rules.

## Hard rules
- **Never modify, move, rename, `utime` or delete a source file.** Only copy into OUTPUT. Never change anything
  in a user's Google account.
- **Never log tokens or Picker `baseUrl`s.** `requests` errors embed URLs: catch them, log status code/type only.
  `credentials.json` and `token.json` stay out of git; the token file is written with mode 0600.
- No real photos in the repo, tests or logs. Test data is synthetic (`photosorter synth DEST`).
- Dependencies are limited to: Pillow, pillow-heif, imagehash, opencv-python-headless, numpy, tqdm,
  google-auth-oauthlib, requests, stdlib sqlite3 (dev: pytest). Ask before adding anything.

## Commands
```sh
uv sync
uv run pytest                      # ~30 s, offline (Picker API mocked)
uv run photosorter synth /tmp/demo
uv run photosorter run /tmp/out --source local:/tmp/demo/local --source takeout:/tmp/demo/takeout_zips --book 12
```

## Layout
`src/photosorter/`: `sources/` (small `Source`/`Item` interface in `sources/base.py`; add iCloud/Dropbox there),
`sidecar.py` (Takeout JSON matching), `dates.py` (date priority, local-time convention), `features.py` +
`cache.py` (per-file analysis, SQLite cache), `classify.py` (screenshot/document/dud rules; swap
`looks_like_document` for a CLIP classifier), `dedupe.py`, `book.py`, `pipeline.py`, `synth.py`.

## Working here
- Every behaviour change gets a test; run the full suite before committing.
- Commit style: `[ADD|IMP|FIX|REM] photosorter: what changed`, one logical change per commit, English.
- Not yet tried against real Google OAuth/Picker, real Takeout archives or phone HEIC files; thresholds
  (`--blur`, document rule) were tuned on synthetic images only. Expect to adjust after a real dry run.
