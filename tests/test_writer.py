"""
تست نویسنده‌ی بیو با کلاینتِ ساختگی — بدون شبکه، بدون اکانت واقعی.

اینجا رفتار در برابر خطاهای تلگرام تست می‌شود: FLOOD_WAIT، بیوی بلندتر از
سقف، و قطعی شبکه. اگر این‌ها درست هندل نشوند، یا اکانت محدود می‌شود یا
لیریک بی‌صدا قطع می‌شود.
"""

from __future__ import annotations

import asyncio
import logging
import unittest
from types import SimpleNamespace

try:
    from telethon.errors import FloodWaitError, RPCError
    HAVE_TELETHON = True
except ImportError:  # pragma: no cover
    HAVE_TELETHON = False

if HAVE_TELETHON:
    from tglyrics.ratelimit import RateConfig
    from tglyrics.telegram_writer import BioWriter

logging.getLogger("tglyrics.bio").setLevel(logging.CRITICAL)
logging.getLogger("tglyrics.rate").setLevel(logging.CRITICAL)


if HAVE_TELETHON:

    class AboutTooLong(RPCError):
        """ادای AboutTooLongError تلگرام (که زیرکلاسِ RPCError است)."""

        def __init__(self) -> None:  # noqa: D107
            pass

        def __str__(self) -> str:
            return "ABOUT_TOO_LONG (400)"


class FakeClient:
    """
    فقط سه درخواستی که BioWriter می‌زند را می‌فهمد.
    `script` صفی از استثناهاست؛ هر None یعنی موفق.
    """

    def __init__(self, bio: str = "بیوی اصلی", premium: bool = False):
        self.bio = bio
        self.premium = premium
        self.script: list = []
        self.written: list[str] = []
        self.calls = 0

    async def get_me(self):
        return SimpleNamespace(premium=self.premium, username="tester", first_name="T")

    async def __call__(self, req):
        name = type(req).__name__
        self.calls += 1
        if name == "GetAppConfigRequest":
            return SimpleNamespace(config=SimpleNamespace(value=[]))
        if name == "GetFullUserRequest":
            return SimpleNamespace(full_user=SimpleNamespace(about=self.bio))
        if name == "UpdateProfileRequest":
            if self.script:
                exc = self.script.pop(0)
                if exc is not None:
                    raise exc
            self.written.append(req.about)
            return SimpleNamespace()
        raise AssertionError(f"درخواست پیش‌بینی‌نشده: {name}")


def run(coro):
    return asyncio.run(coro)


@unittest.skipUnless(HAVE_TELETHON, "telethon نصب نیست")
class TestWriter(unittest.TestCase):
    def mk(self, **kw) -> tuple[BioWriter, FakeClient]:
        kw.setdefault("min_interval", 0.0)
        kw.setdefault("hard_floor", 0.0)
        kw.setdefault("max_writes_per_minute", 0)
        kw.setdefault("state_file", "")
        c = FakeClient()
        w = BioWriter(c, RateConfig(**kw))
        run(w.start())
        return w, c

    # ── راه‌اندازی ───────────────────────────────────────────────
    def test_start_reads_bio_and_limit(self):
        w, _ = self.mk()
        self.assertEqual(w.original_bio, "بیوی اصلی")
        self.assertEqual(w.limit, 70)      # غیرپریمیوم
        self.assertFalse(w.premium)

    def test_premium_default_limit(self):
        c = FakeClient(premium=True)
        w = BioWriter(c, RateConfig(state_file=""))
        run(w.start())
        self.assertEqual(w.limit, 140)

    def test_forced_limit_wins(self):
        c = FakeClient()
        w = BioWriter(c, RateConfig(state_file=""))
        run(w.start(forced_limit=55))
        self.assertEqual(w.limit, 55)

    # ── نوشتن ────────────────────────────────────────────────────
    def test_basic_write(self):
        w, c = self.mk()
        self.assertTrue(run(w.write("خط اول")))
        self.assertEqual(c.written, ["خط اول"])
        self.assertEqual(w.current, "خط اول")

    def test_duplicate_is_never_sent(self):
        """شمارنده‌ی فلاد تلگرام روی «متد + پارامتر» است — تکراری هم خرج دارد."""
        w, c = self.mk()
        run(w.write("یکسان"))
        for _ in range(5):
            self.assertFalse(run(w.write("یکسان")))
        self.assertEqual(len(c.written), 1)

    def test_newlines_replaced(self):
        w, c = self.mk()
        run(w.write("a\nb"))
        self.assertEqual(c.written[-1], "a · b")

    def test_truncated_to_limit(self):
        w, c = self.mk()
        run(w.write("x" * 500))
        self.assertLessEqual(len(c.written[-1]), 70)

    def test_rate_limit_blocks_second_write(self):
        c = FakeClient()
        w = BioWriter(c, RateConfig(min_interval=10.0, hard_floor=10.0, state_file=""))
        run(w.start())
        self.assertTrue(run(w.write("یک")))
        self.assertFalse(run(w.write("دو")))
        self.assertEqual(c.written, ["یک"])
        self.assertGreater(w.ready_in(), 9.0)

    def test_force_bypasses_rate_limit(self):
        c = FakeClient()
        w = BioWriter(c, RateConfig(min_interval=10.0, hard_floor=10.0, state_file=""))
        run(w.start())
        run(w.write("یک"))
        self.assertTrue(run(w.write("دو", force=True)))
        self.assertEqual(c.written, ["یک", "دو"])

    # ── خطاها ────────────────────────────────────────────────────
    def test_flood_backs_off_and_does_not_commit(self):
        w, c = self.mk(min_interval=2.0, hard_floor=2.0, backoff_factor=2.0)
        before = w.interval
        c.script = [FloodWaitError(None, capture=25)]
        self.assertFalse(run(w.write("خط")))
        self.assertEqual(c.written, [])
        self.assertNotEqual(w.current, "خط")     # نباید فکر کند نوشته شده
        self.assertEqual(w.interval, before * 2)
        self.assertGreater(w.ready_in(), 24.0)

    def test_about_too_long_shrinks_limit(self):
        w, c = self.mk()
        c.script = [AboutTooLong()]
        text = "y" * 70
        self.assertFalse(run(w.write(text)))
        self.assertLess(w.limit, 70)
        # دفعه‌ی بعد با سقف جدید می‌رود
        self.assertTrue(run(w.write(text)))
        self.assertLessEqual(len(c.written[-1]), w.limit)

    def test_network_error_is_survivable(self):
        w, c = self.mk()
        c.script = [OSError("قطع شد")]
        self.assertFalse(run(w.write("الف")))
        self.assertEqual(c.written, [])
        self.assertTrue(run(w.write("الف", force=True)))

    def test_rpc_error_does_not_crash(self):
        w, c = self.mk()
        c.script = [RPCError(None, "BOOM")]
        self.assertFalse(run(w.write("ب")))
        self.assertEqual(w.current, "بیوی اصلی")

    # ── بازگردانی ────────────────────────────────────────────────
    def test_restore_puts_original_back(self):
        w, c = self.mk()
        run(w.write("لیریک"))
        run(w.restore())
        self.assertEqual(c.written[-1], "بیوی اصلی")

    def test_restore_is_noop_when_unchanged(self):
        w, c = self.mk()
        n = len(c.written)
        run(w.restore())
        self.assertEqual(len(c.written), n)

    def test_set_original_recovers_after_crash(self):
        """اگر دفعه‌ی قبل وسط لیریک کرش کرده باشیم، اصل از دیتابیس می‌آید."""
        c = FakeClient(bio="یه خط لیریک که از قبل مونده")
        w = BioWriter(c, RateConfig(state_file=""))
        run(w.start())
        w.set_original("بیوی واقعی من")
        run(w.write("چیز دیگه"))
        run(w.restore())
        self.assertEqual(c.written[-1], "بیوی واقعی من")

    # ── آمار ─────────────────────────────────────────────────────
    def test_stats_shape(self):
        w, _ = self.mk()
        run(w.write("z"))
        s = w.stats()
        for k in ("interval", "writes", "floods", "latency_ms", "lead_ms", "limit"):
            self.assertIn(k, s)
        self.assertEqual(s["writes"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
