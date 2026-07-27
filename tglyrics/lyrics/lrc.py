"""
پارسر LRC — هم خطی (استاندارد) و هم کلمه‌ای (Enhanced / A2).

نمونه‌ی خطی:
    [00:12.34]شب که می‌شه دلم می‌گیره

نمونه‌ی کلمه‌ای:
    [00:12.34]<00:12.34>شب <00:12.80>که <00:13.10>می‌شه

قرارداد offset در این پروژه:
    زمانِ جستجو = موقعیتِ پخش + offset
یعنی offset مثبت ⇒ لیریک زودتر نشان داده می‌شود.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

__all__ = ["Word", "Line", "Lyrics", "parse_lrc"]


_TIME = re.compile(r"\[(\d{1,4}):([0-5]?\d)(?:[.:](\d{1,3}))?\]")
_WORD = re.compile(r"<(\d{1,4}):([0-5]?\d)(?:[.:](\d{1,3}))?>")
_META = re.compile(r"^\s*\[(ar|ti|al|au|by|offset|length|re|ve|tool|id):([^\]]*)\]\s*$", re.I)
_WS = re.compile(r"[ \t ]+")


def _ms(mm: str, ss: str, frac: Optional[str]) -> int:
    v = int(mm) * 60_000 + int(ss) * 1000
    if frac:
        # ".5" → 500ms ، ".50" → 500ms ، ".500" → 500ms
        v += int(frac.ljust(3, "0")[:3])
    return v


def _clean(s: str) -> str:
    """تلگرام توی بیو اجازه‌ی newline نمی‌دهد؛ ضمناً فاصله‌ها را جمع می‌کنیم."""
    s = s.replace("​", "").replace("\r", "")
    s = _WS.sub(" ", s)
    return s.strip()


@dataclass(frozen=True)
class Word:
    t_ms: int
    text: str


@dataclass
class Line:
    t_ms: int
    text: str
    words: tuple[Word, ...] = ()
    end_ms: int = 0

    @property
    def blank(self) -> bool:
        return not self.text

    @property
    def dur_ms(self) -> int:
        return max(0, self.end_ms - self.t_ms)


@dataclass
class Lyrics:
    lines: tuple[Line, ...] = ()
    synced: bool = False
    #: میلی‌ثانیه‌ای که باید به موقعیتِ پخش اضافه شود
    offset_ms: int = 0
    source: str = ""
    instrumental: bool = False
    lang: str = ""
    meta: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.lines)

    @property
    def word_level(self) -> bool:
        return any(l.words for l in self.lines)

    def index_at(self, t_ms: float) -> int:
        """اندیس خطِ فعال در زمان t. اگر هنوز به خط اول نرسیده‌ایم ‎-1."""
        lines = self.lines
        if not lines or t_ms < lines[0].t_ms:
            return -1
        lo, hi = 0, len(lines) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if lines[mid].t_ms <= t_ms:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def line_at(self, t_ms: float) -> Optional[Line]:
        i = self.index_at(t_ms)
        return self.lines[i] if i >= 0 else None

    def with_lines(self, lines: Iterable[Line]) -> "Lyrics":
        return Lyrics(
            lines=tuple(lines),
            synced=self.synced,
            offset_ms=self.offset_ms,
            source=self.source,
            instrumental=self.instrumental,
            lang=self.lang,
            meta=dict(self.meta),
        )


def _finalize(
    lines: list[Line],
    duration_ms: int = 0,
    *,
    tail_ms: int = 6000,
) -> tuple[Line, ...]:
    """مرتب‌سازی، حذف تکراری‌ها، و پر کردن end_ms هر خط."""
    lines.sort(key=lambda l: l.t_ms)

    # اگر دو خط دقیقاً هم‌زمان بودند، متنشان را با فاصله بچسبان
    merged: list[Line] = []
    for ln in lines:
        if merged and merged[-1].t_ms == ln.t_ms:
            prev = merged[-1]
            if ln.text and prev.text and ln.text != prev.text:
                merged[-1] = Line(
                    prev.t_ms,
                    f"{prev.text} {ln.text}",
                    prev.words + ln.words,
                )
            elif ln.text and not prev.text:
                merged[-1] = Line(ln.t_ms, ln.text, ln.words)
            continue
        merged.append(ln)

    for i, ln in enumerate(merged):
        if i + 1 < len(merged):
            ln.end_ms = merged[i + 1].t_ms
        elif duration_ms and duration_ms > ln.t_ms:
            ln.end_ms = duration_ms
        else:
            ln.end_ms = ln.t_ms + tail_ms

    return tuple(merged)


def parse_lrc(
    raw: str,
    *,
    duration_ms: int = 0,
    source: str = "",
) -> Lyrics:
    """
    متن LRC را می‌گیرد و `Lyrics` می‌دهد.

    اگر هیچ تگ زمانی نداشت، `synced=False` برمی‌گردد و خطوط با زمان ۰
    پر می‌شوند (برای اینکه لااقل بشود fallback نشان داد).
    """
    if not raw:
        return Lyrics(source=source)

    meta: dict = {}
    offset_ms = 0
    out: list[Line] = []
    plain: list[str] = []

    for raw_line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = _META.match(raw_line)
        if m:
            key, val = m.group(1).lower(), m.group(2).strip()
            meta[key] = val
            if key == "offset":
                try:
                    offset_ms = int(float(val))
                except ValueError:
                    offset_ms = 0
            continue

        stamps = list(_TIME.finditer(raw_line))
        if not stamps:
            t = _clean(raw_line)
            if t:
                plain.append(t)
            continue

        # هر چیزی بعد از آخرین تگِ زمانیِ ابتدای خط، متن است
        body = raw_line[stamps[-1].end():]

        words: list[Word] = []
        for wm in _WORD.finditer(body):
            wt = _ms(wm.group(1), wm.group(2), wm.group(3))
            # متن این کلمه = تا تگ بعدی
            nxt = _WORD.search(body, wm.end())
            chunk = body[wm.end(): nxt.start() if nxt else len(body)]
            chunk = _clean(chunk)
            if chunk:
                words.append(Word(wt, chunk))

        text = _clean(_WORD.sub(" ", body))

        for sm in stamps:
            t_ms = _ms(sm.group(1), sm.group(2), sm.group(3))
            # وقتی یک خط چند تایم‌استمپ دارد، تایمِ کلمات را جابه‌جا کن
            if words:
                delta = t_ms - words[0].t_ms
                shifted = tuple(Word(w.t_ms + delta, w.text) for w in words)
            else:
                shifted = ()
            out.append(Line(t_ms, text, shifted))

    if out:
        return Lyrics(
            lines=_finalize(out, duration_ms),
            synced=True,
            offset_ms=offset_ms,
            source=source,
            meta=meta,
        )

    if plain:
        # لیریک بدون زمان — فقط برای fallback
        step = max(1, (duration_ms or len(plain) * 3000) // max(1, len(plain)))
        lines = [Line(i * step, t) for i, t in enumerate(plain)]
        return Lyrics(
            lines=_finalize(lines, duration_ms),
            synced=False,
            offset_ms=offset_ms,
            source=source,
            meta=meta,
        )

    return Lyrics(source=source, meta=meta)
