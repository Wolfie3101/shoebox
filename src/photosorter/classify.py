"""Screenshot, document and dud heuristics. Pure functions over cached features."""
from __future__ import annotations

SCREEN_SIZES = {
    (750, 1334), (828, 1792), (1080, 1920), (1080, 2160), (1080, 2220), (1080, 2240), (1080, 2280),
    (1080, 2340), (1080, 2400), (1170, 2532), (1179, 2556), (1284, 2778), (1290, 2796), (1125, 2436),
    (1242, 2688), (1242, 2208), (1440, 2560), (1440, 3040), (1440, 3200), (1440, 3120), (640, 1136),
    (640, 960), (720, 1280), (720, 1520), (720, 1600), (1536, 2048), (2048, 2732), (1668, 2388),
    (1366, 768), (1440, 900), (1920, 1080), (2560, 1440), (2560, 1600), (2880, 1800), (3840, 2160),
    (1280, 720), (1280, 800), (1600, 900), (1680, 1050), (3024, 1964), (3456, 2234),
}
SCREEN_SIZES |= {(h, w) for w, h in SCREEN_SIZES}

DARK_BELOW = 25.0       # mean gray (0-255)
BLOWN_ABOVE = 235.0
TINY_PIXELS = 100_000   # w*h below this (~316x316)


def is_screenshot(name: str, feats: dict) -> str | None:
    """Reason, or None.

    Deliberately stricter than "PNG or no camera EXIF": Takeout and chat apps strip EXIF from
    ordinary photos, so a missing Make/Model alone would call them screenshots. A file name with
    "screenshot", or no camera Make/Model *plus* (PNG or a common screen size), is a screenshot.
    """
    low = name.lower()
    if "screenshot" in low or "screen shot" in low or low.startswith("scr_") or "zaslonski" in low:
        return "screenshot in file name"
    if feats.get("make") or feats.get("model"):
        return None
    if feats.get("fmt") == "PNG":
        return "PNG without camera EXIF"
    if (feats.get("w"), feats.get("h")) in SCREEN_SIZES:
        return "screen dimensions without camera EXIF"
    return None


def looks_like_document(feats: dict) -> str | None:
    """Receipt / document heuristic: mostly near-white page, many text edges, almost no colour.

    Isolated here so a CLIP (or any) classifier can replace it: keep the signature
    ``feats -> reason | None``.
    """
    if (feats.get("white_frac", 0) >= 0.55 and feats.get("edge_density", 0) >= 0.03
            and feats.get("sat_mean", 1) <= 0.12):
        return "document-like (white page, text edges, low colour)"
    return None


def dud_reason(feats: dict, blur_threshold: float) -> str | None:
    if feats.get("error"):
        return feats["error"]
    w, h = feats.get("w", 0), feats.get("h", 0)
    if w * h < TINY_PIXELS:
        return f"tiny ({w}x{h})"
    b = feats.get("brightness", 128)
    if b < DARK_BELOW:
        return f"very dark ({b:.0f})"
    if b > BLOWN_ABOVE:
        return f"blown out ({b:.0f})"
    if feats.get("blur", 1e9) < blur_threshold:
        return f"blurry ({feats['blur']:.0f} < {blur_threshold:g})"
    return None
