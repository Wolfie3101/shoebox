"""Which photo of a group is kept, the screenshot/receipt/dud rules and the photo book."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from photosorter import classify, pipeline
from photosorter.book import Candidate, allocate, pick_book
from photosorter.pipeline import B_DUD, B_DUP, B_SHOT, Config, Rec
from photosorter.sources.base import Item

D = datetime(2020, 5, 5, 12, 0, 0)


def rec(order, name, w=1000, h=800, blur=300.0, ph=0, dh=0, when=D, kind="exif", source="s", edited=False,
        sha=None, pair=None, brightness=128.0, **extra):
    feats = {"sha256": sha or f"sha{order}", "w": w, "h": h, "blur": blur, "brightness": brightness,
             "phash": f"{ph:016x}", "dhash": f"{dh:016x}", "make": "Canon", "fmt": "JPEG",
             "white_frac": 0.1, "edge_density": 0.1, "sat_mean": 0.4, **extra}
    it = Item(Path(name), name, source, origin=name, pair_key=pair, is_edited=edited)
    return Rec(order, it, feats, when, kind)


def classify_recs(recs, **kw):
    pipeline.classify_all(recs, Config(Path("."), [], **kw))
    return {r.item.original_name: r for r in recs}


def test_exact_duplicate_keeps_highest_resolution_then_sharpest():
    a = rec(0, "a.jpg", w=500, h=500, sha="X")
    b = rec(1, "b.jpg", w=2000, h=1500, sha="X")
    r = classify_recs([a, b])
    assert r["b.jpg"].bucket == "" and r["a.jpg"].bucket == B_DUP


def test_near_duplicate_across_sources_kept_once_best_resolution():
    local = rec(0, "l.jpg", w=800, h=600, ph=0xFFFF, dh=0xFFFF, source="local")
    take = rec(1, "t.jpg", w=4000, h=3000, ph=0xFFFE, dh=0xFFFF, source="takeout")
    r = classify_recs([local, take])
    assert (r["t.jpg"].bucket, r["l.jpg"].bucket) == ("", B_DUP)
    assert "near duplicate" in r["l.jpg"].reason


def test_same_resolution_prefers_sharper_then_first_source():
    a = rec(0, "a.jpg", blur=200, ph=7, dh=7)
    b = rec(1, "b.jpg", blur=400, ph=7, dh=7, sha="other")
    assert classify_recs([a, b])["b.jpg"].bucket == ""


def test_kept_photo_inherits_the_most_trustworthy_date_of_its_duplicates():
    mtime_only = rec(0, "l.jpg", w=4000, h=3000, ph=5, dh=5, kind="mtime", when=datetime(2024, 1, 1))
    sidecar = rec(1, "t.jpg", w=800, h=600, ph=5, dh=5, kind="sidecar", when=datetime(2019, 7, 4))
    r = classify_recs([mtime_only, sidecar])
    assert r["l.jpg"].bucket == "" and (r["l.jpg"].date, r["l.jpg"].date_kind) == (datetime(2019, 7, 4), "sidecar")


def test_edited_copy_wins_even_when_smaller_and_blurrier():
    orig = rec(0, "IMG.jpg", w=4000, h=3000, blur=900, ph=1, dh=1, pair="d/IMG.jpg")
    edit = rec(1, "IMG-edited.jpg", w=800, h=600, blur=150, ph=0xFFFFFFFF, dh=0xFFFFFFFF, edited=True, pair="d/IMG.jpg")
    r = classify_recs([orig, edit])
    assert r["IMG-edited.jpg"].bucket == "" and r["IMG.jpg"].bucket == B_DUP


def test_burst_keeps_the_sharpest_and_labels_the_rest_burst():
    # visually similar (distance 9: beyond the near-duplicate threshold of 6, within the burst limit of 12)
    shots = [rec(i, f"b{i}.jpg", blur=blur, ph=(1 << 9) - 1 if i else 0, dh=(1 << 9) - 1 if i else 0,
                 when=D + timedelta(seconds=i)) for i, blur in enumerate([150, 500, 250])]
    shots[2].feats["phash"] = shots[2].feats["dhash"] = f"{(1 << 9) - 1:016x}"
    r = classify_recs(shots)
    assert r["b1.jpg"].bucket == "" and r["b0.jpg"].bucket == B_DUP == r["b2.jpg"].bucket
    assert r["b0.jpg"].reason.startswith("burst")


def test_similar_photos_far_apart_in_time_are_not_a_burst():
    a = rec(0, "a.jpg", ph=0, dh=0)
    b = rec(1, "b.jpg", ph=(1 << 9) - 1, dh=(1 << 9) - 1, when=D + timedelta(minutes=5))
    assert {r.bucket for r in classify_recs([a, b]).values()} == {""}


def test_mtime_dates_never_form_bursts():
    a = rec(0, "a.jpg", ph=0, dh=0, kind="mtime")
    b = rec(1, "b.jpg", ph=(1 << 9) - 1, dh=(1 << 9) - 1, kind="mtime", when=D + timedelta(seconds=1))
    assert {r.bucket for r in classify_recs([a, b]).values()} == {""}


def test_identical_videos_are_duplicates_distinct_videos_are_kept():
    def vid(order, name, sha):
        it = Item(Path(name), name, "s", origin=name)
        return Rec(order, it, {"sha256": sha}, D, "mtime")
    r = classify_recs([vid(0, "a.mp4", "x"), vid(1, "b.mp4", "x"), vid(2, "c.mp4", "y")])
    assert [r[n].bucket for n in ("a.mp4", "b.mp4", "c.mp4")] == ["Videos", B_DUP, "Videos"]


def test_unreadable_image_goes_to_duds():
    r = classify_recs([rec(0, "bad.jpg", error="unreadable image: OSError")])
    assert r["bad.jpg"].bucket == B_DUD


# -- screenshot / document / dud rules ---------------------------------------------------
@pytest.mark.parametrize("name,feats,expected", [
    ("Screenshot_2020.jpg", {"make": "Canon"}, True),
    ("IMG_1.png", {"fmt": "PNG"}, True),
    ("IMG_1.png", {"fmt": "PNG", "make": "Apple"}, False),        # a PNG from a camera is a photo
    ("IMG_2.jpg", {"w": 1170, "h": 2532}, True),
    ("IMG_2.jpg", {"w": 1170, "h": 2532, "model": "iPhone"}, False),
    ("IMG_3.jpg", {"w": 4032, "h": 3024}, False),                 # EXIF-stripped ordinary photo stays a photo
    ("IMG_3.jpg", {"w": 1234, "h": 777, "fmt": "JPEG"}, False),
])
def test_screenshot_rules(name, feats, expected):
    assert bool(classify.is_screenshot(name, feats)) is expected


def test_document_heuristic_and_negative():
    page = {"white_frac": 0.8, "edge_density": 0.06, "sat_mean": 0.03}
    assert classify.looks_like_document(page)
    assert not classify.looks_like_document(page | {"sat_mean": 0.4})        # colourful: a photo
    assert not classify.looks_like_document(page | {"edge_density": 0.002})  # blank white: not a document
    assert not classify.looks_like_document(page | {"white_frac": 0.2})      # dark background


@pytest.mark.parametrize("feats,why", [
    ({"w": 800, "h": 600, "brightness": 128, "blur": 50}, "blurry"),
    ({"w": 800, "h": 600, "brightness": 10, "blur": 500}, "dark"),
    ({"w": 800, "h": 600, "brightness": 250, "blur": 500}, "blown"),
    ({"w": 100, "h": 100, "brightness": 128, "blur": 500}, "tiny"),
    ({"w": 800, "h": 600, "brightness": 128, "blur": 500}, None),
])
def test_dud_rules(feats, why):
    r = classify.dud_reason(feats, 100)
    assert (why in r) if why else r is None


def test_blur_threshold_is_configurable():
    f = {"w": 800, "h": 600, "brightness": 128, "blur": 150}
    assert classify.dud_reason(f, 100) is None and classify.dud_reason(f, 200)


# -- photo book --------------------------------------------------------------------------
def cands(spec):
    """spec: {(year, month): n_photos_a_day_apart}"""
    out, i = [], 0
    for (y, m), n in spec.items():
        for k in range(n):
            out.append(Candidate(i, datetime(y, m, 1 + k, 12), 100.0 + i, 128.0))
            i += 1
    return out


def test_book_gives_every_month_a_pick_when_quota_allows():
    c = cands({(2019, m): 4 for m in range(1, 13)} | {(2020, m): 4 for m in range(1, 7)})
    picks = {c[i].date for i in pick_book(c, 24)}
    assert len(picks) == 24
    assert {d.month for d in picks if d.year == 2019} == set(range(1, 13))
    assert {d.month for d in picks if d.year == 2020} == set(range(1, 7))


def test_book_never_exceeds_n_or_supply_and_has_no_duplicates():
    c = cands({(2018, 1): 3, (2019, 6): 2})
    assert len(pick_book(c, 3)) == 3 and len(set(pick_book(c, 3))) == 3
    assert sorted(pick_book(c, 50)) == sorted({i for i in pick_book(c, 50)}) and len(pick_book(c, 50)) <= 5
    assert pick_book(c, 0) == [] and pick_book([], 5) == []


def test_book_one_best_per_time_cluster():
    shots = [Candidate(i, D + timedelta(minutes=i), 10.0 * (i + 1), 128.0) for i in range(5)]   # one event
    assert pick_book(shots, 1) == [4]                                                              # sharpest


def test_book_spreads_when_fewer_picks_than_months():
    c = cands({(2019, m): 1 for m in range(1, 13)})
    months = sorted(c[i].date.month for i in pick_book(c, 3))
    assert len(months) == 3 and months[0] <= 3 and months[-1] >= 10


def test_book_exposure_matters():
    good = Candidate(0, D, 300.0, 128.0)
    dark = Candidate(1, D + timedelta(minutes=1), 300.0, 40.0)
    assert pick_book([good, dark], 1) == [0]


def test_allocate_respects_caps_and_total():
    assert sum(allocate(10, [1, 1, 1], [2, 2, 2])) == 6
    a = allocate(10, [8, 1, 1], [3, 9, 9])
    assert a[0] == 3 and sum(a) == 10
