"""
رندرر — از «لیریک + لحظه» به «متنی که باید توی بیو باشد».

مسئله‌ی اصلی: بیوی اکانت معمولی ۷۰ کاراکتر است و خیلی از خط‌های لیریک
بلندترند. سه راه داشتیم:

  ۱. بریدن با …            → نصف خط را نمی‌بینی
  ۲. کوچک‌کردن فونت        → وجود ندارد
  ۳. اسکرول سینک‌شده       ← این را پیاده کردیم

در حالت «chunk» خط بلند به تیکه‌های ≤ سقف شکسته می‌شود و تیکه‌ها *روی
زمانِ خودِ خط* پخش می‌شوند. اگر لیریک کلمه‌ای (A2) باشد، مرزِ هر تیکه دقیقاً
روی تایم‌استمپِ اولین کلمه‌اش می‌نشیند — یعنی همان لحظه‌ای که خواننده آن
کلمه را می‌خواند. اگر نباشد، به‌تناسبِ طولِ متن تقسیم می‌شود.

`min_chunk_ms` جلوی انفجارِ تعداد نوشتن را می‌گیرد: اگر خط آن‌قدر کوتاه است
که تیکه‌ها کمتر از این مدت دیده می‌شوند، تعداد تیکه‌ها کم می‌شود.

هر فریم علاوه بر متن، می‌گوید «تا کِی معتبر است» — زمان‌بند دقیقاً همان لحظه
بیدار می‌شود، نه یک ثانیه دیرتر.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .clock import Track
from .lyrics.lrc import Line, Lyrics

__all__ = ["RenderConfig", "Frame", "Renderer", "fit", "split_to_pieces"]

ELLIPSIS = "…"


def _len(s: str) -> int:
    return len(s)


def fit(text: str, limit: int) -> str:
    """متن را در سقف جا بده؛ ترجیحاً روی مرز کلمه."""
    text = text.strip()
    if limit <= 0:
        return ""
    if _len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    cut = text[: limit - 1].rstrip()
    sp = cut.rfind(" ")
    # فقط اگر بریدنِ روی کلمه بیش از حد کوتاهش نکند
    if sp >= limit * 0.55:
        cut = cut[:sp].rstrip()
    return (cut + ELLIPSIS) if cut else text[:limit]


def split_to_pieces(text: str, budget: int) -> list[str]:
    """متن را حریصانه به تیکه‌های ≤ budget بشکن، روی مرز کلمه."""
    if budget <= 0:
        return [""]
    text = text.strip()
    if not text:
        return [""]
    if _len(text) <= budget:
        return [text]

    pieces: list[str] = []
    cur = ""
    for w in text.split():
        # کلمه‌ی تک‌تنهایی که از سقف بلندتر است → سخت بشکن
        while _len(w) > budget:
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(w[:budget])
            w = w[budget:]
        cand = w if not cur else f"{cur} {w}"
        if _len(cand) <= budget:
            cur = cand
        else:
            if cur:
                pieces.append(cur)
            cur = w
    if cur:
        pieces.append(cur)
    return pieces or [""]


@dataclass
class RenderConfig:
    limit: int = 70
    prefix: str = ""
    long_line_mode: str = "chunk"          # chunk | truncate
    min_chunk_ms: int = 1300
    interlude: str = "♪"
    show_interlude: bool = True
    interlude_after_ms: int = 7000
    fallback_to_track: bool = True
    fallback_format: str = "♪ {artist} – {title}"
    idle_text: str = ""


@dataclass(frozen=True)
class Frame:
    text: str
    #: زمانِ پخش (میلی‌ثانیه) که این فریم منقضی می‌شود. None = نامحدود
    until_ms: Optional[float]
    kind: str = "lyric"  # lyric | interlude | track | idle


class Renderer:
    def __init__(self, cfg: RenderConfig) -> None:
        self.cfg = cfg

    # ── سقف ──────────────────────────────────────────────────────
    @property
    def limit(self) -> int:
        return self.cfg.limit

    @limit.setter
    def limit(self, v: int) -> None:
        self.cfg.limit = max(8, int(v))

    @property
    def budget(self) -> int:
        return max(6, self.cfg.limit - _len(self.cfg.prefix))

    def _wrap(self, body: str) -> str:
        p = self.cfg.prefix
        return f"{p}{body}" if p else body

    # ── فریم‌های غیرلیریکی ───────────────────────────────────────
    def _track_frame(self, track: Optional[Track], until: Optional[float]) -> Frame:
        cfg = self.cfg
        if not cfg.fallback_to_track or track is None or not track.ok:
            return Frame(self._wrap(cfg.interlude if cfg.show_interlude else ""),
                         until, "interlude")
        body = cfg.fallback_format.format(
            artist=(track.artist or "").strip(),
            title=(track.title or "").strip(),
            album=(track.album or "").strip(),
        ).strip(" –-")
        return Frame(self._wrap(fit(body, self.budget)), until, "track")

    def idle(self) -> Frame:
        return Frame(self.cfg.idle_text, None, "idle")

    # ── هسته ─────────────────────────────────────────────────────
    def render(
        self,
        lyrics: Optional[Lyrics],
        track: Optional[Track],
        t_ms: float,
    ) -> Frame:
        """
        متنی که همین الان باید توی بیو باشد + لحظه‌ی انقضایش.

        `t_ms` باید *بعد از* اعمال offset باشد.
        """
        cfg = self.cfg

        if not lyrics or not lyrics.lines or not lyrics.synced:
            return self._track_frame(track, None)

        idx = lyrics.index_at(t_ms)
        if idx < 0:
            # هنوز مقدمه است
            return self._track_frame(track, float(lyrics.lines[0].t_ms))

        line = lyrics.lines[idx]

        if line.blank:
            return Frame(
                self._wrap(cfg.interlude) if cfg.show_interlude else "",
                float(line.end_ms),
                "interlude",
            )

        sing_end = self._sing_end(line)

        if t_ms >= sing_end:
            # خط تمام شده ولی خط بعدی هنوز نیامده → بین‌نوا
            if cfg.show_interlude:
                return Frame(self._wrap(cfg.interlude), float(line.end_ms), "interlude")
            return Frame("", float(line.end_ms), "interlude")

        for text, start, end in self._pieces(line, sing_end):
            if t_ms < end:
                return Frame(self._wrap(text), float(end), "lyric")

        return Frame(self._wrap(fit(line.text, self.budget)), float(sing_end), "lyric")

    # ── جزئیات ───────────────────────────────────────────────────
    def _sing_end(self, line: Line) -> int:
        """
        کِی خواندنِ این خط تمام می‌شود (نه کِی خط بعدی شروع می‌شود).

        فرقشان مهم است: بین دو خط ممکن است ۲۰ ثانیه ساز باشد و ما نمی‌خواهیم
        ۲۰ ثانیه خطِ قبلی توی بیو بماند.
        """
        cfg = self.cfg
        if line.words:
            natural = line.words[-1].t_ms + 1200
        else:
            natural = line.t_ms + min(line.dur_ms, cfg.interlude_after_ms)

        natural = min(natural, line.end_ms)
        natural = max(natural, line.t_ms + 700)

        gap = line.end_ms - natural
        if not cfg.show_interlude or gap < 2500:
            return line.end_ms
        return natural

    def _token_times(self, line: Line) -> Optional[list[int]]:
        """زمانِ هر توکنِ متن. None اگر نتوانستیم مطمئن هم‌ترازش کنیم."""
        if not line.words:
            return None
        times: list[int] = []
        for w in line.words:
            parts = w.text.split()
            if not parts:
                continue
            times.extend([w.t_ms] * len(parts))
        return times if len(times) == len(line.text.split()) else None

    def _pieces(self, line: Line, sing_end: int) -> list[tuple[str, int, int]]:
        """(متن، شروع، پایان) برای هر تیکه‌ی این خط."""
        cfg = self.cfg
        budget = self.budget
        text = line.text
        start = line.t_ms
        span = max(1, sing_end - start)

        if _len(text) <= budget:
            return [(text, start, sing_end)]

        if cfg.long_line_mode != "chunk":
            return [(fit(text, budget), start, sing_end)]

        pieces = split_to_pieces(text, budget)

        max_pieces = max(1, int(span // max(200, cfg.min_chunk_ms)))
        if len(pieces) > max_pieces:
            kept = pieces[:max_pieces]
            kept[-1] = fit(kept[-1] + " " + ELLIPSIS, budget)
            pieces = kept

        if len(pieces) == 1:
            return [(pieces[0], start, sing_end)]

        bounds = self._piece_bounds(line, pieces, start, sing_end)
        return [(p, bounds[i], bounds[i + 1]) for i, p in enumerate(pieces)]

    def _piece_bounds(
        self, line: Line, pieces: list[str], start: int, end: int
    ) -> list[int]:
        """
        مرزهای زمانی تیکه‌ها: n+1 عدد، اکیداً صعودی، اولی `start`، آخری `end`.

        اگر لیریک کلمه‌ای باشد مرزها روی تایم‌استمپِ خودِ کلمه می‌نشینند
        (یعنی همان لحظه‌ای که خواننده آن کلمه را می‌خواند). وگرنه به‌تناسبِ
        طولِ متن تقسیم می‌شود.
        """
        n = len(pieces)
        bounds: Optional[list[int]] = None
        times = self._token_times(line)

        if times:
            b = [start]
            consumed = 0
            ok = True
            for p in pieces[:-1]:
                consumed += len(p.split())
                if consumed >= len(times):
                    ok = False
                    break
                b.append(times[consumed])
            if ok and len(b) == n:
                b.append(end)
                bounds = b

        if bounds is None:
            total = sum(_len(p) for p in pieces) or 1
            span = max(1, end - start)
            bounds = [start]
            acc = 0
            for p in pieces[:-1]:
                acc += _len(p)
                bounds.append(start + int(span * acc / total))
            bounds.append(end)

        return self._normalize(bounds, start, end)

    @staticmethod
    def _normalize(bounds: list[int], start: int, end: int) -> list[int]:
        """اکیداً صعودی، داخل [start, end]، با انتهای دقیقاً `end`."""
        bounds = list(bounds)
        bounds[0], bounds[-1] = start, end
        n = len(bounds)
        # از آخر به اول: هیچ مرزی نباید از مرزِ بعدی جلو بزند
        for i in range(n - 2, 0, -1):
            bounds[i] = min(bounds[i], bounds[i + 1] - 1)
        # از اول به آخر: هیچ مرزی نباید از مرزِ قبلی عقب بماند
        for i in range(1, n - 1):
            bounds[i] = max(bounds[i], bounds[i - 1] + 1)
            bounds[i] = min(bounds[i], end - 1)
        bounds[-1] = end
        return bounds
