"""Regressions surfaced by the PHI recall eval (tests/eval/phi_recall) and fixed
in the scrubber: sentence-final dates, an over-eager clinical-number guard, the
MRN cue window, URL/handle bodies running into CJK text, same-turn repeats of a
cue-found name, and several Cantonese kinship/suffix gaps."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.inference.scrub import KnownIdentifiers, ScrubContext

NOW = datetime(2026, 9, 9, 10, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))   # a Wednesday
GRACE = KnownIdentifiers(full_name="Grace Tam", username="demo", dob=date(1966, 8, 22))
CHAN = KnownIdentifiers(full_name="陳大文", username="chan_dm", doctor_name="李志明")


def scrub(text, known=GRACE):
    return ScrubContext(known).scrub(text, now=NOW)


def classes(text, known=GRACE):
    res = scrub(text, known)
    return [(s.cls, text[s.start:s.end]) for s in res.spans]


class TestSentenceFinalDates:
    """A full stop after a numeric date is the sentence's, not the date's."""

    @pytest.mark.parametrize("text,expected", [
        ("I was born on 22/08/1966.", "I was born on [DOB]."),
        ("My birthday is 08/22/1966.", "My birthday is [DOB]."),
        ("For the record, DOB 22.08.1966.", "For the record, DOB [DOB]."),
        ("DOB 22/8/66.", "DOB [DOB]."),
        ("No appetite since 5/8.", "No appetite since [DATE_1 · 35 days ago]."),
        ("Worrying about the scan on 25/09/2026.", "Worrying about the scan on [DATE_1 · in 16 days]."),
    ])
    def test_trailing_full_stop_is_kept(self, text, expected):
        assert scrub(text).text == expected

    def test_month_name_dates_keep_the_sentence_stop(self):
        assert scrub("My next appointment is on 15 September.").text == "My next appointment is on [DATE_1 · in 6 days]."
        assert scrub("My birthday is 22 August.").text == "My birthday is [DOB]."

    def test_abbreviated_month_dot_before_a_year_is_part_of_the_date(self):
        assert scrub("born 22 Aug. 1966, in Kowloon").text == "born [DOB], in Kowloon"

    def test_decimals_are_still_not_dates(self):
        assert scrub("the ratio was 1.5/2.0 and 3.5/4.0").text == "the ratio was 1.5/2.0 and 3.5/4.0"


class TestClinicalGuardScope:
    """The A6 guard protects readings, not every number near a clinical word."""

    @pytest.mark.parametrize("text,expected", [
        ("I asked Dr Chan about the dose. I'm 78 years old, so recovery is slower.",
         "I asked [PERSON_2] about the dose. I'm [AGE · 70s], so recovery is slower."),
        ("The pain started on 5 August and got worse on 30/8.",
         "The pain started on [DATE_1 · 35 days ago] and got worse on [DATE_2 · 10 days ago]."),
        ("The pain started on 15th July.", "The pain started on [DATE_1 · 56 days ago]."),
        ("你嗰個表可能係4分。我五十九歲，有啲痛好正常。", "你嗰個表可能係4分。我[AGE · 50s]，有啲痛好正常。"),
        ("大概4分。我71歲，復原慢啲。", "大概4分。我[AGE · 70s]，復原慢啲。"),
        ("機器顯示血氧95%。30號痛得犀利咗。", "機器顯示血氧95%。[DATE_1 · 10 days ago]痛得犀利咗。"),
    ])
    def test_previous_sentence_and_month_names_do_not_guard(self, text, expected):
        assert scrub(text).text == expected

    @pytest.mark.parametrize("text", [
        "pain 7/10", "BP 120/80", "my pressure was 98/60", "I'm 59 kg", "sugar 6/9 this morning", "血壓 12/8 好低",
        "temperature 38.2, pulse 88", "my pain score was 5/3 that day",
    ])
    def test_readings_in_the_same_sentence_are_still_guarded(self, text):
        assert scrub(text).text == text


class TestMedicalRecordNumbers:

    @pytest.mark.parametrize("text,expected", [
        ("My medical record number is HK-1234567.", "My medical record number is [ID_1]."),
        ("My medical record number is KWH-6119720.", "My medical record number is [ID_1]."),   # KWH is also a hospital abbreviation
        ("病歷編號 QMH-0012345 記低先", "病歷編號 [ID_1] 記低先"),
    ])
    def test_record_number_with_a_distant_cue(self, text, expected):
        assert scrub(text).text == expected

    def test_letters_and_digits_without_a_cue_are_left_alone(self):
        assert scrub("the model is XR-2000 and the ward is 5B").text == "the model is XR-2000 and the ward is 5B"


class TestUrlAndHandleBodies:

    @pytest.mark.parametrize("text,expected", [
        ("我喺www.hkcf.org兩個禮拜咁耐。", "我喺[URL_1]兩個禮拜咁耐。"),
        ("我喺https://bit.ly/3xYz9Qk夜晚5 mg嗎啡。", "我喺[URL_1]夜晚5 mg嗎啡。"),
        ("I read about it on https://bit.ly/3xYz9Qk.", "I read about it on [URL_1]."),
        ("See www.cancerfund.org.hk/support, it helps.", "See [URL_1], it helps."),
    ])
    def test_url_stops_at_cjk_and_sentence_punctuation(self, text, expected):
        assert scrub(text).text == expected

    @pytest.mark.parametrize("text,expected", [
        ("Telegram: chan_dm可以搵到我。", "Telegram: [HANDLE_1]可以搵到我。"),
        ("My Instagram account is grace.tam.", "My Instagram account is [HANDLE_1]."),
        ("I post as @wongsm88.", "I post as [HANDLE_1]."),
        ("微信號：wong_sm。", "微信號：[HANDLE_1]。"),
    ])
    def test_handle_is_ascii_and_keeps_the_full_stop(self, text, expected):
        assert scrub(text).text == expected


class TestSameTurnRepeats:
    """A name found by a cue rule is scrubbed wherever else it appears in the same text."""

    def test_relative_repeated_without_cue(self):
        assert scrub("My grandson Dennis came round. Dennis thinks I look pale.").text == \
            "My grandson [PERSON_2] came round. [PERSON_2] thinks I look pale."

    def test_two_word_name_repeated(self):
        assert scrub("I rang my daughter Mei Ling. Mei-Ling is coming tomorrow.").text == \
            "I rang my daughter [PERSON_2]. [PERSON_2] is coming tomorrow."

    def test_queen_mary_hospital_still_one_facility(self):
        assert scrub("My friend Mary drove me to Queen Mary Hospital. Mary waited outside.").text == \
            "My friend [PERSON_2] drove me to [FACILITY_1]. [PERSON_2] waited outside."

    def test_propagation_is_case_sensitive_for_latin(self):
        assert scrub("My daughter Hope came. I hope she stays.").text == "My daughter [PERSON_2] came. I hope she stays."

    def test_cjk_repeat(self):
        assert scrub("我老公志強陪我去。志強好忙。", CHAN).text == "我老公[PERSON_2]陪我去。[PERSON_2]好忙。"

    def test_title_form_repeats(self):
        assert scrub("Dr Chan saw me. Dr Chan said rest.").text == "[PERSON_2] saw me. [PERSON_2] said rest."


class TestCantoneseSuffixRules:

    @pytest.mark.parametrize("text,expected", [
        ("我去黃大仙賽馬會診所換藥。", "我去[FACILITY_1]換藥。"),
        ("我個女喺浸會大學教書。", "我個女喺[ORG_1]教書。"),
        ("我會去瑪麗醫院做化療。", "我會去[FACILITY_1]做化療。"),
        ("佢喺街坊會工作。", "佢喺街坊會工作。"),                       # 會 alone is not an ORG suffix
    ])
    def test_wui_compounds_extend_leftwards(self, text, expected):
        assert scrub(text, CHAN).text == expected

    def test_verb_plus_insurance_is_not_an_org(self):
        assert scrub("護照號碼K12345678，填保險表用。", CHAN).text == "護照號碼[ID_1]，填保險表用。"
        assert scrub("我買咗保險。", CHAN).text == "我買咗保險。"
        assert scrub("我喺友邦保險做。", CHAN).text == "我喺[ORG_1]做。"

    def test_street_with_a_direction_suffix(self):
        res = scrub("送去深水埗太子道西235號16樓B室啦。", CHAN)
        assert "太子道西" not in res.text and "235號" not in res.text and "16樓B室" not in res.text
        assert res.text.endswith("啦。") and res.text.startswith("送去[")
        assert all(s.cls in ("ADDRESS", "PLACE") for s in res.spans)

    def test_direction_char_starting_a_word_is_not_swallowed(self):
        assert scrub("我唔知道西環點去。", CHAN).text == "我唔知道西環點去。"   # 西環 needs a location cue; 知道 is intact


class TestCantoneseKinshipNames:

    @pytest.mark.parametrize("text,expected", [
        ("我新抱婉婷嚟探我。", "我新抱[PERSON_2]嚟探我。"),
        ("我老婆麗珊揸車送我去醫院。", "我老婆[PERSON_2]揸車送我去醫院。"),
        ("我老公家俊嚟探我。", "我老公[PERSON_2]嚟探我。"),
        ("我個女可欣打電話嚟。", "我個女[PERSON_2]打電話嚟。"),
        ("我朋友子軒揸車送我去瑪麗醫院。", "我朋友[PERSON_2]揸車送我去[FACILITY_1]。"),
    ])
    def test_names_followed_by_common_verbs(self, text, expected):
        assert scrub(text, CHAN).text == expected

    @pytest.mark.parametrize("text", [
        "我個女電話係9876 5432。",          # the phone is scrubbed, 電話 is not a name
        "姑娘問起就話佢知。", "我個女家裡有事。", "我個女可以嚟。", "我個女瞓得好差。", "我個仔揸車送我。",
        "我個仔食咗飯。", "我老公成日擔心我。", "我個女最近好忙。",
    ])
    def test_common_words_after_kinship_are_not_names(self, text):
        res = scrub(text, CHAN)
        assert all(s.cls != "PERSON" for s in res.spans), res.text


class TestFutureCueWindow:

    def test_cue_up_to_32_chars_before_the_weekday(self):
        assert scrub("I'm seeing the oncologist Monday.").text == "I'm seeing the oncologist [DATE_1 · next Monday]."
        assert scrub("I'm seeing the oncologist on Tuesday for bloods.").text == \
            "I'm seeing the oncologist on [DATE_1 · next Tuesday] for bloods."

    def test_cue_in_the_previous_sentence_does_not_count(self):
        assert scrub("I will rest now. Friday I felt sick.").text == "I will rest now. [DATE_1 · 5 days ago] I felt sick."


class TestKinshipWordsThatAreAlsoTitles:
    """'sister' / 'nurse' are honorifics when Capitalised (Sister Ho) and relatives when
    lowercase after a possessive; the relative's NAME is the span, so the bare name is
    recognised on later turns."""

    def test_lowercase_sister_is_a_relative(self):
        ctx = ScrubContext(GRACE)
        res = ctx.scrub("My sister Mei Ling drove me to Sha Tin Hospital.", now=NOW)
        assert res.text == "My sister [PERSON_2] drove me to [FACILITY_1]."
        assert ctx.token_map.get("[PERSON_2]") == "Mei Ling"
        assert ctx.scrub("I'm glad Mei Ling is around to help.", now=NOW).text == "I'm glad [PERSON_2] is around to help."

    def test_capitalised_sister_is_still_a_title(self):
        assert scrub("Sister Ho changed the dressing.").text == "[PERSON_2] changed the dressing."
        assert scrub("I saw Nurse Cheung today.").text == "I saw [PERSON_2] today."

    def test_lowercase_nurse_as_a_relation(self):
        assert scrub("my nurse Angela is kind").text == "my nurse [PERSON_2] is kind"


class TestDatedNumericDatesAreNotReadings:

    def test_full_year_date_after_the_word_pain(self):
        assert scrub("I took 2 tablets. The pain started on 28/08/2026.").text == \
            "I took 2 tablets. The pain started on [DATE_1 · 12 days ago]."

    def test_yearless_pair_after_pain_is_still_guarded(self):
        assert scrub("the pain was 6/9 on waking").text == "the pain was 6/9 on waking"
