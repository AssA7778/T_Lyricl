from __future__ import annotations

import re
import unicodedata

__all__ = ["romanize", "normalize_fa", "candidates", "has_persian", "skeleton"]


_PERSIAN = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")

_STRIP = re.compile(r"[ً-ٰٟـ​-‏‪-‮﻿]")

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

_UNIFY = str.maketrans(
    {
        "ي": "ی", "ى": "ی", "ﻯ": "ی", "ﻰ": "ی",
        "ك": "ک", "ﻙ": "ک", "ﻚ": "ک",
        "أ": "ا", "إ": "ا", "آ": "آ", "ٱ": "ا",
        "ؤ": "و", "ئ": "ی", "ة": "ه",
        "ﷲ": "الله",
        "«": '"', "»": '"', "،": ",", "؛": ";", "؟": "?",
    }
)

_MAP = {
    "آ": "a", "ا": "a", "ب": "b", "پ": "p", "ت": "t", "ث": "s",
    "ج": "j", "چ": "ch", "ح": "h", "خ": "kh", "د": "d", "ذ": "z",
    "ر": "r", "ز": "z", "ژ": "zh", "س": "s", "ش": "sh", "ص": "s",
    "ض": "z", "ط": "t", "ظ": "z", "ع": "", "غ": "gh", "ف": "f",
    "ق": "gh", "ک": "k", "گ": "g", "ل": "l", "م": "m", "ن": "n",
    "و": "o", "ه": "h", "ی": "i", "ء": "", "ٔ": "",
}

_DIGRAPHS = [
    ("خوا", "kha"),
    ("ای", "ey"),
    ("او", "u"),
    ("یی", "yi"),
    ("وو", "u"),
    ("ون", "un"),
    ("ان", "an"),
    ("ین", "in"),
]

_OVERRIDES = {
    "محسن یگانه": "Mohsen Yeganeh",
    "سیروان خسروی": "Sirvan Khosravi",
    "گوگوش": "Googoosh",
    "هیچکس": "Hichkas",
    "شادمهر عقیلی": "Shadmehr Aghili",
    "محسن چاوشی": "Mohsen Chavoshi",
    "همایون شجریان": "Homayoun Shajarian",
    "محمدرضا شجریان": "Mohammadreza Shajarian",
    "رضا صادقی": "Reza Sadeghi",
    "بابک جهانبخش": "Babak Jahanbakhsh",
    "علیرضا طلیسچی": "Alireza Talischi",
    "امیر عباس گلاب": "Amirabbas Golab",
    "مهدی یراحی": "Mehdi Yarrahi",
    "سامان جلیلی": "Saman Jalili",
    "فرزاد فرزین": "Farzad Farzin",
    "بنیامین بهادری": "Benyamin Bahadori",
    "احسان خواجه امیری": "Ehsan Khajeh Amiri",
    "زانیار خسروی": "Zanyar Khosravi",
    "مسیح و آرش": "Masih Arash AP",
    "پیشرو": "Pishro",
    "امیر تتلو": "Amir Tataloo",
    "یاس": "Yas",
    "بهرام": "Bahram",
    "شاهین نجفی": "Shahin Najafi",
    "سیاوش قمیشی": "Siavash Ghomayshi",
    "داریوش": "Dariush",
    "ابی": "Ebi",
    "معین": "Moein",
    "مرتضی پاشایی": "Morteza Pashaei",
    "حمید هیراد": "Hamid Hiraad",
    "مازیار فلاحی": "Maziar Fallahi",
    "علی یاسینی": "Ali Yasini",
    "میثم ابراهیمی": "Meysam Ebrahimi",
    "پازل باند": "Puzzle Band",
    "ماکان بند": "Macan Band",
    "سون": "7 Band",
    "تتلو": "Tataloo",
}


def has_persian(s: str) -> bool:
    return bool(_PERSIAN.search(s or ""))


def normalize_fa(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_UNIFY)
    s = _STRIP.sub("", s)
    s = s.translate(_DIGITS)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def romanize(s: str) -> str:
    if not s:
        return ""
    s = normalize_fa(s)

    low = s.strip()
    if low in _OVERRIDES:
        return _OVERRIDES[low]

    for fa, en in _DIGRAPHS:
        s = s.replace(fa, f"\x00{en}\x00")

    out: list[str] = []
    for ch in s:
        if ch == "\x00":
            continue
        if ch in _MAP:
            out.append(_MAP[ch])
        elif ch.isspace():
            out.append(" ")
        elif ch.isascii():
            out.append(ch)

    res = "".join(out)
    res = re.sub(r"([bcdfghjklmnpqrstvwxyz])\1+", r"\1\1", res, flags=re.I)
    res = re.sub(r"\s+", " ", res).strip()
    return res


def skeleton(s: str) -> str:
    if not s:
        return ""
    t = normalize_fa(s)
    if has_persian(t):
        t = romanize(t)
    t = t.casefold()
    t = re.sub(r"[aeiouy]+", "", t)
    t = re.sub(r"[^a-z0-9]+", "", t)
    return t


def candidates(artist: str, title: str) -> list[tuple[str, str]]:
    a0, t0 = normalize_fa(artist or ""), normalize_fa(title or "")
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []

    def add(a: str, t: str) -> None:
        a, t = (a or "").strip(), (t or "").strip()
        if not t:
            return
        k = (a.casefold(), t.casefold())
        if k in seen:
            return
        seen.add(k)
        out.append((a, t))

    add(a0, t0)

    if has_persian(a0) or has_persian(t0):
        ra, rt = romanize(a0), romanize(t0)
        add(ra, rt)
        add(ra, t0)
        add(a0, rt)
        add("", rt)

    add("", t0)
    return out
