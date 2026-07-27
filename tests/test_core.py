from __future__ import annotations

import asyncio
import time
import unittest

from tglyrics.clock import PlaybackClock, Track
from tglyrics.lyrics.lrc import parse_lrc
from tglyrics.lyrics.translit import candidates, romanize
from tglyrics.render import RenderConfig, Renderer, fit, split_to_pieces
from tglyrics.textutil import sanitize

LINE_LRC = """[ar:Test Artist]
[ti:Test Song]
[offset:0]
[00:00.50]اولین خط
[00:04.00]دومین خط که یه کم بلندتره
[00:08.25]
[00:12.00]خط سوم
"""

WORD_LRC = """[00:10.00]<00:10.00>یک <00:10.50>دو <00:11.00>سه <00:11.50>چهار
[00:13.00]<00:13.00>پنج <00:13.60>شش
"""


class TestLrc(unittest.TestCase):
    def test_line_level(self):
        ly = parse_lrc(LINE_LRC, duration_ms=20000)
        self.assertTrue(ly.synced)
        self.assertEqual(len(ly.lines), 4)
        self.assertEqual(ly.lines[0].t_ms, 500)
        self.assertEqual(ly.lines[0].text, "اولین خط")
        self.assertEqual(ly.lines[1].t_ms, 4000)
        self.assertTrue(ly.lines[2].blank)
        self.assertEqual(ly.lines[3].end_ms, 20000)
        self.assertEqual(ly.meta["ar"], "Test Artist")

    def test_end_times_chained(self):
        ly = parse_lrc(LINE_LRC, duration_ms=20000)
        for a, b in zip(ly.lines, ly.lines[1:]):
            self.assertEqual(a.end_ms, b.t_ms)

    def test_index_at(self):
        ly = parse_lrc(LINE_LRC, duration_ms=20000)
        self.assertEqual(ly.index_at(0), -1)
        self.assertEqual(ly.index_at(499), -1)
        self.assertEqual(ly.index_at(500), 0)
        self.assertEqual(ly.index_at(3999), 0)
        self.assertEqual(ly.index_at(4000), 1)
        self.assertEqual(ly.index_at(99999), 3)

    def test_word_level(self):
        ly = parse_lrc(WORD_LRC, duration_ms=20000)
        self.assertTrue(ly.word_level)
        w = ly.lines[0].words
        self.assertEqual(len(w), 4)
        self.assertEqual(w[0].t_ms, 10000)
        self.assertEqual(w[3].t_ms, 11500)
        self.assertEqual(ly.lines[0].text, "یک دو سه چهار")

    def test_fractions(self):
        ly = parse_lrc("[00:01.5]a\n[00:02.05]b\n[00:03.005]c\n")
        self.assertEqual([l.t_ms for l in ly.lines], [1500, 2050, 3005])

    def test_multi_timestamp_line(self):
        ly = parse_lrc("[00:10.00][00:30.00]تکرار\n")
        self.assertEqual(len(ly.lines), 2)
        self.assertEqual(ly.lines[0].t_ms, 10000)
        self.assertEqual(ly.lines[1].t_ms, 30000)
        self.assertEqual(ly.lines[1].text, "تکرار")

    def test_plain_fallback(self):
        ly = parse_lrc("just some words\nsecond line\n")
        self.assertFalse(ly.synced)
        self.assertEqual(len(ly.lines), 2)

    def test_empty(self):
        self.assertFalse(parse_lrc(""))


class TestText(unittest.TestCase):
    def test_fit_short(self):
        self.assertEqual(fit("abc", 10), "abc")

    def test_fit_cuts_and_marks(self):
        out = fit("one two three four five", 12)
        self.assertLessEqual(len(out), 12)
        self.assertTrue(out.endswith("…"))

    def test_fit_never_exceeds(self):
        for n in range(1, 40):
            self.assertLessEqual(len(fit("سلام دنیا این یک تست طولانی است", n)), n)

    def test_split_respects_budget(self):
        pieces = split_to_pieces("a bb ccc dddd eeeee ffffff", 8)
        self.assertTrue(all(len(p) <= 8 for p in pieces))
        self.assertEqual(" ".join(pieces), "a bb ccc dddd eeeee ffffff")

    def test_split_hard_splits_long_word(self):
        pieces = split_to_pieces("x" * 25, 10)
        self.assertTrue(all(len(p) <= 10 for p in pieces))
        self.assertEqual("".join(pieces), "x" * 25)

    def test_sanitize_kills_newlines(self):
        self.assertEqual(sanitize("a\nb", 70), "a · b")
        self.assertEqual(sanitize("  a   b  ", 70), "a b")

    def test_sanitize_enforces_limit(self):
        self.assertLessEqual(len(sanitize("x" * 200, 70)), 70)


class TestClock(unittest.TestCase):
    def setUp(self):
        self.c = PlaybackClock(stale_after=30)
        self.t = Track(title="T", artist="A", duration_ms=200000)

    def test_interpolates_forward(self):
        self.c.update(self.t, 5000, True)
        a = self.c.snapshot().position_ms
        time.sleep(0.05)
        b = self.c.snapshot().position_ms
        self.assertGreater(b, a)
        self.assertAlmostEqual(b - a, 50, delta=40)

    def test_paused_is_frozen(self):
        self.c.update(self.t, 5000, False)
        time.sleep(0.05)
        self.assertAlmostEqual(self.c.snapshot().position_ms, 5000, delta=1)

    def test_rate_applied(self):
        self.c.update(self.t, 0, True, rate=2.0)
        time.sleep(0.05)
        self.assertAlmostEqual(self.c.snapshot().position_ms, 100, delta=60)

    def test_latency_compensation(self):
        self.c.update(self.t, 1000, True, latency_ms=250)
        self.assertGreaterEqual(self.c.snapshot().position_ms, 1245)

    def test_clamped_to_duration(self):
        self.c.update(self.t, 199_999, True)
        time.sleep(0.05)
        self.assertLessEqual(self.c.snapshot().position_ms, 200_000)

    def test_track_change_bumps_generation(self):
        g = self.c.generation
        self.c.update(self.t, 0, True)
        self.c.update(Track(title="Other", artist="A"), 0, True)
        self.assertGreater(self.c.generation, g)

    def test_stale_after(self):
        c = PlaybackClock(stale_after=0.02)
        c.update(self.t, 0, True)
        time.sleep(0.05)
        s = c.snapshot()
        self.assertTrue(s.stale)
        self.assertFalse(s.active)

    def test_noise_does_not_bump_generation(self):
        self.c.update(self.t, 0, True)
        g = self.c.generation
        self.c.update(self.t, 500, True)
        self.assertEqual(self.c.generation, g)


class TestClockWakeup(unittest.IsolatedAsyncioTestCase):

    async def test_change_during_computation_is_not_lost(self):
        c = PlaybackClock()
        c.consume()
        c.update(Track(title="جدید", artist="کسی"), 0, True)
        woke = await asyncio.wait_for(c.wait_change(5.0), timeout=0.5)
        self.assertTrue(woke)

    async def test_wait_times_out_when_nothing_happens(self):
        c = PlaybackClock()
        c.update(Track(title="a", artist="b"), 0, True)
        c.consume()
        self.assertFalse(await c.wait_change(0.05))

    async def test_consume_then_no_change_blocks(self):
        c = PlaybackClock()
        c.consume()
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(c.wait_change(10.0), timeout=0.1)


class TestRender(unittest.TestCase):
    def r(self, **kw):
        cfg = RenderConfig(limit=kw.pop("limit", 70), **kw)
        return Renderer(cfg)

    def test_short_line_passthrough(self):
        ly = parse_lrc(LINE_LRC, duration_ms=20000)
        f = self.r().render(ly, None, 1000)
        self.assertEqual(f.text, "اولین خط")
        self.assertEqual(f.until_ms, 4000)

    def test_before_first_line_shows_track(self):
        ly = parse_lrc(LINE_LRC, duration_ms=20000)
        tr = Track(title="Song", artist="Someone")
        f = self.r().render(ly, tr, 0)
        self.assertEqual(f.kind, "track")
        self.assertIn("Song", f.text)
        self.assertEqual(f.until_ms, 500)

    def test_blank_line_is_interlude(self):
        ly = parse_lrc(LINE_LRC, duration_ms=20000)
        f = self.r().render(ly, None, 9000)
        self.assertEqual(f.kind, "interlude")
        self.assertEqual(f.text, "♪")

    def test_never_exceeds_limit(self):
        long = "این یک خط خیلی خیلی طولانی است که قطعاً از هفتاد کاراکتر بیشتر می‌شود و باید تیکه شود"
        ly = parse_lrc(f"[00:00.00]{long}\n[00:20.00]پایان\n", duration_ms=30000)
        r = self.r(limit=30)
        for t in range(0, 20000, 137):
            self.assertLessEqual(len(r.render(ly, None, t).text), 30, f"t={t}")

    def test_chunking_covers_whole_line(self):
        long = "one two three four five six seven eight nine ten eleven twelve"
        ly = parse_lrc(f"[00:00.00]{long}\n[00:20.00]end\n", duration_ms=30000)
        r = self.r(limit=20, min_chunk_ms=500)
        seen, t = [], 0
        while t < 20000:
            f = r.render(ly, None, t)
            if f.kind == "lyric" and (not seen or seen[-1] != f.text):
                seen.append(f.text)
            t = int(f.until_ms) if f.until_ms else t + 100
        self.assertGreater(len(seen), 1)
        self.assertEqual(" ".join(seen), long)

    def test_chunk_count_bounded_by_min_chunk(self):
        long = " ".join(f"w{i}" for i in range(40))
        ly = parse_lrc(f"[00:00.00]{long}\n[00:04.00]end\n", duration_ms=10000)
        r = self.r(limit=12, min_chunk_ms=1000, show_interlude=False)
        seen, t = [], 0
        while t < 4000:
            f = r.render(ly, None, t)
            if not seen or seen[-1] != f.text:
                seen.append(f.text)
            t = int(f.until_ms) if f.until_ms else t + 100
        self.assertLessEqual(len(seen), 4)

    def test_truncate_mode(self):
        long = "aaa bbb ccc ddd eee fff ggg hhh iii jjj"
        ly = parse_lrc(f"[00:00.00]{long}\n[00:10.00]x\n", duration_ms=20000)
        r = self.r(limit=15, long_line_mode="truncate", show_interlude=False)
        f = r.render(ly, None, 500)
        self.assertLessEqual(len(f.text), 15)
        self.assertTrue(f.text.endswith("…"))
        self.assertEqual(f.until_ms, 10000)

    def test_truncate_mode_yields_to_interlude(self):
        long = "aaa bbb ccc ddd eee fff ggg hhh iii jjj"
        ly = parse_lrc(f"[00:00.00]{long}\n[00:10.00]x\n", duration_ms=20000)
        r = self.r(limit=15, long_line_mode="truncate", interlude_after_ms=7000)
        self.assertEqual(r.render(ly, None, 500).until_ms, 7000)
        self.assertEqual(r.render(ly, None, 8000).kind, "interlude")

    def test_word_level_boundaries_use_word_times(self):
        ly = parse_lrc(WORD_LRC, duration_ms=20000)
        r = self.r(limit=7, min_chunk_ms=300, show_interlude=False)
        f = r.render(ly, None, 10100)
        self.assertLessEqual(len(f.text), 7)
        self.assertIn(f.until_ms, (10500.0, 11000.0, 11500.0))

    def test_long_gap_becomes_interlude(self):
        ly = parse_lrc("[00:00.00]short\n[00:30.00]next\n", duration_ms=60000)
        r = self.r(interlude_after_ms=5000)
        self.assertEqual(r.render(ly, None, 1000).kind, "lyric")
        self.assertEqual(r.render(ly, None, 20000).kind, "interlude")

    def test_prefix_reduces_budget(self):
        ly = parse_lrc("[00:00.00]" + "x " * 40 + "\n", duration_ms=30000)
        r = self.r(limit=20, prefix="♪ ")
        f = r.render(ly, None, 100)
        self.assertTrue(f.text.startswith("♪ "))
        self.assertLessEqual(len(f.text), 20)

    def test_no_lyrics_falls_back_to_track(self):
        tr = Track(title="Title", artist="Artist")
        f = self.r().render(None, tr, 1000)
        self.assertEqual(f.kind, "track")
        self.assertIn("Title", f.text)

    def test_piece_bounds_are_strictly_increasing(self):
        from tglyrics.render import Renderer as R
        for raw in ([0, 0, 0, 0, 10000], [0, 9999, 9999, 10000], [0, -5, 3, 10000]):
            b = R._normalize(list(raw), 0, 10000)
            self.assertEqual(b[0], 0)
            self.assertEqual(b[-1], 10000)
            for x, y in zip(b, b[1:]):
                self.assertLess(x, y, b)

    def test_chunk_until_never_passes_sing_end(self):
        long = " ".join(f"کلمه{i}" for i in range(30))
        ly = parse_lrc(f"[00:00.00]{long}\n[00:30.00]بعدی\n", duration_ms=40000)
        r = self.r(limit=18, min_chunk_ms=400, interlude_after_ms=6000)
        for t in range(0, 6000, 101):
            f = r.render(ly, None, t)
            self.assertLessEqual(f.until_ms, 6000)

    def test_monotonic_until(self):
        long = "الف ب پ ت ث ج چ ح خ د ذ ر ز ژ س ش ص ض ط ظ ع غ ف ق ک گ ل م ن و ه ی"
        ly = parse_lrc(f"[00:00.00]{long}\n[00:09.00]بعدی\n", duration_ms=20000)
        r = self.r(limit=16, min_chunk_ms=400)
        for t in range(0, 19000, 53):
            f = r.render(ly, None, t)
            if f.until_ms is not None:
                self.assertGreater(f.until_ms, t, f"t={t} until={f.until_ms}")


class TestTranslit(unittest.TestCase):
    def test_known_overrides(self):
        self.assertEqual(romanize("محسن یگانه"), "Mohsen Yeganeh")
        self.assertEqual(romanize("گوگوش"), "Googoosh")

    def test_generic_romanize(self):
        out = romanize("دلم")
        self.assertTrue(out.isascii())
        self.assertTrue(out)

    def test_candidates_include_latin_for_persian(self):
        cands = candidates("محسن یگانه", "دوباره")
        self.assertTrue(any(a.isascii() and a for a, _ in cands))
        self.assertTrue(any("محسن" in a for a, _ in cands))

    def test_candidates_for_latin_input(self):
        cands = candidates("Radiohead", "Creep")
        self.assertEqual(cands[0], ("Radiohead", "Creep"))

    def test_no_duplicates(self):
        cands = candidates("Radiohead", "Creep")
        self.assertEqual(len(cands), len(set(cands)))

    def test_arabic_yeh_unified(self):
        self.assertEqual(
            Track(title="بيا", artist="").key, Track(title="بیا", artist="").key
        )


class TestSkeletonMatching(unittest.TestCase):

    def test_skeleton_bridges_persian_and_finglish(self):
        from tglyrics.lyrics.translit import skeleton
        self.assertEqual(skeleton("بهت قول میدم"), skeleton("Behet Ghol Midam"))
        self.assertEqual(skeleton("محسن یگانه"), skeleton("Mohsen Yeganeh"))

    def test_skeleton_drops_vowels_and_punctuation(self):
        from tglyrics.lyrics.translit import skeleton
        self.assertEqual(skeleton("Behet Ghol Midam!"), "bhtghlmdm")

    def test_skeleton_empty_safe(self):
        from tglyrics.lyrics.translit import skeleton
        self.assertEqual(skeleton(""), "")

    def test_sim_scores_cross_script_pair_high(self):
        from tglyrics.lyrics.lrclib import _sim
        self.assertGreater(_sim("بهت قول میدم", "Behet Ghol Midam"), 0.9)
        self.assertGreater(_sim("محسن یگانه", "Mohsen Yeganeh"), 0.9)

    def test_sim_still_rejects_unrelated(self):
        from tglyrics.lyrics.lrclib import _sim
        self.assertLess(_sim("بهت قول میدم", "Bohemian Rhapsody"), 0.6)
        self.assertLess(_sim("Creep", "Paranoid Android"), 0.6)

    def test_sim_same_script_unaffected(self):
        from tglyrics.lyrics.lrclib import _sim
        self.assertEqual(_sim("Creep", "creep"), 1.0)
        self.assertEqual(_sim("بیا", "بيا"), 1.0)


class TestTrackKey(unittest.TestCase):
    def test_noise_stripped(self):
        a = Track(title="Creep (Official Video)", artist="Radiohead")
        b = Track(title="Creep", artist="Radiohead")
        self.assertEqual(a.key, b.key)

    def test_case_insensitive(self):
        self.assertEqual(
            Track(title="CREEP", artist="RadioHead").key,
            Track(title="creep", artist="radiohead").key,
        )

    def test_empty_title_not_ok(self):
        self.assertFalse(Track(title="", artist="A").ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
