from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Optional

__all__ = ["Track", "Snapshot", "PlaybackClock"]


_WS = re.compile(r"\s+")

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
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("ي", "ی").replace("ك", "ک")
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
        return f"{_norm(self.artist)}\x1f{_norm(self.title)}"

    @property
    def ok(self) -> bool:
        return bool(_norm(self.title))

    def __str__(self) -> str:
        a = self.artist or "?"
        return f"{a} – {self.title or '?'}"


@dataclass(frozen=True)
class Snapshot:
    track: Optional[Track]
    position_ms: float
    playing: bool
    rate: float
    age: float
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

    def __init__(self, stale_after: float = 45.0) -> None:
        self.stale_after = float(stale_after)
        self._a = _Anchor()
        self._has_data = False
        self.changed = asyncio.Event()
        self.generation = 0

    def update(
        self,
        track: Optional[Track],
        position_ms: float,
        playing: bool,
        rate: float = 1.0,
        *,
        latency_ms: float = 0.0,
    ) -> None:
        now = time.monotonic()
        rate = float(rate) if rate and rate > 0 else 1.0
        pos = float(position_ms) + (latency_ms if playing else 0.0)
        if pos < 0:
            pos = 0.0

        old = self._a
        changed_track = (old.track.key if old.track else None) != (
            track.key if track else None
        )

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
        a = self._a
        now = time.monotonic()
        self._a = replace(a, position_ms=self._project(a, now), mono=now)

    def clear(self) -> None:
        if self._a.track is not None or self._a.playing:
            self.generation += 1
            self.changed.set()
        self._a = _Anchor()
        self._has_data = True

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
        self.changed.clear()

    async def wait_change(self, timeout: float) -> bool:
        if self.changed.is_set():
            return True
        if timeout <= 0:
            return False
        try:
            await asyncio.wait_for(self.changed.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False
