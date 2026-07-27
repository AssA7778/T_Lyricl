# -*- coding: utf-8 -*-
"""
Telegram Live Lyrics Bio — Playlist Edition
===========================================
"Sings" your playlist in your Telegram profile bio (description):
each song's lyrics appear line by line on the song's exact LRC timing,
the bio is WIPED CLEAN when the song ends, then after a pause the next
song starts. Loops through the playlist forever.

Lyrics source per song:
  1. local file  lyrics/<name>.lrc   (best for Persian songs)
  2. lrclib.net  (free synced-lyrics database, by artist + title)

Run:  python lyrics_bio.py
Stop: Ctrl+C  (bio is left empty, or restored — see config "on_exit")
"""

import asyncio
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from telethon import TelegramClient, functions
from telethon.errors import FloodWaitError

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
LYRICS_DIR = BASE / "lyrics"

TIMESTAMP = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


def load_config():
    if not CONFIG_PATH.exists():
        sys.exit("config.json پیدا نشد! اول فایل config.json رو پر کن.")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not cfg.get("api_id") or not cfg.get("api_hash"):
        sys.exit("api_id و api_hash رو از my.telegram.org بگیر و توی config.json بذار.")
    if not cfg.get("songs"):
        sys.exit('لیست "songs" توی config.json خالیه! حداقل یه آهنگ اضافه کن.')
    return cfg


def parse_lrc(text):
    """Parse LRC text -> sorted list of (seconds, line)."""
    lines = []
    for raw in text.splitlines():
        stamps = TIMESTAMP.findall(raw)
        if not stamps:
            continue
        line = TIMESTAMP.sub("", raw).strip()
        if not line:
            continue
        for minutes, seconds in stamps:
            lines.append((int(minutes) * 60 + float(seconds), line))
    lines.sort(key=lambda item: item[0])
    return lines


def fetch_from_lrclib(artist, title):
    """Try lrclib.net for synced lyrics. Returns LRC text or None."""
    def get_json(url):
        req = urllib.request.Request(url, headers={"User-Agent": "LyricsBio/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        query = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
        data = get_json(f"https://lrclib.net/api/get?{query}")
        if data.get("syncedLyrics"):
            return data["syncedLyrics"]
    except Exception:
        pass

    try:  # fallback: fuzzy search
        query = urllib.parse.urlencode({"q": f"{artist} {title}"})
        results = get_json(f"https://lrclib.net/api/search?{query}")
        for item in results:
            if item.get("syncedLyrics"):
                return item["syncedLyrics"]
    except Exception:
        pass
    return None


def load_playlist(cfg):
    """Resolve lyrics for every song in config. Returns [(label, lines), ...]."""
    playlist = []
    for song in cfg["songs"]:
        artist = song.get("artist", "").strip()
        title = song.get("title", "").strip()
        label = f"{artist} - {title}".strip(" -")

        lrc_text = None
        lrc_name = song.get("lyrics_file")
        if lrc_name and (LYRICS_DIR / lrc_name).exists():
            lrc_text = (LYRICS_DIR / lrc_name).read_text(encoding="utf-8")
            print(f"[+] {label}: متن از فایل محلی lyrics/{lrc_name}")
        elif artist and title:
            print(f"[~] {label}: در حال جستجو توی lrclib.net ...")
            lrc_text = fetch_from_lrclib(artist, title)
            if lrc_text:
                print(f"[+] {label}: متن همگام (synced) پیدا شد ✔")

        if not lrc_text:
            print(f"[!] {label}: متن همگام پیدا نشد — از این آهنگ رد می‌شم. "
                  f"(می‌تونی فایل lyrics/{title or 'song'}.lrc دستی بذاری)")
            continue

        lines = parse_lrc(lrc_text)
        if lines:
            playlist.append((label, lines))

    if not playlist:
        sys.exit("برای هیچ‌کدوم از آهنگ‌ها متن همگام پیدا نشد!")
    return playlist


async def set_bio(client, text):
    await client(functions.account.UpdateProfileRequest(about=text))


async def sing_song(client, label, lines, cfg):
    bio_limit = 140 if cfg.get("premium", True) else 70
    prefix = cfg.get("prefix", "🎙 ")
    min_interval = float(cfg.get("min_interval_seconds", 4))
    late_skip = float(cfg.get("late_skip_seconds", 2.5))

    print("─" * 44)
    print(f"▶️  شروع: {label}")
    start = time.monotonic()
    last_update = 0.0

    for t, line in lines:
        target = start + t
        # respect Telegram rate limits: never update faster than min_interval
        show_at = max(target, last_update + min_interval) if last_update else target
        if show_at - target > late_skip:
            continue  # would be too late — skip to stay in sync
        delay = show_at - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        elif -delay > late_skip:
            continue  # fell behind (e.g. FloodWait) — skip old lines

        text = (prefix + line).strip()[:bio_limit]
        try:
            await set_bio(client, text)
            last_update = time.monotonic()
            mm, ss = int(t // 60), t % 60
            print(f"  [{mm:02d}:{ss:05.2f}] {line}")
        except FloodWaitError as e:
            print(f"  [!] FloodWait: تلگرام گفت {e.seconds} ثانیه صبر کن...")
            await asyncio.sleep(e.seconds + 1)

    # song finished -> wipe the bio clean
    await asyncio.sleep(float(cfg.get("clear_after_seconds", 4)))
    while True:
        try:
            await set_bio(client, "")
            print("⏹  آهنگ تموم شد — بیو پاک شد.")
            break
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)


async def run(cfg):
    playlist = load_playlist(cfg)
    print(f"\n[+] پلی‌لیست آماده‌ست: {len(playlist)} آهنگ")

    client = TelegramClient(str(BASE / "lyrics_session"), cfg["api_id"], cfg["api_hash"])
    await client.start()

    me = await client(functions.users.GetFullUserRequest("me"))
    original_bio = me.full_user.about or ""
    print(f"[+] بیوی فعلی: {original_bio!r}")
    print("[+] شروع شد! برای توقف Ctrl+C بزن.\n")

    gap = float(cfg.get("gap_between_songs_seconds", 20))
    try:
        while True:
            for label, lines in playlist:
                await sing_song(client, label, lines, cfg)
                print(f"⏸  {int(gap)} ثانیه سکوت تا آهنگ بعدی ...\n")
                await asyncio.sleep(gap)
    finally:
        try:
            if cfg.get("on_exit", "empty") == "restore":
                await set_bio(client, original_bio)
                print("\n[+] بیوی اصلی برگشت. خداحافظ!")
            else:
                await set_bio(client, "")
                print("\n[+] بیو خالی شد. خداحافظ!")
        except Exception as e:
            print(f"\n[!] تنظیم بیوی پایانی ناموفق بود: {e}")
        await client.disconnect()


def main():
    cfg = load_config()
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
