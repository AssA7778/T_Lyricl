"""
تست انتخابِ لیریک از LRCLIB — بدون شبکه.

لایه‌ی HTTP با داده‌های ساختگیِ هم‌شکلِ پاسخ‌های واقعی جایگزین شده. چیزی که
اینجا تست می‌شود مهم‌ترین بخشِ کیفیتِ پروژه است: **انتخابِ درست**. لیریکِ
آهنگِ اشتباه بدتر از نداشتنِ لیریک است.
"""

from __future__ import annotations

import asyncio
import logging
import unittest

from tglyrics.lyrics.lrclib import LrcLibClient

logging.getLogger("tglyrics.lrclib").setLevel(logging.CRITICAL)


def row(rid, track, artist, dur, synced=True, album="", instrumental=False):
    return {
        "id": rid,
        "trackName": track,
        "artistName": artist,
        "albumName": album,
        "duration": dur,
        "instrumental": instrumental,
        "plainLyrics": "متن ساده" if not instrumental else None,
        "syncedLyrics": "[00:01.00]خط\n[00:05.00]خط دو" if synced else None,
    }


class Fake(LrcLibClient):
    """`_json` را جایگزین می‌کند؛ هیچ سوکتی باز نمی‌شود."""

    def __init__(self, exact=None, search=None):
        super().__init__(session=None, user_agent="test")
        self._exact = exact or {}      # (track.lower(), artist.lower()) → row
        self._search = search or {}    # زیررشته‌ی q → list[row]
        self.calls: list[tuple[str, dict]] = []

    async def _json(self, path, params):
        self.calls.append((path, dict(params)))
        if path == "get":
            key = (
                str(params.get("track_name", "")).casefold(),
                str(params.get("artist_name", "")).casefold(),
            )
            return self._exact.get(key)
        if path == "search":
            q = str(params.get("q", "") or params.get("track_name", "")).casefold()
            for needle, rows in self._search.items():
                if needle.casefold() in q:
                    return rows
            return []
        return None


def run(c):
    return asyncio.run(c)


class TestExactPath(unittest.TestCase):
    def test_exact_hit_wins_immediately(self):
        c = Fake(exact={("creep", "radiohead"): row(1, "Creep", "Radiohead", 239)})
        r = run(c.best("Radiohead", "Creep", duration_ms=239_000))
        self.assertIsNotNone(r)
        self.assertEqual(r.id, 1)
        self.assertEqual([p for p, _ in c.calls], ["get"])   # سرچ لازم نشد

    def test_falls_back_to_search_when_exact_misses(self):
        c = Fake(search={"radiohead": [row(2, "Creep", "Radiohead", 239)]})
        r = run(c.best("Radiohead", "Creep", duration_ms=239_000))
        self.assertEqual(r.id, 2)
        self.assertIn("search", [p for p, _ in c.calls])


class TestPersian(unittest.TestCase):
    """
    سناریوی واقعی: متادیتای پلیر فارسی است ولی LRCLIB فینگلیش ذخیره کرده.
    بدون فینگلیش‌سازی + اسکلتِ بی‌واکه، این حالت همیشه شکست می‌خورد.
    """

    def test_persian_query_finds_finglish_entry(self):
        c = Fake(
            search={
                "mohsen yeganeh": [
                    row(10, "Behet Ghol Midam", "Mohsen Yeganeh", 245),
                    row(11, "Nashkan Delamo", "Mohsen Yeganeh", 300),
                    row(12, "Doobare", "Mohsen Yeganeh", 210),
                ]
            }
        )
        r = run(c.best("محسن یگانه", "بهت قول میدم", duration_ms=245_000))
        self.assertIsNotNone(r, "آهنگ فارسی پیدا نشد")
        self.assertEqual(r.id, 10)

    def test_picks_right_song_among_same_artist(self):
        c = Fake(
            search={
                "mohsen yeganeh": [
                    row(10, "Behet Ghol Midam", "Mohsen Yeganeh", 245),
                    row(12, "Doobare", "Mohsen Yeganeh", 212),
                ]
            }
        )
        r = run(c.best("محسن یگانه", "دوباره", duration_ms=212_000))
        self.assertEqual(r.id, 12)

    def test_never_searches_artist_name_alone(self):
        """
        تله‌ی مستندنشده‌ی LRCLIB: `/api/search` با فقط `artist_name`
        همیشه آرایه‌ی خالی می‌دهد. باید همیشه q یا track_name بفرستیم.
        """
        c = Fake()
        run(c.best("محسن یگانه", "بهت قول میدم", duration_ms=245_000))
        for path, params in c.calls:
            if path == "search":
                self.assertTrue(
                    params.get("q") or params.get("track_name"),
                    f"سرچ بدون q/track_name: {params}",
                )


class TestScoring(unittest.TestCase):
    def test_prefers_synced_over_plain(self):
        c = Fake(
            search={
                "creep": [
                    row(1, "Creep", "Radiohead", 239, synced=False),
                    row(2, "Creep", "Radiohead", 239, synced=True),
                ]
            }
        )
        self.assertEqual(run(c.best("Radiohead", "Creep", duration_ms=239_000)).id, 2)

    def test_duration_breaks_ties(self):
        c = Fake(
            search={
                "creep": [
                    row(1, "Creep", "Radiohead", 400),
                    row(2, "Creep", "Radiohead", 239),
                ]
            }
        )
        self.assertEqual(run(c.best("Radiohead", "Creep", duration_ms=239_000)).id, 2)

    def test_rejects_weak_match(self):
        c = Fake(search={"creep": [row(9, "Totally Other Song", "Nobody At All", 100)]})
        self.assertIsNone(run(c.best("Radiohead", "Creep", duration_ms=239_000)))

    def test_empty_result_is_none(self):
        self.assertIsNone(run(Fake().best("X", "Y", duration_ms=1000)))

    def test_unsynced_only_returns_none_when_synced_required(self):
        c = Fake(search={"creep": [row(1, "Creep", "Radiohead", 239, synced=False)]})
        self.assertIsNone(run(c.best("Radiohead", "Creep", duration_ms=239_000)))

    def test_no_duplicate_ids_in_pool(self):
        rows = [row(5, "Creep", "Radiohead", 239)]
        c = Fake(exact={("creep", "radiohead"): rows[0]}, search={"creep": rows})
        r = run(c.best("Radiohead", "Creep", duration_ms=239_000))
        self.assertEqual(r.id, 5)


class TestRobustness(unittest.TestCase):
    def test_missing_duration_still_works(self):
        c = Fake(search={"creep": [row(1, "Creep", "Radiohead", 239)]})
        self.assertIsNotNone(run(c.best("Radiohead", "Creep", duration_ms=0)))

    def test_empty_artist_still_works(self):
        c = Fake(search={"creep": [row(1, "Creep", "Radiohead", 239)]})
        self.assertIsNotNone(run(c.best("", "Creep", duration_ms=239_000)))

    def test_noisy_title_from_youtube(self):
        c = Fake(search={"creep": [row(1, "Creep", "Radiohead", 239)]})
        r = run(c.best("Radiohead", "Creep (Official Music Video)", duration_ms=239_000))
        self.assertIsNotNone(r)

    def test_none_response_does_not_crash(self):
        class Broken(Fake):
            async def _json(self, path, params):
                return None

        self.assertIsNone(run(Broken().best("A", "B", duration_ms=1000)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
