from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

from ..clock import Track
from .lrc import Lyrics, parse_lrc
from .lrclib import LrcLibClient
from .store import LyricsStore

log = logging.getLogger("tglyrics.lyrics")

__all__ = ["LyricsEngine"]


class LyricsEngine:
    def __init__(
        self,
        store: LyricsStore,
        *,
        user_agent: str = "tglyrics/1.0",
        global_offset_ms: int = 0,
        mem_size: int = 64,
    ) -> None:
        self.store = store
        self.user_agent = user_agent
        self.global_offset_ms = int(global_offset_ms)
        self._mem: dict[str, Optional[Lyrics]] = {}
        self._order: list[str] = []
        self._mem_size = mem_size
        self._inflight: dict[str, asyncio.Task] = {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._client: Optional[LrcLibClient] = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=4, ttl_dns_cache=300)
        )
        self._client = LrcLibClient(self._session, self.user_agent)

    async def close(self) -> None:
        for t in list(self._inflight.values()):
            t.cancel()
        self._inflight.clear()
        if self._session:
            await self._session.close()
            self._session = None

    def _remember(self, key: str, lyr: Optional[Lyrics]) -> None:
        if key in self._mem:
            self._order.remove(key)
        elif len(self._order) >= self._mem_size:
            self._mem.pop(self._order.pop(0), None)
        self._mem[key] = lyr
        self._order.append(key)

    def cached(self, track: Track) -> tuple[bool, Optional[Lyrics]]:
        k = track.key
        if k in self._mem:
            return True, self._mem[k]
        return False, None

    def offset_for(self, track: Track, lyr: Optional[Lyrics]) -> int:
        total = self.global_offset_ms
        if lyr:
            total += lyr.offset_ms
        total += self.store.get_offset(track.key)
        return total

    def forget(self, track: Track) -> None:
        k = track.key
        try:
            self.store.forget(k)
        except Exception:
            pass
        self._mem.pop(k, None)
        if k in self._order:
            self._order.remove(k)

    def request(self, track: Track) -> Optional[asyncio.Task]:
        k = track.key
        if k in self._mem:
            return None
        t = self._inflight.get(k)
        if t is None:
            t = asyncio.create_task(self._fetch(track), name=f"lyrics:{k[:40]}")
            self._inflight[k] = t
            t.add_done_callback(lambda _t, kk=k: self._inflight.pop(kk, None))
        return t

    async def get(self, track: Track) -> Optional[Lyrics]:
        hit, lyr = self.cached(track)
        if hit:
            return lyr
        t = self.request(track)
        return await t if t else self._mem.get(track.key)

    async def _fetch(self, track: Track) -> Optional[Lyrics]:
        key = track.key
        artist, title = track.artist or "", track.title or ""

        try:
            loc = self.store.local(artist, title)
        except Exception:
            loc = None
        if loc:
            raw, path = loc
            lyr = parse_lrc(raw, duration_ms=track.duration_ms, source=f"local:{path}")
            if lyr.lines:
                log.info("لیریک از فایل دستی: %s", path)
                self._remember(key, lyr)
                return lyr

        try:
            hit = self.store.get(key, track.duration_ms)
        except Exception:
            hit = None
        if hit is not None:
            if not hit.found:
                log.debug("کش: قبلاً پیدا نشده بود — %s", track)
                self._remember(key, None)
                return None
            lyr = parse_lrc(hit.raw or "", duration_ms=track.duration_ms, source=hit.source)
            self._remember(key, lyr if lyr.lines else None)
            return self._mem[key]

        if not self._client:
            return None
        try:
            best = await self._client.best(
                artist, title, track.album, track.duration_ms, synced_only=True
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("خطا در گرفتن لیریک: %s", e)
            return None

        if not best or not best.synced:
            log.info("لیریک سینک‌شده پیدا نشد: %s", track)
            try:
                self.store.put(key, track.duration_ms, None, "lrclib", False)
            except Exception:
                pass
            self._remember(key, None)
            return None

        src = f"lrclib:{best.id}"
        try:
            self.store.put(key, track.duration_ms, best.synced, src, True)
        except Exception:
            pass

        lyr = parse_lrc(best.synced, duration_ms=track.duration_ms, source=src)
        log.info(
            "لیریک پیدا شد: %s – %s (%d خط%s) ← %s",
            best.artist_name,
            best.track_name,
            len(lyr.lines),
            "، کلمه‌ای" if lyr.word_level else "",
            src,
        )
        self._remember(key, lyr if lyr.lines else None)
        return self._mem[key]
