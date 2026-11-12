"""Google Photos Picker source.

The Library API can no longer read a user's library (readonly/sharing scopes were removed on
2025-03-31); the Picker API only returns what the user picks in a Google Photos window.
Docs checked 2026-11-12: developers.google.com/photos/picker (sessions, mediaItems, base URLs).

Logging rule: never log tokens or baseUrls. Errors from `requests` embed the URL, so they are caught
and re-raised with only the status code / exception type.
"""
from __future__ import annotations

import logging
import re
import time
import webbrowser
from pathlib import Path
from typing import Callable, Iterator

from ..dates import parse_rfc3339
from .base import Item

API = "https://photospicker.googleapis.com/v1"
log = logging.getLogger("photosorter.picker")
logging.getLogger("urllib3").setLevel(logging.WARNING)  # its DEBUG lines contain request paths

NOTICE = ("Note: the Google Photos Picker needs you to select photos or albums by hand in your browser. "
          "To process your whole library, use a Google Takeout export instead (takeout:/path).")


class PickerError(Exception):
    pass


def parse_duration(s: str | None, default: float = 0.0) -> float:
    """'3.5s' -> 3.5"""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)s\s*", s or "")
    return float(m.group(1)) if m else default


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "item"


class PickerSource:
    name = "gphotos-picker"

    def __init__(self, session=None, sleep: Callable[[float], None] = time.sleep,
                 open_browser: Callable[[str], object] = webbrowser.open,
                 clock: Callable[[], float] = time.monotonic, out=print):
        self._session = session
        self._sleep, self._open, self._clock, self._out = sleep, open_browser, clock, out
        self.unmatched: list[str] = []

    # -- HTTP (errors carry no URL) ------------------------------------------
    def _http(self):
        if self._session is None:
            import requests
            from .. import auth
            s = requests.Session()
            s.headers["Authorization"] = f"Bearer {auth.load_credentials().token}"
            self._session = s
        return self._session

    def _call(self, method: str, url: str, **kw):
        for attempt in range(4):
            try:
                resp = self._http().request(method, url, timeout=60, **kw)
            except Exception as e:
                if attempt == 3:
                    raise PickerError(f"network error ({type(e).__name__})") from None
                self._sleep(2 ** attempt)
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                self._sleep(2 ** attempt)
                continue
            return resp
        raise PickerError("unreachable")

    def _json(self, method: str, path: str, **kw) -> dict:
        resp = self._call(method, f"{API}{path}", **kw)
        if resp.status_code >= 400:
            raise PickerError(f"Picker API {method} {path.split('?')[0].split('/')[1]} failed: HTTP {resp.status_code}")
        return resp.json() if resp.content else {}

    # -- flow ------------------------------------------------------------------
    def scan(self, staging: Path) -> Iterator[Item]:
        self._out(NOTICE)
        sess = self._json("POST", "/sessions", json={})
        sid = sess["id"]
        try:
            yield from self._run(sess, sid, staging / "gphotos-picker")
        finally:
            try:
                self._call("DELETE", f"{API}/sessions/{sid}")
            except Exception:
                pass

    def _run(self, sess: dict, sid: str, stage: Path) -> Iterator[Item]:
        uri = sess["pickerUri"].rstrip("/") + "/autoclose"
        self._out(f"Open this link and pick your photos (it should open by itself):\n  {uri}")
        try:
            self._open(uri)
        except Exception:
            pass
        timeout = parse_duration(sess.get("pollingConfig", {}).get("timeoutIn"), 600)
        deadline = self._clock() + timeout
        interval = parse_duration(sess.get("pollingConfig", {}).get("pollInterval"), 3)
        while not sess.get("mediaItemsSet"):
            if self._clock() >= deadline:
                raise PickerError("Timed out waiting for you to finish picking. Run the command again.")
            self._sleep(max(interval, 1))
            sess = self._json("GET", f"/sessions/{sid}")
            cfg = sess.get("pollingConfig") or {}
            interval = parse_duration(cfg.get("pollInterval"), interval)
            if cfg and parse_duration(cfg.get("timeoutIn"), 1) == 0 and not sess.get("mediaItemsSet"):
                raise PickerError("Google ended the picking session before items were chosen.")

        stage.mkdir(parents=True, exist_ok=True)
        pending = self._list(sid)
        self._out(f"{len(pending)} picked items.")
        for round_ in range(3):  # a fresh listing gives fresh baseUrls after a 403 / expiry
            failed: dict[str, dict] = {}
            for it in pending:
                try:
                    item = self._download(it, stage)
                except _Retry:
                    failed[it["id"]] = it
                    continue
                except PickerError as e:  # one bad download must not lose the rest
                    log.warning("item %s: %s; skipped", it.get("id"), e)
                    continue
                if item:
                    yield item
            if not failed:
                return
            self._sleep(5 * (round_ + 1))
            fresh = {m["id"]: m for m in self._list(sid)}
            pending = [fresh[i] for i in failed if i in fresh]
        for it in pending:
            log.warning("could not download item %s; skipped", it.get("id"))

    def _list(self, sid: str) -> list[dict]:
        items, token = [], None
        while True:
            q = f"/mediaItems?sessionId={sid}&pageSize=100" + (f"&pageToken={token}" if token else "")
            page = self._json("GET", q)
            items += page.get("mediaItems", [])
            token = page.get("nextPageToken")
            if not token:
                return items

    def _download(self, it: dict, stage: Path) -> Item | None:
        mf = it.get("mediaFile") or {}
        is_video = it.get("type") == "VIDEO"
        name = mf.get("filename") or (_safe(it["id"]) + (".mp4" if is_video else ".jpg"))
        dest = stage / _safe(it["id"]) / name
        taken = parse_rfc3339(it.get("createTime"))
        item = Item(dest, name, self.name, taken, "picker" if taken else None, f"picker:{it['id']}")
        if dest.exists():
            return item  # resume: already staged
        if is_video and (mf.get("mediaFileMetadata", {}).get("videoMetadata", {}).get("processingStatus", "READY") != "READY"):
            raise _Retry()
        resp = self._call("GET", mf["baseUrl"] + ("=dv" if is_video else "=d"), stream=True)
        if resp.status_code in (403, 404):
            raise _Retry()
        if resp.status_code >= 400:
            log.warning("item %s: download failed (HTTP %s); skipped", it["id"], resp.status_code)
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        try:
            with open(part, "wb") as fh:
                for chunk in resp.iter_content(1 << 20):
                    fh.write(chunk)
        except Exception as e:
            part.unlink(missing_ok=True)
            log.warning("item %s: download interrupted (%s); skipped", it["id"], type(e).__name__)
            return None
        part.replace(dest)
        return item


class _Retry(Exception):
    pass
