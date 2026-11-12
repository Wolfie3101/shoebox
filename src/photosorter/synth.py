"""Synthetic test data: ~200 images plus a fake Google Takeout (folder and zips) with sidecar variants.

``generate(dest)`` writes ``dest/local``, ``dest/takeout_folder``, ``dest/takeout_zips`` and
``dest/truth.json`` (what the pipeline should decide). Everything is deterministic for a given seed.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

CAMERAS = [("Apple", "iPhone 12"), ("Canon", "Canon EOS R"), ("samsung", "SM-G991B")]


def scene(seed: int, size=(640, 480)) -> Image.Image:
    """A sharp, textured picture; different seeds give visibly different structure."""
    rng = np.random.default_rng(seed)
    w, h = size
    c1, c2 = rng.integers(20, 235, 3), rng.integers(20, 235, 3)
    t = np.linspace(0, 1, h)[:, None, None]
    arr = (c1 * (1 - t) + c2 * t) * np.ones((h, w, 3))
    img = Image.fromarray(arr.astype(np.uint8))
    d = ImageDraw.Draw(img)
    for _ in range(int(rng.integers(7, 12))):
        x0, y0 = int(rng.integers(0, w - 60)), int(rng.integers(0, h - 60))
        x1, y1 = x0 + int(rng.integers(40, w // 2)), y0 + int(rng.integers(40, h // 2))
        col = tuple(int(v) for v in rng.integers(0, 255, 3))
        (d.ellipse if rng.random() < 0.5 else d.rectangle)([x0, y0, x1, y1], fill=col)
    for _ in range(40):  # thin high-contrast lines: real edges that survive a resize
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        d.line([x, y, x + int(rng.integers(-120, 120)), y + int(rng.integers(-120, 120))],
               fill=tuple(int(v) for v in rng.integers(0, 255, 3)), width=2)
    noise = rng.normal(0, 4, (h, w, 1))
    return Image.fromarray(np.clip(np.asarray(img) + noise, 0, 255).astype(np.uint8))


def shifted(img: Image.Image, dx: int, dy: int) -> Image.Image:
    return img.transform(img.size, Image.AFFINE, (1, 0, -dx, 0, 1, -dy), resample=Image.BILINEAR)


def screenshot(seed: int, size=(1080, 1920)) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("RGB", size, (30, 30, 36) if seed % 2 else (245, 245, 247))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, size[0], 120], fill=(rng.randint(0, 80), rng.randint(60, 160), 220))
    for i in range(9):
        y = 200 + i * 190
        d.rounded_rectangle([60, y, size[0] - 60, y + 150], 24, fill=tuple(rng.randint(40, 230) for _ in range(3)))
        d.rectangle([100, y + 50, 100 + rng.randint(200, 700), y + 70], fill=(255, 255, 255))
    return img


def receipt(seed: int, size=(600, 900)) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("RGB", size, (249, 249, 247))
    d = ImageDraw.Draw(img)
    for row in range(38):
        x, y = 40, 40 + row * 22
        while x < size[0] - 60:
            wd = rng.randint(5, 16)
            d.rectangle([x, y, x + wd, y + 9], fill=(20, 20, 20))
            x += wd + rng.randint(3, 9)
    return img


def save_jpeg(img, path: Path, when: datetime | None = None, camera=None, offset: str | None = None, quality=90, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    kw = {}
    if when or camera:
        ex = Image.Exif()
        if camera:
            ex[271], ex[272] = camera
        if when:
            sub = ex.get_ifd(0x8769)
            sub[36867] = when.strftime("%Y:%m:%d %H:%M:%S")
            if offset:
                sub[36880] = offset
        kw["exif"] = ex
    img.convert("RGB").save(path, "JPEG", quality=quality, **kw)
    if mtime:
        os.utime(path, (mtime.timestamp(), mtime.timestamp()))


def sidecar_json(title: str, when_utc: datetime) -> str:
    return json.dumps({"title": title, "photoTakenTime": {"timestamp": str(int(when_utc.timestamp())), "formatted": "x"}})


def generate(dest: Path, count: int = 200, seed: int = 7) -> str:
    rng = random.Random(seed)
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest} is not empty; choose a new folder")
    local, tf = dest / "local", dest / "takeout_folder" / "Takeout" / "Google Photos"
    truth: dict = {"year": {}, "dup_groups": [], "screenshots": [], "receipts": [], "duds": [], "videos": [],
                   "takeout_no_sidecar": [], "files": 0}
    n_good = max(10, int(count * 0.5))
    sid = [1000]

    def new_scene(**kw):
        sid[0] += 1
        return scene(sid[0], **kw)

    def rand_when(year=None):
        y = year or rng.choice([2018, 2019, 2020, 2021])
        return datetime(y, rng.randint(1, 12), rng.randint(1, 28), rng.randint(8, 20), rng.randint(0, 59), rng.randint(0, 59))

    def rel(p: Path) -> str:
        return p.relative_to(dest).as_posix()

    # -- local, EXIF-dated good photos (some named with a date, some with a repeated name) --
    good: list[tuple[Path, Image.Image, datetime]] = []
    for i in range(n_good):
        when, img, cam = rand_when(), new_scene(), rng.choice(CAMERAS)
        folder = local / f"trip{i % 4}"
        name = f"DSC_{i % 5:04d}.jpg" if i % 9 == 0 else f"IMG_{when:%Y%m%d_%H%M%S}.jpg"  # repeated names: collision test
        p = folder / name
        if p.exists():
            p = folder / f"DSC_{i:04d}x.jpg"
        save_jpeg(img, p, when, cam)
        good.append((p, img, when))
        truth["year"][rel(p)] = when.year
    # no EXIF, no date in name: only mtime says when
    for i in range(5):
        p, when = local / "plain" / f"photo_{i}.jpg", datetime(2017, 3 + i, 10, 12, 0, 0)
        save_jpeg(new_scene(), p, mtime=when)
        truth["year"][rel(p)] = 2017
    # exact duplicates (same bytes, other folder)
    for k, (p, _img, _w) in enumerate(good[:15]):
        q = local / "backup" / p.name
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, q)
        truth["dup_groups"].append([rel(p), rel(q)])
    # near duplicates: smaller, recompressed (e.g. WhatsApp)
    for p, img, when in good[15:30]:
        q = local / "whatsapp" / f"WA_{p.stem}.jpg"
        save_jpeg(img.resize((320, 240)), q, quality=60)
        truth["dup_groups"].append([rel(p), rel(q)])
    # bursts: three frames one second apart, only the first is sharp
    for b in range(5):
        base, when, frames = new_scene(), rand_when(2019), []
        for f in range(3):
            fr = shifted(base, f * 3, f * 2)
            if f:
                fr = fr.filter(ImageFilter.GaussianBlur(1.2 * f))
            p = local / "burst" / f"IMG_{when + timedelta(seconds=f):%Y%m%d_%H%M%S}.jpg"
            save_jpeg(fr, p, when + timedelta(seconds=f), CAMERAS[0])
            frames.append(rel(p))
        truth["dup_groups"].append(frames)
        truth["year"][frames[0]] = 2019
    # screenshots (PNG, no EXIF; a few JPEGs named Screenshot_...)
    for i in range(12):
        p = local / "screens" / f"IMG_{i:04d}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        screenshot(i).save(p)
        truth["screenshots"].append(rel(p))
    for i in range(4):
        p = local / "screens" / f"Screenshot_2020{i + 1:02d}15-101010.jpg"
        save_jpeg(screenshot(40 + i, (720, 1280)), p)
        truth["screenshots"].append(rel(p))
    for i in range(8):
        p = local / "docs" / f"receipt_{i}.jpg"
        save_jpeg(receipt(i), p, rand_when(), CAMERAS[1])
        truth["receipts"].append(rel(p))
    # duds
    for i in range(4):
        p = local / "duds" / f"blurry_{i}.jpg"
        save_jpeg(new_scene().filter(ImageFilter.GaussianBlur(9)), p, rand_when(), CAMERAS[2])
        truth["duds"].append(rel(p))
    for i in range(3):
        p = local / "duds" / f"dark_{i}.jpg"
        save_jpeg(Image.eval(new_scene(), lambda v: v // 20), p, rand_when(), CAMERAS[2])
        truth["duds"].append(rel(p))
    for i in range(2):
        p = local / "duds" / f"white_{i}.jpg"
        save_jpeg(Image.blend(new_scene(), Image.new("RGB", (640, 480), "white"), 0.95), p, rand_when(), CAMERAS[2])
        truth["duds"].append(rel(p))
    p = local / "duds" / "tiny.jpg"
    save_jpeg(new_scene().resize((64, 64)), p, rand_when(), CAMERAS[2])
    truth["duds"].append(rel(p))
    # videos (placeholder bytes; one exact copy)
    vids = []
    for i in range(5):
        p = local / "videos" / f"VID_{i}.mp4"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(rng.randbytes(4096))
        vids.append(p)
        truth["videos"].append(rel(p))
    shutil.copy2(vids[0], local / "videos" / "VID_0_copy.mp4")
    truth["videos"].append(rel(local / "videos" / "VID_0_copy.mp4"))
    truth["dup_groups"].append([rel(vids[0]), rel(local / "videos" / "VID_0_copy.mp4")])

    # -- fake Takeout: EXIF stripped, dates only in the sidecars (UTC) --
    jsons: dict[str, str] = {}   # path relative to "Google Photos" -> json text
    media: dict[str, Path] = {}
    album = "Photos from 2019"

    def tput(name: str, img: Image.Image, json_name: str | None, title: str, when: datetime, quality=90, album=album):
        p = tf / album / name
        save_jpeg(img, p, quality=quality)
        media[f"{album}/{name}"] = p
        if json_name:
            jp = tf / album / json_name
            jp.write_text(sidecar_json(title, when.replace(tzinfo=timezone.utc)))
            jsons[f"{album}/{json_name}"] = jp.read_text()
        return p

    t = 0
    def nxt():
        nonlocal t
        t += 1
        return datetime(2019, 1 + t % 12, 1 + t, 11, 0, 0)
    for i in range(1, 9):   # plain and supplemental-metadata names
        when = nxt()
        suffix = ".json" if i % 2 else ".supplemental-metadata.json"
        p = tput(f"IMG_T{i:03d}.jpg", new_scene(), f"IMG_T{i:03d}.jpg{suffix}", f"IMG_T{i:03d}.jpg", when)
        truth["year"][rel(p)] = 2019
    long_name = "a_very_long_photo_name_that_google_truncates_2019.jpg"
    when = nxt()
    p = tput(long_name, new_scene(), None, long_name, when)   # json name cut at 46 chars
    jp = tf / album / (f"{long_name}.supplemental-metadata"[:46 - 5] + ".json")
    jp.write_text(sidecar_json(long_name, when.replace(tzinfo=timezone.utc)))
    jsons[f"{album}/{jp.name}"] = jp.read_text()
    truth["year"][rel(p)] = 2019
    # edited copy (smaller on purpose): the edited one must win
    when, base = nxt(), new_scene()
    po = tput("IMG_E001.jpg", base, "IMG_E001.jpg.supplemental-metadata.json", "IMG_E001.jpg", when)
    pe = tput("IMG_E001-edited.jpg", base.resize((480, 360)), None, "", when)
    truth["dup_groups"].append([rel(po), rel(pe)])
    truth["edited_winner"] = rel(pe)
    truth["year"][rel(pe)] = 2019
    # duplicate counter: IMG_C001(1).jpg <-> IMG_C001.jpg(1).json
    w1, w2 = nxt(), nxt()
    tput("IMG_C001.jpg", new_scene(), "IMG_C001.jpg.json", "IMG_C001.jpg", w1)
    pc = tput("IMG_C001(1).jpg", new_scene(), "IMG_C001.jpg(1).json", "IMG_C001.jpg", w2)
    truth["year"][rel(tf / album / "IMG_C001.jpg")] = 2019
    truth["year"][rel(pc)] = 2019
    # no sidecar at all, and an album metadata.json
    pn = tput("IMG_N001.jpg", new_scene(), None, "", nxt())
    truth["takeout_no_sidecar"].append(rel(pn))
    truth["year"][rel(pn)] = datetime.now().year   # falls back to file mtime
    (tf / album / "metadata.json").write_text(json.dumps({"title": album, "access": "protected"}))
    jsons[f"{album}/metadata.json"] = (tf / album / "metadata.json").read_text()
    # takeout copies of local photos (EXIF stripped = not byte-identical): cross-source duplicates
    for k, (lp, img, when) in enumerate(good[30:35]):
        p = tput(f"IMG_X{k}.jpg", img, f"IMG_X{k}.jpg.json", f"IMG_X{k}.jpg", when, album=f"Photos from {when.year}")
        truth["dup_groups"].append([rel(lp), rel(p)])

    # -- zips: photos in 001; half the sidecars live in 002 (cross-archive) --
    zdir = dest / "takeout_zips"
    zdir.mkdir()
    z1, z2 = zdir / "takeout-001.zip", zdir / "takeout-002.zip"
    with zipfile.ZipFile(z1, "w") as a, zipfile.ZipFile(z2, "w") as b:
        for key, p in media.items():
            a.write(p, f"Takeout/Google Photos/{key}")
        for i, (key, text) in enumerate(sorted(jsons.items())):
            (a if i % 2 else b).writestr(f"Takeout/Google Photos/{key}", text)

    truth["files"] = sum(1 for _ in local.rglob("*") if _.is_file()) + len(media)
    (dest / "truth.json").write_text(json.dumps(truth, indent=1))
    return (f"Wrote {truth['files']} files to {dest}\n"
            f"  try: photosorter run OUT --source local:{local} --source takeout:{dest / 'takeout_zips'}")
