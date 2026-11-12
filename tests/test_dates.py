from datetime import datetime, timedelta, timezone

import pytest

from photosorter import dates
from photosorter.dates import resolve_date

UTC = timezone.utc
CEST = timezone(timedelta(hours=2))


@pytest.fixture(autouse=True)
def tz(monkeypatch):
    monkeypatch.setattr(dates, "LOCAL_TZ", CEST)


def test_sidecar_beats_exif_and_filename():
    t = datetime(2019, 6, 1, 10, 0, tzinfo=UTC)
    d, kind = resolve_date(t, "sidecar", "2001:01:01 00:00:00", None, "IMG_20180101_000000.jpg", 0)
    assert (d, kind) == (datetime(2019, 6, 1, 12, 0), "sidecar")


def test_exif_beats_filename_and_picker():
    t = datetime(2019, 6, 1, tzinfo=UTC)
    d, kind = resolve_date(t, "picker", "2020:02:03 04:05:06", None, "IMG_20180101_000000.jpg", 0)
    assert (d, kind) == (datetime(2020, 2, 3, 4, 5, 6), "exif")


def test_filename_beats_picker_and_mtime():
    t = datetime(2019, 6, 1, tzinfo=UTC)
    d, kind = resolve_date(t, "picker", None, None, "IMG_20180101_123456.jpg", 0)
    assert (d, kind) == (datetime(2018, 1, 1, 12, 34, 56), "filename")


def test_picker_beats_mtime():
    t = datetime(2019, 6, 1, 10, 0, tzinfo=UTC)
    d, kind = resolve_date(t, "picker", None, None, "x.jpg", 0)
    assert (d, kind) == (datetime(2019, 6, 1, 12, 0), "picker")


def test_mtime_is_last_resort():
    d, kind = resolve_date(None, None, None, None, "x.jpg", 86400 * 365)
    assert kind == "mtime" and d.year == 1971


def test_new_year_boundary_uses_local_time_for_utc_sources():
    # 23:30 UTC on Dec 31 is already Jan 1 at +02:00 (and the reverse for EXIF offsets)
    t = datetime(2019, 12, 31, 23, 30, tzinfo=UTC)
    assert resolve_date(t, "sidecar", None, None, "x.jpg", 0)[0].year == 2020


def test_exif_offset_converted_to_local_without_offset_taken_as_is():
    assert dates.parse_exif("2019:12:31 23:30:00") == datetime(2019, 12, 31, 23, 30)
    # taken at +00:00 = 01:30 local at +02:00 the next day
    assert dates.parse_exif("2019:12:31 23:30:00", "+00:00") == datetime(2020, 1, 1, 1, 30)


@pytest.mark.parametrize("bad", ["0000:00:00 00:00:00", "garbage", "", None, "2019:13:40 00:00:00"])
def test_broken_exif_dates_fall_through(bad):
    assert dates.parse_exif(bad) is None
    assert resolve_date(None, None, bad, None, "x.jpg", 0)[1] == "mtime"


@pytest.mark.parametrize("name,expected", [
    ("IMG_20190704_153012.jpg", datetime(2019, 7, 4, 15, 30, 12)),
    ("PXL_20210101_235959123.jpg", datetime(2021, 1, 1, 23, 59, 59)),
    ("2019-07-04 15.30.12.jpg", datetime(2019, 7, 4, 15, 30, 12)),
    ("20190704_153012.mp4", datetime(2019, 7, 4, 15, 30, 12)),
])
def test_filename_patterns(name, expected):
    r = dates.parse_filename(name)
    assert (r[0] if r else None) == expected


def test_date_only_filename_is_not_a_timed_kind():
    d, kind = resolve_date(None, None, None, None, "IMG-20190704-WA0001.jpg", 0)
    assert (d, kind) == (datetime(2019, 7, 4), "filename_day")
    assert kind not in dates.TIMED_KINDS


def test_impossible_filename_date_ignored():
    assert dates.parse_filename("IMG_20191345_250000.jpg") is None
