"""
ارکستراتور — همه‌چیز اینجا به هم وصل می‌شود.

منطقِ حلقه‌ی اصلی، که کل ادعای «حتی یک ثانیه دیر نمی‌کند» روی آن سوار است:

  ۱. موقعیتِ *همین لحظه* را از ساعتِ درون‌یاب بگیر
  ۲. یک `lead` جلوتر برو (به اندازه‌ی تأخیرِ اندازه‌گیری‌شده‌ی خودِ تلگرام)،
     تا درخواست دقیقاً سرِ تایم‌استمپ روی سرور بنشیند نه بعدش
  ۳. بپرس «الان باید چه متنی باشد و تا کِی معتبر است»
  ۴. اگر با بیوی فعلی فرق دارد و اجازه‌ی نوشتن داریم → بنویس
  ۵. دقیقاً سرِ لحظه‌ی انقضای همین فریم بیدار شو، نه یک لحظه دیرتر

و نکته‌ی حیاتی: هیچ صفی در کار نیست. اگر محدودیتِ نرخ اجازه نداد الان
بنویسیم، وقتی اجازه داد **دوباره از نو حساب می‌کنیم**. یعنی خطِ قدیمی هرگز
با تأخیر نوشته نمی‌شود؛ از رویش می‌پریم و خطِ همان لحظه می‌رود روی بیو.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import re
import signal
import time
from typing import Optional

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from . import __version__
from .clock import PlaybackClock
from .config import Config
from .lyrics.engine import LyricsEngine
from .lyrics.store import LyricsStore
from .render import Renderer
from .scheduler import decide
from .sources import build as build_source
from .telegram_writer import BioWriter

log = logging.getLogger("tglyrics")

__all__ = ["App", "setup_logging"]

MAX_SLEEP = 3.0
KV_ORIGINAL = "original_bio"
KV_LAST = "last_written"


def setup_logging(level: str = "INFO", file: str = "") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter(
        "%(asctime)s %(levelname).1s %(name)s: %(message)s", "%H:%M:%S"
    )
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if file:
        os.makedirs(os.path.dirname(os.path.abspath(file)) or ".", exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            file, maxBytes=2_000_000, backupCount=2, encoding="utf-8"
        )
        fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname).1s %(name)s: %(message)s")
        )
        root.addHandler(fh)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


class App:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.clock = PlaybackClock(stale_after=cfg.stale_after)
        self.store = LyricsStore(cfg.lyrics.cache_db, cfg.lyrics.local_dir)
        self.lyrics = LyricsEngine(
            self.store,
            user_agent=cfg.lyrics.user_agent,
            global_offset_ms=cfg.lyrics.global_offset_ms,
        )
        self.renderer = Renderer(cfg.render)
        self.client = TelegramClient(
            StringSession(cfg.telegram.session),
            cfg.telegram.api_id,
            cfg.telegram.api_hash,
            flood_sleep_threshold=0,   # می‌خواهیم خودمان FLOOD_WAIT را ببینیم
            connection_retries=None,   # بی‌نهایت — VPS و اینترنت قطع‌وصل می‌شود
            retry_delay=2,
            auto_reconnect=True,
            receive_updates=bool(cfg.telegram.control_chat),
        )
        self.writer = BioWriter(self.client, cfg.rate)
        self.source = None
        self.enabled = True
        self._stop = asyncio.Event()
        self._started = time.time()
        self._frames = 0
        self._me_id: Optional[int] = None

    # ── وضعیت ────────────────────────────────────────────────────
    def status(self) -> dict:
        snap = self.clock.snapshot()
        hit, lyr = (False, None)
        if snap.track:
            hit, lyr = self.lyrics.cached(snap.track)
        return {
            "version": __version__,
            "uptime_s": round(time.time() - self._started),
            "enabled": self.enabled,
            "source": self.source.describe() if self.source else None,
            "bio": self.writer.stats(),
            "frames_rendered": self._frames,
            "playback": {
                "track": str(snap.track) if snap.track else None,
                "position_ms": round(snap.position_ms),
                "playing": snap.playing,
                "stale": snap.stale,
                "age_s": round(snap.age, 1),
            },
            "lyrics": {
                "resolved": hit,
                "found": bool(lyr),
                "lines": len(lyr.lines) if lyr else 0,
                "word_level": lyr.word_level if lyr else False,
                "source": lyr.source if lyr else "",
                "offset_ms": self.lyrics.offset_for(snap.track, lyr) if snap.track else 0,
            },
        }

    # ── اجرا ─────────────────────────────────────────────────────
    async def run(self) -> None:
        self.store.open()
        await self.lyrics.start()

        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError(
                "سشن معتبر نیست یا منقضی شده. دوباره بساز:  python login.py"
            )
        await self.writer.start(forced_limit=self.cfg.telegram.bio_limit)
        self.renderer.limit = self.writer.limit
        self._me_id = (await self.client.get_me()).id
        self._resolve_original()

        src_cfg = dict(self.cfg.source_cfg)
        if self.cfg.source_kind == "webhook":
            src_cfg["status_provider"] = self.status
        self.source = build_source(self.cfg.source_kind, src_cfg, self.clock)
        await self.source.start()

        self._install_control()
        self._install_signals()

        log.info(
            "tglyrics %s آماده است — منبع: %s | سقف بیو: %d | فاصله: %.2f ثانیه",
            __version__, self.source.describe(), self.writer.limit, self.writer.interval,
        )

        try:
            await self._render_loop()
        finally:
            await self._shutdown()

    def _resolve_original(self) -> None:
        """بیوی واقعیِ کاربر را پیدا کن، حتی اگر دفعه‌ی قبل وسط کار کرش کرده باشیم."""
        current = self.writer.original_bio
        stored = self.store.kv_get(KV_ORIGINAL)
        last = self.store.kv_get(KV_LAST)

        if stored is None:
            self.store.kv_set(KV_ORIGINAL, current)
            original = current
        elif last is not None and current == last:
            # چیزی که الان توی بیو است را خودمان نوشته بودیم
            original = stored
        else:
            # کاربر خودش بیو را عوض کرده — همان تازه را اصل بگیر
            self.store.kv_set(KV_ORIGINAL, current)
            original = current

        self.writer.set_original(original)
        if original != current:
            log.info("بیوی اصلی از دیتابیس بازیابی شد: %r", original)

    def _install_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for s in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(s, self._stop.set)
            except (NotImplementedError, RuntimeError):
                pass

    async def _shutdown(self) -> None:
        log.info("در حال بستن…")
        if self.source:
            await self.source.stop()
        try:
            await self.writer.restore()
        except Exception as e:  # noqa: BLE001
            log.warning("برگرداندن بیو نشد: %s", e)
        await self.lyrics.close()
        self.store.close()
        try:
            await self.client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    # ── حلقه‌ی اصلی ──────────────────────────────────────────────
    async def _render_loop(self) -> None:
        while not self._stop.is_set():
            # ترتیب مهم است: اول پرچم را پاک کن، بعد وضعیت را بخوان.
            # هر تغییری که از این لحظه به بعد برسد، خواب را می‌شکند.
            self.clock.consume()
            try:
                sleep_for = await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("خطا در حلقه‌ی اصلی: %s", e)
                sleep_for = 2.0

            if sleep_for > 0:
                await self.clock.wait_change(min(sleep_for, MAX_SLEEP))
            else:
                await asyncio.sleep(0)

    async def _tick(self) -> float:
        snap = self.clock.snapshot()

        if not self.enabled:
            await self._write(self._idle_text())
            return MAX_SLEEP

        alive = bool(snap.track and snap.track.ok and not snap.stale)
        if not alive:
            await self._write(self._idle_text())
            return MAX_SLEEP

        track = snap.track
        hit, lyr = self.lyrics.cached(track)
        if not hit:
            t = self.lyrics.request(track)
            if t is not None:
                # به‌محض رسیدنِ لیریک، حلقه را بیدار کن
                t.add_done_callback(lambda _t: self.clock.changed.set())
            lyr = None

        offset = self.lyrics.offset_for(track, lyr)
        lead_ms = (self.writer.lead * 1000.0) if snap.playing else 0.0
        t_ms = snap.position_ms + offset + lead_ms

        frame = self.renderer.render(lyr, track, t_ms)
        self._frames += 1

        d = decide(
            frame_text=frame.text,
            frame_until_ms=frame.until_ms,
            current_text=self.writer.current,
            t_ms=t_ms,
            playing=snap.playing,
            rate=snap.rate,
            ready_in=self.writer.ready_in(),
            max_sleep=MAX_SLEEP,
        )
        if d.write:
            # موفق شد → فوراً دوباره حساب کن. نشد → کمی صبر، بعد دوباره.
            return 0.0 if await self._write(frame.text) else 0.25
        return d.sleep

    async def _write(self, text: str) -> bool:
        ok = await self.writer.write(text)
        if ok:
            try:
                self.store.kv_set(KV_LAST, self.writer.current)
            except Exception:  # noqa: BLE001
                pass
        return ok

    def _idle_text(self) -> str:
        return self.cfg.telegram.idle_bio or self.writer.original_bio

    # ── کنترل از داخل تلگرام ─────────────────────────────────────
    def _install_control(self) -> None:
        chat = self.cfg.telegram.control_chat
        if not chat:
            return
        prefix = re.escape(self.cfg.telegram.control_prefix or ".")
        pat = re.compile(rf"^{prefix}(?:lrc|lyrics|ل)\b\s*(.*)$", re.I | re.S)

        @self.client.on(events.NewMessage(outgoing=True, pattern=pat))
        async def _handler(event):  # noqa: ANN001
            if chat == "me" and event.chat_id != self._me_id:
                return
            arg = (event.pattern_match.group(1) or "").strip()
            try:
                reply = await self._command(arg)
            except Exception as e:  # noqa: BLE001
                reply = f"❌ {e}"
            try:
                await event.edit(reply)
            except Exception:  # noqa: BLE001
                pass

        log.info(
            "کنترل فعال است — توی %s بنویس: %slrc",
            "Saved Messages" if chat == "me" else chat,
            self.cfg.telegram.control_prefix,
        )

    async def _command(self, arg: str) -> str:
        p = self.cfg.telegram.control_prefix
        parts = arg.split()
        cmd = parts[0].lower() if parts else "status"
        rest = " ".join(parts[1:]).strip()
        snap = self.clock.snapshot()

        if cmd in ("status", "s", ""):
            st = self.status()
            pb, b, ly = st["playback"], st["bio"], st["lyrics"]
            emoji = "🟢" if self.enabled else "⏸"
            return (
                f"{emoji} **tglyrics {st['version']}**\n"
                f"منبع: `{st['source']}`\n"
                f"آهنگ: `{pb['track'] or '—'}`\n"
                f"موقعیت: `{pb['position_ms'] / 1000:.1f}s`"
                f"{' ▶️' if pb['playing'] else ' ⏸'}"
                f"{'  ⚠️ قطع' if pb['stale'] else ''}\n"
                f"لیریک: `{ly['lines']} خط`"
                f"{' (کلمه‌ای)' if ly['word_level'] else ''}"
                f"  منبع: `{ly['source'] or '—'}`\n"
                f"آفست: `{ly['offset_ms']:+d}ms`\n"
                f"سقف بیو: `{b['limit']}`  |  فاصله: `{b['interval']}s`\n"
                f"نوشته‌شده: `{b['writes']}`  |  فلاد: `{b['floods']}`"
                f"  |  تأخیر: `{b['latency_ms']}ms`\n"
                f"آپ‌تایم: `{st['uptime_s']}s`"
            )

        if cmd in ("on", "start", "روشن"):
            self.enabled = True
            self.clock.changed.set()
            return "🟢 روشن شد"

        if cmd in ("off", "stop", "pause", "خاموش"):
            self.enabled = False
            self.clock.changed.set()
            return "⏸ خاموش شد — بیو برمی‌گردد سر جایش"

        if cmd in ("sync", "offset", "آفست"):
            if not snap.track:
                return "الان آهنگی پخش نمی‌شود"
            key = snap.track.key
            if not rest:
                return f"آفستِ این آهنگ: `{self.store.get_offset(key):+d}ms`"
            m = re.fullmatch(r"([+-]?\d+)(?:\s*ms)?", rest)
            if not m:
                return f"مثال: `{p}lrc sync +300`  یا  `{p}lrc sync -250`"
            delta = int(m.group(1))
            cur = self.store.get_offset(key)
            new = delta if rest[0] not in "+-" else cur + delta
            self.store.set_offset(key, new)
            self.clock.changed.set()
            return f"آفستِ «{snap.track}» شد `{new:+d}ms`"

        if cmd in ("reload", "refetch", "دوباره"):
            if not snap.track:
                return "الان آهنگی پخش نمی‌شود"
            self.store.forget(snap.track.key)
            self.lyrics._mem.pop(snap.track.key, None)  # noqa: SLF001
            if snap.track.key in self.lyrics._order:    # noqa: SLF001
                self.lyrics._order.remove(snap.track.key)  # noqa: SLF001
            self.clock.changed.set()
            return "🔄 کش پاک شد، دوباره دنبال لیریک می‌گردم"

        if cmd in ("bio", "idle"):
            self.cfg.telegram.idle_bio = rest
            self.renderer.cfg.idle_text = rest
            self.clock.changed.set()
            return f"بیوی حالتِ بیکار شد: `{rest or '(بیوی اصلی)'}`"

        if cmd == "limit":
            if rest.isdigit():
                self.writer.limit = int(rest)
                self.renderer.limit = int(rest)
                return f"سقف بیو دستی شد `{rest}`"
            return f"سقف فعلی `{self.writer.limit}`"

        if cmd in ("help", "h", "?", "راهنما"):
            return (
                f"`{p}lrc`            وضعیت\n"
                f"`{p}lrc on|off`     روشن/خاموش\n"
                f"`{p}lrc sync +300`  جلو بردن لیریک (ms)\n"
                f"`{p}lrc sync -250`  عقب بردن\n"
                f"`{p}lrc sync 0`     صفر کردن\n"
                f"`{p}lrc reload`     دوباره دنبال لیریک بگرد\n"
                f"`{p}lrc bio متن`    بیوی حالت بیکار\n"
                f"`{p}lrc limit 70`   سقف کاراکتر"
            )

        return f"دستور ناشناخته. `{p}lrc help`"
