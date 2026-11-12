"""End to end on the synthetic library: the pipeline must agree with what the generator knows."""
import csv
import hashlib
import os
from pathlib import Path

import pytest

from photosorter import pipeline
from photosorter.pipeline import B_DUD, B_DUP, B_SHOT, B_VIDEO, Config
from photosorter.sources import parse_source


def run_pipeline(data: Path, out: Path, specs, **kw):
    cfg = Config(out, [parse_source(s, out) for s in specs], workers=1, **kw)
    return pipeline.run(cfg)


@pytest.fixture(scope="module")
def result(synth, tmp_path_factory):
    data, truth = synth
    out = tmp_path_factory.mktemp("out")
    res, text = run_pipeline(data, out, [f"local:{data / 'local'}", f"takeout:{data / 'takeout_zips'}"], book=12)
    by_rel = {}
    for r in res.recs:
        o = r.item.origin
        if "!" in o:      # zip member: map back to the folder copy by member path
            member = o.split("!", 1)[1]
            by_rel["takeout_folder/" + member] = r
        else:
            by_rel[Path(o).relative_to(data).as_posix()] = r
    return res, by_rel, truth, text, out


def test_every_file_is_accounted_for(result):
    res, by_rel, truth, *_ = result
    assert len(res.recs) == truth["files"]  # JSON sidecars are not media and are not counted


def test_screenshots_receipts_duds_videos(result):
    _res, by_rel, truth, *_ = result
    assert {by_rel[p].bucket for p in truth["screenshots"]} == {B_SHOT}
    assert {by_rel[p].bucket for p in truth["receipts"]} == {B_SHOT}
    assert {by_rel[p].bucket for p in truth["duds"]} == {B_DUD}
    nondup_videos = [p for p in truth["videos"] if "copy" not in p]
    assert {by_rel[p].bucket for p in nondup_videos} == {B_VIDEO}


def test_each_duplicate_group_keeps_exactly_one(result):
    _res, by_rel, truth, *_ = result
    for g in truth["dup_groups"]:
        kept = [p for p in g if by_rel[p].bucket in ("", B_VIDEO)]
        assert len(kept) == 1, (g, [by_rel[p].bucket for p in g])


def test_edited_copy_wins_over_larger_original(result):
    _res, by_rel, truth, *_ = result
    assert by_rel[truth["edited_winner"]].bucket == ""


def test_cross_source_duplicate_kept_once_with_the_sidecar_date(result):
    _res, by_rel, truth, *_ = result
    local, takeout = truth["dup_groups"][-1]
    assert local.startswith("local/") and takeout.startswith("takeout_folder/")
    kept = by_rel[local] if by_rel[local].bucket == "" else by_rel[takeout]
    assert kept.date_kind == "sidecar"


def test_keepers_land_in_the_right_year(result):
    _res, by_rel, truth, *_ = result
    for p, year in truth["year"].items():
        r = by_rel[p]
        if r.bucket == "":
            assert r.year == year, p


def test_sidecar_matching_across_zips(result):
    _res, by_rel, truth, *_ = result
    takeout = [r for k, r in by_rel.items() if k.startswith("takeout_folder/")]
    unmatched = [k for k, r in by_rel.items() if k.startswith("takeout_folder/") and r.date_kind != "sidecar"
                 and r.bucket != B_DUP]
    assert unmatched == truth["takeout_no_sidecar"]


def test_missing_sidecar_is_reported(result):
    res, *_ = result
    assert res.unmatched_sidecars == 1


def test_book_picks_are_good_photos_spread_over_years(result):
    res, by_rel, *_ = result
    assert len(res.picks) == 12
    assert all(r.bucket == "" for r in res.picks)
    assert len({r.year for r in res.picks}) >= 4


def test_dry_run_copies_nothing_but_writes_manifest(result):
    res, _b, _t, text, out = result
    assert (out / "manifest.csv").is_file()
    assert not any((out / d).exists() for d in ("2019", B_DUP, B_SHOT, B_DUD, B_VIDEO))
    assert "DRY RUN" in text
    rows = list(csv.DictReader(open(out / "manifest.csv", encoding="utf-8")))
    assert {"source", "original", "new_path", "bucket", "reason", "blur"} <= set(rows[0])


def test_apply_copies_never_touches_originals_and_rerun_is_idempotent(synth, tmp_path):
    data, truth = synth
    local = data / "local"

    def snapshot():
        return {str(p): (p.stat().st_size, p.stat().st_mtime_ns, hashlib.md5(p.read_bytes()).hexdigest())
                for p in sorted(local.rglob("*")) if p.is_file()}

    before = snapshot()
    out = tmp_path / "out"
    _r, text = run_pipeline(data, out, [f"local:{local}"], apply=True, book=5)
    assert snapshot() == before
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and ".staging" not in p.parts
                   and not p.name.startswith((".photosorter", "manifest", "photosorter.log")))
    assert files
    assert any(f.startswith(("2018/", "2019/")) for f in files) and any(f.startswith("Photo book picks/") for f in files)
    # same command again: nothing new, nothing duplicated
    run_pipeline(data, out, [f"local:{local}"], apply=True, book=5)
    files2 = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and ".staging" not in p.parts
                    and not p.name.startswith((".photosorter", "manifest", "photosorter.log")))
    assert files2 == files


def test_output_inside_source_is_not_rescanned(synth, tmp_path):
    data, _ = synth
    out = Path(data / "local" / "_out")
    try:
        run_pipeline(data, out, [f"local:{data / 'local'}"], apply=True)
        res, _t = run_pipeline(data, out, [f"local:{data / 'local'}"])
        assert not any("_out" in r.item.origin for r in res.recs)
    finally:
        import shutil
        shutil.rmtree(out, ignore_errors=True)


def test_name_collision_gets_hash_suffix_and_nothing_is_overwritten(synth, tmp_path):
    data, _ = synth
    out = tmp_path / "out"
    res, _ = run_pipeline(data, out, [f"local:{data / 'local'}"], apply=True)
    names = [r.dest.name for r in res.recs]
    assert len(set(str(r.dest).lower() for r in res.recs)) == len(res.recs)
    assert any(len(n.rsplit("_", 1)[-1].split(".")[0]) == 8 for n in names)  # a "_<sha8>" suffix exists
