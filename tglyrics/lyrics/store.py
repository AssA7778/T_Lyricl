"""
انبار لیریک — کش SQLite + فایل‌های دستیِ کاربر.

اولویت:
    ۱. فایل .lrc دستی توی پوشه‌ی lyrics/   ← همیشه برنده است
    ۲. کش
    ۳. اینترنت (LRCLIB)

پوشه‌ی دستی برای آهنگ‌های فارسیِ کمیاب حیاتی است — چیزی که LRCLIB ندارد را
یک بار خودت می‌سازی و برای همیشه دقیق است.

اسم فایل هر کدام از این‌ها می‌تواند باشد:
    Artist - Title.lrc
    Title.lrc
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("tglyrics.store")

__all__ = ["LyricsStore", "CacheHit"]

NEG_TTL = 6 * 3600      # آهنگی که پیدا نشد، ۶ ساعت دیگر دوباره امتحان می‌شود
POS_TTL = 180 * 86400   # لیریکِ پیداشده عملاً برای همیشه

_PUNCT = re.compile(r"[^\w\s؀-ۿ]+")


def norm_key(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("ي", "ی").replace("ك", "ک")
    s = _PUNCT.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip().casefold()


@dataclass
class CacheHit:
    raw: Optional[str]
    source: str
    found: bool
    ts: float


_SCHEMA = """
CREATE TABLE IF NOT EXISTS lyrics (
    key        TEXT NOT NULL,
    duration_s INTEGER NOT NULL DEFAULT 0,
    raw        TEXT,
    source     TEXT NOT NULL DEFAULT '',
    found      INTEGER NOT NULL DEFAULT 0,
    ts         REAL NOT NULL,
    PRIMARY KEY (key, duration_s)
);
CREATE TABLE IF NOT EXISTS offsets (
    key   TEXT PRIMARY KEY,
    ms    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


class LyricsStore:
    def __init__(self, db_path: str, local_dir: str = "") -> None:
        self.db_path = db_path
        self.local_dir = Path(local_dir) if local_dir else None
        self._db: Optional[sqlite3.Connection] = None
        self._local_index: dict[str, Path] = {}
        self._local_mtime = 0.0

    # ── چرخه‌ی عمر ───────────────────────────────────────────────
    def open(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)) or ".", exist_ok=True)
        self._db = sqlite3.connect(self.db_path, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        if self.local_dir:
            self.local_dir.mkdir(parents=True, exist_ok=True)
            self._reindex_local()

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None

    # ── کش ───────────────────────────────────────────────────────
    @staticmethod
    def _bucket(duration_ms: int) -> int:
        """duration را به سطلِ ۵ ثانیه‌ای گرد کن تا نوسان پلیر کش را بی‌اثر نکند."""
        return int(round((duration_ms or 0) / 5000.0))

    def get(self, key: str, duration_ms: int = 0) -> Optional[CacheHit]:
        if not self._db:
            return None
        row = self._db.execute(
            "SELECT raw, source, found, ts FROM lyrics WHERE key=? AND duration_s=?",
            (key, self._bucket(duration_ms)),
        ).fetchone()
        if not row:
            return None
        raw, source, found, ts = row
        age = time.time() - ts
        if (found and age > POS_TTL) or (not found and age > NEG_TTL):
            return None
        return CacheHit(raw=raw, source=source, found=bool(found), ts=ts)

    def put(
        self,
        key: str,
        duration_ms: int,
        raw: Optional[str],
        source: str,
        found: bool,
    ) -> None:
        if not self._db:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO lyrics (key, duration_s, raw, source, found, ts)"
            " VALUES (?,?,?,?,?,?)",
            (key, self._bucket(duration_ms), raw, source, int(found), time.time()),
        )

    def forget(self, key: str) -> int:
        if not self._db:
            return 0
        cur = self._db.execute("DELETE FROM lyrics WHERE key=?", (key,))
        return cur.rowcount or 0

    # ── آفست هر آهنگ ─────────────────────────────────────────────
    def get_offset(self, key: str) -> int:
        if not self._db:
            return 0
        row = self._db.execute("SELECT ms FROM offsets WHERE key=?", (key,)).fetchone()
        return int(row[0]) if row else 0

    def set_offset(self, key: str, ms: int) -> None:
        if not self._db:
            return
        if ms:
            self._db.execute(
                "INSERT OR REPLACE INTO offsets (key, ms) VALUES (?,?)", (key, int(ms))
            )
        else:
            self._db.execute("DELETE FROM offsets WHERE key=?", (key,))

    # ── kv ساده (برای بیوی اصلی و…) ─────────────────────────────
    def kv_get(self, k: str) -> Optional[str]:
        if not self._db:
            return None
        row = self._db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def kv_set(self, k: str, v: str) -> None:
        if not self._db:
            return
        self._db.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)", (k, v))

    # ── فایل‌های دستی ────────────────────────────────────────────
    def _reindex_local(self) -> None:
        if not self.local_dir or not self.local_dir.is_dir():
            return
        idx: dict[str, Path] = {}
        for p in self.local_dir.rglob("*"):
            if p.suffix.lower() not in (".lrc", ".txt"):
                continue
            stem = p.stem
            idx[norm_key(stem)] = p
            if " - " in stem:
                a, _, t = stem.partition(" - ")
                idx.setdefault(norm_key(f"{a} {t}"), p)
                idx.setdefault(norm_key(f"{t} {a}"), p)
                idx.setdefault(norm_key(t), p)
        self._local_index = idx
        try:
            self._local_mtime = self.local_dir.stat().st_mtime
        except OSError:
            pass
        if idx:
            log.info("پوشه‌ی lyrics: %d فایل ایندکس شد", len(set(idx.values())))

    def _maybe_reindex(self) -> None:
        if not self.local_dir:
            return
        try:
            m = self.local_dir.stat().st_mtime
        except OSError:
            return
        if m != self._local_mtime:
            self._reindex_local()

    def local(self, artist: str, title: str) -> Optional[tuple[str, str]]:
        """(متن خام، مسیر) یا None"""
        if not self.local_dir:
            return None
        self._maybe_reindex()
        if not self._local_index:
            return None

        for cand in (f"{artist} - {title}", f"{artist} {title}", title):
            p = self._local_index.get(norm_key(cand))
            if p:
                return self._read(p)

        # تطبیق نرم: کلیدی که عنوان داخلش باشد
        nt = norm_key(title)
        if len(nt) >= 4:
            for k, p in self._local_index.items():
                if nt in k:
                    return self._read(p)
        return None

    @staticmethod
    def _read(p: Path) -> Optional[tuple[str, str]]:
        for enc in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
            try:
                return p.read_text(encoding=enc), str(p)
            except (UnicodeDecodeError, OSError):
                continue
        return None
