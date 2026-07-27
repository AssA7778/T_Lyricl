# Step 2 of remote login: sign in with the code the user received.
# Usage: python login_step2.py <code> [2fa_password]
import asyncio
import json
import sys

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

BASE = "/opt/lyrics-bio"


async def main():
    cfg = json.load(open(f"{BASE}/config.json", encoding="utf-8"))
    state = json.load(open(f"{BASE}/.login_state.json", encoding="utf-8"))
    client = TelegramClient(f"{BASE}/lyrics_session", cfg["api_id"], cfg["api_hash"])
    await client.connect()
    try:
        await client.sign_in(state["phone"], sys.argv[1], phone_code_hash=state["hash"])
    except SessionPasswordNeededError:
        if len(sys.argv) > 2:
            await client.sign_in(password=sys.argv[2])
        else:
            print("NEED_2FA_PASSWORD")
            await client.disconnect()
            return
    me = await client.get_me()
    print("LOGGED_IN", me.id, me.first_name)
    await client.disconnect()


asyncio.run(main())
