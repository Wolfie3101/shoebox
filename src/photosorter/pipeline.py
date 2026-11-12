"""The sorting pipeline: gather, analyse (cached, parallel), classify, plan, report, copy."""
from __future__ import annotations

import csv
import logging
import os
import shutil
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from . import classify, dates
from .book import Candidate, pick_book
from .cache import Cache
from .dedupe import burst_groups, near_duplicate_groups
from .features import compute_features_for, sha256_file
from .sources.base import Item, Source

log = logging.getLogger("photosorter")

B_DUP, B_SHOT, B_DUD, B_VIDEO, B_PICK = "Duplicates", "Screenshots and receipts", "Duds", "Videos", "Photo book picks"
MANIFEST_FIELDS = ["source", "original", "new_path", "bucket", "reason", "date", "date_kind",
                   "width", "height", "blur", "brightness", "sha256"]


@dataclass
class Config:
    output: Path
    sources: list[Source]
    apply: bool = False
    book: int = 0
    threshold: int = 6
    blur: float = 100.0
    workers: int = max(1, (os.cpu_count() or 2) - 1)
    burst_seconds: float = 3.0


@dataclass
class Rec:
    order: int
    item: Item
    feats: dict
    date: datetime
    date_kind: str
    bucket: str = ""     # "" = year folder
    reason: str = ""
    dest: Path | None = None
    skip_copy: bool = False   # identical file already at dest (re-run)

    @property
    def year(self) -> int:
        return self.date.year

    @property
    def phash(self) -> int:
        return int(self.feats["phash"], 16)

    @property
    def dhash(self) -> int:
        return int(self.feats["dhash"], 16)

    @property
    def quality(self) -> tuple:
        """Bigger is better: resolution, then sharpness; the Takeout '-edited' copy wins ties; earliest source last."""
        f = self.feats
        return (f.get("w", 0) * f.get("h", 0), f.get("blur", 0.0), self.item.is_edited, -self.order)

    def absorb_date(self, other: "Rec") -> None:
        """A kept photo takes the most trustworthy date found among its duplicates."""
        if dates.KIND_RANK[other.date_kind] < dates.KIND_RANK[self.date_kind]:
            self.date, self.date_kind = other.date, other.date_kind


@dataclass
class Result:
    recs: list[Rec] = field(default_factory=list)
    picks: list[Rec] = field(default_factory=list)
    unmatched_sidecars: int = 0
    pick_shortfall: int = 0
    manifest: Path | None = None


# -- analysis -----------------------------------------------------------------
def analyse(items: list[Item], cache: Cache, workers: int) -> list[Rec]:
    todo: list[tuple[int, tuple]] = []
    feats_by_idx: dict[int, dict] = {}
    keys: dict[int, tuple] = {}
    for i, it in enumerate(items):
        k = Cache.key(it.staged_path)
        keys[i] = k
        hit = cache.get(k)
        if hit is not None:
            feats_by_idx[i] = hit
        else:
            todo.append((i, (str(it.staged_path), it.is_video)))
    if todo:
        args = [a for _, a in todo]
        if workers > 1 and len(todo) > 16:
            with ProcessPoolExecutor(workers) as pool:
                results = pool.map(compute_features_for, args, chunksize=8)
                for (i, _), f in zip(todo, tqdm(results, total=len(todo), desc="Analysing", unit="file")):
                    feats_by_idx[i] = f
                    cache.put(keys[i], f)
        else:
            for (i, a) in tqdm(todo, desc="Analysing", unit="file", disable=len(todo) < 50):
                f = compute_features_for(a)
                feats_by_idx[i] = f
                cache.put(keys[i], f)
        cache.commit()
    recs = []
    for i, it in enumerate(items):
        f = feats_by_idx[i]
        mtime = it.staged_path.stat().st_mtime
        d, kind = dates.resolve_date(it.taken_time, it.taken_kind, f.get("exif_dt"), f.get("exif_offset"),
                                     it.original_name, mtime)
        recs.append(Rec(i, it, f, d, kind))
    return recs


# -- classification -----------------------------------------------------------
def _best(group: list[Rec], key=lambda r: r.quality) -> Rec:
    return max(group, key=key)


def _demote(keeper: Rec, losers: list[Rec], reason: str) -> None:
    for r in losers:
        r.bucket, r.reason = B_DUP, f"{reason} of {keeper.item.original_name}"
        keeper.absorb_date(r)


def classify_all(recs: list[Rec], cfg: Config) -> None:
    # videos: separate bucket; byte-identical ones are still duplicates
    videos = [r for r in recs if r.item.is_video]
    photos = [r for r in recs if not r.item.is_video]
    for group in _group_by(videos, lambda r: r.feats.get("sha256")):
        first = min(group, key=lambda r: r.order)
        _demote(first, [r for r in group if r is not first], "exact duplicate")
    for r in videos:
        if not r.bucket:
            r.bucket, r.reason = B_VIDEO, "video"

    live = []
    for r in photos:
        if r.feats.get("error"):
            r.bucket, r.reason = B_DUD, r.feats["error"]
        else:
            live.append(r)

    # 1. exact duplicates (across sources too)
    for group in _group_by(live, lambda r: r.feats["sha256"]):
        keeper = _best(group)
        _demote(keeper, [r for r in group if r is not keeper], "exact duplicate")
    live = [r for r in live if not r.bucket]

    # 2. Takeout "-edited" copy beats its original, whatever the resolution
    for group in _group_by(live, lambda r: r.item.pair_key):
        edited = [r for r in group if r.item.is_edited]
        if edited:
            keeper = _best(edited)
            _demote(keeper, [r for r in group if r is not keeper and not r.item.is_edited], "original of edited copy")
    live = [r for r in live if not r.bucket]

    # 3. screenshots and receipts are set aside before near-duplicate matching
    for r in live:
        why = classify.is_screenshot(r.item.original_name, r.feats)
        if why:
            r.bucket, r.reason = B_SHOT, f"screenshot: {why}"
            continue
        why = classify.looks_like_document(r.feats)
        if why:
            r.bucket, r.reason = B_SHOT, f"receipt/document: {why}"
    live = [r for r in live if not r.bucket]

    # 4. near duplicates: phash and dhash both within the threshold
    groups = near_duplicate_groups([r.phash for r in live], [r.dhash for r in live], cfg.threshold)
    for g in groups:
        members = [live[i] for i in g]
        keeper = _best(members)
        _demote(keeper, [r for r in members if r is not keeper], "near duplicate")
    live = [r for r in live if not r.bucket]

    # 5. bursts: <= 3 s apart and similar; keep the sharpest
    times = [r.date if r.date_kind in dates.TIMED_KINDS else None for r in live]
    for g in burst_groups(times, [r.phash for r in live], cfg.burst_seconds, max_dist=2 * cfg.threshold):
        members = [live[i] for i in g]
        keeper = max(members, key=lambda r: (r.feats["blur"], r.quality))
        for r in members:
            if r is not keeper:
                r.bucket, r.reason = B_DUP, f"burst of {keeper.item.original_name}"
                keeper.absorb_date(r)
    live = [r for r in live if not r.bucket]

    # 6. duds, the rest is a keeper
    for r in live:
        why = classify.dud_reason(r.feats, cfg.blur)
        if why:
            r.bucket, r.reason = B_DUD, why


def _group_by(recs: list[Rec], keyfn) -> list[list[Rec]]:
    d: dict = {}
    for r in recs:
        k = keyfn(r)
        if k is not None:
            d.setdefault(k, []).append(r)
    return [g for g in d.values() if len(g) > 1]


def choose_picks(recs: list[Rec], n: int) -> tuple[list[Rec], int]:
    good = [r for r in recs if not r.bucket and not r.item.is_video]
    cands = [Candidate(i, r.date, r.feats.get("blur", 0.0), r.feats.get("brightness", 128.0)) for i, r in enumerate(good)]
    idxs = pick_book(cands, n)
    return [good[i] for i in idxs], max(0, n - len(idxs))


# -- destinations -------------------------------------------------------------
def plan_destinations(recs: list[Rec], picks: list[Rec], out: Path) -> list[tuple[Rec, Path, bool]]:
    """(record, destination, skip_copy) for every file to place; picks are extra copies."""
    used: set[str] = set()
    plan: list[tuple[Rec, Path, bool]] = []

    def place(r: Rec, folder: Path) -> tuple[Path, bool]:
        name = r.item.original_name
        sha = r.feats.get("sha256") or ""
        cand = folder / name
        if cand.exists() and not os.path.islink(cand):
            try:
                if sha and sha256_file(str(cand)) == sha and str(cand).lower() not in used:
                    used.add(str(cand).lower())
                    return cand, True   # same file from an earlier run: leave it
            except OSError:
                pass
        n = 0
        while str(cand).lower() in used or cand.exists():
            stem, ext = os.path.splitext(name)
            n += 1
            tag = sha[:8] if n == 1 else f"{sha[:8]}_{n}"
            cand = folder / f"{stem}_{tag}{ext}"
            if cand.exists() and sha and sha256_file(str(cand)) == sha and str(cand).lower() not in used:
                used.add(str(cand).lower())
                return cand, True
        used.add(str(cand).lower())
        return cand, False

    for r in recs:
        folder = out / (r.bucket if r.bucket else str(r.year))
        dest, skip = place(r, folder)
        r.dest, r.skip_copy = dest, skip
        plan.append((r, dest, skip))
    for r in picks:
        dest, skip = place(r, out / B_PICK / str(r.year))
        plan.append((r, dest, skip))
    return plan


def copy_plan(plan: list[tuple[Rec, Path, bool]]) -> int:
    """Copy only; originals are never moved, changed or deleted. Returns files copied."""
    copied = 0
    for r, dest, skip in tqdm(plan, desc="Copying", unit="file"):
        if skip:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        shutil.copy2(r.item.staged_path, part)
        os.replace(part, dest)
        copied += 1
    return copied


# -- run ----------------------------------------------------------------------
def write_manifest(path: Path, plan, out: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(MANIFEST_FIELDS)
        for r, dest, skip in plan:
            is_pick = dest.is_relative_to(out / B_PICK)
            f = r.feats
            w.writerow([
                r.item.source_name, r.item.origin or str(r.item.staged_path),
                dest.relative_to(out).as_posix(), B_PICK if is_pick else (r.bucket or "Year folder"),
                "best of its time cluster" if is_pick else (r.reason or r.date_kind),
                r.date.isoformat(sep=" "), r.date_kind, f.get("w", ""), f.get("h", ""),
                f"{f['blur']:.1f}" if "blur" in f else "", f"{f['brightness']:.1f}" if "brightness" in f else "",
                f.get("sha256", ""),
            ])


def run(cfg: Config) -> tuple[Result, str]:
    out = cfg.output
    out.mkdir(parents=True, exist_ok=True)
    staging = out / ".staging"
    items: list[Item] = []
    unmatched = 0
    for src in cfg.sources:
        before = len(items)
        for it in src.scan(staging):
            if it.staged_path.is_file():
                items.append(it)
        unmatched += len(getattr(src, "unmatched", []))
        log.info("%s: %d files", src.name, len(items) - before)
    if not items:
        return Result(), "No photos or videos found in the given sources."

    cache = Cache(out / ".photosorter-cache.sqlite")
    try:
        recs = analyse(items, cache, cfg.workers)
    finally:
        cache.close()
    classify_all(recs, cfg)
    picks, short = choose_picks(recs, cfg.book) if cfg.book else ([], 0)
    plan = plan_destinations(recs, picks, out)
    manifest = out / "manifest.csv"
    write_manifest(manifest, plan, out)
    if cfg.apply:
        copy_plan(plan)
    res = Result(recs, picks, unmatched, short, manifest)
    return res, summary(res, cfg)


def summary(res: Result, cfg: Config) -> str:
    recs = res.recs
    L = [f"{'APPLIED' if cfg.apply else 'DRY RUN (nothing copied; use --apply)'}: {len(recs)} files"]
    per_src: dict[str, Counter] = {}
    for r in recs:
        c = per_src.setdefault(r.item.source_name, Counter())
        c["videos" if r.item.is_video else "photos"] += 1
    L.append("Per source:")
    for s, c in per_src.items():
        L.append(f"  {s}: {c['photos']} photos, {c['videos']} videos")
    years = Counter(r.year for r in recs if not r.bucket)
    L.append("Per year: " + (", ".join(f"{y}: {n}" for y, n in sorted(years.items())) or "none"))
    reasons = Counter(r.reason.split(" of ")[0] for r in recs if r.bucket == B_DUP)
    L.append(f"Duplicates: {sum(reasons.values())}" + (f" ({', '.join(f'{k}: {v}' for k, v in reasons.items())})" if reasons else ""))
    shots = [r for r in recs if r.bucket == B_SHOT]
    L.append(f"Screenshots and receipts: {len(shots)} "
             f"({sum(r.reason.startswith('screenshot') for r in shots)} screenshots, "
             f"{sum(r.reason.startswith('receipt') for r in shots)} receipts/documents)")
    L.append(f"Duds: {sum(r.bucket == B_DUD for r in recs)}")
    L.append(f"Videos: {sum(r.bucket == B_VIDEO for r in recs)}")
    if cfg.book:
        L.append(f"Photo book picks: {len(res.picks)} of {cfg.book}" +
                 (f" (only {len(res.picks)} good photos available)" if res.pick_shortfall else ""))
    if res.unmatched_sidecars:
        L.append(f"Takeout files with no JSON sidecar: {res.unmatched_sidecars} (dates came from EXIF/filename/mtime; see photosorter.log)")
    L.append(f"Manifest: {res.manifest}")
    return "\n".join(L)
