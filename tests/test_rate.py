from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest

from tglyrics.ratelimit import AdaptiveLimiter, RateConfig

logging.getLogger("tglyrics.rate").setLevel(logging.CRITICAL)


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def mk(clock: FakeClock, **kw) -> AdaptiveLimiter:
    kw.setdefault("state_file", "")
    cfg = RateConfig(**kw)
    return AdaptiveLimiter(cfg, clock=clock, load=False)


class TestBasics(unittest.TestCase):
    def test_first_write_is_immediate(self):
        c = FakeClock()
        lim = mk(c, min_interval=4.0)
        self.assertTrue(lim.ready())

    def test_enforces_min_interval(self):
        c = FakeClock()
        lim = mk(c, min_interval=4.0, hard_floor=1.0)
        lim.on_success(0.1)
        self.assertFalse(lim.ready())
        self.assertAlmostEqual(lim.ready_in(), 4.0, places=3)
        c.advance(3.99)
        self.assertFalse(lim.ready())
        c.advance(0.02)
        self.assertTrue(lim.ready())

    def test_hard_floor_wins_over_low_min_interval(self):
        lim = mk(FakeClock(), min_interval=0.1, hard_floor=2.5)
        self.assertGreaterEqual(lim.interval, 2.5)

    def test_per_minute_cap(self):
        c = FakeClock()
        lim = mk(c, min_interval=0.0, hard_floor=0.0, max_writes_per_minute=5)
        for _ in range(5):
            self.assertTrue(lim.ready())
            lim.on_success(0.05)
            c.advance(1.0)
        self.assertFalse(lim.ready())
        self.assertAlmostEqual(lim.ready_in(), 55.0, places=3)
        c.advance(55.1)
        self.assertTrue(lim.ready())

    def test_cap_disabled_when_zero(self):
        c = FakeClock()
        lim = mk(c, min_interval=0.0, hard_floor=0.0, max_writes_per_minute=0)
        for _ in range(50):
            lim.on_success(0.01)
        self.assertTrue(lim.ready())


class TestFlood(unittest.TestCase):
    def test_flood_sleeps_and_backs_off(self):
        c = FakeClock()
        lim = mk(c, min_interval=4.0, hard_floor=2.0, backoff_factor=2.0)
        before = lim.interval
        lim.on_flood(30)
        self.assertEqual(lim.interval, before * 2.0)
        self.assertFalse(lim.ready())
        self.assertAlmostEqual(lim.ready_in(), 31.0, places=3)
        c.advance(31.1)
        self.assertTrue(lim.ready())

    def test_backoff_respects_cap(self):
        lim = mk(FakeClock(), min_interval=4.0, backoff_factor=3.0, max_interval_cap=20.0)
        for _ in range(10):
            lim.on_flood(1)
        self.assertLessEqual(lim.interval, 20.0)
        self.assertEqual(lim.floods, 10)

    def test_recovery_after_calm_period(self):
        c = FakeClock()
        lim = mk(
            c, min_interval=4.0, hard_floor=2.0, backoff_factor=2.0,
            recover_after=3, recover_factor=0.5, max_writes_per_minute=0,
        )
        lim.on_flood(1)
        self.assertEqual(lim.interval, 8.0)
        for _ in range(3):
            c.advance(10)
            lim.on_success(0.05)
        self.assertEqual(lim.interval, 4.0)

    def test_recovery_never_below_floor(self):
        c = FakeClock()
        lim = mk(
            c, min_interval=3.0, hard_floor=3.0, recover_after=1,
            recover_factor=0.1, max_writes_per_minute=0,
        )
        for _ in range(20):
            c.advance(10)
            lim.on_success(0.05)
        self.assertGreaterEqual(lim.interval, 3.0)

    def test_flood_resets_recovery_streak(self):
        c = FakeClock()
        lim = mk(
            c, min_interval=4.0, hard_floor=2.0, recover_after=3,
            recover_factor=0.5, backoff_factor=2.0, max_writes_per_minute=0,
        )
        lim.on_flood(1)
        c.advance(10); lim.on_success(0.05)
        c.advance(10); lim.on_success(0.05)
        lim.on_flood(1)
        self.assertEqual(lim.interval, 16.0)
        c.advance(20); lim.on_success(0.05)
        self.assertEqual(lim.interval, 16.0)


class TestLead(unittest.TestCase):
    def test_lead_tracks_latency(self):
        lim = mk(FakeClock())
        for _ in range(30):
            lim.on_success(0.20)
        self.assertAlmostEqual(lim.lead, 0.18, delta=0.02)

    def test_lead_is_capped(self):
        lim = mk(FakeClock())
        for _ in range(50):
            lim.on_success(5.0)
        self.assertLessEqual(lim.lead, 0.6)

    def test_lead_non_negative(self):
        self.assertGreaterEqual(mk(FakeClock()).lead, 0.0)

    def test_transient_error_pauses_briefly_without_backoff(self):
        c = FakeClock()
        lim = mk(c, min_interval=4.0)
        before = lim.interval
        lim.on_error()
        self.assertEqual(lim.interval, before)
        self.assertAlmostEqual(lim.ready_in(), 1.0, places=3)


class TestPersistence(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            c = FakeClock()
            a = AdaptiveLimiter(
                RateConfig(min_interval=4.0, backoff_factor=2.0, state_file=path),
                clock=c, load=False,
            )
            a.on_flood(1)
            self.assertTrue(os.path.exists(path))

            b = AdaptiveLimiter(
                RateConfig(min_interval=4.0, state_file=path), clock=FakeClock()
            )
            self.assertEqual(b.interval, 8.0)
            self.assertEqual(b.floods, 1)

    def test_corrupt_state_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            with open(path, "w") as f:
                f.write("{not json")
            lim = AdaptiveLimiter(RateConfig(min_interval=5.0, state_file=path))
            self.assertEqual(lim.interval, 5.0)

    def test_loaded_value_clamped_to_cap(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            with open(path, "w") as f:
                json.dump({"interval": 9999}, f)
            lim = AdaptiveLimiter(RateConfig(max_interval_cap=30.0, state_file=path))
            self.assertEqual(lim.interval, 30.0)


class TestNoStarvation(unittest.TestCase):
    def test_ready_in_always_finite_and_shrinking(self):
        c = FakeClock()
        lim = mk(c, min_interval=4.0, max_writes_per_minute=3)
        lim.on_success(0.1)
        lim.on_flood(20)
        for _ in range(200):
            w = lim.ready_in()
            self.assertLess(w, 120.0)
            if w <= 0:
                break
            c.advance(min(w, 5.0))
        else:
            self.fail("هیچ‌وقت اجازه‌ی نوشتن نداد")


if __name__ == "__main__":
    unittest.main(verbosity=2)
