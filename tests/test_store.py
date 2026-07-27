from __future__ import annotations

import os
import tempfile
import time
import unittest

from tglyrics.lyrics import store as store_mod
from tglyrics.lyrics.store import LyricsStore, norm_key


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lyrics_dir = os.path.join(self.tmp.name, "lyrics")
        os.makedirs(self.lyrics_dir)
        self.s = LyricsStore(os.path.join(self.tmp.name, "c.db"), self.lyrics_dir)
        self.s.open()

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()


class TestCache(Base):
    def test_miss_then_hit(self):
        self.assertIsNone(self.s.get("k", 200000))
        self.s.put("k", 200000, "[00:01.00]hi", "lrclib:1", True)
        h = self.s.get("k", 200000)
        self.assertIsNotNone(h)
        self.assertTrue(h.found)
        self.assertEqual(h.raw, "[00:01.00]hi")
        self.assertEqual(h.source, "lrclib:1")

    def test_duration_bucketing_tolerates_jitter(self):
        self.s.put("k", 200000, "x", "s", True)
        self.assertIsNotNone(self.s.get("k", 200400))
        self.assertIsNotNone(self.s.get("k", 199600))

    def test_far_duration_is_a_different_song(self):
        self.s.put("k", 200000, "x", "s", True)
        self.assertIsNone(self.s.get("k", 260000))

    def test_negative_cache(self):
        self.s.put("k", 0, None, "lrclib", False)
        h = self.s.get("k", 0)
        self.assertIsNotNone(h)
        self.assertFalse(h.found)

    def test_negative_cache_expires(self):
        old = store_mod.NEG_TTL
        try:
            store_mod.NEG_TTL = 0.01
            self.s.put("k", 0, None, "lrclib", False)
            time.sleep(0.03)
            self.assertIsNone(self.s.get("k", 0))
        finally:
            store_mod.NEG_TTL = old

    def test_forget(self):
        self.s.put("k", 1000, "x", "s", True)
        self.assertEqual(self.s.forget("k"), 1)
        self.assertIsNone(self.s.get("k", 1000))

    def test_overwrite(self):
        self.s.put("k", 1000, "a", "s1", True)
        self.s.put("k", 1000, "b", "s2", True)
        self.assertEqual(self.s.get("k", 1000).raw, "b")


class TestOffsets(Base):
    def test_default_zero(self):
        self.assertEqual(self.s.get_offset("k"), 0)

    def test_set_and_get(self):
        self.s.set_offset("k", -350)
        self.assertEqual(self.s.get_offset("k"), -350)

    def test_zero_clears(self):
        self.s.set_offset("k", 500)
        self.s.set_offset("k", 0)
        self.assertEqual(self.s.get_offset("k"), 0)


class TestKV(Base):
    def test_roundtrip(self):
        self.assertIsNone(self.s.kv_get("bio"))
        self.s.kv_set("bio", "سلام")
        self.assertEqual(self.s.kv_get("bio"), "سلام")

    def test_empty_string_is_not_none(self):
        self.s.kv_set("bio", "")
        self.assertEqual(self.s.kv_get("bio"), "")
        self.assertIsNotNone(self.s.kv_get("bio"))


class TestLocalFiles(Base):
    def write(self, name: str, body: str = "[00:01.00]سلام"):
        p = os.path.join(self.lyrics_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        st = os.stat(self.lyrics_dir)
        os.utime(self.lyrics_dir, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        return p

    def test_artist_dash_title(self):
        self.write("Mohsen Yeganeh - Behet Ghol Midam.lrc")
        got = self.s.local("Mohsen Yeganeh", "Behet Ghol Midam")
        self.assertIsNotNone(got)
        self.assertIn("سلام", got[0])

    def test_title_only_file(self):
        self.write("Creep.lrc")
        self.assertIsNotNone(self.s.local("Radiohead", "Creep"))

    def test_case_and_punctuation_insensitive(self):
        self.write("Radiohead - Creep.lrc")
        self.assertIsNotNone(self.s.local("radiohead", "creep!"))

    def test_persian_filename(self):
        self.write("محسن یگانه - بهت قول میدم.lrc")
        self.assertIsNotNone(self.s.local("محسن یگانه", "بهت قول میدم"))

    def test_arabic_yeh_in_filename_still_matches(self):
        self.write("بيا.lrc")
        self.assertIsNotNone(self.s.local("", "بیا"))

    def test_unknown_returns_none(self):
        self.write("Creep.lrc")
        self.assertIsNone(self.s.local("Someone", "Totally Different"))

    def test_picks_up_new_file_without_restart(self):
        self.assertIsNone(self.s.local("A", "Later Song"))
        self.write("A - Later Song.lrc")
        self.assertIsNotNone(self.s.local("A", "Later Song"))

    def test_txt_extension_supported(self):
        self.write("A - B.txt")
        self.assertIsNotNone(self.s.local("A", "B"))


class TestNormKey(unittest.TestCase):
    def test_strips_punctuation_and_case(self):
        self.assertEqual(norm_key("Creep!"), norm_key("creep"))

    def test_collapses_whitespace(self):
        self.assertEqual(norm_key("a   b"), "a b")

    def test_unifies_arabic_letters(self):
        self.assertEqual(norm_key("بيا"), norm_key("بیا"))
        self.assertEqual(norm_key("كار"), norm_key("کار"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
