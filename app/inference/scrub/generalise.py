"""Layer 6: generalise dates and ages so timing survives but the calendar does not.

- Absolute dates -> ``[DATE_n · k days ago]`` (``~N years ago`` beyond 730 days,
  ``in k days`` for upcoming ones, ``next <weekday>`` for future-cued weekdays,
  bare ``[DATE_n]`` only when ``now`` is unknown).
- Any date whose (month, day) equals the patient's DOB -> ``[DOB]``.
- Stated ages and birth years -> ``[AGE · 50s]`` (``90+`` collapsed).
- Already-relative phrases (yesterday, 3 days ago, 上星期, 琴日) are untouched.

The clinical-number guard (amendment A6) lives here and ONLY here: a number
followed by a unit (mg, kg, °C, /10, days…) or preceded by a reading cue (BP,
CA-125, pain, 血壓…) is never an AGE or DATE candidate.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .patterns import PHONE_CUE
from .tokens import AGE, DATE, DOB, LB, PRIORITY_GENERALISE, PRIORITY_KNOWN, RB, Span, sentence_initial

LAYER = "generalise"
DEFAULT_TZ = "Asia/Hong_Kong"

# --- clinical-number guard -------------------------------------------------------------
_UNIT_AFTER_RE = re.compile(
    r"^\s?(?:mg|ml|mcg|g|kg|°C?|度|bpm|mmHg|%|次|粒|tablets?|pills?|doses?|days?|hours?|hrs?|weeks?|wks?|months?|"
    r"minutes?|mins?|/10|/5|out of|litres?|liters?|cm|mm|lbs?|units?)(?![A-Za-z])"
)
_CUE_BEFORE_RE = re.compile(
    rf"{LB}(?:CA-?125|CEA|PSA|temperature|temp|BP|blood pressure|pressure|sugar|glucose|pulse|heart rate|HR|sats|"
    rf"SpO2|weight|score|scale|level|pain|severity|rating|rate|dose|dosage){RB}",
    re.IGNORECASE,
)
_CUE_BEFORE_ZH_RE = re.compile(r"血壓|體溫|體重|血糖|心跳|脈搏|血氧|分|劑量|評分")
_GUARD_BREAK_RE = re.compile(r"[.!?。！？\n]")


def _before(text: str, start: int, window: int) -> str:
    """The ``window`` chars before ``start``, cut at the last sentence boundary so
    a reading in the previous sentence ("...about the dose. I'm 78") cannot
    guard a number in this one."""
    before = text[max(0, start - window):start]
    breaks = list(_GUARD_BREAK_RE.finditer(before))
    return before[breaks[-1].end():] if breaks else before


def clinical_guarded(text: str, start: int, end: int) -> bool:
    """True when the number at ``text[start:end]`` is a clinical quantity."""
    if _UNIT_AFTER_RE.match(text[end:end + 12]):
        return True
    if _CUE_BEFORE_RE.search(_before(text, start, 20)):
        return True
    return bool(_CUE_BEFORE_ZH_RE.search(_before(text, start, 6)))


# --- vocab -----------------------------------------------------------------------------
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_RE = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
             r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3,
    "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_FULL_WEEKDAYS = frozenset(n.lower() for n in _WEEKDAY_NAMES)
_ZH_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_ZH_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def zh_number(s: str) -> int | None:
    """Parse 五十九 / 十五 / 廿五 / 卅 / 一百 / 二零二四 (digit string)."""
    if not s:
        return None
    if all(ch in _ZH_DIGITS for ch in s) and len(s) >= 3:   # 一九六六 -> 1966
        return int("".join(str(_ZH_DIGITS[ch]) for ch in s))
    total, current = 0, 0
    for ch in s:
        if ch in _ZH_DIGITS:
            current = _ZH_DIGITS[ch]
        elif ch == "十":
            total += (current or 1) * 10
            current = 0
        elif ch == "廿":
            total += 20
            current = 0
        elif ch == "卅":
            total += 30
            current = 0
        elif ch == "百":
            total += (current or 1) * 100
            current = 0
        else:
            return None
    return total + current


# --- date expressions ---------------------------------------------------------------------
_D = r"(\d{1,2})(?:st|nd|rd|th)?"
# The month's abbreviation dot is consumed only when a year follows ("22 Aug. 1966"),
# never a sentence-final full stop ("on 15 September.").
DMY_RE = re.compile(rf"{LB}{_D}\s+(?:of\s+)?({_MONTH_RE})(?:\.(?=,?\s+\d{{4}}))?(?:,?\s+(\d{{4}}))?{RB}", re.IGNORECASE)
MDY_RE = re.compile(rf"{LB}({_MONTH_RE})\.?\s+{_D}(?:,?\s+(\d{{4}}))?{RB}", re.IGNORECASE)
# A trailing "." is a sentence end ("since 5/8.") unless a digit follows it (1.5/2.0).
SLASH_RE = re.compile(r"(?<![\d/.\-])(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?(?![\d/]|\.\d)")
DOT_RE = re.compile(r"(?<![\d/.\-])(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?!\d|\.\d)")
ISO_RE = re.compile(r"(?<![\d])(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?![\d])")
ZH_DATE_RE = re.compile(r"(?:(\d{4}|\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日號]")
ZH_DATE_NUM_RE = re.compile(r"(?:([一二三四五六七八九零〇]{2,4})年)?([一二三四五六七八九十]{1,2})月([一二三四五六七八九十廿卅]{1,3})[日號]")
ZH_DAY_RE = re.compile(r"(?<![\d月])(\d{1,2})\s*號(?!風球|巴士|線|房|座|樓|室|號碼|碼|車|鐵|月台|出口|櫃|枱|檯|床|病房|閘|仔|機)")

WEEKDAY_EN_RE = re.compile(
    rf"{LB}(?:((?i:last|this|next|coming))\s+)?((?i:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)|"
    rf"(?:Mon|Tues?|Wed|Thur?s?|Fri|Sat|Sun)\.?){RB}"
)
WEEKDAY_ZH_RE = re.compile(r"(?<![一二兩幾每成個半\d])(上個|上|下個|下|今個|呢個|今|這個|這|本)?\s*(?:星期|週|周|禮拜)([一二三四五六日天])(?![次至到個])")
_FUTURE_CUE_RE = re.compile(
    rf"{LB}(?:this|next|coming|appointment|see|seeing|visit|going|will|booked|book|scheduled|due){RB}|"
    r"下星期|下個禮拜|下週|下周|聽日|覆診|預約|去|見|會",
    re.IGNORECASE,
)

# --- ages ----------------------------------------------------------------------------------
AGE_CUE_RE = re.compile(
    rf"{LB}(?:I'?m|I\s+am|I\s+was|I'?m\s+now|I\s+am\s+now|aged|age|he'?s|she'?s|he\s+is|she\s+is|turned|turning)\s+(\d{{1,3}})(?![\d])",
    re.IGNORECASE,
)
AGE_YEARS_RE = re.compile(
    rf"(?<![\d])(\d{{1,3}})\s*-?\s*(?:years?|yrs?)\s*-?\s*(?:old|of\s+age){RB}|(?<![\d])(\d{{1,3}})\s*y/?o{RB}",
    re.IGNORECASE,
)
AGE_ZH_RE = re.compile(r"(?<![\d])(\d{1,3}|[一二三四五六七八九十廿卅百零兩]{1,5})\s*歲(?!數)")
AGE_ZH_YEAR_RE = re.compile(r"(?:我|佢|他|她)?今年\s*(\d{1,3})(?![\d歲月日號年])")
BIRTH_YEAR_RE = re.compile(
    r"(?i:born\s+(?:in\s+)?)(\d{4})(?![\d])|(?<![\d])(\d{4})\s*年\s*(?:出世|出生|生)(?![一-鿿]?日)|生於\s*(\d{4})(?![\d])"
)


def age_band(age: int) -> str:
    if age >= 90:
        return "90+"
    if age < 10:
        return "under 10"
    return f"{(age // 10) * 10}s"


# --- resolution --------------------------------------------------------------------------------
def _today(now, tz: str) -> date | None:
    if now is None:
        return None
    if isinstance(now, datetime):
        if now.tzinfo is not None:
            try:
                now = now.astimezone(ZoneInfo(tz))
            except Exception:  # unknown tz name: keep the instant as given
                pass
        return now.date()
    if isinstance(now, date):
        return now
    return None


def _year4(y: str | None, today: date | None) -> int | None:
    if not y:
        return None
    n = int(y)
    if len(y) == 4:
        return n
    pivot = (today.year % 100) + 1 if today else 30
    return 1900 + n if n > pivot else 2000 + n


def _valid(y: int | None, m: int, d: int) -> bool:
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return False
    if y is None:
        return d <= calendar.monthrange(2000, m)[1]   # leap-safe upper bound
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def offset_note(d: date, today: date) -> str:
    delta = (today - d).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "1 day ago"
    if delta <= 730 and delta > 0:
        return f"{delta} days ago"
    if delta > 730:
        return f"~{int(delta / 365.25)} years ago"
    if delta == -1:
        return "in 1 day"
    if delta >= -730:
        return f"in {-delta} days"
    return f"in ~{int(-delta / 365.25)} years"


def _resolve_no_year(m: int, d: int, today: date) -> date | None:
    try:
        cand = date(today.year, m, d)
    except ValueError:
        return None
    if cand <= today:
        return cand
    if (cand - today).days <= 90:
        return cand
    try:
        return date(today.year - 1, m, d)
    except ValueError:
        return None


@dataclass(frozen=True)
class _Hit:
    start: int
    end: int
    kind: str                      # "date" | "weekday" | "day" | "age"
    y: int | None = None
    m: int | None = None
    d: int | None = None
    alt: tuple | None = None       # alternative (m, d) reading for numeric dates
    weekday: int | None = None
    mode: str | None = None        # weekday: past | last | next ; day: past | next
    age: int | None = None
    numeric: bool = False          # bare d/m or d.m.y — the only date forms a clinical reading can mimic


def _find_dates(text: str, today: date | None) -> list[_Hit]:
    hits: list[_Hit] = []
    for m in DMY_RE.finditer(text):
        mon = _MONTHS[m.group(2).lower()]
        if m.group(2) == "may":   # lowercase "may" is a verb
            continue
        hits.append(_Hit(m.start(), m.end(), "date", _year4(m.group(3), today), mon, int(m.group(1))))
    for m in MDY_RE.finditer(text):
        if m.group(1) == "may":
            continue
        hits.append(_Hit(m.start(), m.end(), "date", _year4(m.group(3), today), _MONTHS[m.group(1).lower()], int(m.group(2))))
    for m in SLASH_RE.finditer(text):
        a, b, y = int(m.group(1)), int(m.group(2)), _year4(m.group(3), today)
        if m.group(3) is None and ((b == 10 and a <= 10) or (b == 5 and a <= 5)):
            continue   # 7/10, 4/5: symptom scales, not dates
        hits.extend(_numeric(m.start(), m.end(), a, b, y))
    for m in DOT_RE.finditer(text):
        hits.extend(_numeric(m.start(), m.end(), int(m.group(1)), int(m.group(2)), _year4(m.group(3), today)))
    for m in ISO_RE.finditer(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid(y, mo, d):
            hits.append(_Hit(m.start(), m.end(), "date", y, mo, d))
    for m in ZH_DATE_RE.finditer(text):
        y, mo, d = _year4(m.group(1), today), int(m.group(2)), int(m.group(3))
        if _valid(y, mo, d):
            hits.append(_Hit(m.start(), m.end(), "date", y, mo, d))
    for m in ZH_DATE_NUM_RE.finditer(text):
        y = zh_number(m.group(1)) if m.group(1) else None
        if y is not None and y < 100:
            y = _year4(f"{y:02d}", today)
        mo, d = zh_number(m.group(2)), zh_number(m.group(3))
        if mo is not None and d is not None and _valid(y, mo, d):
            hits.append(_Hit(m.start(), m.end(), "date", y, mo, d))
    return hits


def _numeric(start: int, end: int, a: int, b: int, y: int | None) -> list[_Hit]:
    """DD/MM first (HK locale); MM/DD only if DD/MM is invalid. Never emit for a
    pair that fits neither (98/60, 120/80)."""
    # Only a year-less pair (7/10, 6/9) can be mistaken for a reading; 28/08/2026 cannot.
    if _valid(y, b, a):
        alt = (a, b) if _valid(y, a, b) and (a, b) != (b, a) else None
        return [_Hit(start, end, "date", y, b, a, alt=alt, numeric=y is None)]
    if _valid(y, a, b):
        return [_Hit(start, end, "date", y, a, b, numeric=y is None)]
    return []


_SENTENCE_BREAK_RE = re.compile(r"[.!?。！？\n]")


def _future_cued(text: str, start: int, end: int, window: int = 32) -> bool:
    """A future cue within ``window`` chars, not crossing a sentence boundary
    ("I'm seeing the oncologist Monday" puts 22 chars between cue and weekday)."""
    before = text[max(0, start - window):start]
    breaks = list(_SENTENCE_BREAK_RE.finditer(before))
    if breaks:
        before = before[breaks[-1].end():]
    after = text[end:end + window]
    brk = _SENTENCE_BREAK_RE.search(after)
    if brk:
        after = after[:brk.start()]
    return bool(_FUTURE_CUE_RE.search(before) or _FUTURE_CUE_RE.search(after))


def _find_weekdays(text: str) -> list[_Hit]:
    hits: list[_Hit] = []
    for m in WEEKDAY_EN_RE.finditer(text):
        word = m.group(2).rstrip(".").lower()
        if word not in _WEEKDAYS:
            continue
        if word not in _FULL_WEEKDAYS:   # abbreviations: Capitalised and mid-sentence only ("sat down")
            if not m.group(2)[0].isupper() or sentence_initial(text, m.start(2)):
                continue
        prefix = (m.group(1) or "").lower()
        if prefix == "last":
            mode = "last"
        elif prefix in ("this", "next", "coming") or _future_cued(text, m.start(), m.end()):
            mode = "next"
        else:
            mode = "past"
        hits.append(_Hit(m.start(), m.end(), "weekday", weekday=_WEEKDAYS[word], mode=mode))
    for m in WEEKDAY_ZH_RE.finditer(text):
        prefix = m.group(1) or ""
        if prefix in ("上", "上個"):
            mode = "last"
        elif prefix in ("下", "下個", "今個", "呢個", "今", "這個", "這", "本") or _future_cued(text, m.start(), m.end()):
            mode = "next"
        else:
            mode = "past"
        hits.append(_Hit(m.start(), m.end(), "weekday", weekday=_ZH_WEEKDAY[m.group(2)], mode=mode))
    return hits


def _find_days_zh(text: str) -> list[_Hit]:
    hits: list[_Hit] = []
    for m in ZH_DAY_RE.finditer(text):
        n = int(m.group(1))
        if not 1 <= n <= 31:
            continue
        if PHONE_CUE.search(text[max(0, m.start() - 20):m.start()]) or PHONE_CUE.search(text[m.end():m.end() + 20]):
            continue
        future = _future_cued(text, m.start(), m.end())
        hits.append(_Hit(m.start(), m.end(), "day", d=n, mode="next" if future else "past"))
    return hits


def _find_ages(text: str) -> list[_Hit]:
    hits: list[_Hit] = []
    for m in AGE_CUE_RE.finditer(text):
        hits.append(_Hit(m.start(1), m.end(1), "age", age=int(m.group(1))))
    for m in AGE_YEARS_RE.finditer(text):
        g = 1 if m.group(1) else 2
        hits.append(_Hit(m.start(), m.end(), "age", age=int(m.group(g))))
    for m in AGE_ZH_RE.finditer(text):
        raw = m.group(1)
        age = int(raw) if raw.isdigit() else zh_number(raw)
        if age is not None:
            hits.append(_Hit(m.start(), m.end(), "age", age=age))
    for m in AGE_ZH_YEAR_RE.finditer(text):
        hits.append(_Hit(m.start(1), m.end(1), "age", age=int(m.group(1))))
    return [h for h in hits if h.age is not None and 0 < h.age <= 120]


def _find_birth_years(text: str) -> list[_Hit]:
    hits: list[_Hit] = []
    for m in BIRTH_YEAR_RE.finditer(text):
        g = next(i for i in (1, 2, 3) if m.group(i))
        y = int(m.group(g))
        if 1900 <= y <= 2100:
            hits.append(_Hit(m.start(g), m.end(g), "birth_year", y=y))
    return hits


# --- the layer ------------------------------------------------------------------------------------
def generalise_spans(text: str, *, now=None, tz: str = DEFAULT_TZ, dob: date | None = None) -> list[Span]:
    today = _today(now, tz)
    spans: list[Span] = []

    for h in _find_dates(text, today):
        # "The pain started on 5 August" is a date however close the word "pain" is;
        # only numeric d/m forms can be mistaken for a reading (A6 guard).
        if h.numeric and clinical_guarded(text, h.start, h.end):
            continue
        original = text[h.start:h.end]
        readings = [(h.m, h.d)] + ([h.alt] if h.alt else [])
        if dob is not None and any((mm, dd) == (dob.month, dob.day) for mm, dd in readings) and (h.y is None or h.y == dob.year):
            spans.append(Span(h.start, h.end, DOB, PRIORITY_KNOWN, LAYER, key=original, token="[DOB]"))
            continue
        note = dedup = None
        if today is not None:
            if h.y is not None:
                try:
                    resolved = date(h.y, h.m, h.d)
                except ValueError:
                    resolved = None
            else:
                resolved = _resolve_no_year(h.m, h.d, today)
            if resolved is not None:
                note = offset_note(resolved, today)
                dedup = resolved.isoformat()
        spans.append(Span(h.start, h.end, DATE, PRIORITY_GENERALISE, LAYER, key=original, note=note, dedup=dedup))

    for h in _find_weekdays(text):
        original = text[h.start:h.end]
        dedup = None
        if h.mode == "next":
            note = f"next {_WEEKDAY_NAMES[h.weekday]}"
            dedup = note
        elif today is None:
            note = None
        else:
            if h.mode == "last":
                monday = today - timedelta(days=today.weekday())
                resolved = monday - timedelta(days=7) + timedelta(days=h.weekday)
            else:
                resolved = today - timedelta(days=(today.weekday() - h.weekday) % 7)
            note = offset_note(resolved, today)
            dedup = resolved.isoformat()
        spans.append(Span(h.start, h.end, DATE, PRIORITY_GENERALISE, LAYER, key=original, note=note, dedup=dedup))

    for h in _find_days_zh(text):
        if clinical_guarded(text, h.start, h.end):
            continue
        original = text[h.start:h.end]   # a bare 號 without a month is never treated as the DOB
        note = dedup = None
        if today is not None:
            resolved = _resolve_day_of_month(h.d, today, h.mode == "next")
            if resolved is not None:
                note = offset_note(resolved, today)
                dedup = resolved.isoformat()
        spans.append(Span(h.start, h.end, DATE, PRIORITY_GENERALISE, LAYER, key=original, note=note, dedup=dedup))

    for h in _find_ages(text):
        if clinical_guarded(text, h.start, h.end):
            continue
        band = age_band(h.age)
        spans.append(Span(h.start, h.end, AGE, PRIORITY_GENERALISE, LAYER, key=text[h.start:h.end],
                          token=f"[AGE · {band}]", note=band))

    ref_year = (today or date.today()).year
    for h in _find_birth_years(text):
        age = ref_year - h.y
        if 0 <= age <= 120:
            band = age_band(age)
            spans.append(Span(h.start, h.end, AGE, PRIORITY_GENERALISE, LAYER, key=text[h.start:h.end],
                              token=f"[AGE · {band}]", note=band))
    return spans


def _resolve_day_of_month(day: int, today: date, future: bool) -> date | None:
    def try_month(y: int, m: int) -> date | None:
        try:
            return date(y, m, day)
        except ValueError:
            return None

    y, m = today.year, today.month
    if future:
        cand = try_month(y, m)
        if cand is None or cand < today:
            m2, y2 = (m % 12) + 1, y + (1 if m == 12 else 0)
            cand = try_month(y2, m2)
        return cand
    cand = try_month(y, m)
    if cand is None or cand > today:
        m2, y2 = (m - 2) % 12 + 1, y - (1 if m == 1 else 0)
        cand = try_month(y2, m2)
    return cand


__all__ = [
    "DEFAULT_TZ", "LAYER", "age_band", "clinical_guarded", "generalise_spans", "offset_note", "zh_number",
]
