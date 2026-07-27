#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
except ImportError:
    print("Install dependencies first:  pip install -r requirements.txt", file=sys.stderr)
    raise SystemExit(1)

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def _ask(label: str, cast=str):
    while True:
        v = input(label).strip()
        if not v:
            continue
        try:
            return cast(v)
        except ValueError:
            print("  invalid value, try again.")


def _read_existing(cfg_path: Path) -> tuple[int, str]:
    try:
        with open(cfg_path, "rb") as f:
            tg = tomllib.load(f).get("telegram", {})
        return int(tg.get("api_id") or 0), str(tg.get("api_hash") or "")
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return 0, ""


def _patch(cfg_path: Path, api_id: int, api_hash: str, session: str) -> bool:
    text = cfg_path.read_text(encoding="utf-8")
    repl = {
        "api_id": str(api_id),
        "api_hash": f'"{api_hash}"',
        "session": f'"{session}"',
    }
    for key, val in repl.items():
        pat = re.compile(rf"^(\s*{key}\s*=\s*).*$", re.MULTILINE)
        if not pat.search(text):
            return False
        text = pat.sub(lambda m, v=val: m.group(1) + v, text, count=1)
    cfg_path.write_text(text, encoding="utf-8")
    return True


async def main() -> None:
    ap = argparse.ArgumentParser(
        "login.py", description="Create a Telegram StringSession for tglyrics"
    )
    ap.add_argument(
        "--write", metavar="CONFIG", default="",
        help="after login, write api_id/api_hash/session into this config.toml",
    )
    args = ap.parse_args()

    cfg_path = Path(args.write).expanduser() if args.write else None
    if cfg_path and not cfg_path.is_file():
        print(f"config file not found: {cfg_path}", file=sys.stderr)
        raise SystemExit(1)

    print(
        "\n"
        "------------------------------------------------\n"
        " Telegram login for tglyrics\n"
        "------------------------------------------------\n"
        " No api_id/api_hash yet? Get them at my.telegram.org\n"
        " -> API development tools -> create an app.\n"
    )

    api_id, api_hash = 0, ""
    if cfg_path:
        api_id, api_hash = _read_existing(cfg_path)
        if api_id and api_hash:
            print(f" Using api_id/api_hash already in the config ({api_id}).\n")
    if not api_id:
        api_id = _ask("api_id  : ", int)
    if not api_hash:
        api_hash = _ask("api_hash: ")

    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        me = await client.get_me()
        s = client.session.save()
        premium = "yes (140-char bio)" if getattr(me, "premium", False) else "no (70-char bio)"
        print(
            "\nOK — logged in.\n"
            f"   account : {me.first_name or ''} (@{me.username or '-'})\n"
            f"   premium : {premium}\n"
        )

        if cfg_path and _patch(cfg_path, api_id, api_hash, s):
            print(f"Wrote api_id, api_hash and session into {cfg_path}")
            print("Do NOT terminate this device in Telegram -> Settings -> Devices.\n")
            return
        if cfg_path:
            print("unrecognized config layout — add these lines yourself:", file=sys.stderr)

        print(
            "Put these under [telegram] in config.toml:\n"
            "------------------------------------------------\n"
            f"api_id  = {api_id}\n"
            f'api_hash = "{api_hash}"\n'
            f'session = "{s}"\n'
            "------------------------------------------------\n"
            "The session string is the FULL key to your account. Never share it.\n"
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
