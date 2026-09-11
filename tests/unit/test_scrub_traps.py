"""MUST-pass over-redaction traps and recall traps from the spec (line 54) and
amendments A3, A5, A6, A7, A11 — end to end through ScrubContext."""

from datetime import date, datetime

import pytest

from app.inference.scrub import KnownIdentifiers, ScrubContext, reidentify

NOW = datetime(2026, 9, 9, 10, 0)
GRACE = KnownIdentifiers(full_name="Grace Tam", username="demo", dob=date(1966, 8, 22))
FIXTURE = KnownIdentifiers(full_name="Test Patient", username="testpatient", doctor_name="Dr. Test")


def scrub(text, known=GRACE, now=NOW):
    return ScrubContext(known).scrub(text, now=now).text


class TestUnchanged:
    """Clinical text, drug names, food and common words must survive verbatim."""

    @pytest.mark.parametrize("text", [
        "CA-125 was 35", "temperature 38.2", "took 2 tablets", "pain 7/10", "3 days", "fatigue 4 out of 5",
        "59 kg", "59 days", "5 g of salt", "BP 120/80", "98/60", "血壓 98/60", "my pressure was 120/80",
        "I take Tamoxifen, Letrozole, Oxycodone and Panadol", "必理痛", "陳皮", "李子", "王子", "黃芪", "甘草", "白蘿蔔",
        "掛咗8號風球", "搭5號巴士", "3號線", "yesterday", "3 days ago", "last week", "昨日", "前日", "上星期", "三日前", "琴日",
        "一星期三次", "I went to the hospital for a demo of the machine and I take tamoxifen to demonstrate",
        "花生", "醫生", "衛生", "何時發生", "阿媽", "我知道呼吸道有問題", "食道癌", "by the grace of god",
    ])
    def test_unchanged(self, text):
        assert scrub(text) == text

    @pytest.mark.parametrize("text", [
        "I had a blood test and the patient leaflet says pain 7/10",
        "I had a blood test", "the patient leaflet",
    ])
    def test_fixture_names_do_not_mangle_clinical_text(self, text):
        assert scrub(text, FIXTURE) == text

    @pytest.mark.parametrize("name,text", [
        ("Wong Ka Long", "long-term pain"),
        ("Ho Long", "how long have you felt this"),
        ("Ka Man", "a man in the clinic"),
        ("May Chan", "I may feel better"),
        ("Grace Tam", "by the grace of god"),
    ])
    def test_common_word_names(self, name, text):
        assert scrub(text, KnownIdentifiers(full_name=name, username="u1")) == text


class TestScrubbed:

    def test_queen_mary_hospital_is_one_facility_not_a_person(self):
        assert scrub("Queen Mary Hospital") == "[FACILITY_1]"
        assert scrub("my friend Mary drove me to Queen Mary Hospital") == "my friend [PERSON_2] drove me to [FACILITY_1]"
        assert scrub("Sha Tin Hospital") == "[FACILITY_1]"          # "Sha Tin" inside a hospital name

    def test_dr_pepper_fine_either_way(self):
        assert scrub("Dr Pepper is a drink") in ("Dr Pepper is a drink", "[PERSON_2] is a drink")

    def test_age_vs_weight(self):
        assert scrub("I'm 59") == "I'm [AGE · 50s]"
        assert scrub("I'm 59 but 59 kg") == "I'm [AGE · 50s] but 59 kg"

    @pytest.mark.parametrize("text,expected", [
        ("call 9123 4567 day or night", "call [PHONE_1] day or night"),
        ("WhatsApp 6123 4567 got it?", "WhatsApp [PHONE_1] got it?"),
        ("my HKID is A123456(7) give it to the nurse", "my HKID is [ID_1] give it to the nurse"),
    ])
    def test_a6_identifiers_followed_by_unit_like_words(self, text, expected):
        assert scrub(text) == expected

    @pytest.mark.parametrize("text,expected", [
        ("my number is 9123 4567", "my number is [PHONE_1]"),
        ("you can reach me on 9123-4567", "you can reach me on [PHONE_1]"),
        ("我手機 9123 4567", "我手機 [PHONE_1]"),
    ])
    def test_a5_known_phone(self, text, expected):
        k = KnownIdentifiers(full_name="Grace Tam", username="demo", phone="91234567")
        assert scrub(text, k) == expected

    @pytest.mark.parametrize("text", [
        "22/08/1966", "08/22/1966", "1966-08-22", "August 22, 1966", "22.08.1966", "1966年8月22日", "8月22日",
    ])
    def test_a11_dob_formats(self, text):
        assert scrub(text) == "[DOB]"

    def test_a2_dates(self):
        assert scrub("I was born in 1966") == "I was born in [AGE · 60s]"
        assert scrub("my surgery was on 5 March 2024") == "my surgery was on [DATE_1 · ~2 years ago]"
        assert scrub("started on 5 March") == "started on [DATE_1 · 188 days ago]"

    def test_a7_future_weekdays_and_dd_mm(self):
        assert scrub("I'm seeing the oncologist this Friday") == "I'm seeing the oncologist [DATE_1 · next Friday]"
        assert scrub("覆診係下星期二") == "覆診係[DATE_1 · next Tuesday]"
        ctx = ScrubContext(GRACE)
        res = ctx.scrub("5/3", now=NOW)
        assert res.text == "[DATE_1 · 188 days ago]"                 # 5 March, not 3 May

    @pytest.mark.parametrize("name,text,expected", [
        ("Wong Ka Long", "Long came to visit", "[PERSON_1] came to visit"),
        ("Ho Long", "Mr Long", "[PERSON_1]"),
        ("Test Patient", "Test Patient", "[PERSON_1]"),
    ])
    def test_a11_common_word_names_still_scrubbed_when_clearly_names(self, name, text, expected):
        assert scrub(text, KnownIdentifiers(full_name=name, username="u1")) == expected

    def test_a3_cjk(self):
        k = KnownIdentifiers(full_name="陳大文", username="chan123", doctor_name="李志明")
        assert scrub("叫我大文得喇", k) == "叫我[PERSON_1]得喇"
        assert scrub("陳生你好", k) == "[PERSON_1]你好"
        assert scrub("陳太", k) == "[PERSON_1]"
        assert scrub("阿文", k) == "[PERSON_1]"
        assert scrub("李生話要覆診", k) == "[PERSON_2]話要覆診"

    def test_plan_sentences(self):
        ctx = ScrubContext(GRACE)
        assert ctx.scrub("my daughter Mei Ling took me to Queen Mary on Tuesday", now=NOW).text == \
            "my daughter [PERSON_2] took me to [FACILITY_1] on [DATE_1 · 1 day ago]"
        assert ctx.scrub("I live in Tai Koo Shing", now=NOW).text == "I live in [PLACE_1]"
        assert ctx.scrub("Dr Chan at the Sanatorium said I should rest", now=NOW).text == \
            "[PERSON_3] at [FACILITY_2] said I should rest"
        assert ctx.scrub("I'm 59 and I teach at a school in Sha Tin", now=NOW).text == \
            "I'm [AGE · 50s] and I teach at a school in [PLACE_2]"
        assert ctx.scrub("I'm a teacher at Sha Tin", now=NOW).text == "I'm a [OCCUPATION_1] at [PLACE_2]"


class TestA11MultiTurn:

    def test_mary_multi_turn(self):
        ctx = ScrubContext(FIXTURE)
        assert ctx.scrub("my friend Mary drove me to Queen Mary Hospital", now=NOW).text == \
            "my friend [PERSON_2] drove me to [FACILITY_1]"
        echoed = reidentify("That was kind of [PERSON_2].", ctx.token_map)
        out = ctx.scrub_messages([{"role": "assistant", "content": echoed}, {"role": "user", "content": "Mary is visiting tomorrow"}], now=NOW)
        assert [m["content"] for m in out] == ["That was kind of [PERSON_2].", "[PERSON_2] is visiting tomorrow"]
        assert ctx.scrub("back to Queen Mary Hospital", now=NOW).text == "back to [FACILITY_1]"
