from __future__ import annotations

import hashlib
import logging
import os
import zipfile
from pathlib import Path
from typing import Iterator

from ..sidecar import SidecarIndex, pair_key
from .base import PHOTO_EXT, VIDEO_EXT, Item

log = logging.getLogger("photosorter.takeout")
MEDIA_EXT = PHOTO_EXT | VIDEO_EXT


class TakeoutSource:
    """A Google Takeout folder, or one or more .zip files (a folder holding zips works too).

    Zips are read in place; only media files are extracted, one by one, into the
    staging folder. Dates come from the JSON sidecar (photoTakenTime).
    """

    def __init__(self, path: str):
        self.path = Path(path).expanduser()
        self.name = f"takeout:{path}"
        self.unmatched: list[str] = []

    # -- listing -----------------------------------------------------------
    def _containers(self) -> tuple[list[Path], list[Path]]:
        """(zip files, loose root folders)."""
        if self.path.is_file():
            if self.path.suffix.lower() != ".zip":
                raise SystemExit(f"{self.name}: expected a folder or .zip file")
            return [self.path], []
        if not self.path.is_dir():
            raise SystemExit(f"{self.name}: not found")
        zips = sorted(p for p in self.path.rglob("*.zip"))
        return zips, [self.path]

    def scan(self, staging: Path) -> Iterator[Item]:
        zips, folders = self._containers()
        index = SidecarIndex()
        media: list[tuple[str, object]] = []  # (internal path, ("zip", Path, name) | ("file", Path))

        for z in zips:
            try:
                with zipfile.ZipFile(z) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        low = info.filename.lower()
                        if low.endswith(".json"):
                            index.add(info.filename, zf.read(info))
                        elif Path(low).suffix in MEDIA_EXT:
                            media.append((info.filename, ("zip", z, info.filename)))
            except zipfile.BadZipFile:
                log.warning("%s: not a valid zip, skipped", z.name)
        for root in folders:
            for dirpath, _dirs, files in os.walk(root):
                for fn in sorted(files):
                    p = Path(dirpath) / fn
                    rel = p.relative_to(root).as_posix()
                    if fn.lower().endswith(".json"):
                        index.add(rel, p.read_bytes())
                    elif p.suffix.lower() in MEDIA_EXT:
                        media.append((rel, ("file", p)))
        log.info("%s: %d sidecars, %d media files", self.name, index.count, len(media))

        tag = hashlib.sha1(str(self.path.resolve()).encode()).hexdigest()[:8]
        for internal, where in sorted(media, key=lambda m: m[0]):
            name = internal.rsplit("/", 1)[-1]
            sc = index.match(internal)
            if sc is None:
                self.unmatched.append(internal)
                log.warning("no sidecar for %s", internal)
            key, edited = pair_key(internal)
            if where[0] == "zip":
                _, zpath, member = where
                dest = staging / "takeout" / tag / hashlib.sha1(
                    (zpath.name + "/" + member).encode()).hexdigest()[:10] / name
                self._extract(zpath, member, dest)
                staged, origin = dest, f"{zpath.name}!{member}"
            else:
                staged, origin = where[1], str(where[1])
            yield Item(staged, name, self.name, sc.taken if sc else None,
                       "sidecar" if sc else None, origin, f"{self.name}|{key}", edited)

    @staticmethod
    def _extract(zpath: Path, member: str, dest: Path) -> None:
        with zipfile.ZipFile(zpath) as zf:
            size = zf.getinfo(member).file_size
            if dest.exists() and dest.stat().st_size == size:
                return  # resume: already staged
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_name(dest.name + ".part")
            with zf.open(member) as src, open(part, "wb") as out:
                while chunk := src.read(1 << 20):
                    out.write(chunk)
            os.replace(part, dest)
