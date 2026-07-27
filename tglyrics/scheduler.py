"""
تصمیمِ زمان‌بندی — منطقِ خالصِ «الان بنویسم یا بخوابم، و چقدر».

عمداً بدون I/O نوشته شده تا بشود دقیقاً تستش کرد. کلِ ادعای «حتی یک ثانیه
دیر نمی‌کند» همین چند خط است.

قاعده‌ی طلایی: **صف نداریم.**

اگر محدودیتِ نرخ اجازه‌ی نوشتن نداد، فریمِ فعلی را نگه نمی‌داریم که بعداً
بنویسیم. فقط تا لحظه‌ی مجاز می‌خوابیم و آن‌وقت **از نو** حساب می‌کنیم که
«الان چه خطی باید باشد». نتیجه: در بدترین حالت بعضی خط‌ها رد می‌شوند، ولی
هیچ خطی با تأخیر نمایش داده نمی‌شود. عقب‌افتادن بدترین حالتِ ممکن است،
چون از آن به بعد کلِ آهنگ ناهماهنگ می‌ماند.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

__all__ = ["Decision", "decide"]


@dataclass(frozen=True)
class Decision:
    #: همین حالا بنویس
    write: bool
    #: چند ثانیه بخواب (۰ = فوراً دوباره حساب کن)
    sleep: float
    #: فقط برای لاگ/تست
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
    """
    :param frame_text:      متنی که *باید* الان روی بیو باشد
    :param frame_until_ms:  تا کِی این متن معتبر است (زمانِ لیریک، ms)
    :param current_text:    چیزی که الان واقعاً روی بیو است
    :param t_ms:            زمانِ فعلیِ لیریک (موقعیت + آفست + lead)
    :param ready_in:        چند ثانیه دیگر اجازه‌ی نوشتن داریم
    """
    if frame_text != current_text:
        if ready_in <= 0:
            return Decision(True, 0.0, "متن عوض شده و اجازه داریم")
        # نه صف، نه نوشتنِ دیرهنگام — دوباره حساب می‌کنیم
        return Decision(False, ready_in, "منتظر اجازه‌ی نرخ")

    if not playing:
        return Decision(False, max_sleep, "متوقف")

    if frame_until_ms is None:
        return Decision(False, max_sleep, "بدون انقضا")

    r = rate if rate and rate > 0 else 1.0
    remain_s = (frame_until_ms - t_ms) / r / 1000.0
    if remain_s <= 0:
        # مرز رد شده — فوراً دوباره حساب کن (نگذار حلقه گیر کند)
        return Decision(False, 0.01, "مرزِ رد شده")
    return Decision(False, min(remain_s, max_sleep * 100), "تا مرزِ فریم بخواب")
