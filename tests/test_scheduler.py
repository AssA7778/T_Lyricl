"""
تست تصمیمِ زمان‌بندی.

مهم‌ترین چیزی که اینجا اثبات می‌شود: **هیچ‌وقت متنِ منقضی‌شده نوشته
نمی‌شود.** وقتی محدودیت نرخ اجازه ندهد، برنامه صبر می‌کند و بعد *دوباره*
حساب می‌کند — یعنی از خط می‌پرد، عقب نمی‌ماند.
"""

from __future__ import annotations

import unittest

from tglyrics.render import RenderConfig, Renderer
from tglyrics.lyrics.lrc import parse_lrc
from tglyrics.scheduler import decide


class TestDecide(unittest.TestCase):
    def test_writes_when_changed_and_allowed(self):
        d = decide(
            frame_text="new", frame_until_ms=5000, current_text="old",
            t_ms=1000, playing=True, ready_in=0.0,
        )
        self.assertTrue(d.write)
        self.assertEqual(d.sleep, 0.0)

    def test_waits_exactly_until_allowed_without_writing(self):
        d = decide(
            frame_text="new", frame_until_ms=5000, current_text="old",
            t_ms=1000, playing=True, ready_in=2.4,
        )
        self.assertFalse(d.write)
        self.assertAlmostEqual(d.sleep, 2.4)

    def test_sleeps_until_frame_boundary(self):
        d = decide(
            frame_text="same", frame_until_ms=5000, current_text="same",
            t_ms=1500, playing=True, rate=1.0,
        )
        self.assertFalse(d.write)
        self.assertAlmostEqual(d.sleep, 3.5)

    def test_boundary_scales_with_playback_rate(self):
        d = decide(
            frame_text="same", frame_until_ms=5000, current_text="same",
            t_ms=1000, playing=True, rate=2.0,
        )
        self.assertAlmostEqual(d.sleep, 2.0)

    def test_paused_idles(self):
        d = decide(
            frame_text="same", frame_until_ms=5000, current_text="same",
            t_ms=1000, playing=False, max_sleep=3.0,
        )
        self.assertFalse(d.write)
        self.assertEqual(d.sleep, 3.0)

    def test_no_expiry_idles(self):
        d = decide(
            frame_text="same", frame_until_ms=None, current_text="same",
            t_ms=1000, playing=True, max_sleep=3.0,
        )
        self.assertEqual(d.sleep, 3.0)

    def test_past_boundary_never_sleeps_zero_forever(self):
        d = decide(
            frame_text="same", frame_until_ms=900, current_text="same",
            t_ms=1000, playing=True,
        )
        self.assertFalse(d.write)
        self.assertGreater(d.sleep, 0.0)

    def test_never_negative_sleep(self):
        for until in (0, 500, 999, 1000, 1001, 99999):
            d = decide(
                frame_text="same", frame_until_ms=until, current_text="same",
                t_ms=1000, playing=True,
            )
            self.assertGreaterEqual(d.sleep, 0.0)


class TestNoStaleWrites(unittest.TestCase):
    """
    شبیه‌سازیِ کامل: یک آهنگ با خط‌های تند + محدودیت نرخِ سخت.
    ادعا: هر چیزی که نوشته می‌شود، دقیقاً همان چیزی است که *در آن لحظه*
    باید روی بیو باشد — نه یک خطِ قدیمی.
    """

    def test_skips_forward_never_lags(self):
        lrc = "\n".join(f"[00:{i:02d}.00]خط شماره {i}" for i in range(0, 40, 2))
        ly = parse_lrc(lrc, duration_ms=42000)
        r = Renderer(RenderConfig(limit=70, show_interlude=False))

        MIN_INTERVAL = 7.0          # عمداً کندتر از نرخِ خط‌ها (۲ ثانیه)
        t = 0.0                     # زمان لیریک (ms) — همان زمان دیوار اینجا
        last_write_at = -1e9
        current = ""
        written: list[tuple[float, str]] = []

        while t < 40000:
            frame = r.render(ly, None, t)
            ready_in = max(0.0, (last_write_at + MIN_INTERVAL * 1000 - t) / 1000.0)
            d = decide(
                frame_text=frame.text, frame_until_ms=frame.until_ms,
                current_text=current, t_ms=t, playing=True, ready_in=ready_in,
            )
            if d.write:
                # ادعای اصلی: چیزی که می‌نویسیم همین الان معتبر است
                expected = r.render(ly, None, t)
                self.assertEqual(frame.text, expected.text)
                self.assertLess(t, expected.until_ms)
                current = frame.text
                last_write_at = t
                written.append((t, frame.text))
                continue
            t += max(d.sleep, 0.001) * 1000.0

        self.assertGreater(len(written), 3)
        # فاصله‌ی نوشتن‌ها هرگز از حد مجاز کمتر نشده
        for (a, _), (b, _) in zip(written, written[1:]):
            self.assertGreaterEqual(b - a, MIN_INTERVAL * 1000 - 1)
        # و چون خط‌ها هر ۲ ثانیه‌اند ولی ما هر ۷ ثانیه می‌نویسیم، پریدن
        # اتفاق افتاده — یعنی خط‌ها را جا انداختیم نه اینکه عقب بیفتیم
        nums = [int(txt.split()[-1]) for _, txt in written]
        self.assertEqual(nums, sorted(nums))
        self.assertLess(len(nums), 20)

    def test_fast_limit_shows_every_line(self):
        lrc = "\n".join(f"[00:{i:02d}.00]خط {i}" for i in range(0, 20, 4))
        ly = parse_lrc(lrc, duration_ms=24000)
        r = Renderer(RenderConfig(limit=70, show_interlude=False))

        t, current, seen = 0.0, "", []
        while t < 20000:
            frame = r.render(ly, None, t)
            d = decide(
                frame_text=frame.text, frame_until_ms=frame.until_ms,
                current_text=current, t_ms=t, playing=True, ready_in=0.0,
            )
            if d.write:
                current = frame.text
                seen.append(frame.text)
                continue
            t += max(d.sleep, 0.001) * 1000.0

        self.assertEqual(seen, [f"خط {i}" for i in range(0, 20, 4)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
