# Step 1 of remote login: send the verification code to the user's Telegram.
# Usage: python login_step1.py +989xxxxxxxxx
import asyncio
import json
import sys

from telethon import TelegramClient

BASE = "/opt/lyrics-bio"


async def main():
    cfg = json.load(open(f"{BASE}/config.json", encoding="utf-8"))
    client = TelegramClient(f"{BASE}/lyrics_session", cfg["api_id"], cfg["api_hash"])
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        print("ALREADY_LOGGED_IN", me.id)
        return
    phone = sys.argv[1]
    sent = await client.send_code_request(phone)
    with open(f"{BASE}/.login_state.json", "w") as f:
        json.dump({"phone": phone, "hash": sent.phone_code_hash}, f)
    print("CODE_SENT")
    await client.disconnect()


asyncio.run(main())
