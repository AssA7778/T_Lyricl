"""
منبع MPRIS (اختیاری) — پلیرِ روی همین ماشینِ لینوکسی.

فقط وقتی به درد می‌خورد که برنامه روی *همان* کامپیوتری اجرا شود که آهنگ پخش
می‌کند (نه VPS). MPRIS موقعیت را با دقتِ میکروثانیه می‌دهد.

نیازمندی:  pip install dbus-next
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from ..clock import PlaybackClock, Track
from .base import Source

log = logging.getLogger("tglyrics.mpris")

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
PLAYER_IF = "org.mpris.MediaPlayer2.Player"

__all__ = ["MprisSource"]


def _unwrap(v: Any) -> Any:
    return getattr(v, "value", v)


class MprisSource(Source):
    name = "mpris"

    def __init__(
        self, clock: PlaybackClock, player: str = "", poll_interval: float = 1.0
    ) -> None:
        super().__init__(clock)
        self.player = (player or "").lower()
        self.poll = max(0.2, float(poll_interval))
        self._bus = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        try:
            from dbus_next.aio import MessageBus  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "منبع mpris به dbus-next نیاز دارد:  pip install dbus-next"
            ) from e
        self._task = asyncio.create_task(self._loop(), name="mpris-poll")
        log.info("MPRIS فعال شد%s", f" (پلیر: {self.player})" if self.player else "")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._bus:
            self._bus.disconnect()
            self._bus = None

    async def _connect(self):
        from dbus_next.aio import MessageBus

        if self._bus is None:
            self._bus = await MessageBus().connect()
        return self._bus

    async def _names(self) -> list[str]:
        from dbus_next import Message, MessageType

        bus = await self._connect()
        reply = await bus.call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="ListNames",
            )
        )
        if reply is None or reply.message_type != MessageType.METHOD_RETURN:
            return []
        return [n for n in reply.body[0] if n.startswith(MPRIS_PREFIX)]

    async def _props(self, name: str) -> Optional[dict]:
        from dbus_next import Message, MessageType, Variant  # noqa: F401

        bus = await self._connect()
        reply = await bus.call(
            Message(
                destination=name,
                path=PATH,
                interface="org.freedesktop.DBus.Properties",
                member="GetAll",
                signature="s",
                body=[PLAYER_IF],
            )
        )
        if reply is None or reply.message_type != MessageType.METHOD_RETURN:
            return None
        return {k: _unwrap(v) for k, v in reply.body[0].items()}

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.debug("mpris: %s", e)
                self._bus = None
            await asyncio.sleep(self.poll)

    async def _tick(self) -> None:
        names = await self._names()
        if self.player:
            names = [n for n in names if self.player in n.lower()] or names
        if not names:
            self.clock.clear()
            return

        chosen: Optional[tuple[str, dict]] = None
        for n in names:
            p = await self._props(n)
            if not p:
                continue
            if str(p.get("PlaybackStatus", "")).lower() == "playing":
                chosen = (n, p)
                break
            chosen = chosen or (n, p)

        if not chosen:
            self.clock.clear()
            return

        _name, p = chosen
        status = str(p.get("PlaybackStatus", "Stopped")).lower()
        if status == "stopped":
            self.clock.clear()
            return

        meta = {k: _unwrap(v) for k, v in (p.get("Metadata") or {}).items()}
        artists = meta.get("xesam:artist") or []
        if isinstance(artists, str):
            artists = [artists]

        track = Track(
            title=str(meta.get("xesam:title") or ""),
            artist=", ".join(str(a) for a in artists if a),
            album=str(meta.get("xesam:album") or ""),
            duration_ms=int((meta.get("mpris:length") or 0) // 1000),
        )
        pos_ms = float((p.get("Position") or 0)) / 1000.0
        rate = float(p.get("Rate") or 1.0) or 1.0

        snap = self.clock.snapshot()
        same = snap.track and snap.track.key == track.key
        drift = abs(snap.position_ms - pos_ms) if same else 9e9
        if same and snap.playing == (status == "playing") and drift < 250:
            self.clock.touch()
            return

        self.clock.update(track, pos_ms, status == "playing", rate)
