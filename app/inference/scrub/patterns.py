"""Layer 2: deterministic patterns (IDs, phones, emails, URLs, handles,
addresses) and the facility/org/street suffix rules.

No clinical-number guard lives here (amendment A6): once an ID/PHONE/EMAIL/
HANDLE/URL pattern and its cue conditions are met the span is always scrubbed.
The guard belongs to the AGE/DATE generaliser only.
"""

from __future__ import annotations

import re

from .gazetteers import org_suffixes, split_scripts
from .tokens import (
    ADDRESS, EMAIL, FACILITY, HANDLE, ID, LB, ORG, PHONE, PLACE, PRIORITY_PATTERNS, RB, URL, Span, has_cjk,
)

LAYER = "patterns"
CJK = "一-鿿㐀-䶿"
CAP = r"[A-Z][A-Za-z'&.\-]*"
CONN = r"(?:of|&|the|de|for)"   # not "and": it would join two neighbouring names

# First-word stoplist for the Capitalised-phrase suffix rules ("The Hospital",
# "Private Hospital", "Yesterday Queen Mary Hospital" must not swallow the
# function word).
_EN_FIRST_STOP = (
    "The|A|An|My|Our|Your|His|Her|Their|Its|This|That|These|Those|At|In|To|From|For|With|On|Of|And|But|Or|So|"
    "If|When|Then|Also|Private|Public|Government|General|Local|Nearby|Nearest|Another|Same|Other|Any|Some|Every|"
    "Each|Which|Whose|Is|Was|Has|Have|Had|Day|Night|Yesterday|Today|Tomorrow|Last|Next|Went|Go|Going|Visit|"
    "Visited|Near|Big|Small|New|Old|Good|Bad|I|We|They|He|She|It|You|No|Not|Yes|Hospital|Clinic|Health|"
    "Support|Cancer|Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Please|Thanks|Thank|Okay|OK|"
    "Supreme|High|District|Magistrates|Food|Tennis|Squash|Basketball|Badminton|Botanical|Zoological|Care|Clinical|"
    "Critical|Treatment|Recovery|Patient|Disease|Family|Real|Emergency|Accident"
)
_FIRST = rf"(?!(?:{_EN_FIRST_STOP}){RB})"


def _cue(latin: str, cjk: str = "") -> re.Pattern:
    parts = []
    if latin:
        parts.append(rf"{LB}(?:{latin}){RB}")
    if cjk:
        parts.append(f"(?:{cjk})")
    return re.compile("|".join(parts), re.IGNORECASE)


def _near(text: str, start: int, end: int, cue: re.Pattern, before: int = 20, after: int = 20) -> bool:
    return bool(cue.search(text[max(0, start - before):start]) or cue.search(text[end:end + after]))


# --- direct identifiers ------------------------------------------------------------
HKID_RE = re.compile(rf"{LB}[A-Z]{{1,2}}\d{{6}}\(?[0-9A]\)?{RB}")

# Any HK-shaped phone run; used by the known-identifier layer and leak_check too.
PHONE_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\(?\+?852\)?[\s\-()]*)?[2-9]\d{3}[\s\-.]?\d{4}(?!\d)")
PHONE_CUE = _cue(
    r"phone|tel|mobile|cell|number|contact|reach|call|whatsapp|hotline",
    r"電話|手機|手提|聯絡|號碼|致電|打",
)

EMAIL_RE = re.compile(rf"{LB}[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{{2,}}{RB}")

# A URL never contains CJK or a sentence-final stop ("我喺bit.ly/x睇到。", "see www.x.com.").
_URL_TAIL = r"[A-Za-z0-9\-._~:/?#@!$&*+;=%]*[A-Za-z0-9/_\-#=~]"
URL_RE = re.compile(
    rf"(?:https?://|www\.){_URL_TAIL}"
    rf"|{LB}[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.(?:com|net|org|hk|io|co|edu|gov|info|me|app|tv|cn|uk|us)(?:\.hk)?(?:/[^\s]*)?{RB}",
    re.IGNORECASE,
)

# Handles are ASCII (\w would run into CJK text: "Telegram: chan_dm可以搵到我") and never end
# with the sentence's full stop ("my Instagram account is grace.tam.").
_HANDLE_BODY = r"@?(?=[A-Za-z0-9_.]*[A-Za-z_])[A-Za-z0-9_.]{2,}[A-Za-z0-9_]"
HANDLE_AT_RE = re.compile(r"(?<![A-Za-z0-9._])@(?=[A-Za-z0-9_.]*[A-Za-z_])[A-Za-z0-9_.]{2,}[A-Za-z0-9_]")
HANDLE_PLATFORM_RE = re.compile(
    r"(?:whatsapp|wechat|instagram|telegram|facebook|微信|電報|\bIG\b|\bFB\b)"
    r"(?:\s*(?:id|account|handle|name|username|帳號|賬號|號|名)\s*(?:[:：]|is|係|是)?|\s*[:：])\s*"
    rf"({_HANDLE_BODY})",
    re.IGNORECASE,
)

PASSPORT_RE = re.compile(rf"{LB}(?:[A-Z]\d{{8}}|H\d{{8,10}}){RB}")
PASSPORT_CUE = _cue(r"passport|home return|HRP|travel document", r"護照|回鄉證|回鄉卡|通行證")

MRN_RE = re.compile(rf"{LB}[A-Z]{{2,4}}-?\d{{5,10}}{RB}")
MRN_DIGITS_RE = re.compile(
    r"(?:policy|medical record|reference|ref|MRN|case)\s*(?:no\.?|number|#|:|：)?\s*(\d{5,10})(?!\d)"
    r"|(?:保單|病歷|檔案|編號|個案)\s*(?:號碼|號|編號)?\s*[:：#]?\s*(\d{5,10})(?!\d)",
    re.IGNORECASE,
)
MRN_CUE = _cue(r"policy|medical record|record|reference|ref|no\.|MRN|case no|case number", r"保單|病歷|編號|檔案|個案")
# "My medical record number is HK-1234567" puts 24 chars between cue and value.
MRN_WINDOW = 30

CARD_RE = re.compile(r"(?<![\d])(?:\d[ \-]?){12,18}\d(?![\d])")

PLATE_RE = re.compile(rf"{LB}[A-Z]{{1,2}}\s?\d{{1,4}}{RB}")
PLATE_CUE = _cue(r"plate|car|registration|licence plate|license plate", r"車牌|車|架車")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


# --- addresses & streets (Latin) ------------------------------------------------------
_STREET = r"(?:Road|Street|Avenue|Lane|Drive|Path|Terrace|Boulevard|Crescent|Square)"
_STREET_TAIL = r"(?:\s+(?:Central|East|West|North|South))?"
_BUILDING = (r"(?:Estate|Court|Garden|Gardens|Mansion|Mansions|Building|House|Terrace|Villa|Villas|Tower|Towers|"
             r"Centre|Center|Plaza|Village|Shing|Chuen|Tsuen)")
_FLOOR = r"(?:\d{1,3}/F|\d{1,3}(?:st|nd|rd|th)\s+[Ff]loor|[Ff]loor\s+\d{1,3}|G/F|Ground\s+[Ff]loor)"
_BLOCK = r"(?:Block|Tower|Blk\.?|Phase|Bldg\.?)\s*[A-Za-z0-9]{1,4}"

ADDRESS_EN_RE = re.compile(
    rf"""{LB}
    (?:Flat|Unit|Room|Rm\.?|Apt\.?|Apartment|Suite)\s*[A-Za-z0-9]{{1,5}}
    (?:[,\s]+{_FLOOR})?
    (?:[,\s]+{_BLOCK})?
    (?:[,\s]+(?:No\.?\s*)?\d{{1,4}}[A-Za-z]?(?:-\d{{1,4}})?\s+(?:{CAP}\s+){{1,4}}{_STREET}{_STREET_TAIL})?
    (?:,\s*(?:(?:{CAP}\s+){{0,4}}{CAP}\s*{_BUILDING}|(?:{CAP}\s+){{1,4}}{CAP})(?![A-Za-z]))?
    """,
    re.X,
)
ADDRESS_FLOOR_RE = re.compile(
    rf"{LB}(?:{_FLOOR}|{_BLOCK})[,\s]+(?:{_BLOCK}[,\s]+)?{_FIRST}(?:{CAP}\s+){{1,4}}{_BUILDING}{RB}"
)
ADDRESS_STREET_RE = re.compile(
    rf"{LB}(?:No\.?\s*)?\d{{1,4}}[A-Za-z]?(?:-\d{{1,4}})?\s+{_FIRST}(?:{CAP}\s+){{1,4}}{_STREET}{_STREET_TAIL}{RB}"
)
STREET_EN_RE = re.compile(rf"{LB}{_FIRST}(?:{CAP}\s+){{1,3}}{_STREET}{_STREET_TAIL}{RB}")
ESTATE_EN_RE = re.compile(rf"{LB}{_FIRST}(?:{CAP}\s+){{1,4}}(?:Estate|Court|Gardens?|Mansions?|Villas?){RB}")

# --- facility / org suffix rules (Latin) ----------------------------------------------
_FAC_SUFFIX = (r"(?:Hospital|Clinic|Medical Centre|Medical Center|Sanatorium|Health Centre|Health Center|Polyclinic|"
               r"Cancer Centre|Cancer Center|Oncology Centre|Oncology Center|Hospice|Nursing Home|Care Home)")
FACILITY_EN_RE = re.compile(rf"{LB}{_FIRST}(?:{CAP}\s+(?:{CONN}\s+)?){{1,5}}{_FAC_SUFFIX}{RB}")


def _org_suffix_alt() -> str:
    latin, _ = split_scripts(org_suffixes())
    return "|".join(re.escape(s) for s in sorted(latin, key=lambda s: (-len(s), s)))


ORG_EN_RE = re.compile(rf"{LB}{_FIRST}(?:{CAP}\s+(?:{CONN}\s+)?){{1,5}}(?:{_org_suffix_alt()}){RB}")

# --- CJK suffix rules ----------------------------------------------------------------
# Characters that end the leftward extension of a CJK name (function words, verbs,
# pronouns, classifiers, punctuation).
ZH_STOP = set(
    "去返入到喺在出嗰呢的嘅我你妳他她佢哋們有冇係是上落住同和與或但就都又也先再才已未曾將會要想需應可能得過了咗啦喇吖呀啊嘛吧嗎麼"
    "個間所家次仲好最更很咁點樣邊度幾某該此這那每成條隻張部架位名讀做打搵睇坐搭行嚟來從由經話講說叫俾畀比埋及即然因為所以如果不而且緊"
    "，。、！？：；「」『』（）()[]【】《》〈〉〔〕 \n\t\r,.!?;:'\"-"
)
_ZH_GENERIC = frozenset({
    "醫院", "診所", "醫療中心", "療養院", "健康中心", "健康院", "門診", "急症室", "老人院", "安老院", "護老院", "復康院", "護養院",
    "私家醫院", "公立醫院", "政府醫院", "公營醫院", "私立醫院", "大醫院", "細醫院", "間醫院", "私家診所", "政府診所",
    "中醫診所", "西醫診所", "牙科診所", "普通科診所", "專科診所", "門診診所", "私家", "公立",
    "學校", "中學", "小學", "大學", "書院", "學院", "幼稚園", "銀行", "公司", "有限公司", "集團", "保險", "教會", "堂會",
    "基金會", "協會", "學會", "商會", "工會", "證券", "物業", "工程", "貿易", "物流", "保險公司", "物流公司", "貿易公司",
    "證券公司", "物業公司", "工程公司", "建築公司", "地產公司", "上市公司", "大公司", "細公司", "私人公司", "間公司",
    "呢間", "嗰間", "邊間", "國際學校", "小學校", "中學校",
    "知道", "味道", "道理", "呼吸道", "消化道", "食道", "尿道", "腸道", "陰道", "泌尿道", "通道", "管道", "軌道", "報道",
    "頻道", "地道", "難道", "一路", "走路", "路程", "思路", "網路", "線路", "電路", "出路", "迷路", "馬路", "過路", "大路",
    "小路", "公里", "鄰里", "直徑", "途徑", "捷徑", "路徑", "行街", "出街", "逛街", "街市", "街坊", "上街", "落街", "條街",
    "大街", "小巷", "後巷", "橫巷", "條路", "條巷", "山路", "水路", "血路", "腦血管", "沿路", "順路", "半路", "呼吸",
})
_ZH_BUILDING = r"(?:新邨|邨|苑|花園|大廈|閣|台|臺|軒|居|灣畔|城|中心|廣場|大樓|樓|別墅|山莊|豪庭|半島)"


# 會 is a function word (will/can) and stops the leftward walk — except inside the
# organisation compounds that name Hong Kong institutions (賽馬會診所, 浸會大學, 街坊會).
_PASSABLE_WUI_RE = re.compile(r"(?:賽馬|基金|同鄉|婦女|街坊|互助|青年|體育|校友|家長|浸|協|商|工|學|公|總|聯|教|農|漁)會$")


def _extend_left(text: str, suffix_start: int, max_len: int) -> int:
    """Walk left from ``suffix_start`` over CJK/Latin/digit chars, stopping at a
    function word, punctuation or the length cap. Returns the name start."""
    i = suffix_start
    while i > 0 and suffix_start - i < max_len:
        ch = text[i - 1]
        if ch in ZH_STOP and not (ch == "會" and _PASSABLE_WUI_RE.search(text[:i])):
            break
        if not (has_cjk(ch) or ch.isalnum()):
            break
        i -= 1
    return i


_DIRECTION = "東南西北"


def _cjk_suffix_spans(text: str, suffix_re: re.Pattern, cls: str, max_len: int, *, gate=None, min_prefix: int = 1,
                      direction: bool = False) -> list[Span]:
    """``min_prefix`` chars must precede the suffix (填保險 is a verb phrase, not an
    ORG); ``direction`` extends a street over a trailing 東/南/西/北 when that
    character ends the name (太子道西235號, not 知道西環)."""
    out = []
    for m in suffix_re.finditer(text):
        start = _extend_left(text, m.start(), max_len)
        if start == m.start() or m.start() - start < min_prefix:
            continue
        name = text[start:m.end()]
        if name in _ZH_GENERIC or text[start:m.start()] in _ZH_GENERIC:
            continue
        end = m.end()
        if direction and end < len(text) and text[end] in _DIRECTION and (end + 1 == len(text) or not has_cjk(text[end + 1])):
            end += 1
        if gate is not None and not gate(text, start, end):
            continue
        out.append(Span(start, end, cls, PRIORITY_PATTERNS, LAYER))
    return out


FACILITY_ZH_SUFFIX_RE = re.compile(
    r"醫療中心|健康中心|寧養中心|腫瘤中心|癌症中心|復康院|護養院|療養院|健康院|老人院|安老院|護老院|醫院|診所"
)


def _org_zh_suffix_re() -> re.Pattern:
    _, cjk = split_scripts(org_suffixes())
    return re.compile("|".join(re.escape(s) for s in sorted(cjk, key=lambda s: (-len(s), s))))


ORG_ZH_SUFFIX_RE = _org_zh_suffix_re()
STREET_ZH_SUFFIX_RE = re.compile(r"[道街路里徑巷]")
_ZH_STREET_CUE_BEFORE = set("住喺在去到返搬過經近由從")
_ZH_STREET_CUE_AFTER = ("附近", "嗰邊", "那邊", "一帶", "轉角", "口", "尾", "中段", "交界", "嗰度", "度")


def _street_gate(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 2):start]
    after = text[end:end + 2]
    if any(ch in _ZH_STREET_CUE_BEFORE for ch in before):
        return True
    return any(after.startswith(p) for p in _ZH_STREET_CUE_AFTER)


STREET_NUMBER_ZH_RE = re.compile(rf"[道街路里徑巷][東南西北]?\s*\d{{1,4}}(?:-\d{{1,4}})?號")
UNIT_ZH_RE = re.compile(
    rf"(?:第?(?:[A-Z]{{1,2}}|\d{{1,3}}|[{CJK}])座\s*\d{{1,3}}樓(?:\s*[A-Z0-9]{{1,5}}室)?"
    rf"|\d{{1,3}}樓\s*[A-Z0-9]{{1,5}}室"
    rf"|(?<![\d])\d{{3,5}}[A-Z]?室)"
)
_BUILDING_BEFORE_RE = re.compile(rf"([A-Za-z0-9{CJK}]{{1,12}}{_ZH_BUILDING})\s*$")


def _address_zh_spans(text: str) -> list[Span]:
    out = []
    for m in UNIT_ZH_RE.finditer(text):
        start = m.start()
        b = _BUILDING_BEFORE_RE.search(text[:start])
        if b:
            suffix_pos = b.start(1) + _suffix_offset(b.group(1))
            name_start = _extend_left(text, suffix_pos, 12)
            if name_start < suffix_pos:
                start = name_start
        out.append(Span(start, m.end(), ADDRESS, PRIORITY_PATTERNS, LAYER))
    for m in STREET_NUMBER_ZH_RE.finditer(text):
        start = _extend_left(text, m.start(), 8)
        if start == m.start():
            continue
        if text[start:m.start() + 1] in _ZH_GENERIC:
            continue
        out.append(Span(start, m.end(), ADDRESS, PRIORITY_PATTERNS, LAYER))
    return out


_ZH_BUILDING_RE = re.compile(_ZH_BUILDING + "$")


def _suffix_offset(building: str) -> int:
    """Index within ``building`` where its suffix (邨/苑/花園…) begins."""
    m = _ZH_BUILDING_RE.search(building)
    return m.start() if m else len(building)


# --- the layer ---------------------------------------------------------------------------
def pattern_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    add = spans.append

    for m in HKID_RE.finditer(text):
        add(Span(m.start(), m.end(), ID, PRIORITY_PATTERNS, LAYER))
    for m in PHONE_CANDIDATE_RE.finditer(text):
        has_prefix = "852" in m.group(0)
        if has_prefix or _near(text, m.start(), m.end(), PHONE_CUE):
            add(Span(m.start(), m.end(), PHONE, PRIORITY_PATTERNS, LAYER))
    for m in EMAIL_RE.finditer(text):
        add(Span(m.start(), m.end(), EMAIL, PRIORITY_PATTERNS, LAYER))
    for m in URL_RE.finditer(text):
        add(Span(m.start(), m.end(), URL, PRIORITY_PATTERNS - 1, LAYER))
    for m in HANDLE_AT_RE.finditer(text):
        add(Span(m.start(), m.end(), HANDLE, PRIORITY_PATTERNS - 2, LAYER))
    for m in HANDLE_PLATFORM_RE.finditer(text):
        add(Span(m.start(1), m.end(1), HANDLE, PRIORITY_PATTERNS - 2, LAYER))
    for m in PASSPORT_RE.finditer(text):
        if _near(text, m.start(), m.end(), PASSPORT_CUE):
            add(Span(m.start(), m.end(), ID, PRIORITY_PATTERNS, LAYER))
    for m in MRN_RE.finditer(text):
        if _near(text, m.start(), m.end(), MRN_CUE, MRN_WINDOW, MRN_WINDOW):
            add(Span(m.start(), m.end(), ID, PRIORITY_PATTERNS, LAYER))
    for m in MRN_DIGITS_RE.finditer(text):
        g = 1 if m.group(1) else 2
        add(Span(m.start(g), m.end(g), ID, PRIORITY_PATTERNS - 1, LAYER))
    for m in CARD_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            add(Span(m.start(), m.end(), ID, PRIORITY_PATTERNS, LAYER))
    for m in PLATE_RE.finditer(text):
        if _near(text, m.start(), m.end(), PLATE_CUE):
            add(Span(m.start(), m.end(), ID, PRIORITY_PATTERNS - 1, LAYER))

    for m in ADDRESS_EN_RE.finditer(text):
        add(Span(m.start(), m.end(), ADDRESS, PRIORITY_PATTERNS, LAYER))
    for m in ADDRESS_FLOOR_RE.finditer(text):
        add(Span(m.start(), m.end(), ADDRESS, PRIORITY_PATTERNS, LAYER))
    for m in ADDRESS_STREET_RE.finditer(text):
        add(Span(m.start(), m.end(), ADDRESS, PRIORITY_PATTERNS, LAYER))
    for m in STREET_EN_RE.finditer(text):
        add(Span(m.start(), m.end(), PLACE, PRIORITY_PATTERNS - 3, LAYER))
    for m in ESTATE_EN_RE.finditer(text):
        add(Span(m.start(), m.end(), PLACE, PRIORITY_PATTERNS - 3, LAYER))
    for m in FACILITY_EN_RE.finditer(text):
        add(Span(m.start(), m.end(), FACILITY, PRIORITY_PATTERNS - 3, LAYER))
    for m in ORG_EN_RE.finditer(text):
        add(Span(m.start(), m.end(), ORG, PRIORITY_PATTERNS - 4, LAYER))

    if has_cjk(text):
        spans.extend(_cjk_suffix_spans(text, FACILITY_ZH_SUFFIX_RE, FACILITY, 10))
        spans.extend(_cjk_suffix_spans(text, ORG_ZH_SUFFIX_RE, ORG, 10, min_prefix=2))
        spans.extend(_cjk_suffix_spans(text, STREET_ZH_SUFFIX_RE, PLACE, 6, gate=_street_gate, direction=True))
        spans.extend(_address_zh_spans(text))
    return spans


__all__ = [
    "ADDRESS_EN_RE", "CARD_RE", "EMAIL_RE", "FACILITY_EN_RE", "HANDLE_AT_RE", "HANDLE_PLATFORM_RE", "HKID_RE",
    "LAYER", "MRN_RE", "ORG_EN_RE", "PASSPORT_RE", "PHONE_CANDIDATE_RE", "PHONE_CUE", "PLATE_RE", "STREET_EN_RE",
    "URL_RE", "ZH_STOP", "pattern_spans",
]
