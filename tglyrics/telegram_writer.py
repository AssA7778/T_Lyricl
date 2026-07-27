from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from telethon import TelegramClient, functions
from telethon.errors import FloodWaitError, RPCError

from .ratelimit import AdaptiveLimiter, RateConfig
from .textutil import sanitize

log = logging.getLogger("tglyrics.bio")

__all__ = ["BioWriter", "RateConfig"]


class BioWriter:
    def __init__(self, client: TelegramClient, cfg: RateConfig) -> None:
        self.client = client
        self.limiter = AdaptiveLimiter(cfg)
        self.limit = 70
        self.premium = False
        self._current: Optional[str] = None
        self._original: Optional[str] = None
        self._lock = asyncio.Lock()

    async def start(self, forced_limit: int = 0) -> None:
        me = await self.client.get_me()
        self.premium = bool(getattr(me, "premium", False))
        self.limit = int(forced_limit) if forced_limit > 0 else await self._discover_limit()
        self._original = await self._read_bio()
        self._current = self._original
        log.info(
            "وارد شدم: %s | پریمیوم: %s | سقف بیو: %d کاراکتر",
            getattr(me, "username", None) or getattr(me, "first_name", "?"),
            "بله" if self.premium else "نه",
            self.limit,
        )

    async def _discover_limit(self) -> int:
        default = 140 if self.premium else 70
        want = "about_length_limit_premium" if self.premium else "about_length_limit_default"
        try:
            res = await self.client(functions.help.GetAppConfigRequest(hash=0))
            for item in getattr(getattr(res, "config", None), "value", None) or []:
                if getattr(item, "key", "") == want:
                    v = getattr(getattr(item, "value", None), "value", None)
                    if v:
                        return int(v)
        except (RPCError, OSError, AttributeError, TypeError, ValueError) as e:
            log.debug("خواندن appConfig نشد (%s) — پیش‌فرض %d", e, default)
        return default

    async def _read_bio(self) -> str:
        try:
            full = await self.client(functions.users.GetFullUserRequest("me"))
            return getattr(full.full_user, "about", "") or ""
        except (RPCError, OSError, AttributeError) as e:
            log.debug("خواندن بیو نشد: %s", e)
            return ""

    @property
    def original_bio(self) -> str:
        return self._original or ""

    def set_original(self, text: str) -> None:
        self._original = text or ""

    @property
    def current(self) -> str:
        return self._current or ""

    @property
    def interval(self) -> float:
        return self.limiter.interval

    @property
    def lead(self) -> float:
        return self.limiter.lead

    def ready_in(self) -> float:
        return self.limiter.ready_in()

    def stats(self) -> dict:
        return self.limiter.stats() | {"limit": self.limit, "premium": self.premium}

    async def write(self, text: str, *, force: bool = False) -> bool:
        text = sanitize(text, self.limit)
        if not force and text == self._current:
            return False

        async with self._lock:
            if not force:
                if not self.limiter.ready() or text == self._current:
                    return False

            t0 = time.monotonic()
            try:
                await self.client(functions.account.UpdateProfileRequest(about=text))
            except FloodWaitError as e:
                self.limiter.on_flood(getattr(e, "seconds", 30))
                return False
            except RPCError as e:
                if "ABOUT_TOO_LONG" in str(e).upper() or "AboutTooLong" in type(e).__name__:
                    old, self.limit = self.limit, max(20, len(text) - 4)
                    log.warning("سقف واقعی بیو %d بود نه %d — تنظیم شد", self.limit, old)
                else:
                    log.warning("خطای تلگرام موقع نوشتن بیو: %s", e)
                    self.limiter.on_error()
                return False
            except (OSError, asyncio.TimeoutError, ConnectionError) as e:
                log.warning("مشکل شبکه موقع نوشتن بیو: %s", e)
                self.limiter.on_error()
                return False

            dt = time.monotonic() - t0
            self._current = text
            self.limiter.on_success(dt)
            log.debug("بیو ← %r (%.0fms)", text, dt * 1000)
            return True

    async def restore(self) -> None:
        if self._original is None or self._current == self._original:
            return
        try:
            await self.client(
                functions.account.UpdateProfileRequest(about=self._original)
            )
            self._current = self._original
            log.info("بیوی اصلی برگردانده شد")
        except (RPCError, OSError) as e:
            log.warning("برگرداندن بیو نشد: %s", e)
