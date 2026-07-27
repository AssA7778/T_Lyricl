from __future__ import annotations

import asyncio
import difflib
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp

from .translit import candidates, has_persian, normalize_fa, romanize, skeleton

log = logging.getLogger("tglyrics.lrclib")

BASE = "https://lrclib.net/api"

__all__ = ["LrcLibResult", "LrcLibClient"]

_PUNCT = re.compile(r"[^\w\s؀-ۿ]+")


def _key(s: str) -> str:
    s = normalize_fa(s or "")
    s = _PUNCT.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip().casefold()


def _sim(a: str, b: str) -> float:
    ka, kb = _key(a), _key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    if ka in kb or kb in ka:
        return 0.92
    best = difflib.SequenceMatcher(None, ka, kb).ratio()

    if has_persian(a) != has_persian(b):
        sa, sb = skeleton(a), skeleton(b)
        if len(sa) >= 3 and len(sb) >= 3:
            if sa == sb:
                return max(best, 0.95)
            if sa in sb or sb in sa:
                return max(best, 0.85)
            best = max(best, difflib.SequenceMatcher(None, sa, sb).ratio() * 0.92)
    return best


@dataclass
class LrcLibResult:
    id: int
    track_name: str
    artist_name: str
    album_name: str
    duration: float
    synced: Optional[str]
    plain: Optional[str]
    instrumental: bool
    score: float = 0.0

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "LrcLibResult":
        return cls(
            id=int(d.get("id") or 0),
            track_name=d.get("trackName") or d.get("name") or "",
            artist_name=d.get("artistName") or "",
            album_name=d.get("albumName") or "",
            duration=float(d.get("duration") or 0.0),
            synced=d.get("syncedLyrics") or None,
            plain=d.get("plainLyrics") or None,
            instrumental=bool(d.get("instrumental")),
        )


class LrcLibClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        user_agent: str = "tglyrics/1.0",
        timeout: float = 10.0,
    ) -> None:
        self._s = session
        self._ua = user_agent
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _json(self, path: str, params: dict[str, Any]) -> Any:
        url = f"{BASE}/{path}?{urlencode({k: v for k, v in params.items() if v not in (None, '')})}"
        try:
            async with self._s.get(
                url, headers={"User-Agent": self._ua}, timeout=self._timeout
            ) as r:
                if r.status == 404:
                    return None
                if r.status != 200:
                    log.debug("lrclib %s → HTTP %s", path, r.status)
                    return None
                return await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.debug("lrclib %s → %s", path, e)
            return None

    async def get_exact(
        self,
        track: str,
        artist: str,
        album: str = "",
        duration_s: Optional[int] = None,
    ) -> Optional[LrcLibResult]:
        d = await self._json(
            "get",
            {
                "track_name": track,
                "artist_name": artist,
                "album_name": album,
                "duration": int(duration_s) if duration_s else None,
            },
        )
        if isinstance(d, dict) and d.get("id"):
            return LrcLibResult.from_json(d)
        return None

    async def search(
        self, q: str = "", track: str = "", artist: str = ""
    ) -> list[LrcLibResult]:
        params: dict[str, Any] = {}
        if q:
            params["q"] = q
        if track:
            params["track_name"] = track
            if artist:
                params["artist_name"] = artist
        if not params:
            return []
        d = await self._json("search", params)
        if not isinstance(d, list):
            return []
        return [LrcLibResult.from_json(x) for x in d if isinstance(x, dict)]

    @staticmethod
    def _score(
        r: LrcLibResult, artist: str, title: str, duration_s: Optional[int]
    ) -> float:
        s = 0.0
        s += 3.0 * _sim(title, r.track_name)

        if artist:
            a = _sim(artist, r.artist_name)
            a = max(a, _sim(romanize(artist), r.artist_name))
            s += 2.0 * a

        if duration_s and r.duration:
            diff = abs(r.duration - duration_s)
            if diff <= 2:
                s += 2.0
            elif diff <= 5:
                s += 1.2
            elif diff <= 12:
                s += 0.4
            else:
                s -= min(2.5, diff / 30.0)

        if r.synced:
            s += 2.5
        elif r.plain:
            s += 0.3
        if r.instrumental:
            s -= 1.0
        return s

    async def best(
        self,
        artist: str,
        title: str,
        album: str = "",
        duration_ms: int = 0,
        *,
        synced_only: bool = True,
    ) -> Optional[LrcLibResult]:
        dur = int(round(duration_ms / 1000)) if duration_ms else None
        cands = candidates(artist, title)
        pool: dict[int, LrcLibResult] = {}

        for a, t in cands[:3]:
            if not a:
                continue
            r = await self.get_exact(t, a, album, dur)
            if r and (r.synced or not synced_only):
                r.score = self._score(r, artist, title, dur) + 1.0
                pool[r.id] = r
                if r.synced:
                    break

        if not any(r.synced for r in pool.values()):
            for a, t in cands:
                q = f"{a} {t}".strip() if a else t
                for r in await self.search(q=q):
                    if r.id not in pool:
                        r.score = self._score(r, artist, title, dur)
                        pool[r.id] = r
                if any(x.synced and x.score > 6.0 for x in pool.values()):
                    break

        if not any(r.synced and r.score > 5.0 for r in pool.values()):
            ra = romanize(artist) if has_persian(artist) else artist
            if ra and len(ra) >= 3:
                for r in await self.search(q=ra):
                    if r.id not in pool:
                        r.score = self._score(r, artist, title, dur)
                        pool[r.id] = r

        if not pool:
            return None

        if synced_only:
            picks = [r for r in pool.values() if r.synced]
            if not picks:
                log.info("لیریک هست ولی سینک‌شده نیست: %s – %s", artist, title)
                return None
        else:
            picks = list(pool.values())

        best = max(picks, key=lambda r: r.score)
        if best.score < 3.2:
            log.info(
                "lrclib: بهترین تطبیق ضعیف بود (%.1f) برای %s – %s", best.score, artist, title
            )
            return None
        return best
