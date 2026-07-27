"""
منبع وب‌هوک — دستگاهِ پخش‌کننده خودش خبر می‌دهد.

این دقیق‌ترین راهِ ممکن است، و برخلاف چیزی که به‌نظر می‌رسد از Spotify Web API
هم دقیق‌تر است: اسپاتیفای `progress_ms` را با ۰.۵ تا ۱.۵ ثانیه تأخیرِ تصادفی
می‌دهد (باگ شناخته‌شده و حل‌نشده)، ولی `video.currentTime` مرورگر و
`PlaybackState.getPosition()` اندروید مقدار واقعیِ خودِ پلیرند.

جبران تأخیر شبکه
────────────────
یک هماهنگ‌سازیِ ساعتِ سبک به سبک NTP داریم. کلاینت چند بار `GET /time` می‌زند و
اختلافِ ساعتش با سرور را حساب می‌کند، بعد موقع ارسال می‌گوید «این عدد را در
فلان لحظه‌ی *ساعتِ تو* گرفتم». سرور تفاضل را به‌عنوان تأخیر به موقعیت اضافه
می‌کند. نتیجه: چند میلی‌ثانیه خطا، نه چند صد میلی‌ثانیه.

پروتکل
──────
GET  /time     → {"server_ms": 1690000000000}
POST /ingest   → بدنه‌ی JSON (پایین)
GET  /ingest   → همان با پارامترهای کوئری (برای اسکریپت‌های ساده / curl)
GET  /health   → {"ok": true}

بدنه:
{
  "token": "...",                  یا هدر  Authorization: Bearer ...
  "event": "state" | "heartbeat" | "stop",
  "title": "...", "artist": "...", "album": "...",
  "duration_ms": 214000,
  "position_ms": 41230,
  "playing": true,
  "rate": 1.0,
  "captured_at_server_ms": 1690000000123   (اختیاری ولی توصیه‌شده)
}
"""

from __future__ import annotations

import hmac
import logging
import time
from typing import Any, Optional

from aiohttp import web

from ..clock import PlaybackClock, Track
from .base import Source

log = logging.getLogger("tglyrics.webhook")

__all__ = ["WebhookSource"]


def _f(d: dict, *keys, default=0.0) -> float:
    for k in keys:
        if k in d and d[k] is not None:
            try:
                return float(d[k])
            except (TypeError, ValueError):
                pass
    return default


def _s(d: dict, *keys, default="") -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default


def _b(d: dict, *keys, default=False) -> bool:
    for k in keys:
        if k in d and d[k] is not None:
            v = d[k]
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "on", "playing")
    return default


class WebhookSource(Source):
    name = "webhook"

    def __init__(
        self,
        clock: PlaybackClock,
        host: str = "0.0.0.0",
        port: int = 8787,
        token: str = "",
        status_provider=None,
    ) -> None:
        super().__init__(clock)
        self.host = host
        self.port = int(port)
        self.token = token or ""
        self.status_provider = status_provider
        self._runner: Optional[web.AppRunner] = None
        self.last_seen: float = 0.0
        self.last_agent: str = ""

    # ── سرور ─────────────────────────────────────────────────────
    async def start(self) -> None:
        if not self.token or self.token == "CHANGE_ME_TO_SOMETHING_LONG":
            log.warning(
                "⚠️  توکن وب‌هوک تنظیم نشده! هر کسی که آی‌پی سرورت را بداند "
                "می‌تواند بیوت را عوض کند. توی config.toml عوضش کن."
            )
        app = web.Application(client_max_size=64 * 1024)
        app.add_routes(
            [
                web.get("/time", self._h_time),
                web.get("/health", self._h_health),
                web.get("/status", self._h_status),
                web.post("/ingest", self._h_ingest),
                web.get("/ingest", self._h_ingest),
                web.options("/ingest", self._h_options),
            ]
        )
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        log.info("وب‌هوک روی http://%s:%d گوش می‌دهد", self.host, self.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    def describe(self) -> str:
        return f"webhook :{self.port}"

    # ── هندلرها ──────────────────────────────────────────────────
    @staticmethod
    def _cors(resp: web.Response) -> web.Response:
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

    async def _h_options(self, _req: web.Request) -> web.Response:
        return self._cors(web.Response(status=204))

    async def _h_time(self, _req: web.Request) -> web.Response:
        return self._cors(web.json_response({"server_ms": time.time() * 1000.0}))

    async def _h_health(self, _req: web.Request) -> web.Response:
        return self._cors(
            web.json_response(
                {
                    "ok": True,
                    "last_seen_s": round(time.time() - self.last_seen, 1)
                    if self.last_seen
                    else None,
                    "agent": self.last_agent,
                }
            )
        )

    async def _h_status(self, _req: web.Request) -> web.Response:
        data: dict[str, Any] = {}
        if self.status_provider:
            try:
                data = self.status_provider() or {}
            except Exception as e:  # noqa: BLE001
                data = {"error": str(e)}
        snap = self.clock.snapshot()
        data.setdefault("playback", {
            "track": str(snap.track) if snap.track else None,
            "position_ms": round(snap.position_ms),
            "playing": snap.playing,
            "stale": snap.stale,
            "age_s": round(snap.age, 1),
        })
        return self._cors(web.json_response(data))

    def _auth_ok(self, req: web.Request, body: dict) -> bool:
        if not self.token:
            return True
        given = ""
        auth = req.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            given = auth[7:].strip()
        given = given or req.query.get("token", "") or str(body.get("token") or "")
        return hmac.compare_digest(given, self.token)

    async def _h_ingest(self, req: web.Request) -> web.Response:
        body: dict = {}
        if req.method == "POST":
            try:
                body = await req.json()
            except Exception:  # noqa: BLE001
                body = {}
            if not isinstance(body, dict):
                body = {}
        merged = {**dict(req.query), **body}

        if not self._auth_ok(req, body):
            return self._cors(web.json_response({"error": "unauthorized"}, status=401))

        self.last_seen = time.time()
        self.last_agent = _s(merged, "agent", default=req.headers.get("User-Agent", "")[:60])

        event = _s(merged, "event", default="state").lower()
        if event == "stop":
            self.clock.clear()
            return self._cors(web.json_response({"ok": True, "cleared": True}))

        title = _s(merged, "title", "track", "name")
        if not title:
            # ضربان بدون متادیتا — فقط زنده‌بودن دستگاه را اعلام می‌کند
            self.clock.touch()
            return self._cors(web.json_response({"ok": True, "noop": True}))

        # تأخیر شبکه
        latency_ms = 0.0
        cap = _f(merged, "captured_at_server_ms", default=0.0)
        if cap > 0:
            latency_ms = max(0.0, min(3000.0, time.time() * 1000.0 - cap))

        track = Track(
            title=title,
            artist=_s(merged, "artist", "artists", "author", "performer"),
            album=_s(merged, "album"),
            duration_ms=int(_f(merged, "duration_ms", default=0.0))
            or int(_f(merged, "duration", default=0.0) * 1000.0),
        )

        pos = _f(merged, "position_ms", default=-1.0)
        if pos < 0:
            pos = _f(merged, "position", "currentTime", "elapsed", default=0.0) * 1000.0

        self.clock.update(
            track,
            pos,
            _b(merged, "playing", "is_playing", default=True),
            _f(merged, "rate", "playbackRate", default=1.0),
            latency_ms=latency_ms,
        )
        return self._cors(
            web.json_response({"ok": True, "latency_ms": round(latency_ms)})
        )
