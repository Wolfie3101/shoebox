# Shoebox (`photosorter`)

Sorts photos from several sources into year folders, sets aside duplicates, screenshots, receipts and duds, and can pick the best N photos for a photo book.

> **Status: early and unproven.** The logic is covered by tests on synthetic photos and a mocked Google Picker API, but it has **not** yet been run against real Google OAuth/Picker sessions, real Google Takeout archives or HEIC files from a phone. The blur and receipt thresholds were tuned on synthetic images only. Always start with the default dry run and read `manifest.csv`. The tool only copies files and never changes your originals, but treat the results as a first draft and expect to tweak things. Bug reports and pull requests are welcome.

**Originals are never modified, moved or deleted, and nothing in your Google account is changed.** Files are only *copied* into the output folder. The default is a dry run.

## Install

Needs Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

git clone https://github.com/Wolfie3101/shoebox.git && cd shoebox
uv sync
uv run photosorter --help
```

## Usage

```
photosorter run OUTPUT --source <spec> [--source <spec> ...] [--apply] [--book N] [--threshold 6] [--blur 100]
photosorter auth-google        # one-time Google sign-in, only for gphotos-picker
photosorter synth DEST         # write ~200 synthetic photos + a fake Takeout to try the tool on
```

Sources (repeatable, mixed freely; results are merged and deduplicated **across** sources):

| Spec | Meaning |
|---|---|
| `local:/path/to/folder` | any folder, recursive (external drives, iCloud/OneDrive synced folders). The output folder is skipped if it sits inside. |
| `takeout:/path` | a Google Takeout folder, one `.zip`, or a folder holding several zips. Zips are read in place; only media files are extracted, one by one, into `OUTPUT/.staging/`. |
| `gphotos-picker` | interactive Google Photos Picker (see below). |

Default is a **dry run**: prints a summary and writes `OUTPUT/manifest.csv` (source, original path/id, new path, bucket, reason, date, scores). Add `--apply` to copy.

### Examples

macOS:

```sh
uv run photosorter run ~/Pictures/Sorted \
  --source local:~/Pictures/OldPhotos \
  --source takeout:~/Downloads/takeout \
  --source local:/Volumes/Backup/Photos \
  --book 60                      # dry run: look at ~/Pictures/Sorted/manifest.csv
uv run photosorter run ~/Pictures/Sorted --source local:~/Pictures/OldPhotos --source takeout:~/Downloads/takeout --book 60 --apply
```

Windows (PowerShell):

```powershell
uv run photosorter run D:\Sorted `
  --source local:C:\Users\me\Pictures `
  --source "takeout:C:\Users\me\Downloads\takeout-001.zip" `
  --source "takeout:C:\Users\me\Downloads\takeout-002.zip" `
  --book 60 --apply
```

Try it without your own photos:

```sh
uv run photosorter synth /tmp/demo
uv run photosorter run /tmp/demo-out --source local:/tmp/demo/local --source takeout:/tmp/demo/takeout_zips --book 12
```

## Output

```
OUTPUT/
  2019/  2020/ ...             best photos by year (copied)
  Duplicates/                  exact, near-duplicate, burst, edited-original losers
  Screenshots and receipts/
  Duds/                        blurry, very dark, blown out, tiny, unreadable
  Videos/                      .mp4 .mov .m4v .avi
  Photo book picks/<year>/     extra copies of the picks (--book N); the year folder keeps its copy too
  manifest.csv  photosorter.log  .photosorter-cache.sqlite  .staging/
```

A file name that is already taken gets a short hash (`IMG_0001_ab12cd34.jpg`); nothing is overwritten. Running `--apply` again skips files already there with identical content, so re-runs are safe. `.staging/` can be deleted after a run (it only speeds up resuming).

## How it decides

- **Date** (first that exists): Takeout JSON `photoTakenTime`, EXIF `DateTimeOriginal`, a `YYYYMMDD_HHMMSS` file name, Picker `createTime`, file mtime. All dates are converted to the **local time of the machine running the tool** (UTC sources are converted; EXIF with `OffsetTimeOriginal` is converted; EXIF without an offset is taken as written). The year folder is the year of that local time, so a photo taken just after midnight UTC on 1 January may land in the previous or next year relative to where it was taken if you run the tool in another time zone.
- **Exact duplicates:** sha256. **Near duplicates:** perceptual hash *and* difference hash both within `--threshold` bits (union-find grouping; a BK-tree is used above 20 000 photos). The kept photo is the highest resolution, then the sharpest. It also takes the most trustworthy date found among its duplicates, so a local file with only an mtime inherits the Takeout date.
- **Takeout `-edited` copy** beats its original, regardless of size.
- **Bursts:** photos with a real time of day within 3 s of each other that look alike: the sharpest stays, the rest go to Duplicates with reason `burst`. Photos dated only by mtime or a bare date never form bursts.
- **Screenshots:** `screenshot` in the file name, or no camera Make/Model **and** (PNG or a common screen size). This is deliberately stricter than "PNG or no camera EXIF": Takeout and chat apps strip EXIF from ordinary photos, and those must not be called screenshots.
- **Receipts/documents:** a heuristic (mostly near-white, many edges, almost no colour) in `classify.looks_like_document`. Replace that one function to plug in a CLIP classifier. Expect some false positives (e.g. snow scenes) and misses; check `manifest.csv`.
- **Duds:** blur score below `--blur` (variance of the Laplacian of a grayscale copy scaled to at most 512 px), mean brightness under 25 or over 235, or under 0.1 megapixels. The blur score depends on content, so tune `--blur` after a dry run.
- **Photo book (`--book N`):** from kept photos only (no duplicates, screenshots, duds, videos). Photos are clustered into events (a gap over 2 h starts a new one); the best photo of each event is a candidate (score = 70 % sharpness + 30 % exposure). N is split across years in proportion to their number of events, then across months so that no month is empty when the quota allows (with fewer picks than months, months are spread evenly).

## Google Takeout

Takeout drops or damages EXIF on some files, so dates come from the JSON next to each file. Handled: `<name>.json`, `<name>.supplemental-metadata.json`, names truncated by Google, `-edited` copies using the original's JSON, the duplicate counter (`IMG(1).jpg` ↔ `IMG.jpg(1).json`), and JSON living in a different zip than its photo. Files with no match are listed in `photosorter.log` and counted in the summary; they fall back to EXIF, file name, then mtime.

## Google Photos Picker

> The Picker needs you to select photos or albums by hand in the browser. **To process your whole library, use Takeout.** The old Library API can no longer read your library (its scopes were removed on 2025-03-31), so the Picker API is the only option for live access.

### One-time Google Cloud setup

1. Go to <https://console.cloud.google.com/> and create a project (any name).
2. **APIs & Services → Library**: search for **Photos Picker API** and enable it.
3. **APIs & Services → OAuth consent screen** (Google Auth platform): choose *External*, fill in an app name and your e-mail, and under *Test users* add your own Google account. Leave the app in *Testing* (refresh tokens for apps in testing expire after 7 days; run `auth-google` again when asked).
4. **Data access / Scopes**: add `https://www.googleapis.com/auth/photospicker.mediaitems.readonly`.
5. **Credentials → Create credentials → OAuth client ID**, application type **Desktop app**. Download the JSON.
6. Save it as `credentials.json`:
   - macOS/Linux: `~/.config/photosorter/credentials.json`
   - Windows: `%APPDATA%\photosorter\credentials.json`
   (or pass `--credentials PATH` once). Set `PHOTOSORTER_HOME` to use another folder.
7. `uv run photosorter auth-google` opens the browser; approve. The token is stored next to `credentials.json` with owner-only permissions (`0600`; Windows ignores file modes, so it relies on your user profile's own protection).

### Using it

```sh
uv run photosorter run ~/Pictures/Sorted --source gphotos-picker --source local:~/Pictures/OldPhotos
```

The tool prints a link (and opens it), you pick photos or albums in Google Photos, and it downloads them into `OUTPUT/.staging/`. Downloads are resumable (already staged items are skipped); links that expire (60 minutes) are refreshed automatically. The picking session is deleted when done. Tokens and download URLs are never written to logs.

## Adding a source (iCloud, Dropbox, ...)

A source is a class with `name` and `scan(staging) -> Iterator[Item]` (see `sources/base.py`: `Item(staged_path, original_name, source_name, taken_time, ...)`). Download into `staging` if remote, yield items, register it in `sources/__init__.py:parse_source`.

## Development

```sh
uv run pytest            # ~30 s; no network: the Picker API is mocked
```

Tests cover date resolution, sidecar matching, duplicate grouping, burst selection, the photo book, the Picker flow (mocked) and an end-to-end run on the synthetic library.

## License

MIT, see `LICENSE`.
