"""
ساعتِ پخش — قلبِ دقتِ این پروژه.

ایده: دستگاهِ پخش‌کننده هر بار که وضعیت عوض می‌شه (پلی/پاز/سیک/آهنگ جدید)
یک عکس لحظه‌ای می‌فرسته. بین این پیام‌ها ما موقعیت را با ساعتِ مونوتونیکِ
خودمان *درون‌یابی* می‌کنیم، نه اینکه دوباره بپرسیم.

چرا این دقیق‌تر از پرسیدن مکرر است:
  • هیچ تأخیر شبکه‌ای وارد عدد نمی‌شود
  • بین دو پیام هم عدد زنده می‌ماند (رزولوشن نامحدود)
  • ترافیک تقریباً صفر است

`time.monotonic()` استفاده می‌شود نه `time.time()` — تا تنظیم ساعت سیستم
یا NTP وسط آهنگ همه‌چیز را خراب نکند.
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Optional

__all__ = ["Track", "Snapshot", "PlaybackClock"]


_WS = re.compile(r"\s+")

# آشغال‌هایی که توی اسم فایل/تایتلِ سایت‌های دانلود آهنگ زیاد دیده می‌شود
_NOISE = re.compile(
    r"""(?ix)
    \s*(?:
        [\(\[\{]\s*(?:
            official(?:\s+(?:music\s+)?(?:video|audio|lyric[s]?|visualizer))?
          | lyric[s]?(?:\s+video)?
          | audio | video | hd | hq | 4k | m/?v
          | remaster(?:ed)?(?:\s*\d{4})?
          | explicit | clean | radio\s+edit
          | full\s+album | free\s+download
          | prod\.?\s*by\s+[^\)\]\}]*
        )\s*[\)\]\}]
      | \|\s*(?:radio\s*javan|rj|music\s*fa|nex1music|bia2music|ganja2music)[^|]*
      | \s[-–—]\s*(?:\d{3}\s*kbps?|\d{3})\s*$
    )""",
)


def _norm(s: str) -> str:
    """نرمال‌سازی برای مقایسه — نه برای نمایش."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    # عربی → فارسی
    s = s.replace("ي", "ی").replace("ك", "ک")
    # اعراب و کشیده
    s = re.sub(r"[ؐ-ًؚ-ْـ‌‏‎]", "", s)
    s = _NOISE.sub("", s)
    s = _WS.sub(" ", s).strip().casefold()
    return s


@dataclass(frozen=True)
class Track:
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_ms: int = 0

    @property
    def key(self) -> str:
        """شناسه‌ی پایدار برای تشخیص «آهنگ عوض شد» و برای کش."""
        return f"{_norm(self.artist)}\x1f{_norm(self.title)}"

    @property
    def ok(self) -> bool:
        return bool(_norm(self.title))

    def __str__(self) -> str:  # برای لاگ
        a = self.artist or "?"
        return f"{a} – {self.title or '?'}"


@dataclass(frozen=True)
class Snapshot:
    track: Optional[Track]
    position_ms: float
    playing: bool
    rate: float
    #: چند ثانیه از آخرین خبرِ دستگاه گذشته
    age: float
    #: دستگاه قطع شده / خبری نیست
    stale: bool

    @property
    def active(self) -> bool:
        return bool(self.track and self.track.ok and self.playing and not self.stale)


@dataclass
class _Anchor:
    track: Optional[Track] = None
    position_ms: float = 0.0
    playing: bool = False
    rate: float = 1.0
    mono: float = field(default_factory=time.monotonic)


class PlaybackClock:
    """
    نگه‌دارنده‌ی «آخرین لنگر» + درون‌یابی.

    thread-safe نیست؛ فقط از داخل حلقه‌ی asyncio صدایش بزن.
    """

    def __init__(self, stale_after: float = 45.0) -> None:
        self.stale_after = float(stale_after)
        self._a = _Anchor()
        self._has_data = False
        #: هر تغییرِ معنادار این را ست می‌کند تا رندرر فوراً بیدار شود
        self.changed = asyncio.Event()
        #: شمارنده‌ی تغییر آهنگ — برای اینکه رندرر بفهمد باید لیریک نو بگیرد
        self.generation = 0

    # ── ورودی ────────────────────────────────────────────────────
    def update(
        self,
        track: Optional[Track],
        position_ms: float,
        playing: bool,
        rate: float = 1.0,
        *,
        latency_ms: float = 0.0,
    ) -> None:
        """
        عکس لحظه‌ای جدید از دستگاه.

        `latency_ms` = تخمین زمانی که این عدد در راه بوده. اگر دستگاه
        زمان ارسال را بفرستد، منبع می‌تواند این را حساب کند و ما جبرانش
        می‌کنیم.
        """
        now = time.monotonic()
        rate = float(rate) if rate and rate > 0 else 1.0
        pos = float(position_ms) + (latency_ms if playing else 0.0)
        if pos < 0:
            pos = 0.0

        old = self._a
        changed_track = (old.track.key if old.track else None) != (
            track.key if track else None
        )

        # پرش معنادار؟ (سیک کردن یا دریفت)
        drift = abs(self._project(old, now) - pos) if not changed_track else 0.0

        self._a = _Anchor(
            track=track, position_ms=pos, playing=bool(playing), rate=rate, mono=now
        )
        self._has_data = True

        if changed_track:
            self.generation += 1

        if changed_track or old.playing != playing or drift > 400.0:
            self.changed.set()

    def touch(self) -> None:
        """ضربان بدون تغییر وضعیت — فقط برای اینکه stale نشویم."""
        a = self._a
        now = time.monotonic()
        self._a = replace(a, position_ms=self._project(a, now), mono=now)

    def clear(self) -> None:
        """دستگاه رفت / چیزی پخش نمی‌شود."""
        if self._a.track is not None or self._a.playing:
            self.generation += 1
            self.changed.set()
        self._a = _Anchor()
        self._has_data = True

    # ── خروجی ────────────────────────────────────────────────────
    @staticmethod
    def _project(a: _Anchor, now: float) -> float:
        if not a.playing:
            return a.position_ms
        return a.position_ms + (now - a.mono) * 1000.0 * a.rate

    def snapshot(self) -> Snapshot:
        a = self._a
        now = time.monotonic()
        age = now - a.mono
        pos = self._project(a, now)

        # از ته آهنگ رد نشو
        if a.track and a.track.duration_ms:
            pos = min(pos, float(a.track.duration_ms))

        stale = (not self._has_data) or (age > self.stale_after)
        return Snapshot(
            track=a.track,
            position_ms=max(0.0, pos),
            playing=a.playing and not stale,
            rate=a.rate,
            age=age,
            stale=stale,
        )

    def consume(self) -> None:
        """
        پرچمِ «تغییر» را پاک کن.

        باید *قبل از* خواندن snapshot صدا زده شود، نه قبل از خوابیدن. اگر
        برعکس باشد، تغییری که وسطِ محاسبه برسد پاک می‌شود و تا سقفِ خواب
        (چند ثانیه) نادیده می‌ماند — یعنی دقیقاً همان تأخیری که کل پروژه
        برای نداشتنش نوشته شده.
        """
        self.changed.clear()

    async def wait_change(self, timeout: float) -> bool:
        """
        تا سقف `timeout` ثانیه بخواب، ولی با هر تغییرِ وضعیت فوراً بیدار شو.

        پرچم را پاک نمی‌کند؛ اگر از قبل ست شده باشد بلافاصله برمی‌گردد.
        """
        if self.changed.is_set():
            return True
        if timeout <= 0:
            return False
        try:
            await asyncio.wait_for(self.changed.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False
