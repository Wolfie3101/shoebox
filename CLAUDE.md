# photosorter

Wolfie's personal open-source project: a Python CLI that sorts photos from local folders,
Google Takeout and the Google Photos Picker into year folders. No Odoo, no client, no client prefix.

## Ours / never touched
- Everything here is ours. Users' **originals and their Google account are read-only**: the tool only copies
  into OUTPUT. Never add code that moves, edits, renames, `utime`s or deletes a source file.
- Real photos never go in the repo, the tests or logs. Test data is synthetic (`photosorter synth`).
- **Never log tokens or Picker `baseUrl`s** (`requests` errors embed the URL: catch, log status/type only).
  `credentials.json` / `token.json` are git-ignored; the token is written with mode 0600.

## Git
- Owner: Wolfie. Remote: github.com/Wolfie3101/shoebox (public, MIT). Repo-local identity: `Wolfie`, GitHub noreply address.
- Commit message: one line `[ADD|IMP|FIX|REM] photosorter: what`, trailers as separate `-m` lines
  (commit-gate splits the command on newlines). Stage files by name.
- Default flow is an MR/PR from a branch once a remote exists; until then commits go on `master`.

## Work
- Stack: Python 3.11+, uv. Allowed libs: Pillow, pillow-heif, imagehash, opencv-python-headless, numpy,
  tqdm, google-auth-oauthlib, requests, stdlib sqlite3. **Nothing else without asking.** Dev: pytest.
- Tests: `uv run pytest` (~30 s, no network; the Picker API is mocked). Long runs: `run-with-progress`.
- Layout: `sources/` (small `Source`/`Item` interface in `sources/base.py`), `sidecar.py` (Takeout JSON
  matching), `dates.py` (priority + time-zone convention), `features.py` (worker side, cached in
  `cache.py`), `classify.py` (screenshot/document/dud rules; `looks_like_document` is the swap point for
  CLIP), `dedupe.py`, `book.py`, `pipeline.py`, `synth.py` (test data).
- Dates are naive **local** times of the machine (see `dates.py` docstring and README).
- Picker facts were checked against developers.google.com/photos/picker on 2026-11-12; re-check before
  changing the flow (the Library API can no longer read a library).

## Real-data and throwaway
- No database. The only real data is the user's own photos: never read them from tests. Throwaway output:
  `$CLAUDE_JOB_DIR/tmp` or pytest `tmp_path`.

## Open list
- Not tried against real Google OAuth/Picker, real Takeout archives, or phone HEIC files.
- Blur/document thresholds are tuned on synthetic images only: tune on a real dry run.
- Repo is public (MIT). Nothing private (real photos, tokens, personal paths or addresses) may be committed.
