#!/usr/bin/env python3
"""
ساختِ StringSession برای تلگرام.

بدون آرگومان: رشته را چاپ می‌کند تا خودت توی config.toml بگذاری.
با «--write مسیرِ config.toml»: بعد از لاگین، api_id و api_hash و session
را مستقیم توی همان فایل می‌نویسد (اینستالر همین‌طوری صدایش می‌زند).

⚠️ آن رشته کلیدِ کاملِ اکانتت است. هر کسی داشته باشد وارد اکانتت می‌شود.
   جایی نفرست، توی گیت کامیت نکن.
"""

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
    print("اول نصب کن:  pip install -r requirements.txt", file=sys.stderr)
    raise SystemExit(1)

try:  # py3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


def _ask(label: str, cast=str):
    while True:
        v = input(label).strip()
        if not v:
            continue
        try:
            return cast(v)
        except ValueError:
            print("  مقدار نامعتبر، دوباره.")


def _read_existing(cfg_path: Path) -> tuple[int, str]:
    """اگر config از قبل api_id/api_hash دارد، دوباره نپرس."""
    try:
        with open(cfg_path, "rb") as f:
            tg = tomllib.load(f).get("telegram", {})
        return int(tg.get("api_id") or 0), str(tg.get("api_hash") or "")
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return 0, ""


def _patch(cfg_path: Path, api_id: int, api_hash: str, session: str) -> bool:
    """api_id / api_hash / session را توی همان config.toml بنویس."""
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
    ap = argparse.ArgumentParser("login.py", description="ساخت سشن تلگرام برای tglyrics")
    ap.add_argument(
        "--write", metavar="CONFIG", default="",
        help="بعد از لاگین، api_id/api_hash/session را مستقیم توی این فایل بنویس",
    )
    args = ap.parse_args()

    cfg_path = Path(args.write).expanduser() if args.write else None
    if cfg_path and not cfg_path.is_file():
        print(f"فایل کانفیگ پیدا نشد: {cfg_path}", file=sys.stderr)
        raise SystemExit(1)

    print(
        "\n"
        "──────────────────────────────────────────────\n"
        " ساخت سشن تلگرام برای tglyrics\n"
        "──────────────────────────────────────────────\n"
        " اگر api_id و api_hash نداری، برو به my.telegram.org\n"
        " → API development tools → یک اپ بساز.\n"
    )

    api_id, api_hash = 0, ""
    if cfg_path:
        api_id, api_hash = _read_existing(cfg_path)
        if api_id and api_hash:
            print(f" api_id و api_hash از کانفیگ برداشته شد ({api_id}).\n")
    if not api_id:
        api_id = _ask("api_id  : ", int)
    if not api_hash:
        api_hash = _ask("api_hash: ")

    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        me = await client.get_me()
        s = client.session.save()
        print(
            "\n✅ انجام شد.\n"
            f"   وارد شدی به‌عنوان: {me.first_name or ''} "
            f"(@{me.username or '—'})\n"
            f"   پریمیوم: {'بله (۱۴۰ کاراکتر)' if getattr(me, 'premium', False) else 'نه (۷۰ کاراکتر)'}\n"
        )

        if cfg_path and _patch(cfg_path, api_id, api_hash, s):
            print(
                f"✍️  api_id و api_hash و session توی {cfg_path} نوشته شد.\n"
                "⚠️  توی تلگرام → Settings → Devices این دستگاه را Terminate نکن.\n"
            )
            return
        if cfg_path:
            print("! ساختار کانفیگ ناآشنا بود — خودت دستی بگذار:", file=sys.stderr)

        print(
            "این‌ها را توی config.toml زیر [telegram] بگذار:\n"
            "──────────────────────────────────────────────\n"
            f"api_id  = {api_id}\n"
            f'api_hash = "{api_hash}"\n'
            f'session = "{s}"\n'
            "──────────────────────────────────────────────\n"
            "⚠️  با هیچ‌کس شریکش نکن.\n"
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
