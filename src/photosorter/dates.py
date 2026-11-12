"""Date resolution.

Convention: every resolved date is a *naive local time* of the machine running
the tool (``LOCAL_TZ`` = None means the system zone). UTC sources (Takeout
sidecar, Picker createTime, file mtime) are converted to local. EXIF times are
naive wall-clock: with ``OffsetTimeOriginal`` they are converted to local too,
otherwise taken as is. The year folder is the year of that local time.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone, tzinfo

LOCAL_TZ: tzinfo | None = None  # tests override this

# Lower rank = more trusted.
KIND_RANK = {"sidecar": 0, "exif": 1, "filename": 2, "filename_day": 3, "picker": 4, "mtime": 5}
# Kinds that carry a real time of day (usable for burst detection).
TIMED_KINDS = {"sidecar", "exif", "filename", "picker"}

_FULL = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_]?([01]\d)[-_]?([0-3]\d)[ _T.-]?([0-2]\d)[.:_-]?([0-5]\d)[.:_-]?([0-5]\d)")
_DAY = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_]?([01]\d)[-_]?([0-3]\d)(?!\d)")


def utc_to_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ).replace(tzinfo=None)


def parse_rfc3339(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def parse_exif(dt: str | None, offset: str | None = None) -> datetime | None:
    """EXIF 'YYYY:MM:DD HH:MM:SS' (+ optional '+02:00') -> naive local."""
    if not dt:
        return None
    try:
        naive = datetime.strptime(dt.strip().strip("\x00"), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    if naive.year < 1900:
        return None
    if offset:
        m = re.fullmatch(r"\s*([+-])(\d\d):?(\d\d)\s*", offset.strip("\x00"))
        if m:
            delta = timedelta(hours=int(m[2]), minutes=int(m[3]))
            tz = timezone(delta if m[1] == "+" else -delta)
            return naive.replace(tzinfo=tz).astimezone(LOCAL_TZ).replace(tzinfo=None)
    return naive


def parse_filename(name: str) -> tuple[datetime, str] | None:
    """YYYYMMDD_HHMMSS in a file name -> (naive datetime, 'filename'); date only -> 'filename_day'."""
    for rx, kind in ((_FULL, "filename"), (_DAY, "filename_day")):
        m = rx.search(name)
        if m:
            try:
                parts = [int(g) for g in m.groups()]
                return datetime(*parts), kind
            except ValueError:
                continue
    return None


def resolve_date(taken_time, taken_kind, exif_dt, exif_offset, filename, mtime) -> tuple[datetime, str]:
    """Priority: Takeout sidecar, EXIF DateTimeOriginal, filename, Picker createTime, mtime."""
    if taken_time is not None and taken_kind == "sidecar":
        return utc_to_local(taken_time), "sidecar"
    d = parse_exif(exif_dt, exif_offset)
    if d:
        return d, "exif"
    f = parse_filename(filename)
    if f:
        return f
    if taken_time is not None and taken_kind == "picker":
        return utc_to_local(taken_time), "picker"
    return utc_to_local(datetime.fromtimestamp(mtime, tz=timezone.utc)), "mtime"
