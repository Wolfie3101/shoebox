"""Google Takeout sidecar (.json) matching.

Takeout naming varies: ``IMG.jpg.json``, ``IMG.jpg.supplemental-metadata.json``,
names truncated at ~46 chars, ``-edited`` copies using the original's JSON, and
the duplicate counter that moves behind the extension (``IMG(1).jpg`` pairs with
``IMG.jpg(1).json``). Sidecars may live in a different zip than their photo, so
all of them are indexed first (per folder) and photos are matched afterwards.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from posixpath import dirname

SUPP = ".supplemental-metadata"
_COUNTER_JSON = re.compile(r"^(.*)\((\d+)\)$")
_COUNTER_FILE = re.compile(r"^(.*)\((\d+)\)(\.[^./\\]+)$")
_EDITED = re.compile(r"^(.*)-edited(\.[^./\\]+)$", re.IGNORECASE)
MIN_PREFIX = 8


def norm_dir(path: str) -> str:
    """Folder of a member path, without a leading 'Takeout/' (zips have it, unpacked folders may not)."""
    d = dirname(path.replace("\\", "/")).strip("/")
    return d[len("Takeout/"):] if d.startswith("Takeout/") else ("" if d == "Takeout" else d)


@dataclass
class Sidecar:
    stem: str            # json file name without ".json" and without the counter
    counter: str | None  # "(1)" part
    title: str | None
    taken: datetime      # aware UTC


def parse_sidecar(json_name: str, data: bytes | str) -> Sidecar | None:
    """None for JSON without photoTakenTime (e.g. album metadata.json) or broken JSON."""
    try:
        doc = json.loads(data)
        ts = int(doc["photoTakenTime"]["timestamp"])
        taken = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, KeyError, TypeError, OSError, OverflowError):
        return None
    stem = json_name.rsplit("/", 1)[-1]
    if stem.lower().endswith(".json"):
        stem = stem[:-5]
    counter = None
    m = _COUNTER_JSON.match(stem)
    if m:
        stem, counter = m.group(1), f"({m.group(2)})"
    title = doc.get("title") if isinstance(doc, dict) else None
    return Sidecar(stem, counter, title if isinstance(title, str) else None, taken)


def split_media_name(name: str) -> tuple[str, str | None, bool]:
    """'IMG(1)-edited.jpg' -> ('IMG.jpg', '(1)', True): base name, counter, is_edited."""
    edited = False
    m = _EDITED.match(name)
    if m:
        name, edited = m.group(1) + m.group(2), True
    counter = None
    m = _COUNTER_FILE.match(name)
    if m:
        name, counter = m.group(1) + m.group(3), f"({m.group(2)})"
    return name, counter, edited


class SidecarIndex:
    def __init__(self) -> None:
        self._by_dir: dict[str, list[Sidecar]] = {}
        self.count = 0

    def add(self, json_path: str, data: bytes | str) -> None:
        sc = parse_sidecar(json_path, data)
        if sc:
            self._by_dir.setdefault(norm_dir(json_path), []).append(sc)
            self.count += 1

    def match(self, media_path: str) -> Sidecar | None:
        base, counter, _ = split_media_name(media_path.replace("\\", "/").rsplit("/", 1)[-1])
        cands = [s for s in self._by_dir.get(norm_dir(media_path), []) if s.counter == counter]
        # 1. the JSON's own title is the file name
        hit = [s for s in cands if s.title == base]
        if len(hit) == 1:
            return hit[0]
        # 2. exact file-name forms
        for want in (base, base + SUPP):
            hit = [s for s in cands if s.stem == want]
            if len(hit) == 1:
                return hit[0]
        # 3. truncated: the JSON stem is a prefix of the expected one (longest, unique wins)
        full = base + SUPP
        hit = [s for s in cands if len(s.stem) >= MIN_PREFIX and full.startswith(s.stem)]
        if hit:
            best = max(len(s.stem) for s in hit)
            top = [s for s in hit if len(s.stem) == best]
            if len(top) == 1:
                return top[0]
        # 4. the media name itself was cut: title starts with the cut name
        stem_noext = base.rsplit(".", 1)[0]
        hit = [s for s in cands if s.title and len(stem_noext) >= MIN_PREFIX and s.title.startswith(stem_noext)]
        return hit[0] if len(hit) == 1 else None


def pair_key(media_path: str) -> tuple[str, bool]:
    base, _, edited = split_media_name(media_path.replace("\\", "/").rsplit("/", 1)[-1])
    counter = split_media_name(media_path.rsplit("/", 1)[-1])[1] or ""
    return f"{norm_dir(media_path)}/{base}{counter}", edited
