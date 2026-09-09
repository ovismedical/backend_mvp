"""Deterministic synthetic corpus for the PHI recall eval (seed 20260909).

200 transcripts — 100 English, 100 Cantonese (zh-HK) — of 6–12 turns: Florence
symptom chats for fatigue / nausea / appetite / cough / pain plus questionnaire
free-text answers (the six "Please specify" fields, synthesised the way
``app.questionnaire_triage_bridge`` does). Every identifier the generator plants
is recorded as a ``GoldSpan`` with exact offsets; every over-redaction trap
(drug names, lab values, doses, scales, 陳皮/李子/王子, common-word name parts…)
is a ``TrapSpan`` that must survive scrubbing verbatim.

All randomness goes through one ``random.Random(SEED)``; nothing reads the clock.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.inference.scrub import KnownIdentifiers
from app.inference.scrub.tokens import (
    ADDRESS, AGE, DATE, DOB, EMAIL, FACILITY, HANDLE, ID, OCCUPATION, ORG, PERSON, PHONE, PLACE, URL,
)

from . import templates_en as EN
from . import templates_zh as ZH

SEED = 20260909
TZ = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 9, 9, 10, 0, tzinfo=TZ)          # a Wednesday
TODAY = NOW.date()
N_PER_LANGUAGE = 100
LANGUAGES = ("en", "zh-HK")
SYMPTOM_NAMES = ("fatigue", "nausea", "appetite", "cough", "pain")

DIRECT_CLASSES = frozenset({PERSON, ID, PHONE, EMAIL, HANDLE, URL, DOB})
INDIRECT_CLASSES = frozenset({FACILITY, PLACE, ADDRESS, ORG, OCCUPATION, DATE, AGE})
ALL_GOLD_CLASSES = DIRECT_CLASSES | INDIRECT_CLASSES

# Match families for span matching (spec: PERSON; ID/PHONE/EMAIL/HANDLE/URL; FACILITY/PLACE/ADDRESS;
# ORG/OCCUPATION; DATE/AGE — DOB joins the time family because a DOB rendered as [DATE_n] is still redacted).
FAMILY = {
    PERSON: "person",
    ID: "contact", PHONE: "contact", EMAIL: "contact", HANDLE: "contact", URL: "contact",
    FACILITY: "location", PLACE: "location", ADDRESS: "location",
    ORG: "affiliation", OCCUPATION: "affiliation",
    DATE: "time", AGE: "time", DOB: "time",
}

_TOKEN_RE = re.compile(r"\{([A-Za-z_]+)(?::([^{}]*))?\}")
_ZH_DIGITS = "零一二三四五六七八九"
_AH_STOP = frozenset("媽爸哥姐妹姨叔伯婆公嫲爺囡仔女嫂")


# --- data model -----------------------------------------------------------------------------------
@dataclass(frozen=True)
class GoldSpan:
    start: int
    end: int
    cls: str
    surface: str
    kind: str
    future: bool | None = None      # DATE only: the template's tense (appointment vs history)

    @property
    def direct(self) -> bool:
        return self.cls in DIRECT_CLASSES


@dataclass(frozen=True)
class TrapSpan:
    start: int
    end: int
    surface: str
    kind: str


@dataclass
class Turn:
    role: str
    text: str
    gold: list[GoldSpan] = field(default_factory=list)
    traps: list[TrapSpan] = field(default_factory=list)
    neutral: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Persona:
    language: str
    full_name: str
    given: str
    surname: str
    sex: str
    order: str                      # "hk" | "west" | "cjk"
    common: bool
    username: str
    email: str
    phone: str
    dob: date
    doctor_name: str                # as stored on the doctor record (EN keeps the title)
    doctor_stripped: str
    doctor_surname: str
    hospital: str
    known_phone: bool
    known_email: bool
    handle: str

    @property
    def age(self) -> int:
        years = TODAY.year - self.dob.year
        if (TODAY.month, TODAY.day) < (self.dob.month, self.dob.day):
            years -= 1
        return years

    @property
    def given_scrubbable(self) -> bool:
        if self.order == "cjk":
            return len(self.given) >= 2
        return len(self.given) >= 3


@dataclass
class Transcript:
    id: str
    language: str
    kind: str                       # "chat" | "questionnaire"
    symptom: str | None
    persona: Persona
    turns: list[Turn]
    colloquial: bool = False        # ZH: a 陳生/陳太/阿文/given-name form of the patient or doctor was used
    common_word_trap: bool = False  # EN: the persona's common-word part appears as an ordinary word

    def known(self) -> KnownIdentifiers:
        p = self.persona
        return KnownIdentifiers(
            full_name=p.full_name, username=p.username,
            email=p.email if p.known_email else None,
            phone=p.phone if p.known_phone else None,
            dob=p.dob, doctor_name=p.doctor_name, hospital=p.hospital,
        )

    def gold_classes(self) -> set[str]:
        return {g.cls for t in self.turns for g in t.gold}

    def indirect_classes(self) -> set[str]:
        return self.gold_classes() & INDIRECT_CLASSES

    def has_traps(self) -> bool:
        return any(t.traps for t in self.turns)

    @property
    def n_turns(self) -> int:
        return len(self.turns)


# Segment = ("lit", text) | ("gold", cls, text, kind, future) | ("trap", text, kind) | ("neutral", text)
Segment = tuple


class Skip(Exception):
    """A template needs state the transcript does not have yet (e.g. {rel_again} before {rel})."""


# --- helpers --------------------------------------------------------------------------------------------
def zh_int(n: int) -> str:
    """1..99 -> 一 / 十五 / 廿二 is avoided (kept as 二十二) / 五十九."""
    if n < 10:
        return _ZH_DIGITS[n]
    tens, units = divmod(n, 10)
    out = ("" if tens == 1 else _ZH_DIGITS[tens]) + "十"
    return out + (_ZH_DIGITS[units] if units else "")


def luhn_complete(prefix: str) -> str:
    """Append the Luhn check digit to ``prefix`` (digits only)."""
    total = 0
    for i, ch in enumerate(reversed(prefix)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return prefix + str((10 - total % 10) % 10)


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_ZH_WEEKDAYS = "一二三四五六日"


def fmt_date_en(d: date, fmt: str) -> str:
    mon, mon3 = _MONTHS[d.month - 1], _MONTHS[d.month - 1][:3]
    return {
        "d Month": f"{d.day} {mon}", "d Mon": f"{d.day} {mon3}", "Month d": f"{mon} {d.day}",
        "d/m": f"{d.day}/{d.month}", "dd/mm/yyyy": d.strftime("%d/%m/%Y"), "iso": d.isoformat(),
        "dth Month": f"{d.day}{_ordinal(d.day)} {mon}", "d Month yyyy": f"{d.day} {mon} {d.year}",
    }[fmt]


def fmt_date_zh(d: date, fmt: str) -> str:
    return {
        "m月d日": f"{d.month}月{d.day}日", "m月d號": f"{d.month}月{d.day}號", "yyyy年m月d日": f"{d.year}年{d.month}月{d.day}日",
        "d/m": f"{d.day}/{d.month}", "zh": f"{zh_int(d.month)}月{zh_int(d.day)}日", "d號": f"{d.day}號",
    }[fmt]


def fmt_dob(d: date, fmt: str) -> str:
    mon, mon3 = _MONTHS[d.month - 1], _MONTHS[d.month - 1][:3]
    return {
        "dd/mm/yyyy": d.strftime("%d/%m/%Y"), "mm/dd/yyyy": d.strftime("%m/%d/%Y"), "iso": d.isoformat(),
        "d Month yyyy": f"{d.day} {mon} {d.year}", "Month d, yyyy": f"{mon} {d.day}, {d.year}",
        "Mon d yyyy": f"{mon3} {d.day} {d.year}", "dd.mm.yyyy": d.strftime("%d.%m.%Y"), "d/m/yy": f"{d.day}/{d.month}/{d.year % 100:02d}",
        "yyyy/mm/dd": d.strftime("%Y/%m/%d"), "d Month": f"{d.day} {mon}", "Month d": f"{mon} {d.day}",
        "yyyy年m月d日": f"{d.year}年{d.month}月{d.day}日", "yy年m月d日": f"{d.year % 100:02d}年{d.month}月{d.day}日",
        "m月d日": f"{d.month}月{d.day}日", "m月d號": f"{d.month}月{d.day}號", "d/m/yyyy": f"{d.day}/{d.month}/{d.year}",
        "zh": f"{zh_int(d.month)}月{zh_int(d.day)}日",
    }[fmt]


DOB_FORMATS_EN = ("dd/mm/yyyy", "mm/dd/yyyy", "iso", "d Month yyyy", "Month d, yyyy", "Mon d yyyy", "dd.mm.yyyy",
                  "d/m/yy", "yyyy/mm/dd", "d Month", "Month d")
DOB_FORMATS_ZH = ("yyyy年m月d日", "yy年m月d日", "m月d日", "m月d號", "d/m/yyyy", "zh", "dd/mm/yyyy")
DATE_FORMATS_EN = ("d Month", "d Mon", "Month d", "d/m", "dd/mm/yyyy", "iso", "dth Month")
DATE_FORMATS_ZH = ("m月d日", "m月d號", "yyyy年m月d日", "d/m", "zh", "d號")
PAST_OFFSETS = (5, 10, 12, 19, 28, 35, 41, 56)
FUTURE_OFFSETS = (3, 6, 8, 13, 16, 20)


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _romanise(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


# --- personas ----------------------------------------------------------------------------------------------
def make_personas(rng: random.Random, language: str) -> list[Persona]:
    out: list[Persona] = []
    if language == "en":
        for i, (full, given, surname, sex, order, common) in enumerate(EN.PERSONAS):
            doc = ("Dr. Test", "Test", "Test") if full == "Test Patient" else EN.DOCTORS[i % len(EN.DOCTORS)]
            while doc[2].lower() == surname.lower():
                doc = EN.DOCTORS[(EN.DOCTORS.index(doc) + 1) % len(EN.DOCTORS)]
            base = _romanise(given) + _romanise(surname)[:1] if order == "hk" else _romanise(given) + "." + _romanise(surname)
            username = f"{_romanise(given)}{_romanise(surname)[:2]}{rng.randint(10, 99)}"
            if full == "Test Patient":
                username = "testpatient"
            out.append(Persona(
                language="en", full_name=full, given=given, surname=surname, sex=sex, order=order, common=common,
                username=username, email=f"{base}@{rng.choice(['gmail.com', 'yahoo.com.hk', 'outlook.com', 'netvigator.com'])}",
                phone=f"{rng.choice('569')}{rng.randint(100, 999)} {rng.randint(1000, 9999)}",
                dob=date(rng.randint(1945, 1982), rng.randint(1, 12), rng.randint(13, 28)),
                doctor_name=doc[0], doctor_stripped=doc[1], doctor_surname=doc[2],
                hospital=rng.choice(EN.HOSPITALS), known_phone=rng.random() < 0.6, known_email=rng.random() < 0.5,
                handle=f"{_romanise(given)}{rng.choice(['.', '_', ''])}{_romanise(surname)}{rng.randint(1, 99)}",
            ))
        return out
    for i, (surname, given, sex) in enumerate(ZH.PERSONAS):
        doc = ZH.DOCTORS[i % len(ZH.DOCTORS)]
        while doc[0] == surname[0]:
            doc = ZH.DOCTORS[(ZH.DOCTORS.index(doc) + 1) % len(ZH.DOCTORS)]
        latin = rng.choice(["chan", "lee", "wong", "cheung", "ho", "leung", "lam", "ng", "au", "chow"])
        username = f"{latin}_{rng.choice(['dm', 'ml', 'kf', 'wk', 'sy', 'hk'])}{rng.randint(10, 99)}"
        out.append(Persona(
            language="zh-HK", full_name=surname + given, given=given, surname=surname, sex=sex, order="cjk", common=False,
            username=username, email=f"{username}@{rng.choice(['gmail.com', 'yahoo.com.hk', 'hotmail.com'])}",
            phone=f"{rng.choice('569')}{rng.randint(100, 999)} {rng.randint(1000, 9999)}",
            dob=date(rng.randint(1945, 1982), rng.randint(1, 12), rng.randint(13, 28)),
            doctor_name=doc, doctor_stripped=doc, doctor_surname=doc[0],
            hospital=rng.choice(ZH.HOSPITALS), known_phone=rng.random() < 0.6, known_email=rng.random() < 0.5,
            handle=f"{latin}{rng.choice(['.', '_'])}{rng.choice(['hk', 'cancerfree', 'daily', 'mum', 'dad'])}{rng.randint(1, 99)}",
        ))
    return out


# --- template filling --------------------------------------------------------------------------------------
class Resolver:
    """Fills slots for one transcript; remembers introduced people so later
    ``{rel_again}`` / ``{other_again}`` mentions refer back to them."""

    def __init__(self, rng: random.Random, persona: Persona, transcript: Transcript):
        self.rng = rng
        self.p = persona
        self.t = transcript
        self.zh = persona.language == "zh-HK"
        self.T = ZH if self.zh else EN
        self.relatives: list[str] = []
        self.others: list[str] = []
        self._pending_kin: str | None = None
        self._used_places: set[str] = set()

    # -- primitives --
    def gold(self, cls: str, text: str, kind: str, future: bool | None = None) -> Segment:
        return ("gold", cls, text, kind, future)

    def pick(self, seq):
        return self.rng.choice(list(seq))

    def pick_new(self, seq, used: set[str]):
        choices = [s for s in seq if s not in used]
        if not choices:
            choices = list(seq)
        v = self.rng.choice(choices)
        used.add(v)
        return v

    # -- patient / doctor --
    def _patient_title(self) -> str:
        p = self.p
        if self.zh:
            return p.surname + ("先生" if p.sex == "m" else self.pick(["太太", "小姐"]))
        title = "Mr" if p.sex == "m" else self.pick(["Mrs", "Ms"])
        return f"{title} {p.surname}"

    def _patient_colloq(self) -> tuple[str, str]:
        p = self.p
        if not self.zh:
            if p.given_scrubbable:
                return p.given, "patient_given"
            return p.full_name, "patient_full"
        forms = [(p.surname + ("生" if p.sex == "m" else "太"), "patient_surname_short")]
        if p.given_scrubbable:
            forms.append((p.given, "patient_given"))
        ah = "阿" + p.given[-1]
        if p.given[-1] not in _AH_STOP:
            forms.append((ah, "patient_ah"))
        surface, kind = self.pick(forms)
        self.t.colloquial = True
        return surface, kind

    # -- dates --
    def _date(self, offsets, formats, fmt_fn, kind_prefix: str, future: bool) -> Segment:
        for _ in range(20):
            d = TODAY + timedelta(days=(1 if future else -1) * self.pick(offsets))
            if (d.month, d.day) != (self.p.dob.month, self.p.dob.day):
                break
        fmt = self.pick(formats)
        if fmt == "d號" and (TODAY - d).days > 31:
            fmt = "m月d日"
        return self.gold(DATE, fmt_fn(d, fmt), f"{kind_prefix}:{fmt}", future)

    def _weekday(self, future: bool) -> Segment:
        if self.zh:
            if future:
                form = self.pick(["下星期", "下個禮拜", "下週"])
                kind = "weekday_future:prefixed"
            else:
                form = self.pick(["上星期", "上個禮拜", "上星期"])
                kind = "weekday_past:prefixed"
            return self.gold(DATE, form + self.pick(_ZH_WEEKDAYS[:6]), kind, future)
        day = self.pick(_WEEKDAYS[:5])
        if future:
            form = self.pick(["this", "next", "coming", None])
            if form is None:
                return self.gold(DATE, day, "weekday_future:cued", True)
            return self.gold(DATE, f"{form} {day}", "weekday_future:prefixed", True)
        form = self.pick(["last", None])
        if form is None:
            return self.gold(DATE, day, "weekday_past:bare", False)
        return self.gold(DATE, f"last {day}", "weekday_past:prefixed", False)

    # -- addresses --
    def _address(self) -> Segment:
        r = self.rng
        if self.zh:
            form = self.pick(["estate", "street"])
            if form == "estate":
                estate = self.pick(ZH.ESTATES)
                text = f"{estate}{r.randint(1, 12)}座{r.randint(2, 40)}樓{self.pick('ABCDEFGH')}室"
            else:
                text = f"{self.pick(ZH.PLACES_SHORT + ZH.PLACES_LONG)}{self.pick(ZH.STREETS)}{r.randint(2, 300)}號{r.randint(2, 30)}樓{self.pick('ABCD')}室"
            return self.gold(ADDRESS, text, f"address:{form}")
        form = self.pick(["flat_block", "room_street", "unit_tower"])
        if form == "flat_block":
            text = f"Flat {r.randint(1, 30)}{self.pick('ABCDEFGH')}, {r.randint(2, 40)}/F, Block {r.randint(1, 12)}, {self.pick(EN.ESTATES)}"
        elif form == "room_street":
            fl = r.randint(2, 25)
            text = f"Room {r.randint(1, 20)}, {fl}{_ordinal(fl)} Floor, {r.randint(2, 300)} {self.pick(EN.STREETS)}, {self.pick(EN.DISTRICTS)}"
        else:
            text = f"Unit {r.randint(1, 30)}, {r.randint(2, 40)}/F, Tower {r.randint(1, 9)}, {self.pick(EN.ESTATES)}"
        return self.gold(ADDRESS, text, f"address:{form}")

    # -- phones / ids --
    def _phone_known(self) -> Segment:
        digits = re.sub(r"\D", "", self.p.phone)
        fmt = self.pick(["space", "bare", "dash", "852"])
        text = {
            "space": f"{digits[:4]} {digits[4:]}", "bare": digits, "dash": f"{digits[:4]}-{digits[4:]}",
            "852": f"+852 {digits[:4]} {digits[4:]}",
        }[fmt]
        return self.gold(PHONE, text, f"phone_known:{fmt}" if self.p.known_phone else f"phone_unknown:{fmt}")

    def _phone_other(self, prefix: bool) -> Segment:
        r = self.rng
        digits = f"{self.pick('569')}{r.randint(100, 999)}{r.randint(1000, 9999)}"
        text = f"+852 {digits[:4]} {digits[4:]}" if prefix else self.pick([f"{digits[:4]} {digits[4:]}", digits])
        return self.gold(PHONE, text, "phone_852" if prefix else "phone_other")

    def _id(self, kind: str) -> Segment:
        r = self.rng
        if kind == "hkid":
            letters = self.pick(["A", "B", "C", "D", "E", "G", "K", "M", "P", "R", "W", "Y", "Z", "AB", "WX"])
            check = self.pick("0123456789A")
            text = f"{letters}{r.randint(100000, 999999)}({check})"
        elif kind == "mrn":
            text = f"{self.pick(['HK', 'QMH', 'PWH', 'KWH', 'MR', 'HA'])}-{r.randint(1000000, 9999999)}"
        elif kind == "passport":
            text = f"{self.pick('KHMC')}{r.randint(10000000, 99999999)}"
        elif kind == "hrp":
            text = f"H{r.randint(10000000, 99999999)}"
        else:  # card
            digits = luhn_complete("".join(str(r.randint(0, 9)) for _ in range(15)))
            text = " ".join(digits[i:i + 4] for i in range(0, 16, 4))
        return self.gold(ID, text, f"id:{kind}")

    # -- the dispatcher --
    def __call__(self, name: str, arg: str | None) -> list[Segment]:
        p, T, r = self.p, self.T, self.rng
        g = self.gold
        if name == "T":
            return [("trap", arg or "", (arg or "").lower())]
        if name == "N":
            return [("neutral", arg or "")]
        if name == "kin":
            word, sex = self.pick(T.KIN)
            self._pending_kin = sex
            return [("lit", word)]
        if name == "patient_full":
            return [g(PERSON, p.full_name, "patient_full")]
        if name == "patient_title":
            return [g(PERSON, self._patient_title(), "patient_title")]
        if name == "patient_given":
            if not p.given_scrubbable:
                return [g(PERSON, p.full_name, "patient_full")]
            if self.zh:
                self.t.colloquial = True
            return [g(PERSON, p.given, "patient_given")]
        if name == "patient_part":
            part = p.surname if len(p.surname) >= 3 else p.given.split()[-1]
            if len(part) < 3:
                return [g(PERSON, p.full_name, "patient_full")]
            return [g(PERSON, part, "patient_part")]
        if name == "patient_colloq":
            surface, kind = self._patient_colloq()
            return [g(PERSON, surface, kind)]
        if name == "username":
            return [g(PERSON, p.username, "username")]
        if name == "doctor_title":
            text = f"{p.doctor_surname}醫生" if self.zh else f"Dr {p.doctor_surname}"
            return [g(PERSON, text, "doctor_title")]
        if name == "doctor_full":
            text = p.doctor_name if self.zh else self.pick([p.doctor_name, p.doctor_stripped])
            return [g(PERSON, text, "doctor_full")]
        if name == "doctor_colloq":
            self.t.colloquial = True
            return [g(PERSON, f"{p.doctor_surname}生", "doctor_colloq")]
        if name == "rel":
            if arg:
                nm = arg
            else:
                sex = self._pending_kin if self._pending_kin in ("f", "m") else self.pick("fm")
                self._pending_kin = None
                pool = [n for n in T.RELATIVE_NAMES[sex] if n not in self.relatives and n != p.given and n != p.doctor_stripped]
                nm = self.pick(pool)
            self.relatives.append(nm)
            return [g(PERSON, nm, "rel_intro")]
        if name == "rel_again":
            if not self.relatives:
                raise Skip()
            return [g(PERSON, self.relatives[-1], "rel_again")]
        if name == "other_title":
            if self.zh:
                pool = [o for o in T.OTHER_TITLED if o not in self.others and o[0] not in (p.surname[0], p.doctor_surname[0])]
            else:
                pool = [o for o in T.OTHER_TITLED if o not in self.others
                        and o.split()[-1].lower() not in (p.surname.lower(), p.doctor_surname.lower())]
            nm = self.pick(pool)
            self.others.append(nm)
            return [g(PERSON, nm, "other_title")]
        if name == "other_again":
            if not self.others:
                raise Skip()
            return [g(PERSON, self.others[-1], "other_again")]
        if name == "facility":
            if arg:
                return [g(FACILITY, arg, "facility:gazetteer")]
            if r.random() < 0.25:
                return [g(FACILITY, self.pick(T.FACILITIES_SUFFIX), "facility:suffix")]
            return [g(FACILITY, self.pick(T.HOSPITALS), "facility:gazetteer")]
        if name == "facility_trap":
            return [g(FACILITY, self.pick(T.FACILITY_TRAPS), "facility:trap")]
        if name == "place":
            if self.zh:
                return [g(PLACE, self.pick_new(ZH.PLACES_LONG, self._used_places), "place:district")]
            return [g(PLACE, self.pick_new(EN.DISTRICTS, self._used_places), "place:district")]
        if name == "place_cued":
            return [g(PLACE, self.pick_new(ZH.PLACES_SHORT, self._used_places), "place:short_cued")]
        if name == "place_long":
            return [g(PLACE, self.pick_new(ZH.PLACES_LONG, self._used_places), "place:district")]
        if name == "mtr":
            return [g(PLACE, self.pick_new(EN.MTR, self._used_places), "place:mtr")]
        if name == "mtr_cued":
            return [g(PLACE, self.pick_new(ZH.PLACES_SHORT + ZH.PLACES_LONG, self._used_places), "place:mtr")]
        if name == "estate":
            return [g(PLACE, self.pick_new(T.ESTATES, self._used_places), "place:estate")]
        if name == "street":
            return [g(PLACE, self.pick_new(T.STREETS, self._used_places), "place:street")]
        if name == "address":
            return [self._address()]
        if name == "org":
            return [g(ORG, self.pick(T.ORGS), "org")]
        if name == "occ":
            return [g(OCCUPATION, self.pick(T.OCCUPATIONS), "occ")]
        if name == "a_occ":
            occ = self.pick(T.OCCUPATIONS)
            return [("lit", "an " if occ[0] in "aeiou" else "a "), g(OCCUPATION, occ, "occ")]
        if name == "date_past":
            return [self._date(PAST_OFFSETS, DATE_FORMATS_ZH if self.zh else DATE_FORMATS_EN,
                               fmt_date_zh if self.zh else fmt_date_en, "date_past", False)]
        if name == "date_future":
            formats = tuple(f for f in (DATE_FORMATS_ZH if self.zh else DATE_FORMATS_EN) if f != "d號")
            return [self._date(FUTURE_OFFSETS, formats, fmt_date_zh if self.zh else fmt_date_en, "date_future", True)]
        if name == "weekday_past":
            return [self._weekday(False)]
        if name == "weekday_future":
            return [self._weekday(True)]
        if name == "age_num":
            return [g(AGE, str(p.age), "age_num")]
        if name == "age_phrase":
            return [g(AGE, f"{p.age}歲" if self.zh else f"{p.age} years old", "age_phrase")]
        if name == "age_zh":
            return [g(AGE, f"{zh_int(p.age)}歲", "age_zh")]
        if name == "birth_year":
            return [g(AGE, str(p.dob.year), "birth_year")]
        if name == "dob":
            fmt = self.pick(DOB_FORMATS_ZH if self.zh else DOB_FORMATS_EN)
            return [g(DOB, fmt_dob(p.dob, fmt), f"dob:{fmt}")]
        if name in ("hkid", "mrn", "passport", "hrp", "card"):
            return [self._id(name)]
        if name == "phone":
            return [self._phone_known()]
        if name == "phone_other":
            return [self._phone_other(False)]
        if name == "phone_852":
            return [self._phone_other(True)]
        if name == "email":
            return [g(EMAIL, p.email, "email_known" if p.known_email else "email_unknown")]
        if name == "email_other":
            local = _romanise(self.relatives[-1]) if self.relatives else self.pick(["kelvin.wong", "mei.ling.c", "peterlau", "carmen.ho"])
            return [g(EMAIL, f"{local}@{self.pick(['outlook.com', 'gmail.com', 'yahoo.com.hk'])}", "email_other")]
        if name == "handle":
            return [g(HANDLE, p.handle, "handle")]
        if name == "handle_at":
            return [g(HANDLE, "@" + p.handle, "handle_at")]
        if name == "url":
            return [g(URL, self.pick(["cancerfund.org.hk", "www.hkcf.org", f"{_romanise(p.given)}diary.wordpress.com",
                                      "https://bit.ly/3xYz9Qk", "mydiary.blogspot.com", "www.facebook.com/groups/hkbreastcare"]), "url")]
        raise KeyError(name)


def fill(template: str, resolve: Resolver) -> list[Segment]:
    out: list[Segment] = []
    pos = 0
    for m in _TOKEN_RE.finditer(template):
        if m.start() > pos:
            out.append(("lit", template[pos:m.start()]))
        out.extend(resolve(m.group(1), m.group(2)))
        pos = m.end()
    if pos < len(template):
        out.append(("lit", template[pos:]))
    return out


def build_turn(role: str, sentences: list[list[Segment]], sep: str) -> Turn:
    turn = Turn(role=role, text="")
    parts: list[str] = []
    pos = 0
    for i, sentence in enumerate(sentences):
        if i:
            parts.append(sep)
            pos += len(sep)
        for seg in sentence:
            text = seg[1] if seg[0] != "gold" else seg[2]
            start, end = pos, pos + len(text)
            if seg[0] == "gold":
                turn.gold.append(GoldSpan(start, end, seg[1], text, seg[3], seg[4]))
            elif seg[0] == "trap":
                turn.traps.append(TrapSpan(start, end, text, seg[2]))
            elif seg[0] == "neutral":
                turn.neutral.append((start, end))
            parts.append(text)
            pos = end
    turn.text = "".join(parts)
    return turn


# --- transcript assembly ---------------------------------------------------------------------------------------
CLASS_KEYS = {
    PERSON: ("PERSON_REL", "PERSON_OTHER_TITLE", "PERSON_DOCTOR", "PERSON_PATIENT"),
    FACILITY: ("FACILITY",), PLACE: ("PLACE",), ADDRESS: ("ADDRESS",), ORG: ("ORG",), OCCUPATION: ("OCCUPATION",),
    DATE: ("DATE_PAST", "DATE_FUTURE"), AGE: ("AGE",), DOB: ("DOB",), ID: ("ID",), PHONE: ("PHONE",),
    EMAIL: ("EMAIL",), HANDLE: ("HANDLE",), URL: ("URL",),
}
SAME_TURN_REPEAT_EN = [
    "My {kin} {rel} came round. {rel_again} thinks I look pale.",
    "I rang my {kin} {rel}. {rel_again} is coming tomorrow.",
]
SAME_TURN_REPEAT_ZH = [
    "我{kin}{rel}嚟探我。{rel_again}話我好蒼白。",
    "我打俾我{kin}{rel}。{rel_again}聽日嚟。",
]


def _profile(i: int, rng: random.Random) -> tuple[int, int]:
    """(indirect classes, direct classes) to seed; 40% of transcripts get >=3 indirect classes."""
    if i % 5 in (0, 1):
        indirect = rng.randint(3, 5)
        direct = rng.randint(0, 6 - indirect)
    elif i % 5 in (2, 3):
        indirect = rng.randint(1, 2)
        direct = rng.randint(0, 3)
    else:
        indirect = 0
        direct = rng.randint(0, 3)
    return indirect, direct


def _choose_identifier_sentences(rng: random.Random, res: Resolver, indirect_k: int, direct_k: int) -> tuple[list[list[Segment]], list[list[Segment]]]:
    T = res.T
    classes = rng.sample(sorted(INDIRECT_CLASSES), indirect_k) + rng.sample(sorted(DIRECT_CLASSES), direct_k)
    rng.shuffle(classes)
    sentences: list[list[Segment]] = []
    followups: list[list[Segment]] = []
    for cls in classes:
        key = rng.choice(CLASS_KEYS[cls])
        if cls == PERSON and res.relatives and rng.random() < 0.3:
            key = "PERSON_REL"
        template = rng.choice(T.IDENT[key])
        try:
            sentences.append(fill(template, res))
        except Skip:
            continue
        if key == "PERSON_REL" and rng.random() < 0.7:
            followups.append(fill(rng.choice(T.IDENT["PERSON_REL_AGAIN"]), res))
        if key == "PERSON_OTHER_TITLE" and rng.random() < 0.5:
            followups.append(fill(rng.choice(T.IDENT["PERSON_OTHER_AGAIN"]), res))
    return sentences, followups


def _trap_sentences(rng: random.Random, res: Resolver, n: int) -> list[list[Segment]]:
    T = res.T
    out = [fill(t, res) for t in rng.sample(T.TRAPS, n)]
    if rng.random() < 0.3:
        out.append(fill(rng.choice(T.FACILITY_TRAP_SENTENCES), res))
    if rng.random() < 0.15:
        # the A11 trap: a friend called Mary and Queen Mary Hospital in one breath
        tmpl = "我朋友{rel:Mary}揸車送我去{facility:瑪麗醫院}。" if res.zh else "My friend {rel:Mary} drove me to {facility:Queen Mary Hospital}."
        out.append(fill(tmpl, res))
    if res.zh:
        for tmpl in ZH.SURNAME_TRAPS.get(res.p.surname, []):
            if rng.random() < 0.6:
                out.append(fill(tmpl, res))
    return out


def _distribute(items: list, n_slots: int, rng: random.Random) -> list[list]:
    """Spread ``items`` over ``n_slots`` user turns, preserving order (so a repeat
    mention always lands after its introduction)."""
    slots: list[list] = [[] for _ in range(n_slots)]
    if not items or not n_slots:
        return slots
    prev = 0
    for idx, item in enumerate(items):
        base = int(idx * n_slots / len(items)) + (1 if idx and rng.random() < 0.3 else 0)
        slot = min(n_slots - 1, max(prev, base))
        slots[slot].append(item)
        prev = slot
    return slots


def _echo(rng: random.Random, res: Resolver, turns: list[Turn], T, sep: str) -> list[Segment] | None:
    """An assistant turn that names a person ALREADY introduced in an emitted
    turn (Florence echoing a relative), so the gold span is only ever claimed
    for a name the scrubber had a chance to learn."""
    seen_rel = [g.surface for turn in turns for g in turn.gold if g.kind == "rel_intro"]
    seen_other = [g.surface for turn in turns for g in turn.gold if g.kind == "other_title"]
    if not seen_rel and not seen_other:
        return None
    saved = (res.relatives, res.others)
    res.relatives, res.others = seen_rel, seen_other
    try:
        templates = [e for e in T.ECHOES if ("{rel_again}" in e and seen_rel) or ("{other_again}" in e and seen_other)]
        return fill(rng.choice(templates), res) if templates else None
    finally:
        res.relatives, res.others = saved


def build_chat(i: int, rng: random.Random, persona: Persona, symptom: str) -> Transcript:
    lang = persona.language
    T = ZH if lang == "zh-HK" else EN
    sep = "" if lang == "zh-HK" else " "
    t = Transcript(id=f"{lang}-{i:03d}", language=lang, kind="chat", symptom=symptom, persona=persona, turns=[])
    res = Resolver(rng, persona, t)
    sym = T.SYMPTOMS[symptom]

    n_pairs = rng.choice([2, 3, 3, 4, 4])
    pairs = rng.sample(sym["pairs"], n_pairs)
    indirect_k, direct_k = _profile(i, rng)
    ident, followups = _choose_identifier_sentences(rng, res, indirect_k, direct_k)
    if rng.random() < 0.15:
        ident.append(fill(rng.choice(SAME_TURN_REPEAT_ZH if res.zh else SAME_TURN_REPEAT_EN), res))
    traps = _trap_sentences(rng, res, rng.choice([0, 1, 1, 2, 2, 3]))
    if persona.common and persona.full_name in EN.COMMON_WORD_TRAPS:
        traps.extend(fill(s, res) for s in rng.sample(EN.COMMON_WORD_TRAPS[persona.full_name], rng.randint(1, 2)))
        t.common_word_trap = True
    context = [fill(rng.choice(T.TREATMENT_CONTEXT), res)] if rng.random() < 0.6 else []

    n_user = 1 + n_pairs
    # identifiers first (introductions before repeats), traps/context interleaved afterwards
    ordered = ident + followups
    for s in traps + context:
        ordered.insert(rng.randint(0, len(ordered)), s)
    slots = _distribute(ordered, n_user, rng)

    turns: list[Turn] = []
    turns.append(build_turn("assistant", [fill(rng.choice(T.OPENERS), res)], sep))
    turns.append(build_turn("user", [fill(rng.choice(sym["intro"]), res)] + slots[0], sep))
    echo_done = False
    for k, (q, answers) in enumerate(pairs):
        q_sentences = [fill(q, res)]
        if persona.common and persona.full_name in EN.COMMON_WORD_ASSISTANT_TRAPS and k == 0:
            q_sentences.append(fill(rng.choice(EN.COMMON_WORD_ASSISTANT_TRAPS[persona.full_name]), res))
            t.common_word_trap = True
        if not echo_done and rng.random() < 0.5:
            echo = _echo(rng, res, turns, T, sep)
            if echo is not None:
                q_sentences.insert(0, echo)
                echo_done = True
        turns.append(build_turn("assistant", q_sentences, sep))
        turns.append(build_turn("user", [fill(rng.choice(answers), res)] + slots[k + 1], sep))
    if len(turns) < 12 and (len(turns) < 7 or rng.random() < 0.8):
        turns.append(build_turn("assistant", [fill(rng.choice(T.CLOSERS), res)], sep))
    if len(turns) < 6:
        turns.append(build_turn("user", [fill(rng.choice(T.TREATMENT_CONTEXT), res)], sep))
    t.turns = turns
    return t


_BRIDGE_GREETING = "Hello! I'm going to review your symptom questionnaire responses to assess how you're feeling today."
_SEVERITY = (("Mild", 1), ("Moderate", 2), ("Moderate", 3), ("Severe", 4))


def build_questionnaire(i: int, rng: random.Random, persona: Persona) -> Transcript:
    lang = persona.language
    T = ZH if lang == "zh-HK" else EN
    t = Transcript(id=f"{lang}-{i:03d}", language=lang, kind="questionnaire", symptom=None, persona=persona, turns=[])
    res = Resolver(rng, persona, t)
    n_sections = rng.choice([3, 3, 4, 5])
    sections = rng.sample(EN.QUESTIONNAIRE_SECTIONS, n_sections)
    turns = [build_turn("assistant", [[("lit", _BRIDGE_GREETING)]], " ")]
    for k, (title, question, value) in enumerate(sections):
        turns.append(build_turn("assistant", [[("lit", f"Let me ask about {title}.")]], " "))
        free = fill(rng.choice(T.QUESTIONNAIRE_FREE_TEXT[title]), res)
        if k == 0 and persona.common and persona.full_name in EN.COMMON_WORD_TRAPS:
            free = free + [("lit", " ")] + fill(rng.choice(EN.COMMON_WORD_TRAPS[persona.full_name]), res)
            t.common_word_trap = True
        label, score = rng.choice(_SEVERITY)
        body = [[("lit", f"Regarding {title}:\n- {question}: {value}\n- Please specify: ")] + free
                + [("lit", f"\nOverall severity: {label} ({score})")]]
        turns.append(build_turn("user", body, ""))
    if len(turns) < 7 or rng.random() < 0.4:
        flags = rng.sample(EN.QUESTIONNAIRE_FLAGS, rng.randint(1, 2))
        sentences = [[("lit", "Additional concerns I want to flag:")]]
        for f in flags:
            sentences.append([("lit", "- ")] + fill(f, res))
        turns.append(build_turn("user", sentences, "\n"))
    t.turns = turns
    return t


def generate(seed: int = SEED, n_per_language: int = N_PER_LANGUAGE) -> list[Transcript]:
    rng = random.Random(seed)
    out: list[Transcript] = []
    for lang in LANGUAGES:
        personas = make_personas(rng, lang)
        for i in range(n_per_language):
            persona = personas[i % len(personas)]
            if i % 7 == 6:
                out.append(build_questionnaire(i, rng, persona))
            else:
                out.append(build_chat(i, rng, persona, SYMPTOM_NAMES[i % len(SYMPTOM_NAMES)]))
    return out


def corpus_stats(transcripts: list[Transcript]) -> dict:
    by_lang: dict[str, dict] = {}
    for lang in LANGUAGES:
        ts = [t for t in transcripts if t.language == lang]
        gold = [g for t in ts for turn in t.turns for g in turn.gold]
        by_lang[lang] = {
            "transcripts": len(ts),
            "questionnaires": sum(t.kind == "questionnaire" for t in ts),
            "turns": sum(t.n_turns for t in ts),
            "gold_spans": len(gold),
            "trap_spans": sum(len(turn.traps) for t in ts for turn in t.turns),
            "with_traps": sum(t.has_traps() for t in ts),
            "with_3plus_indirect": sum(len(t.indirect_classes()) >= 3 for t in ts),
            "common_word_personas": sum(t.persona.common for t in ts),
            "colloquial_cjk_forms": sum(t.colloquial for t in ts),
            "classes": sorted({g.cls for g in gold}),
            "gold_per_class": {c: sum(g.cls == c for g in gold) for c in sorted({g.cls for g in gold})},
        }
    return {"total": len(transcripts), "seed": SEED, "now": NOW.isoformat(), "by_language": by_lang}


__all__ = [
    "ALL_GOLD_CLASSES", "DIRECT_CLASSES", "FAMILY", "GoldSpan", "INDIRECT_CLASSES", "NOW", "Persona", "SEED",
    "TODAY", "Transcript", "TrapSpan", "Turn", "corpus_stats", "generate",
]
