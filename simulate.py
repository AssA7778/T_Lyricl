#!/usr/bin/env python3
"""
شبیه‌ساز — بدون اینکه به تلگرام دست بزند، دقیقاً همان چیزی را که قرار است
توی بیو بنویسد، توی ترمینال نشان می‌دهد.

قبل از اینکه اکانتت را درگیر کنی این را اجرا کن. سه چیز را جواب می‌دهد:

  ۱. اصلاً برای این آهنگ لیریک سینک‌شده هست؟
  ۲. با سقفِ ۷۰ کاراکتر چه شکلی در می‌آید؟
  ۳. **دقیقاً چند بار در دقیقه باید بیو را عوض کند؟**  ← مهم‌ترین عدد

مثال:
    python simulate.py "Mohsen Yeganeh" "Behet Ghol Midam"
    python simulate.py "Radiohead" "Creep" --duration 239 --speed 8
    python simulate.py "Sirvan Khosravi" "Dust Daram Zendegi Ro" --from 45
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import deque

from tglyrics.clock import Track
from tglyrics.lyrics.engine import LyricsEngine
from tglyrics.lyrics.store import LyricsStore
from tglyrics.render import RenderConfig, Renderer

C_DIM = "\033[2m"
C_HL = "\033[1;36m"
C_OK = "\033[32m"
C_WARN = "\033[33m"
C_ERR = "\033[31m"
C_0 = "\033[0m"


def hms(ms: float) -> str:
    s = ms / 1000.0
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


async def main() -> int:
    ap = argparse.ArgumentParser("simulate")
    ap.add_argument("artist")
    ap.add_argument("title")
    ap.add_argument("--duration", type=float, default=0, help="مدت آهنگ (ثانیه)")
    ap.add_argument("--limit", type=int, default=70, help="سقف کاراکتر بیو")
    ap.add_argument("--speed", type=float, default=1.0, help="سرعت شبیه‌سازی")
    ap.add_argument("--from", dest="start", type=float, default=0.0, help="از ثانیه‌ی…")
    ap.add_argument("--offset", type=int, default=0, help="آفست (ms)")
    ap.add_argument("--mode", default="chunk", choices=["chunk", "truncate"])
    ap.add_argument("--min-chunk", type=int, default=1300)
    ap.add_argument("--cache", default="data/cache.db")
    ap.add_argument("--lyrics-dir", default="lyrics")
    ap.add_argument("--no-wait", action="store_true", help="بدون تأخیر، همه را چاپ کن")
    a = ap.parse_args()

    store = LyricsStore(a.cache, a.lyrics_dir)
    store.open()
    engine = LyricsEngine(store, user_agent="tglyrics-sim/1.0", global_offset_ms=a.offset)
    await engine.start()

    track = Track(
        title=a.title, artist=a.artist, duration_ms=int(a.duration * 1000)
    )

    print(f"\n{C_DIM}دنبال لیریک می‌گردم…{C_0}")
    t0 = time.monotonic()
    lyr = await engine.get(track)
    dt = time.monotonic() - t0

    if not lyr or not lyr.lines:
        print(
            f"{C_ERR}✗ لیریک سینک‌شده پیدا نشد.{C_0}\n\n"
            f"  کارهایی که می‌شود کرد:\n"
            f"  • اگر اسم فارسی دادی، فینگلیش امتحان کن (LRCLIB متادیتا را لاتین نگه می‌دارد)\n"
            f"  • --duration را بده؛ خیلی وقت‌ها تطبیق را درست می‌کند\n"
            f"  • فایل «{a.artist} - {a.title}.lrc» را دستی توی پوشه‌ی {a.lyrics_dir}/ بگذار\n"
        )
        await engine.close()
        store.close()
        return 1

    dur_ms = a.duration * 1000 or (lyr.lines[-1].end_ms + 5000)
    print(
        f"{C_OK}✓ پیدا شد{C_0} در {dt:.2f}s — "
        f"{len(lyr.lines)} خط"
        f"{'، کلمه‌ای (A2)' if lyr.word_level else '، خطی'}"
        f"  ← {lyr.source}\n"
    )

    rc = RenderConfig(
        limit=a.limit,
        long_line_mode=a.mode,
        min_chunk_ms=a.min_chunk,
        fallback_to_track=True,
    )
    renderer = Renderer(rc)
    offset = engine.offset_for(track, lyr)

    print(
        f"{C_DIM}{'زمان':>9}  {'کاراکتر':>4}  متن (سقف {a.limit}){C_0}\n"
        f"{C_DIM}{'─' * 78}{C_0}"
    )

    pos = a.start * 1000.0
    cur = None
    writes = 0
    stamps: list[float] = []
    over = 0

    wall0 = time.monotonic()
    while pos < dur_ms:
        frame = renderer.render(lyr, track, pos + offset)
        if frame.text != cur:
            cur = frame.text
            writes += 1
            stamps.append(pos / 1000.0)
            n = len(cur)
            color = C_HL if frame.kind == "lyric" else C_DIM
            flag = f" {C_ERR}!{C_0}" if n > a.limit else ""
            if n > a.limit:
                over += 1
            print(f"{hms(pos):>9}  {n:>4}{flag}  {color}{cur}{C_0}")

        nxt = frame.until_ms - offset if frame.until_ms is not None else dur_ms
        nxt = min(max(nxt, pos + 30), dur_ms)
        if not a.no_wait and a.speed > 0:
            target = wall0 + ((nxt - a.start * 1000.0) / 1000.0) / a.speed
            delay = target - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
        pos = nxt

    # ── آمار ─────────────────────────────────────────────────────
    total_min = max(1e-9, (dur_ms - a.start * 1000.0) / 60000.0)
    avg = writes / total_min

    win: deque[float] = deque()
    peak = 0
    for s in stamps:
        win.append(s)
        while win and s - win[0] > 60.0:
            win.popleft()
        peak = max(peak, len(win))

    print(f"{C_DIM}{'─' * 78}{C_0}")
    print(f"\n  کل نوشتن‌ها      : {writes}")
    print(f"  میانگین در دقیقه : {avg:.1f}")
    print(f"  {'اوج در ۶۰ ثانیه ':<17}: {peak}", end="")
    if peak > 25:
        print(f"   {C_ERR}← زیاد است، ریسک FLOOD_WAIT بالاست{C_0}")
    elif peak > 16:
        print(f"   {C_WARN}← نسبتاً زیاد؛ min_interval را ببر بالاتر{C_0}")
    else:
        print(f"   {C_OK}← منطقی{C_0}")
    if over:
        print(f"  {C_ERR}{over} فریم از سقف رد شد (نباید اتفاق بیفتد){C_0}")

    need = 60.0 / peak if peak else 0
    if peak:
        print(
            f"\n  {C_DIM}برای اینکه هیچ خطی جا نیفتد، min_interval باید "
            f"≤ {need:.1f} ثانیه باشد.{C_0}"
        )
        print(
            f"  {C_DIM}اگر بیشتر بگذاری، بعضی خط‌ها رد می‌شوند — ولی هیچ‌وقت "
            f"عقب نمی‌افتد؛ می‌پرد جلو.{C_0}\n"
        )

    await engine.close()
    store.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print()
