"""ابزارهای متنی مربوط به محدودیت‌های بیوی تلگرام."""

from __future__ import annotations

import re

__all__ = ["sanitize"]

_WS = re.compile(r"\s+")


def sanitize(text: str, limit: int) -> str:
    """
    متن را برای فیلد `about` تلگرام آماده کن.

    تلگرام توی بیو خط جدید قبول نمی‌کند — با « · » جایگزین می‌شود.
    """
    if not text:
        return ""
    t = text.replace("\r", " ").replace("\n", " · ")
    t = _WS.sub(" ", t).strip()
    if limit > 0 and len(t) > limit:
        t = t[: max(0, limit - 1)].rstrip() + "…"
    return t
