import io
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from photosorter import features, pipeline
from photosorter.cache import Cache
from photosorter.sources.base import Item
from photosorter.synth import save_jpeg, scene


def test_exif_date_read_from_the_exif_sub_ifd(tmp_path):
    p = tmp_path / "a.jpg"
    save_jpeg(scene(1), p, datetime(2019, 12, 31, 23, 59, 58), ("Apple", "iPhone 12"), offset="+01:00")
    f = features.compute_features(str(p))
    assert f["exif_dt"] == "2019:12:31 23:59:58" and f["exif_offset"] == "+01:00"
    assert (f["make"], f["model"]) == ("Apple", "iPhone 12")
    assert f["error"] is None and f["w"] == 640 and len(f["sha256"]) == 64


def test_orientation_is_applied_before_hashing_and_sizing(tmp_path):
    p = tmp_path / "rot.jpg"
    img = scene(2)
    ex = Image.Exif()
    ex[274] = 6   # rotate 90 on display
    img.save(p, "JPEG", exif=ex)
    f = features.compute_features(str(p))
    assert (f["w"], f["h"]) == (480, 640)
    upright = tmp_path / "up.jpg"
    img.rotate(-90, expand=True).save(upright, "JPEG")
    g = features.compute_features(str(upright))
    from photosorter.dedupe import hamming
    assert hamming(int(f["phash"], 16), int(g["phash"], 16)) <= 6


def test_heic_is_supported(tmp_path):
    pytest.importorskip("pillow_heif")
    import pillow_heif
    p = tmp_path / "a.heic"
    try:
        pillow_heif.from_pillow(scene(3)).save(str(p))
    except Exception as e:  # encoder missing in this libheif build
        pytest.skip(f"no HEIC encoder: {e}")
    f = features.compute_features(str(p))
    assert f["error"] is None and f["w"] == 640 and f["phash"]


def test_corrupt_file_is_reported_not_raised(tmp_path):
    p = tmp_path / "bad.jpg"
    p.write_bytes(b"not an image")
    f = features.compute_features(str(p))
    assert f["error"].startswith("unreadable image") and f["sha256"]


def test_missing_file_is_reported_not_raised(tmp_path):
    assert features.compute_features(str(tmp_path / "gone.jpg"))["error"].startswith("unreadable")


def test_cache_hit_skips_recompute_and_changed_file_recomputes(tmp_path, monkeypatch):
    p = tmp_path / "a.jpg"
    save_jpeg(scene(4), p)
    items = [Item(p, "a.jpg", "s")]
    cache = Cache(tmp_path / "c.sqlite")
    pipeline.analyse(items, cache, 1)
    monkeypatch.setattr(pipeline, "compute_features_for", lambda a: (_ for _ in ()).throw(AssertionError("recomputed")))
    pipeline.analyse(items, cache, 1)                      # served from cache
    save_jpeg(scene(5), p)                                 # size/mtime change: must be recomputed
    with pytest.raises(AssertionError, match="recomputed"):
        pipeline.analyse(items, cache, 1)
    cache.close()


def test_cache_survives_reopen(tmp_path):
    p = tmp_path / "a.jpg"
    save_jpeg(scene(6), p)
    c = Cache(tmp_path / "c.sqlite")
    k = Cache.key(p)
    c.put(k, {"v": features.CACHE_VERSION, "error": None, "sha256": "x"})
    c.close()
    assert Cache(tmp_path / "c.sqlite").get(k)["sha256"] == "x"
