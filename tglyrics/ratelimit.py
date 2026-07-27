"""
محدودکننده‌ی نرخِ تطبیقی — بخشِ ریسکیِ پروژه، جدا و قابل‌تست.

چرا جداست: منطقش هیچ ربطی به تلگرام ندارد و باید بتوان بدون شبکه، بدون
اکانت، و با ساعتِ ساختگی تستش کرد. حدس زدن در این بخش گران تمام می‌شود.

مسئله
─────
تلگرام هیچ عددی برای نرخِ مجازِ `account.updateProfile` منتشر نکرده و
صریحاً می‌گوید محدودیت‌های سمت سرور قابل پیش‌بینی نیستند. ضمناً شمارنده‌ی
۴۲۰ روی «متد + همان پارامترها» کلید می‌خورد، پس نوشتنِ بی‌تغییر هم خرج دارد
و باید سمتِ خودمان حذف شود.

راهبرد
──────
عدد را حدس نزن، یاد بگیر:

  • با یک فاصله‌ی محافظه‌کارانه شروع کن
  • FLOOD_WAIT ⇒ همان‌قدر که گفت بخواب + فاصله را نمایی ببر بالا
  • بعد از یک دوره‌ی آرام، آرام برگرد پایین (ولی هرگز زیر `hard_floor`)
  • مقدار یادگرفته را روی دیسک نگه دار

بعلاوه یک سقفِ سختِ «حداکثر N نوشتن در هر ۶۰ ثانیه» به‌عنوان کمربند ایمنی،
که مستقل از یادگیری عمل می‌کند.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger("tglyrics.rate")

__all__ = ["RateConfig", "AdaptiveLimiter"]


@dataclass
class RateConfig:
    min_interval: float = 3.5
    backoff_factor: float = 1.6
    max_interval_cap: float = 60.0
    recover_after: int = 25
    recover_factor: float = 0.92
    max_writes_per_minute: int = 18
    hard_floor: float = 2.5
    state_file: str = "data/rate_state.json"


class AdaptiveLimiter:
    def __init__(
        self,
        cfg: RateConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        load: bool = True,
    ) -> None:
        self.cfg = cfg
        self._now = clock
        self._interval = max(cfg.hard_floor, cfg.min_interval)
        self._ok_streak = 0
        self.floods = 0
        self.writes = 0
        self._last_write = float("-inf")
        self._blocked_until = float("-inf")
        self._window: deque[float] = deque()
        self._latency = 0.25          # میانگین متحرکِ تأخیرِ نوشتن (ثانیه)
        if load:
            self.load()

    # ── مقادیر ───────────────────────────────────────────────────
    @property
    def interval(self) -> float:
        return self._interval

    @property
    def latency(self) -> float:
        return self._latency

    @property
    def lead(self) -> float:
        """
        چقدر زودتر بفرستیم که دقیقاً سرِ وقت روی سرور بنشیند.

        سقف ۰.۶ ثانیه دارد تا اگر شبکه یک لحظه خراب شد، لیریک ناگهان
        چند ثانیه جلو نیفتد.
        """
        return min(0.6, max(0.0, self._latency * 0.9))

    def floor(self) -> float:
        return max(self.cfg.hard_floor, self.cfg.min_interval)

    # ── زمان‌بندی ────────────────────────────────────────────────
    def _trim(self, now: float) -> None:
        while self._window and now - self._window[0] > 60.0:
            self._window.popleft()

    def next_allowed(self, now: Optional[float] = None) -> float:
        now = self._now() if now is None else now
        t = max(self._last_write + self._interval, self._blocked_until)
        cap = self.cfg.max_writes_per_minute
        if cap > 0:
            self._trim(now)
            if len(self._window) >= cap:
                t = max(t, self._window[0] + 60.0)
        return t

    def ready_in(self, now: Optional[float] = None) -> float:
        now = self._now() if now is None else now
        return max(0.0, self.next_allowed(now) - now)

    def ready(self, now: Optional[float] = None) -> bool:
        return self.ready_in(now) <= 0.0

    # ── بازخورد ──────────────────────────────────────────────────
    def on_success(self, latency_s: float = 0.0, now: Optional[float] = None) -> None:
        now = self._now() if now is None else now
        if latency_s > 0:
            self._latency = self._latency * 0.7 + latency_s * 0.3
        self._last_write = now
        self._window.append(now)
        self._trim(now)
        self.writes += 1

        self._ok_streak += 1
        if self._ok_streak >= self.cfg.recover_after:
            self._ok_streak = 0
            floor = self.floor()
            if self._interval > floor:
                old = self._interval
                self._interval = max(floor, self._interval * self.cfg.recover_factor)
                log.info("اوضاع آرام است — فاصله %.2f → %.2f ثانیه", old, self._interval)
                self.save()

    def on_flood(self, seconds: float, now: Optional[float] = None) -> None:
        now = self._now() if now is None else now
        seconds = max(1.0, float(seconds))
        self.floods += 1
        self._ok_streak = 0
        self._blocked_until = now + seconds + 1.0
        old = self._interval
        self._interval = min(
            self.cfg.max_interval_cap,
            max(self._interval * self.cfg.backoff_factor, self.cfg.hard_floor),
        )
        log.warning(
            "FLOOD_WAIT %.0fs — فاصله %.2f → %.2f ثانیه (فلاد #%d)",
            seconds, old, self._interval, self.floods,
        )
        self.save()

    def on_error(self, now: Optional[float] = None) -> None:
        """خطای گذرا (شبکه و…): فقط کمی صبر، بدون عقب‌نشینیِ نرخ."""
        now = self._now() if now is None else now
        self._blocked_until = max(self._blocked_until, now + 1.0)

    # ── ماندگاری ─────────────────────────────────────────────────
    def load(self) -> None:
        try:
            with open(self.cfg.state_file, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        try:
            self._interval = max(
                self.cfg.hard_floor,
                min(self.cfg.max_interval_cap, float(d["interval"])),
            )
            self.floods = int(d.get("floods", 0))
            log.info("فاصله‌ی یادگرفته‌شده از اجرای قبلی: %.2f ثانیه", self._interval)
        except (KeyError, TypeError, ValueError):
            pass

    def save(self) -> None:
        path = self.cfg.state_file
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "interval": round(self._interval, 3),
                        "floods": self.floods,
                        "writes": self.writes,
                    },
                    f,
                )
            os.replace(tmp, path)
        except OSError as e:
            log.debug("ذخیره‌ی وضعیت نرخ نشد: %s", e)

    def stats(self) -> dict:
        return {
            "interval": round(self._interval, 2),
            "writes": self.writes,
            "floods": self.floods,
            "latency_ms": round(self._latency * 1000),
            "lead_ms": round(self.lead * 1000),
            "in_window": len(self._window),
        }
