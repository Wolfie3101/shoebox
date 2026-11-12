"""SQLite cache of per-file features, keyed by path + size + mtime. Only the main process writes."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .features import CACHE_VERSION


class Cache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS features ("
            "path TEXT, size INTEGER, mtime_ns INTEGER, data TEXT, PRIMARY KEY (path, size, mtime_ns))"
        )

    @staticmethod
    def key(p: Path) -> tuple[str, int, int]:
        st = p.stat()
        return str(p.resolve()), st.st_size, st.st_mtime_ns

    def get(self, key) -> dict | None:
        row = self.db.execute(
            "SELECT data FROM features WHERE path=? AND size=? AND mtime_ns=?", key).fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        return data if data.get("v") == CACHE_VERSION and not data.get("error") else None

    def put(self, key, data: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO features VALUES (?,?,?,?)", (*key, json.dumps(data)))

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()
