# -*- coding: utf-8 -*-
"""Interactive Telegram login for the lyrics-bio userbot.

Usage:
  python deploy/login.py          interactive: asks phone + code (+ 2FA password)
  python deploy/login.py --check  exit 0 if a valid session already exists, else 1
"""
import json
import sys
from pathlib import Path

from telethon.sync import TelegramClient

BASE = Path(__file__).resolve().parent.parent
CONFIG = BASE / "config.json"

if not CONFIG.exists():
    sys.exit("[x] config.json پیدا نشد!")

cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
client = TelegramClient(str(BASE / "lyrics_session"), cfg["api_id"], cfg["api_hash"])
client.connect()

if client.is_user_authorized():
    me = client.get_me()
    print(f"[+] لاگین از قبل انجام شده: {me.first_name} (id={me.id})")
    client.disconnect()
    sys.exit(0)

if "--check" in sys.argv:
    print("[!] سشن معتبری وجود نداره — لاگین لازمه.")
    client.disconnect()
    sys.exit(1)

client.start()  # prompts: phone -> code -> (2FA password)
me = client.get_me()
print(f"[+] لاگین موفق: {me.first_name} (id={me.id})")
print("[!] مهم: توی تلگرام → Settings → Devices این دستگاه رو Terminate نکن!")
client.disconnect()
