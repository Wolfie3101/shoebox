from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from .base import PHOTO_EXT, VIDEO_EXT, Item


class LocalSource:
    """Any folder, recursive. Files are read in place (never copied or touched)."""

    def __init__(self, root: str, exclude: list[Path] | None = None):
        self.root = Path(root).expanduser()
        self.name = f"local:{root}"
        self.exclude = [p.resolve() for p in (exclude or [])]

    def scan(self, staging: Path) -> Iterator[Item]:
        if not self.root.is_dir():
            raise SystemExit(f"{self.name}: not a folder")
        for dirpath, dirnames, filenames in os.walk(self.root):
            here = Path(dirpath).resolve()
            dirnames[:] = sorted(
                d for d in dirnames
                if not d.startswith(".") and (here / d).resolve() not in self.exclude
            )
            for fn in sorted(filenames):
                if fn.startswith(".") or Path(fn).suffix.lower() not in PHOTO_EXT | VIDEO_EXT:
                    continue
                p = Path(dirpath) / fn
                yield Item(p, fn, self.name, origin=str(p))
