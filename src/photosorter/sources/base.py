"""The small interface every source implements.

To add a source (iCloud, Dropbox, ...): write a class with ``name`` and
``scan(staging)`` yielding ``Item`` objects, then register it in
``sources.parse_source``. Remote sources download into ``staging`` first.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Protocol

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".tif", ".tiff", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi"}


@dataclass(frozen=True)
class Item:
    staged_path: Path            # a readable file on disk (never modified)
    original_name: str           # file name as the user knows it
    source_name: str             # label used in the summary / manifest
    taken_time: datetime | None = None   # timezone-aware (UTC) when the source knows it
    taken_kind: str | None = None        # "sidecar" | "picker" (how trustworthy taken_time is)
    origin: str = ""             # original path / id, for the manifest
    pair_key: str | None = None  # Takeout: "<dir>/<name without -edited>"
    is_edited: bool = False      # Takeout "-edited" copy

    @property
    def is_video(self) -> bool:
        return Path(self.original_name).suffix.lower() in VIDEO_EXT


class Source(Protocol):
    name: str

    def scan(self, staging: Path) -> Iterator[Item]: ...
