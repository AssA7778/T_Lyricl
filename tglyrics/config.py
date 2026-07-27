"""خواندن و اعتبارسنجی config.toml"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # py3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from .ratelimit import RateConfig
from .render import RenderConfig

__all__ = ["Config", "load"]


class ConfigError(RuntimeError):
    pass


@dataclass
class TelegramCfg:
    api_id: int = 0
    api_hash: str = ""
    session: str = ""
    bio_limit: int = 0
    idle_bio: str = ""
    control_chat: str = "me"
    control_prefix: str = "."


@dataclass
class LyricsCfg:
    user_agent: str = "tglyrics/1.0"
    cache_db: str = "data/cache.db"
    local_dir: str = "lyrics"
    global_offset_ms: int = 0


@dataclass
class Config:
    root: Path
    telegram: TelegramCfg = field(default_factory=TelegramCfg)
    rate: RateConfig = field(default_factory=RateConfig)
    lyrics: LyricsCfg = field(default_factory=LyricsCfg)
    render: RenderConfig = field(default_factory=RenderConfig)
    source_kind: str = "webhook"
    source_cfg: dict[str, Any] = field(default_factory=dict)
    stale_after: float = 45.0
    log_level: str = "INFO"
    log_file: str = "data/tglyrics.log"

    def path(self, p: str) -> str:
        """مسیر نسبی را نسبت به پوشه‌ی کانفیگ حساب کن."""
        if not p:
            return p
        q = Path(p)
        return str(q if q.is_absolute() else (self.root / q))


def _sub(d: dict, key: str) -> dict:
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def _pick(dst: Any, src: dict, *names: str) -> None:
    """فقط کلیدهای شناخته‌شده را بردار — غلط املایی توی کانفیگ بی‌صدا رد نشود."""
    for n in names:
        if n in src:
            cur = getattr(dst, n)
            val = src[n]
            try:
                if isinstance(cur, bool):
                    val = bool(val)
                elif isinstance(cur, int) and not isinstance(cur, bool):
                    val = int(val)
                elif isinstance(cur, float):
                    val = float(val)
                elif isinstance(cur, str):
                    val = str(val)
            except (TypeError, ValueError) as e:
                raise ConfigError(f"مقدار نامعتبر برای «{n}»: {src[n]!r} ({e})") from e
            setattr(dst, n, val)


def load(path: str = "config.toml") -> Config:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise ConfigError(
            f"فایل کانفیگ پیدا نشد: {p}\n"
            f"از روی نمونه بسازش:  cp config.example.toml config.toml"
        )
    with open(p, "rb") as f:
        raw = tomllib.load(f)

    cfg = Config(root=p.parent)

    tg = _sub(raw, "telegram")
    _pick(cfg.telegram, tg, "api_id", "api_hash", "session", "bio_limit",
          "idle_bio", "control_chat", "control_prefix")

    _pick(cfg.rate, _sub(raw, "rate"), "min_interval", "backoff_factor",
          "max_interval_cap", "recover_after", "recover_factor",
          "max_writes_per_minute", "hard_floor", "state_file")

    ly = _sub(raw, "lyrics")
    _pick(cfg.lyrics, ly, "user_agent", "cache_db", "local_dir", "global_offset_ms")
    _pick(cfg.render, ly, "interlude", "show_interlude", "long_line_mode",
          "min_chunk_ms", "prefix", "fallback_to_track", "fallback_format")
    if "interlude_after_ms" in ly:
        cfg.render.interlude_after_ms = int(ly["interlude_after_ms"])

    src = _sub(raw, "source")
    cfg.source_kind = str(src.get("kind", "webhook")).lower()
    cfg.stale_after = float(src.get("stale_after", 45.0))
    cfg.source_cfg = dict(_sub(src, cfg.source_kind))

    lg = _sub(raw, "log")
    cfg.log_level = str(lg.get("level", "INFO")).upper()
    cfg.log_file = str(lg.get("file", "data/tglyrics.log"))

    # ── اعتبارسنجی ───────────────────────────────────────────────
    t = cfg.telegram
    if not t.api_id or not t.api_hash:
        raise ConfigError(
            "api_id و api_hash خالی‌اند. از my.telegram.org بگیرشان و توی config.toml بگذار."
        )
    if not t.session:
        raise ConfigError(
            "session خالی است. اول یک بار اجرا کن:  python login.py\n"
            "بعد رشته‌ای که می‌دهد را توی config.toml بگذار."
        )
    if cfg.render.long_line_mode not in ("chunk", "truncate"):
        raise ConfigError("long_line_mode باید chunk یا truncate باشد")
    if cfg.source_kind not in ("webhook", "spotify", "mpris"):
        raise ConfigError("source.kind باید webhook یا spotify یا mpris باشد")

    # مسیرها را مطلق کن
    cfg.rate.state_file = cfg.path(cfg.rate.state_file)
    cfg.lyrics.cache_db = cfg.path(cfg.lyrics.cache_db)
    cfg.lyrics.local_dir = cfg.path(cfg.lyrics.local_dir)
    cfg.log_file = cfg.path(cfg.log_file)

    if cfg.telegram.bio_limit:
        cfg.render.limit = int(cfg.telegram.bio_limit)
    cfg.render.idle_text = cfg.telegram.idle_bio

    os.makedirs(os.path.dirname(cfg.lyrics.cache_db) or ".", exist_ok=True)
    return cfg
