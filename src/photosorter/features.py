"""Per-file features, computed in worker processes (top-level functions: they must pickle under spawn)."""
from __future__ import annotations

import hashlib
from pathlib import Path

CACHE_VERSION = 1
ANALYSIS_SIDE = 512     # grayscale is downscaled to this longest side before blur/edge analysis
_heif_ready = False


def _register_heif() -> None:
    global _heif_ready
    if not _heif_ready:
        import pillow_heif
        pillow_heif.register_heif_opener()
        _heif_ready = True


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:  # originals are only ever opened read-only
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _exif_fields(img) -> dict:
    out = {"exif_dt": None, "exif_offset": None, "make": None, "model": None}
    try:
        ex = img.getexif()
        sub = ex.get_ifd(0x8769)  # DateTimeOriginal lives in the Exif sub-IFD, not the top level
        dt = sub.get(36867) or sub.get(36868) or ex.get(306)
        out["exif_dt"] = dt if isinstance(dt, str) else None
        off = sub.get(36880)
        out["exif_offset"] = off if isinstance(off, str) else None
        for key, tag in (("make", 271), ("model", 272)):
            v = ex.get(tag)
            out[key] = v.strip("\x00 ").strip() if isinstance(v, str) and v.strip("\x00 ").strip() else None
    except Exception:  # damaged EXIF must not sink the file
        pass
    return out


def compute_features(path: str, is_video: bool = False) -> dict:
    """Everything needed for sorting, from one read of the file. Never raises."""
    feats: dict = {"v": CACHE_VERSION, "error": None, "is_video": is_video}
    try:
        feats["sha256"] = sha256_file(path)
    except OSError as e:
        feats["error"] = f"unreadable: {type(e).__name__}"
        return feats
    if is_video:
        return feats
    try:
        import cv2
        import imagehash
        import numpy as np
        from PIL import Image, ImageOps

        _register_heif()
        with Image.open(path) as img:
            feats["fmt"] = img.format
            feats.update(_exif_fields(img))
            img.load()
            img = ImageOps.exif_transpose(img)  # orient before hashing
            rgb = img.convert("RGB")
        feats["w"], feats["h"] = rgb.size
        feats["phash"] = str(imagehash.phash(rgb))
        feats["dhash"] = str(imagehash.dhash(rgb))

        small = rgb.copy()
        small.thumbnail((ANALYSIS_SIDE, ANALYSIS_SIDE))
        arr = np.asarray(small)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        feats["blur"] = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        feats["brightness"] = float(gray.mean())
        # document-ness metrics (see classify.looks_like_document)
        feats["white_frac"] = float((gray > 200).mean())
        feats["edge_density"] = float((cv2.Canny(gray, 100, 200) > 0).mean())
        feats["sat_mean"] = float(cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)[..., 1].mean() / 255.0)
    except Exception as e:
        feats["error"] = f"unreadable image: {type(e).__name__}"
    return feats


def compute_features_for(args: tuple[str, bool]) -> dict:
    return compute_features(*args)
