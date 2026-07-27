from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

__all__ = ["Decision", "decide"]


@dataclass(frozen=True)
class Decision:
    write: bool
    sleep: float
    reason: str = ""


def decide(
    *,
    frame_text: str,
    frame_until_ms: Optional[float],
    current_text: str,
    t_ms: float,
    playing: bool,
    rate: float = 1.0,
    ready_in: float = 0.0,
    max_sleep: float = 3.0,
) -> Decision:
    if frame_text != current_text:
        if ready_in <= 0:
            return Decision(True, 0.0, "متن عوض شده و اجازه داریم")
        return Decision(False, ready_in, "منتظر اجازه‌ی نرخ")

    if not playing:
        return Decision(False, max_sleep, "متوقف")

    if frame_until_ms is None:
        return Decision(False, max_sleep, "بدون انقضا")

    r = rate if rate and rate > 0 else 1.0
    remain_s = (frame_until_ms - t_ms) / r / 1000.0
    if remain_s <= 0:
        return Decision(False, 0.01, "مرزِ رد شده")
    return Decision(False, min(remain_s, max_sleep * 100), "تا مرزِ فریم بخواب")
