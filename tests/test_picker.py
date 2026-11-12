"""Picker source against a mocked API: no network is ever touched."""
import io
import logging

import pytest
from PIL import Image

from photosorter.sources.picker import NOTICE, PickerError, PickerSource, parse_duration

SECRET_URL = "https://lh3.example.invalid/SECRETPATH"
TOKEN = "ya29.SECRETTOKEN"


def jpeg_bytes(color=(200, 30, 30)):
    b = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(b, "JPEG")
    return b.getvalue()


class Resp:
    def __init__(self, status=200, body=None, data=b""):
        self.status_code, self._body, self._data = status, body, data
        self.content = b"x" if body is not None or data else b""

    def json(self):
        return self._body

    def iter_content(self, n):
        yield self._data


class FakeSession:
    def __init__(self, items_pages, polls_until_set=1, forbid_first=()):
        self.headers = {"Authorization": f"Bearer {TOKEN}"}
        self.calls = []
        self.pages = items_pages
        self.polls = 0
        self.polls_until_set = polls_until_set
        self.forbid_first = set(forbid_first)
        self.generation = 0

    def request(self, method, url, **kw):
        self.calls.append((method, url))
        assert self.headers["Authorization"] == f"Bearer {TOKEN}"
        if url.endswith("/v1/sessions") and method == "POST":
            return Resp(200, {"id": "S1", "pickerUri": "https://photos.example.invalid/picker/abc",
                              "pollingConfig": {"pollInterval": "2s", "timeoutIn": "30s"}, "mediaItemsSet": False})
        if "/v1/sessions/S1" in url and method == "GET":
            self.polls += 1
            done = self.polls >= self.polls_until_set
            return Resp(200, {"id": "S1", "mediaItemsSet": done} | ({} if done else {"pollingConfig": {"pollInterval": "2s", "timeoutIn": "20s"}}))
        if "/v1/sessions/S1" in url and method == "DELETE":
            return Resp(200, {})
        if "/v1/mediaItems" in url:
            if "pageToken=P2" in url:
                return Resp(200, {"mediaItems": self.pages[1]})
            if len(self.pages) > 1:
                return Resp(200, {"mediaItems": self.pages[0], "nextPageToken": "P2"})
            self.generation += 1
            return Resp(200, {"mediaItems": [self._item(m) for m in self.pages[0]]})
        if url.startswith(SECRET_URL):
            item = url[len(SECRET_URL) + 1:].split("=")[0]
            if item in self.forbid_first:
                self.forbid_first.discard(item)
                return Resp(403)
            return Resp(200, data=jpeg_bytes())
        raise AssertionError(f"unexpected call {method} {url}")

    def _item(self, m):
        m = dict(m)
        m["mediaFile"] = dict(m["mediaFile"], baseUrl=f"{SECRET_URL}/{m['id']}g{self.generation}"[:-2])
        return m


def photo(i, name=None, create="2019-07-04T10:00:00Z"):
    return {"id": f"id{i}", "type": "PHOTO", "createTime": create,
            "mediaFile": {"baseUrl": f"{SECRET_URL}/id{i}", "mimeType": "image/jpeg", "filename": name or f"P{i}.jpg"}}


def make(session, **kw):
    out = []
    src = PickerSource(session=session, sleep=lambda s: None, open_browser=lambda u: out.append(u), out=out.append, **kw)
    return src, out


def test_full_flow_pagination_headers_autoclose_delete(tmp_path):
    s = FakeSession([[photo(1), photo(2)], [photo(3)]], polls_until_set=2)
    src, out = make(s)
    items = list(src.scan(tmp_path))
    assert [i.original_name for i in items] == ["P1.jpg", "P2.jpg", "P3.jpg"]
    assert all(i.staged_path.is_file() and i.taken_kind == "picker" and i.taken_time.year == 2019 for i in items)
    assert any(u.endswith("/picker/abc/autoclose") for u in out if isinstance(u, str) and u.startswith("http"))
    assert NOTICE in out and "Takeout" in NOTICE
    assert ("DELETE", "https://photospicker.googleapis.com/v1/sessions/S1") in s.calls
    assert s.polls == 2
    downloads = [u for m, u in s.calls if u.startswith(SECRET_URL)]
    assert downloads and all(u.endswith("=d") for u in downloads)


def test_video_uses_dv_and_waits_for_ready(tmp_path):
    v = {"id": "v1", "type": "VIDEO", "createTime": "2020-01-01T00:00:00Z",
         "mediaFile": {"baseUrl": f"{SECRET_URL}/v1", "mimeType": "video/mp4", "filename": "V.mp4",
                       "mediaFileMetadata": {"videoMetadata": {"processingStatus": "READY"}}}}
    s = FakeSession([[v]])
    src, _ = make(s)
    assert [i.original_name for i in src.scan(tmp_path)] == ["V.mp4"]
    assert any(u.endswith("=dv") for _m, u in s.calls)


def test_expired_base_url_relists_and_retries(tmp_path):
    s = FakeSession([[photo(1), photo(2)]], forbid_first={"id2"})
    src, _ = make(s)
    items = list(src.scan(tmp_path))
    assert sorted(i.original_name for i in items) == ["P1.jpg", "P2.jpg"]
    lists = [u for _m, u in s.calls if "/v1/mediaItems" in u]
    assert len(lists) == 2  # one listing, one fresh listing after the 403


def test_resume_skips_staged_items(tmp_path):
    s = FakeSession([[photo(1)]])
    src, _ = make(s)
    list(src.scan(tmp_path))
    s2 = FakeSession([[photo(1)]])
    src2, _ = make(s2)
    assert len(list(src2.scan(tmp_path))) == 1
    assert not [u for _m, u in s2.calls if u.startswith(SECRET_URL)]


def test_timeout_raises_and_still_deletes_session(tmp_path):
    s = FakeSession([[photo(1)]], polls_until_set=10**6)
    t = iter(range(0, 10**6, 10))
    src, _ = make(s, clock=lambda: next(t))
    with pytest.raises(PickerError, match="Timed out"):
        list(src.scan(tmp_path))
    assert ("DELETE", "https://photospicker.googleapis.com/v1/sessions/S1") in s.calls


def test_session_deleted_when_consumer_stops_early(tmp_path):
    s = FakeSession([[photo(1), photo(2)]])
    src, _ = make(s)
    gen = src.scan(tmp_path)
    next(gen)
    gen.close()
    assert any(m == "DELETE" for m, _u in s.calls)


def test_no_token_or_base_url_in_logs_or_errors(tmp_path, caplog, capsys):
    caplog.set_level(logging.DEBUG)

    class Boom(FakeSession):
        def request(self, method, url, **kw):
            if url.startswith(SECRET_URL):
                raise ConnectionError(f"failed {url} Bearer {TOKEN}")
            return super().request(method, url, **kw)

    s = Boom([[photo(1)]])
    src, out = make(s)
    list(src.scan(tmp_path))  # item skipped after retries
    text = caplog.text + capsys.readouterr().out + "\n".join(str(o) for o in out)
    assert "SECRETPATH" not in text and "SECRETTOKEN" not in text


def test_http_error_message_has_status_only(tmp_path):
    class Denied(FakeSession):
        def request(self, method, url, **kw):
            if method == "POST":
                return Resp(401, {})
            return super().request(method, url, **kw)

    src, _ = make(Denied([[]]))
    with pytest.raises(PickerError, match="HTTP 401"):
        list(src.scan(tmp_path))


def test_parse_duration():
    assert parse_duration("3.5s") == 3.5 and parse_duration("0s") == 0 and parse_duration(None, 7) == 7 and parse_duration("junk", 2) == 2


def test_token_file_is_private(tmp_path):
    import os
    import stat
    from photosorter.auth import write_private
    p = tmp_path / "sub" / "token.json"
    write_private(p, "{}")
    if os.name == "posix":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
