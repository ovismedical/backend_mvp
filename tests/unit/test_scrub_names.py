"""Layer 3b: cue-gated name rules (titles, kinship, occupations, surname+生/太)
and the CJK known-name variants, end to end through ScrubContext."""

from datetime import datetime

import pytest

from app.inference.scrub import KnownIdentifiers, ScrubContext
from app.inference.scrub.names import name_rules

NOW = datetime(2026, 9, 9, 10, 0)


def rule_spans(text):
    return sorted({(text[s.start:s.end], s.cls) for s in name_rules().spans(text)})


def scrub(known, text):
    return ScrubContext(known).scrub(text, now=NOW).text


PATIENT = KnownIdentifiers(full_name="Grace Tam", username="demo")


class TestTitles:

    def test_english_title_plus_capitalised_tokens(self):
        assert rule_spans("Dr Chan said I should rest") == [("Dr Chan", "PERSON")]
        assert rule_spans("Mrs Wong Tai Man came by") == [("Mrs Wong Tai Man", "PERSON")]
        assert ("Doctor Lee", "PERSON") in rule_spans("I saw Doctor Lee, the oncologist")
        assert rule_spans("Nurse Florence") == []       # Florence is the bot, excluded
        assert rule_spans("dr pepper") == []            # name must be Capitalised
        assert rule_spans("Dr Pepper is a drink") == [("Dr Pepper", "PERSON")]

    def test_title_span_maps_to_known_person(self):
        ctx = ScrubContext(KnownIdentifiers(full_name="Ho Long", username="u1", doctor_name="Dr. Amanda Lee"))
        assert ctx.scrub("Mr Long").text == "[PERSON_1]"
        assert ctx.scrub("Dr Lee said so; Dr. Amanda Lee agreed").text == "[PERSON_2] said so; [PERSON_2] agreed"
        assert ctx.scrub("Dr Chan disagreed").text == "[PERSON_3] disagreed"
        assert ctx.token_map.get("[PERSON_2]") == "Dr. Amanda Lee"
        assert ctx.token_map.get("[PERSON_3]") == "Dr Chan"

    def test_chinese_surname_title_rule(self):
        assert rule_spans("陳醫生話冇事") == [("陳醫生", "PERSON")]
        assert rule_spans("李志明醫生同黃姑娘講") == [("李志明醫生", "PERSON"), ("黃姑娘", "PERSON")]
        assert rule_spans("陳太太好擔心") == [("陳太", "PERSON"), ("陳太太", "PERSON")]   # longest wins later
        assert rule_spans("何伯 張叔 李姨") == [("何伯", "PERSON"), ("張叔", "PERSON"), ("李姨", "PERSON")]
        for t in ["我的醫生話", "睇醫生", "家庭醫生", "阿伯", "我太太", "你先生", "腫瘤科醫生", "文同陳醫生講"]:
            assert all(cls != "PERSON" or surface in ("陳醫生",) for surface, cls in rule_spans(t)), t

    def test_chinese_surname_short_form_with_stoplist(self):
        assert rule_spans("陳生你好") == [("陳生", "PERSON")]
        assert rule_spans("李太話") == [("李太", "PERSON")]
        for t in ["衛生", "余生", "花生", "畢生", "平生", "安生", "馬太", "方太", "醫生", "學生", "何時發生", "周末發生", "陳生日"]:
            assert rule_spans(t) == [], t


class TestKinship:

    def test_english_kinship_cue(self):
        assert rule_spans("my friend Mary drove me") == [("Mary", "PERSON")]
        assert rule_spans("my daughter Mei Ling brought me soup") == [("Mei Ling", "PERSON")]
        assert rule_spans("My daughter, Mei Ling, came") == [("Mei Ling", "PERSON")]
        assert rule_spans("my husband is called John") == [("John", "PERSON")]
        assert rule_spans("our neighbour Mrs Wong") == [("Mrs Wong", "PERSON")]

    def test_english_kinship_without_a_name(self):
        for t in ["my sister said she is fine", "my daughter Tuesday", "my son The", "my mother is tired",
                  "my daughter came on Monday"]:
            assert rule_spans(t) == [], t

    def test_chinese_kinship_cue(self):
        assert rule_spans("我老公陳大文同我去") == [("陳大文", "PERSON")]
        assert rule_spans("我個女美玲帶我去") == [("美玲", "PERSON")]
        assert rule_spans("我個女叫美玲") == [("美玲", "PERSON")]
        assert rule_spans("我個仔志明係醫生") == [("志明", "PERSON")]
        assert rule_spans("我朋友阿明話") == [("阿明", "PERSON")]
        assert rule_spans("我朋友李生") == [("李生", "PERSON")]

    def test_chinese_kinship_without_a_name(self):
        for t in ["我個女好擔心", "我個女成日話我", "我個女話我應該休息", "我老公今日返工", "我阿媽昨日嚟探我", "我老公嫁咗",
                  "我個女高興到跳起", "我老公關心我", "我個仔方便嘅話", "我阿媽常常咳", "我阿媽話要去瑪麗醫院", "阿媽"]:
            assert rule_spans(t) == [], t


class TestOccupations:

    def test_english_needs_self_description_cue(self):
        assert rule_spans("I am a teacher") == [("teacher", "OCCUPATION")]
        assert rule_spans("I work as a nurse in a clinic") == [("nurse", "OCCUPATION")]
        assert rule_spans("I'm a retired taxi driver") == [("taxi driver", "OCCUPATION")]
        assert rule_spans("the nurse was kind") == []
        assert rule_spans("I am a bit tired") == []

    def test_chinese_needs_cue(self):
        assert rule_spans("我係老師") == [("老師", "OCCUPATION")]
        assert rule_spans("我做護士") == [("護士", "OCCUPATION")]
        assert rule_spans("我以前係的士司機") == [("的士司機", "OCCUPATION")]
        assert rule_spans("老師話") == []

    def test_end_to_end_token(self):
        assert scrub(PATIENT, "I'm a teacher at a school in Sha Tin") == "I'm a [OCCUPATION_1] at a school in [PLACE_1]"
        assert scrub(PATIENT, "我係老師") == "我係[OCCUPATION_1]"


class TestKnownCjkNames:

    K = KnownIdentifiers(full_name="陳大文", username="chan123", doctor_name="李志明")

    @pytest.mark.parametrize("text,expected", [
        ("叫我大文得喇", "叫我[PERSON_1]得喇"),
        ("陳生你好", "[PERSON_1]你好"),
        ("陳太", "[PERSON_1]"),
        ("阿文", "[PERSON_1]"),
        ("陳先生", "[PERSON_1]"),
        ("陳醫生", "[PERSON_1]"),
        ("李生話要覆診", "[PERSON_2]話要覆診"),
        ("李志明", "[PERSON_2]"),
        ("志明", "[PERSON_2]"),
    ])
    def test_variants(self, text, expected):
        assert scrub(self.K, text) == expected

    @pytest.mark.parametrize("text", ["花生", "醫生", "衛生", "何時發生", "阿媽", "文", "大", "陳皮"])
    def test_traps(self, text):
        assert scrub(self.K, text) == text

    def test_one_char_given_name_never_alone(self):
        k = KnownIdentifiers(full_name="陳文", username="u1")
        assert scrub(k, "文件") == "文件"
        assert scrub(k, "陳文") == "[PERSON_1]"
        assert scrub(k, "阿文") == "[PERSON_1]"

    def test_compound_surname(self):
        k = KnownIdentifiers(full_name="歐陽志明", username="u1")
        assert scrub(k, "歐陽生 歐陽太 志明") == "[PERSON_1] [PERSON_1] [PERSON_1]"


class TestCommonWordNames:

    @pytest.mark.parametrize("name,text,expected", [
        ("Wong Ka Long", "long-term pain", "long-term pain"),
        ("Wong Ka Long", "Long came to visit", "[PERSON_1] came to visit"),
        ("Wong Ka Long", "Long-term pain", "Long-term pain"),
        ("Wong Ka Long", "Ka Long is here", "[PERSON_1] is here"),
        ("Ho Long", "how long have you felt this", "how long have you felt this"),
        ("Ho Long", "Mr Long", "[PERSON_1]"),
        ("Ho Long", "Ho Long", "[PERSON_1]"),
        ("Ka Man", "a man in the clinic", "a man in the clinic"),
        ("Ka Man", "Ka-Man is fine", "[PERSON_1] is fine"),
        ("May Chan", "I may feel better", "I may feel better"),
        ("May Chan", "May Chan", "[PERSON_1]"),
        ("May Chan", "May I ask something", "May I ask something"),
        ("Grace Tam", "by the grace of god", "by the grace of god"),
        ("Test Patient", "I had a blood test", "I had a blood test"),
        ("Test Patient", "the patient leaflet", "the patient leaflet"),
        ("Test Patient", "Test Patient", "[PERSON_1]"),
    ])
    def test_stoplisted_parts(self, name, text, expected):
        assert scrub(KnownIdentifiers(full_name=name, username="u1"), text) == expected
