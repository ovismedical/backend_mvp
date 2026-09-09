"""Layer 1 (known identifiers + session-known originals) and layer 3b (kinship,
title, occupation and surname rules that need a cue).

Layer 1 knows who the patient is: full name, username, email, phone, DOB, the
doctor, the hospital. It also re-matches every original already in the session
TokenMap (amendment A0) so a name learned on turn 1 never reaches the provider
on turn 5 just because the cue was missing the second time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .gazetteers import (
    common_words, compound_surnames_zh, kinship_words, kinship_zh_with_ah, occupations, single_surnames_zh,
    split_scripts, titles,
)
from .generalise import age_band
from .patterns import ZH_STOP
from .tokens import (
    AGE, EMAIL, FACILITY, ID, LB, OCCUPATION, PERSON, PHONE, PRIORITY_KNOWN, PRIORITY_NAMES, RB,
    SESSION_EXCLUDED, Span, TokenMap, flexible_literal, has_cjk, normalise_phone,
    sentence_initial,
)

LAYER_KNOWN = "known"
LAYER_SESSION = "session_known"
LAYER_NAMES = "names"

_TITLE_PREFIX_RE = re.compile(
    r"^(?:dr|doctor|prof|professor|mr|mrs|ms|miss|madam|mdm|nurse|sister)\.?\s+", re.IGNORECASE
)
_SPLIT_RE = re.compile(r"[\s\-]+")
_CJK_NAME_RE = re.compile(r"^[一-鿿]{2,4}$")
_COMPOUND_SURNAMES = ("歐陽", "司徒", "司馬", "諸葛", "夏侯", "公孫", "慕容", "宇文", "長孫", "令狐", "皇甫")
_CJK_SUFFIX_VARIANTS = ("先生", "太太", "小姐", "女士", "姑娘", "醫生", "生", "太", "伯", "叔", "嬸", "姨")
_DOB_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y")


def strip_title(name: str | None) -> str:
    return _TITLE_PREFIX_RE.sub("", (name or "").strip()).strip()


def parse_dob(value) -> date | None:
    """MM/DD/YYYY first (seed/register format), then YYYY-MM-DD, then DD/MM/YYYY.
    Returns None when unparseable (the caller keeps the raw string as ``extra``)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in _DOB_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class KnownIdentifiers:
    full_name: str | None = None
    username: str | None = None
    email: str | None = None
    phone: str | None = None
    dob: date | None = None
    doctor_name: str | None = None
    hospital: str | None = None
    extra: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.dob is not None and not isinstance(self.dob, date):
            parsed = parse_dob(self.dob)
            if parsed is None:
                self.extra = list(self.extra) + [str(self.dob)]
            self.dob = parsed
        elif isinstance(self.dob, datetime):
            self.dob = self.dob.date()
        self.extra = [e for e in (self.extra or []) if isinstance(e, str) and e.strip()]

    @property
    def display_name(self) -> str:
        """The patient's name for [PERSON_1]: full_name, else username (A1)."""
        return (self.full_name or "").strip() or (self.username or "").strip()

    # -- derived forms -------------------------------------------------------------
    def username_scrubbed(self) -> bool:
        u = (self.username or "").strip()
        return len(u) >= 4 and u.lower() not in common_words()

    def dob_forms(self) -> list[str]:
        if not self.dob:
            return []
        d = self.dob
        return [
            d.strftime("%m/%d/%Y"), d.strftime("%d/%m/%Y"), d.isoformat(),
            f"{d.day}/{d.month}/{d.year}", d.strftime("%Y/%m/%d"), str(d.year),
        ]

    def leak_forms(self) -> list[str]:
        """Exactly the whole-string surface forms layer 1 replaces unconditionally
        (A9): anything leak_check flags is something ctx.scrub would have replaced."""
        forms: list[str] = []
        forms.extend(_latin_name_forms(self.full_name, unconditional_only=True))
        if self.username_scrubbed():
            forms.append(self.username.strip())
        if self.email and self.email.strip():
            forms.append(self.email.strip())
        if self.phone and len(normalise_phone(self.phone)) >= 7:
            forms.append(self.phone.strip())
            forms.append(normalise_phone(self.phone))
        forms.extend(self.dob_forms())
        if self.doctor_name and self.doctor_name.strip():
            doc = self.doctor_name.strip()
            stripped = strip_title(doc)
            if has_cjk(stripped) or len(_SPLIT_RE.split(stripped)) >= 2:
                forms.append(doc)
                if stripped != doc:
                    forms.append(stripped)
            elif stripped != doc:
                forms.append(doc)  # "Dr. Test": title form is unconditional, bare part is not
        if self.hospital and self.hospital.strip():
            forms.append(self.hospital.strip())
        forms.extend(self.extra)
        out: list[str] = []
        seen: set[str] = set()
        for f in forms:
            if f and f not in seen:
                seen.add(f)
                out.append(f)
        return out


def _latin_name_forms(name: str | None, *, unconditional_only: bool) -> list[str]:
    """Whole name, title-stripped whole, adjacent two-token forms and (when
    ``unconditional_only``) non-stoplisted ≥3-char parts."""
    raw = (name or "").strip()
    if not raw or has_cjk(raw):
        return [raw] if raw and has_cjk(raw) else []
    stripped = strip_title(raw)
    parts = [p for p in _SPLIT_RE.split(stripped) if p]
    stop = common_words()
    forms: list[str] = []
    if len(parts) >= 2:
        forms.append(stripped)
        if raw != stripped:
            forms.append(raw)
        for a, b in zip(parts, parts[1:]):
            forms += [f"{a} {b}", f"{a}-{b}", f"{a}{b}"]
    elif raw != stripped:
        forms.append(raw)  # "Dr. Test" as a whole
    for p in parts:
        if len(p) >= 3 and p.lower() not in stop:
            forms.append(p)
        elif not unconditional_only and len(p) >= 3:
            forms.append(p)
    return forms


# --- layer 1 matcher -------------------------------------------------------------------
@dataclass(frozen=True)
class _Form:
    regex: re.Pattern
    cls: str
    canonical: str
    conditional: bool = False   # Capitalised & not sentence-initial only


class KnownMatcher:
    """Compiled once per ScrubContext."""

    def __init__(self, known: KnownIdentifiers):
        self.known = known
        self.forms: list[_Form] = []
        self._part_index: dict[str, str] = {}   # lowercase part/variant -> canonical
        display = known.display_name
        if display:
            self._add_person(known.full_name or known.username, canonical=display)
            if known.username_scrubbed():
                u = known.username.strip()
                self.forms.append(_Form(re.compile(flexible_literal(u), re.IGNORECASE), PERSON, display))
                self._part_index[u.lower()] = display
        if known.doctor_name and known.doctor_name.strip():
            self._add_person(known.doctor_name, canonical=known.doctor_name.strip())
        if known.email and known.email.strip():
            e = known.email.strip()
            self.forms.append(_Form(re.compile(flexible_literal(e), re.IGNORECASE), EMAIL, e))
        if known.phone:
            digits = normalise_phone(known.phone)
            if len(digits) >= 7:
                self.forms.append(_Form(phone_regex(digits), PHONE, known.phone.strip()))
        if known.hospital and known.hospital.strip():
            h = known.hospital.strip()
            self.forms.append(_Form(re.compile(flexible_literal(h), re.IGNORECASE), FACILITY, h))
        for x in known.extra:
            self.forms.append(_Form(re.compile(flexible_literal(x), re.IGNORECASE), ID, x))
        self.dob_year_re = re.compile(rf"(?<![\d]){known.dob.year}(?![\d])") if known.dob else None

    def _add_person(self, name: str | None, *, canonical: str) -> None:
        raw = (name or "").strip()
        if not raw:
            return
        if has_cjk(raw):
            for variant in cjk_name_variants(raw):
                self.forms.append(_Form(re.compile(re.escape(variant)), PERSON, canonical))
                self._part_index[variant] = canonical
            return
        stripped = strip_title(raw)
        parts = [p for p in _SPLIT_RE.split(stripped) if p]
        stop = common_words()
        if len(parts) >= 2:
            self.forms.append(_Form(re.compile(flexible_literal(stripped), re.IGNORECASE), PERSON, canonical))
            if raw != stripped:
                self.forms.append(_Form(re.compile(flexible_literal(raw), re.IGNORECASE), PERSON, canonical))
            for a, b in zip(parts, parts[1:]):
                self.forms.append(_Form(re.compile(flexible_literal(f"{a} {b}"), re.IGNORECASE), PERSON, canonical))
        elif raw != stripped:
            self.forms.append(_Form(re.compile(flexible_literal(raw), re.IGNORECASE), PERSON, canonical))
        for p in parts:
            if len(p) < 3:
                continue
            self._part_index[p.lower()] = canonical
            if p.lower() not in stop:
                self.forms.append(_Form(re.compile(LB + re.escape(p) + RB, re.IGNORECASE), PERSON, canonical))
            else:
                cap = p[0].upper() + p[1:].lower()
                self.forms.append(_Form(re.compile(LB + re.escape(cap) + RB), PERSON, canonical, conditional=True))

    def spans(self, text: str, *, today: date | None = None) -> list[Span]:
        out: list[Span] = []
        for f in self.forms:
            for m in f.regex.finditer(text):
                if f.conditional and not _capitalised_part_ok(text, m.start(), m.end()):
                    continue
                out.append(Span(m.start(), m.end(), f.cls, PRIORITY_KNOWN, LAYER_KNOWN, key=f.canonical))
        if self.dob_year_re is not None:
            band = age_band((today or date.today()).year - self.known.dob.year)
            for m in self.dob_year_re.finditer(text):
                out.append(Span(m.start(), m.end(), AGE, PRIORITY_KNOWN, LAYER_KNOWN, key=m.group(0),
                                token=f"[AGE · {band}]", note=band))
        return out

    def resolve_person(self, surface: str) -> str | None:
        """Canonical person for a bare name surface (used by the title rule so
        "Mr Long" maps to the known patient "Ho Long")."""
        s = surface.strip()
        if has_cjk(s):
            return self._part_index.get(s)
        key = " ".join(_SPLIT_RE.split(s)).lower()
        if key in self._part_index:
            return self._part_index[key]
        for form in self.forms:
            if form.cls == PERSON and form.regex.fullmatch(s):
                return form.canonical
        return None


_LOWER_WORD_AFTER_RE = re.compile(r"\s+[a-z]")


def _capitalised_part_ok(text: str, start: int, end: int) -> bool:
    """A stoplisted name part written Capitalised counts as the name when it is
    not sentence-initial (A2). Reconciliation with A11 ("Long came to visit" ->
    PERSON_1): at the very start of the text it also counts when a lowercase
    word follows — "Long came…" yes, "Long-term pain" / "May I ask" no."""
    if not sentence_initial(text, start):
        return True
    if text[:start].strip() == "":
        return bool(_LOWER_WORD_AFTER_RE.match(text, end))
    return False


def phone_regex(digits: str) -> re.Pattern:
    """Match ``digits`` with any separators between them, optionally prefixed by
    +852 / 852 / (852)."""
    body = r"[\s\-.()]*".join(re.escape(d) for d in digits)
    return re.compile(rf"(?<!\d)(?:\(?\+?852\)?[\s\-.()]*)?{body}(?!\d)")


def cjk_split(name: str) -> tuple[str, str]:
    """(surname, given) for a 2–4 char CJK name; compound surnames first."""
    for c in compound_surnames_zh() or _COMPOUND_SURNAMES:
        if name.startswith(c) and len(name) > len(c):
            return c, name[len(c):]
    return name[0], name[1:]


def cjk_name_variants(name: str) -> list[str]:
    """Full name plus the colloquial variants that map to the same token (A3)."""
    name = name.strip()
    if not _CJK_NAME_RE.match(name):
        return [name] if name else []
    surname, given = cjk_split(name)
    out = [name]
    if len(given) >= 2:
        out.append(given)
    for suf in _CJK_SUFFIX_VARIANTS:
        out.append(surname + suf)
    if given:
        ah = "阿" + given[-1]
        if ah not in kinship_zh_with_ah():
            out.append(ah)
    return out


# --- layer 0: session-known originals ------------------------------------------------------
class SessionMatcher:
    """Re-matches every original in the TokenMap (except AGE/DATE/DOB) and reuses
    its token. Rebuilt only when the map's version changes."""

    def __init__(self) -> None:
        self._version = -1
        self._forms: list[tuple[re.Pattern, str]] = []

    def _rebuild(self, token_map: TokenMap) -> None:
        self._forms = []
        for token, cls, original in token_map.entries():
            if cls in SESSION_EXCLUDED or not original.strip():
                continue
            if cls == PHONE:
                digits = normalise_phone(original)
                if len(digits) >= 7:
                    self._forms.append((phone_regex(digits), token))
                continue
            flags = 0 if has_cjk(original) else re.IGNORECASE
            self._forms.append((re.compile(flexible_literal(original), flags), token))
        self._version = token_map.version

    def spans(self, text: str, token_map: TokenMap) -> list[Span]:
        if token_map.version != self._version:
            self._rebuild(token_map)
        out: list[Span] = []
        for regex, token in self._forms:
            cls = token_map.class_of(token)
            for m in regex.finditer(text):
                out.append(Span(m.start(), m.end(), cls, PRIORITY_KNOWN, LAYER_SESSION, key=token_map.get(token), token=token))
        return out


# --- layer 3b: cue-gated name rules -------------------------------------------------------------
def _alt(entries) -> str:
    return "|".join(re.escape(e) for e in sorted(set(entries), key=lambda s: (-len(s), s)))


_NAME_WORD = r"[A-Z][a-z'\-]+"
_NAME_EXCLUDE = (r"(?!(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|January|February|March|April|May|June|"
                 r"July|August|September|October|November|December|I|The|And|But|Or|So|If|When|Then|Who|What|Where|"
                 r"How|Why|Yes|No|Not|Is|Was|Has|Had|Will|Can|Could|Should|Would|Did|Do|Does|Am|Are|Were|Be|Been|"
                 r"Hospital|Clinic|Doctor|Nurse|Dr|Mr|Mrs|Ms|Miss|Madam|Today|Tomorrow|Yesterday|Next|Last|"
                 r"Also|Just|Very|Really|Because|Since|After|Before|Again|Still|Always|Never|Sometimes|Often|"
                 r"Please|Thanks|Thank|Okay|OK|Florence|Hong|Kong)(?![a-z]))")


def _build_title_re() -> re.Pattern:
    latin, _ = split_scripts(titles())
    t = _alt(latin)
    # Scoped (?i:) keeps the title case-insensitive while the name stays Capitalised.
    return re.compile(rf"{LB}(?i:{t})\s+({_NAME_EXCLUDE}{_NAME_WORD}(?:\s+{_NAME_EXCLUDE}{_NAME_WORD}){{0,2}}){RB}")


def _build_title_zh_re() -> re.Pattern:
    _, cjk = split_scripts(titles())
    surnames = "".join(sorted(single_surnames_zh()))
    compound = _alt(compound_surnames_zh())
    # The wildcard may not cross a function word (文同陳醫生 must not be one span).
    stop = re.escape("".join(sorted(ch for ch in ZH_STOP if has_cjk(ch))))
    return re.compile(rf"(?:{compound}|[{surnames}])(?:(?![{stop}])[一-鿿]){{0,2}}(?:{_alt(cjk)})")


def _build_surname_short_re() -> re.Pattern:
    surnames = "".join(sorted(single_surnames_zh()))
    compound = _alt(compound_surnames_zh())
    return re.compile(rf"(?:{compound}|[{surnames}])[生太](?![日活命病意效產長存態理物涯氣肖陽])")


_SURNAME_SHORT_STOP = frozenset({
    "衛生", "余生", "花生", "畢生", "平生", "安生", "馬太", "方太", "原生", "新生", "重生", "長生", "野生", "後生", "再生",
    "養生", "維生", "永生", "寄生", "共生", "衍生", "滋生", "晚生", "怕生", "此生", "今生", "往生", "終生", "好生", "書生",
    "女生", "男生", "考生", "招生", "產生", "人生", "一生", "天生", "陌生", "出生", "發生", "醫生", "學生", "先生", "太太",
    "高生", "白生", "金生", "田生", "石生", "文生", "康生", "黑生",
})


def _build_kinship_re() -> re.Pattern:
    latin, _ = split_scripts(kinship_words())
    k = _alt(latin)
    return re.compile(
        rf"{LB}(?:(?i:my|his|her|our|their|the)\s+)?(?i:{k})(?:\s*,\s*|\s+)(?:(?i:is|was)\s+)?(?i:called|named)?\s*,?\s*"
        rf"({_NAME_EXCLUDE}{_NAME_WORD}(?:\s+{_NAME_EXCLUDE}{_NAME_WORD}){{0,2}}){RB}"
    )


# Ambiguous surnames are excluded from the cue-gated ZH kinship rule (高興, 容易,
# 關心, 方便, 常常, 白天 must survive "我個女高興"); the known layer still uses them.
_AMBIGUOUS_SURNAMES = set("高容關方常白文孫戴賴簡湯任莫尤程易包江游溫夏石章田金康黑史萬紀邊申伊辛尚卜祝單談俞巫鄔邵郁董錢陸侯顧毛倪賀練熊殷龍鄒凌勞符喬阮薛嚴歐")
# What may follow a given name after a kinship word: punctuation, or the verb/
# adverb that starts the predicate (我個女美玲揸車送我 / 我新抱婉婷嚟探我 / 我老公志強打電話).
_ZH_NAME_TAIL = (r"(?=$|[，。、！？,.!?\s]|話|講|說|同|陪|帶|送|叫|係|是|去|返|都|今|昨|琴|會|喺|在|幫|煮|煲|買|佢|嘅|的|呀|啦|喎|"
                 r"囉|添|又|成日|日日|經常|好|唔|冇|有|仲|就|先|已經|剛|剩|仍|嚟|來|揸|打|開|見|睇|聽|問|教|畀|請|約|接|載|推|扶|"
                 r"攞|拎|搵|照顧|擔心|想|要|肯|識|知|記|每|最|成|同我|陪我|帶我|幫我|住|瞓|食|飲|行|坐|企|返工|放工|病|攰|忙|"
                 r"哭|笑|驚|怕|嘈|鬧|罵|讚|催|逼|迫|勸|提|允|答|應|肯|願|敢|話我|叫我|同我)")


# Two-character words that often follow a kinship word and are never a name.
_ZH_GIVEN_STOP = frozenset({
    "成日", "經常", "今日", "昨日", "琴日", "尋日", "前日", "聽日", "剛剛", "已經", "仍然", "最近", "一直", "好似", "覺得", "知道",
    "擔心", "開心", "唔係", "唔會", "唔想", "冇事", "有事", "返工", "放工", "返學", "放學", "出街", "今年", "舊年", "而家", "現在",
    "之前", "之後", "以前", "以後", "上次", "今次", "下次", "每次", "自己", "一齊", "一起", "鍾意", "喜歡", "真係", "都係",
    "都會", "都有", "都唔", "話我", "同我", "陪我", "帶我", "幫我", "叫我", "俾我", "送我", "想我", "要我", "見我", "問我",
    "同佢", "佢話", "睇我", "照顧", "煮飯", "買餸", "食飯", "做嘢", "返嚟", "過嚟", "嚟探", "探我", "探病", "陪診", "好好",
    "幾好", "唔好", "好叻", "好乖", "好忙", "好攰", "咁話", "咁講", "咁樣", "嗰日", "嗰陣", "呢排", "呢日", "昨晚", "今晚",
    "今朝", "琴晚", "尋晚", "早上", "晚上", "夜晚", "朝早", "下午", "上午", "中午", "仲有", "仲係", "亦都", "而且", "不過",
    "但係", "所以", "因為", "如果", "雖然", "快啲", "慢慢", "突然", "忽然", "終於", "點解", "點算", "點樣", "幾時", "邊個",
    "邊度", "乜嘢", "咩事", "冇嘢", "有嘢", "無事", "生病", "病咗", "入院", "出院", "去咗", "返咗", "嚟咗", "死咗", "走咗",
    "嫁咗", "娶咗", "結婚", "生日", "放假", "開工", "收工", "移民", "出國", "旅行", "打針", "食藥", "覆診", "手術", "化療",
    "電療", "抽血", "檢查", "住院", "過身", "過世", "離世", "去世", "退休", "讀書", "上班", "下班", "工作", "每日", "日日",
    "成個", "兩個", "幾個", "一個", "個個", "全部", "大家", "非常", "十分", "比較", "的確", "仲未", "亦係", "不停", "不斷",
    "一早", "一向", "一定", "可能", "應該", "需要", "想要", "希望", "決定", "打算", "準備", "記得", "忘記", "識得", "學識",
    "方便", "高興", "關心", "常常", "容易", "白天", "方法", "關係", "白色", "石頭", "包容", "游水", "任何", "溫柔", "夏天",
    "程度", "尤其", "莫名", "賴床", "簡單", "洪水", "湯水", "容忍", "高燒", "高血", "白血", "江湖", "文靜", "石膏", "章節",
    "田野", "金錢", "康復", "黑色", "史上", "萬一", "紀錄", "申請", "辛苦", "尚未", "祝福", "單獨", "談話", "董事", "錢包",
    "陸續", "顧問", "毛病", "賀年", "練習", "熊貓", "殷勤", "龍蝦", "凌晨", "勞累", "符合", "嚴重", "歐洲", "好返", "返嚟",
    "身體", "健康", "精神", "情況", "情緒", "心情", "胃口", "食慾", "體重", "血壓", "病情", "痛楚", "頭痛", "肚痛", "發燒",
    "咳嗽", "作嘔", "嘔吐", "疲倦", "攰到", "冇力", "無力", "不適", "唔舒", "好返", "差咗", "好咗", "轉差", "轉好",
    # nouns and verb phrases that routinely follow a kinship word (我個女電話係… / 姑娘問起…)
    "電話", "手機", "手提", "地址", "電郵", "號碼", "生日", "年紀", "歲數", "身份", "屋企", "公司", "學校", "醫院", "診所",
    "問起", "講起", "提起", "話起", "聽講", "話過", "講過", "見過", "問過", "嚟過", "去過", "返過", "做過", "食過", "睇過", "試過",
    "揸車", "開車", "搭車", "坐車", "行路", "跑步", "打機", "煲湯", "煲水", "買菜", "買藥", "拎藥", "攞藥", "配藥", "照肺",
    "探熱", "量度", "接我", "載我", "推我", "扶我", "教我", "請我", "約我", "搵我", "聽我", "打俾", "打電", "打去", "打嚟",
    "嚟到", "嚟睇", "嚟陪", "返到", "去到", "入到", "出到", "住喺", "住在", "喺度", "在家", "先生", "太太", "小姐", "女士",
    "醫生", "姑娘", "護士", "老師", "同學", "同事", "朋友", "鄰居", "細路", "小朋友", "大人", "老人", "長者", "病人",
    "家裡", "家人", "家中", "家庭", "家務", "家長", "家居", "家下", "家姐", "家嫂", "家婆", "家公", "家翁", "家鄉",
    "可以", "可能", "可唔", "可惜", "可怕", "可愛", "可靠", "可否",
})


_NAME_INITIALS = frozenset("家可")


def _build_kinship_zh_re() -> re.Pattern:
    _, cjk = split_scripts(kinship_words())
    k = _alt(cjk)
    surnames = "".join(sorted(single_surnames_zh() - _AMBIGUOUS_SURNAMES))
    compound = _alt(compound_surnames_zh())
    # 家 and 可 are function words for the leftward walk but the commonest given-name
    # initials in Hong Kong (家俊, 家豪, 可欣); the two-character stoplist covers 家裡/可以.
    stop = re.escape("".join(sorted(ch for ch in ZH_STOP - _NAME_INITIALS if has_cjk(ch))))
    return re.compile(
        rf"(?:{k})(?:叫|叫做|名叫|係|是)?\s*"
        rf"((?:阿|小)[一-鿿](?![一-鿿])"
        rf"|(?:{compound}|[{surnames}])[一-鿿]{{1,2}}{_ZH_NAME_TAIL}"
        rf"|(?![{stop}])(?![一-鿿][得咗緊埋晒])[一-鿿]{{2}}{_ZH_NAME_TAIL}"   # 瞓得/食咗 are verb + particle
        rf"|{_NAME_WORD}(?:\s{_NAME_WORD})?)"
    )


def _build_occupation_re() -> tuple[re.Pattern, re.Pattern]:
    latin, cjk = split_scripts(occupations())
    en = re.compile(
        rf"{LB}(?:I\s+am|I'm|I\s+was|I\s+work\s+as|I\s+worked\s+as|I\s+used\s+to\s+be|my\s+job\s+is|I\s+am\s+now|"
        rf"I'm\s+now|I\s+work\s+as\s+a|worked\s+as\s+a)\s+(?:an?\s+|the\s+)?(?:retired\s+|former\s+|part-time\s+|full-time\s+)?"
        rf"({_alt(latin)}){RB}",
        re.IGNORECASE,
    )
    zh = re.compile(
        rf"(?:我係|我是|我做|我份工係|我份工是|我份工|做緊|我從事|我以前係|我以前是|我退休前係|我退休前是|我做過|我係做|我是做|我之前係|我之前是)"
        rf"\s*(?:一個|一名|一位|個|名|位)?\s*(?:退休|前|以前)?\s*({_alt(cjk)})"
    )
    return en, zh


class NameRules:
    def __init__(self) -> None:
        self.title_re = _build_title_re()
        self.title_zh_re = _build_title_zh_re()
        self.surname_short_re = _build_surname_short_re()
        self.kinship_re = _build_kinship_re()
        self.kinship_zh_re = _build_kinship_zh_re()
        self.occupation_en_re, self.occupation_zh_re = _build_occupation_re()
        self._ah_stop = kinship_zh_with_ah()
        latin_kin, _ = split_scripts(kinship_words())
        self._kin_titles = frozenset(k.lower() for k in latin_kin)

    def spans(self, text: str, known: KnownMatcher | None = None) -> list[Span]:
        out: list[Span] = []

        def person(start: int, end: int, name_surface: str) -> None:
            canonical = known.resolve_person(name_surface) if known else None
            if canonical:
                out.append(Span(start, end, PERSON, PRIORITY_KNOWN, LAYER_NAMES, key=canonical))
            else:
                out.append(Span(start, end, PERSON, PRIORITY_NAMES, LAYER_NAMES))

        for m in self.title_re.finditer(text):
            title = text[m.start():m.start(1)].strip().rstrip(".").lower()
            if text[m.start()].islower() and title in self._kin_titles:
                # "my sister Mei Ling" is a relative, not Sister (the nurse): the name alone
                # is the span, so later bare mentions of "Mei Ling" are recognised.
                person(m.start(1), m.end(1), m.group(1))
            else:
                person(m.start(), m.end(), m.group(1))
        for m in self.kinship_re.finditer(text):
            person(m.start(1), m.end(1), m.group(1))
        for m in self.occupation_en_re.finditer(text):
            out.append(Span(m.start(1), m.end(1), OCCUPATION, PRIORITY_NAMES, LAYER_NAMES))

        if has_cjk(text):
            for m in self.title_zh_re.finditer(text):
                person(m.start(), m.end(), m.group(0))
            for m in self.surname_short_re.finditer(text):
                if m.group(0) in _SURNAME_SHORT_STOP:
                    continue
                person(m.start(), m.end(), m.group(0))
            for m in self.kinship_zh_re.finditer(text):
                surface = m.group(1)
                if surface in self._ah_stop or surface in _ZH_GIVEN_STOP:
                    continue
                person(m.start(1), m.end(1), surface)
            for m in self.occupation_zh_re.finditer(text):
                out.append(Span(m.start(1), m.end(1), OCCUPATION, PRIORITY_NAMES, LAYER_NAMES))
        return out


_rules: NameRules | None = None


def name_rules() -> NameRules:
    global _rules
    if _rules is None:
        _rules = NameRules()
    return _rules


__all__ = [
    "KnownIdentifiers", "KnownMatcher", "LAYER_KNOWN", "LAYER_NAMES", "LAYER_SESSION", "NameRules", "SessionMatcher",
    "age_band", "cjk_name_variants", "cjk_split", "name_rules", "parse_dob", "phone_regex", "strip_title",
]
