"""
منبع اسپاتیفای (اختیاری).

⚠️ وضعیت در ۲۰۲۶: خودِ endpoint پریمیوم نمی‌خواهد، ولی از ۱۱ فوریه ۲۰۲۶
اسپاتیفای شرط گذاشته که **صاحبِ اپ** در Development Mode باید پریمیوم داشته
باشد. اکانت‌های تست لازم نیست پریمیوم باشند — پس اگر کسی با پریمیوم اپ را
بسازد و تو را به‌عنوان تست‌یوزر اضافه کند، اکانت فریِ تو کار می‌کند.
ضمناً از ۱۸ ژوئن ۲۰۲۶ refresh token بعد از ۶ ماه منقضی می‌شود و باید دوباره
لاگین کنی.

نکته‌ی دقت: `progress_ms` اسپاتیفای ۰.۵ تا ۱.۵ ثانیه تأخیرِ تصادفی دارد
(باگ شناخته‌شده). برای همین اینجا لنگر را فقط وقتی جابه‌جا می‌کنیم که دریفت
واقعاً بزرگ باشد — وگرنه ساعتِ درون‌یابِ خودمان از عددِ اسپاتیفای دقیق‌تر
است و نباید با جیترِ آن تکان بخورد.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiohttp

from ..clock import PlaybackClock, Track
from .base import Source

log = logging.getLogger("tglyrics.spotify")

TOKEN_URL = "https://accounts.spotify.com/api/token"
NOW_URL = "https://api.spotify.com/v1/me/player/currently-playing"

__all__ = ["SpotifySource"]


class SpotifySource(Source):
    name = "spotify"

    def __init__(
        self,
        clock: PlaybackClock,
        client_id: str = "",
        client_secret: str = "",
        refresh_token: str = "",
        poll_interval: float = 3.0,
    ) -> None:
        super().__init__(clock)
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.poll = max(1.0, float(poll_interval))
        self._token = ""
        self._token_exp = 0.0
        self._s: Optional[aiohttp.ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        #: تأخیرِ ذاتیِ اسپاتیفای — با مشاهده تخمین زده می‌شود
        self._bias_ms = 0.0

    async def start(self) -> None:
        if not (self.client_id and self.client_secret and self.refresh_token):
            raise RuntimeError(
                "برای منبع spotify باید client_id / client_secret / refresh_token را بدهی"
            )
        self._s = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._loop(), name="spotify-poll")
        log.info("پولینگ اسپاتیفای هر %.1f ثانیه", self.poll)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._s:
            await self._s.close()
            self._s = None

    # ── توکن ─────────────────────────────────────────────────────
    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        assert self._s
        async with self._s.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": self.refresh_token},
            auth=aiohttp.BasicAuth(self.client_id, self.client_secret),
        ) as r:
            d = await r.json(content_type=None)
            if r.status != 200:
                if d.get("error") == "invalid_grant":
                    raise RuntimeError(
                        "refresh token اسپاتیفای منقضی شده (از ژوئن ۲۰۲۶ عمرش ۶ ماه است) "
                        "— باید دوباره لاگین کنی."
                    )
                raise RuntimeError(f"گرفتن توکن اسپاتیفای نشد: {r.status} {d}")
            self._token = d["access_token"]
            self._token_exp = time.time() + int(d.get("expires_in", 3600))
            if d.get("refresh_token"):
                self.refresh_token = d["refresh_token"]
            return self._token

    # ── حلقه ─────────────────────────────────────────────────────
    async def _loop(self) -> None:
        backoff = self.poll
        while True:
            try:
                await self._tick()
                backoff = self.poll
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("اسپاتیفای: %s", e)
                backoff = min(60.0, backoff * 1.8)
            await asyncio.sleep(backoff)

    async def _tick(self) -> None:
        assert self._s
        tok = await self._access_token()
        t0 = time.monotonic()
        async with self._s.get(
            NOW_URL,
            headers={"Authorization": f"Bearer {tok}"},
            params={"additional_types": "track"},
        ) as r:
            if r.status == 204:
                self.clock.clear()
                return
            if r.status == 429:
                wait = int(r.headers.get("Retry-After", "5"))
                log.warning("اسپاتیفای rate limit — %ds صبر", wait)
                await asyncio.sleep(wait + 1)
                return
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            d = await r.json(content_type=None)

        rtt_ms = (time.monotonic() - t0) * 1000.0
        item = d.get("item") or {}
        if d.get("currently_playing_type") not in (None, "track") or not item:
            self.clock.clear()
            return

        artists = item.get("artists") or []
        track = Track(
            title=item.get("name") or "",
            artist=", ".join(a.get("name", "") for a in artists if a.get("name")),
            album=(item.get("album") or {}).get("name") or "",
            duration_ms=int(item.get("duration_ms") or 0),
        )
        pos = float(d.get("progress_ms") or 0.0)
        playing = bool(d.get("is_playing"))

        # نیمی از RTT به‌عنوان تأخیر یک‌طرفه
        latency = rtt_ms / 2.0

        snap = self.clock.snapshot()
        same = snap.track and snap.track.key == track.key
        drift = abs(snap.position_ms - (pos + latency)) if same else 9e9

        # جیترِ ±۱.۵ ثانیه‌ی اسپاتیفای نباید ساعتِ ما را تکان بدهد
        if same and snap.playing == playing and drift < 1800:
            self.clock.touch()
            return

        self.clock.update(track, pos, playing, 1.0, latency_ms=latency)
