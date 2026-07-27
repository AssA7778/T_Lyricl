from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .app import App, setup_logging
from .config import ConfigError, load


def main() -> None:
    ap = argparse.ArgumentParser(
        "tglyrics", description="لیریک زنده و سینک‌شده توی بیوی تلگرام"
    )
    ap.add_argument("-c", "--config", default="config.toml", help="مسیر config.toml")
    ap.add_argument("--check", action="store_true", help="فقط کانفیگ را بررسی کن و برو")
    args = ap.parse_args()

    try:
        cfg = load(args.config)
    except ConfigError as e:
        print(f"\n❌ {e}\n", file=sys.stderr)
        raise SystemExit(2)

    setup_logging(cfg.log_level, cfg.log_file)

    if args.check:
        print("✅ کانفیگ سالم است\n")
        print(
            json.dumps(
                {
                    "source": cfg.source_kind,
                    "bio_limit": cfg.render.limit or "خودکار",
                    "long_line_mode": cfg.render.long_line_mode,
                    "min_interval": cfg.rate.min_interval,
                    "cache_db": cfg.lyrics.cache_db,
                    "local_lyrics": cfg.lyrics.local_dir,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    try:
        asyncio.run(App(cfg).run())
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        print(f"\n❌ {e}\n", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
