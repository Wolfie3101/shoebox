from __future__ import annotations

from pathlib import Path

from .base import Item, Source


def parse_source(spec: str, output: Path | None = None) -> Source:
    """'local:/path', 'takeout:/path' or 'gphotos-picker'."""
    if spec == "gphotos-picker":
        from .picker import PickerSource
        return PickerSource()
    kind, sep, rest = spec.partition(":")  # split once: Windows paths contain ':'
    if sep and rest:
        if kind == "local":
            from .local import LocalSource
            return LocalSource(rest, exclude=[output] if output else None)
        if kind == "takeout":
            from .takeout import TakeoutSource
            return TakeoutSource(rest)
    raise SystemExit(f"Unknown source {spec!r}. Use local:/path, takeout:/path or gphotos-picker.")


__all__ = ["Item", "Source", "parse_source"]
