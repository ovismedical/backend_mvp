"""Layer 1: KnownIdentifiers — name forms, stoplist, username, email, phone,
DOB semantics, doctor/hospital, display_name and leak_forms."""

from datetime import date, datetime

import pytest

from app.inference.scrub import KnownIdentifiers, ScrubContext, leak_check, parse_dob, strip_title
from app.inference.scrub.names import KnownMatcher, cjk_name_variants, cjk_split

NOW = datetime(2026, 9, 9, 10, 0)


def scrub(known: KnownIdentifiers, text: str, now=NOW) -> str:
    return ScrubContext(known).scrub(text, now=now).text


class TestDisplayName:

    def test_full_name_wins(self):
        assert KnownIdentifiers(full_name="Grace Tam", username="demo").display_name == "Grace Tam"

    def test_username_fallback_for_none_and_blank(self):
        assert KnownIdentifiers(full_name=None, username="testpatient").display_name == "testpatient"
        assert KnownIdentifiers(full_name="  ", username="testpatient").display_name == "testpatient"

    def test_both_missing_is_empty(self):
        assert KnownIdentifiers().display_name == ""


class TestParseDob:

    def test_mm_dd_yyyy_first(self):
        assert parse_dob("08/22/1966") == date(1966, 8, 22)
        assert parse_dob("01/01/1990") == date(1990, 1, 1)

    def test_iso_and_dd_mm(self):
        assert parse_dob("1990-01-01") == date(1990, 1, 1)
        assert parse_dob("22/08/1966") == date(1966, 8, 22)   # MM/DD invalid -> DD/MM

    def test_unparseable_kept_as_extra(self):
        k = KnownIdentifiers(full_name="A B", dob="twenty-two august")
        assert k.dob is None and "twenty-two august" in k.extra
        assert parse_dob("") is None and parse_dob(None) is None

    def test_date_and_datetime_passthrough(self):
        assert parse_dob(date(1966, 8, 22)) == date(1966, 8, 22)
        assert parse_dob(datetime(1966, 8, 22, 5)) == date(1966, 8, 22)
        assert KnownIdentifiers(full_name="A B", dob=datetime(1966, 8, 22)).dob == date(1966, 8, 22)


class TestNameForms:

    def test_full_name_and_romanised_variants(self):
        k = KnownIdentifiers(full_name="Tam Mei Ling", username="u1")
        assert scrub(k, "Tam Mei Ling is here") == "[PERSON_1] is here"
        assert scrub(k, "mei ling is here") == "[PERSON_1] is here"
        assert scrub(k, "Mei-Ling is here") == "[PERSON_1] is here"
        assert scrub(k, "Meiling is here") == "[PERSON_1] is here"
        assert scrub(k, "TAM MEI LING") == "[PERSON_1]"

    def test_single_part_three_chars_not_stoplisted(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="u1")
        assert scrub(k, "Mrs Tam felt tired; tam said so") == "[PERSON_1] felt tired; [PERSON_1] said so"

    def test_single_part_under_three_chars_never_alone(self):
        k = KnownIdentifiers(full_name="Ng Ka Yu", username="u1")
        assert scrub(k, "ng yu ka") == "ng yu ka"
        assert scrub(k, "Ng Ka Yu came") == "[PERSON_1] came"

    def test_stoplisted_part_only_capitalised_mid_sentence(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="u1")
        assert scrub(k, "by the grace of god") == "by the grace of god"
        assert scrub(k, "thank you Grace, rest well") == "thank you [PERSON_1], rest well"
        assert scrub(k, "Grace, how are you") == "Grace, how are you"        # sentence-initial + punctuation
        assert scrub(k, "Hello. Grace is here") == "Hello. Grace is here"      # after a full stop
        assert scrub(k, "and Grace is here") == "and [PERSON_1] is here"

    def test_leading_title_stripped_before_parts(self):
        k = KnownIdentifiers(full_name="Dr. Test", username="u1", doctor_name="Professor Amanda Lee")
        assert scrub(k, "Dr. Test said; the test result; Dr Test is in") == "[PERSON_1] said; the test result; [PERSON_1] is in"
        assert scrub(k, "Professor Amanda Lee and Amanda Lee and Amanda") == "[PERSON_2] and [PERSON_2] and [PERSON_2]"

    def test_username_scrubbed_when_four_chars_and_not_common(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="gracetam88")
        assert scrub(k, "login gracetam88 now") == "login [PERSON_1] now"
        k = KnownIdentifiers(full_name="Grace Tam", username="demo")
        assert scrub(k, "this is just a demo") == "this is just a demo"
        k = KnownIdentifiers(full_name="Grace Tam", username="gt")
        assert scrub(k, "gt gt") == "gt gt"

    def test_username_maps_to_patient_token(self):
        k = KnownIdentifiers(full_name="Test Patient", username="testpatient")
        ctx = ScrubContext(k)
        assert ctx.scrub("testpatient logged in").text == "[PERSON_1] logged in"
        assert ctx.token_map.get("[PERSON_1]") == "Test Patient"


class TestCjkNames:

    def test_split_and_variants(self):
        assert cjk_split("陳大文") == ("陳", "大文")
        assert cjk_split("歐陽志明") == ("歐陽", "志明")
        v = cjk_name_variants("陳大文")
        assert {"陳大文", "大文", "陳生", "陳太", "陳先生", "陳太太", "陳小姐", "陳女士", "陳姑娘", "陳醫生", "陳伯", "陳叔", "陳嬸", "陳姨", "阿文"} <= set(v)

    def test_one_char_given_name_not_a_variant(self):
        v = cjk_name_variants("陳文")
        assert "文" not in v and "阿文" in v and "陳生" in v

    def test_ah_variant_skipped_when_kinship(self):
        # 陳美媽 -> 阿媽 would be a kinship word
        assert "阿媽" not in cjk_name_variants("陳美媽")

    def test_patient_and_doctor_variants_scrub_to_their_tokens(self):
        k = KnownIdentifiers(full_name="陳大文", username="chan123", doctor_name="李志明")
        assert scrub(k, "叫我大文得喇") == "叫我[PERSON_1]得喇"
        assert scrub(k, "陳生你好") == "[PERSON_1]你好"
        assert scrub(k, "陳太") == "[PERSON_1]"
        assert scrub(k, "阿文") == "[PERSON_1]"
        assert scrub(k, "李生話要覆診") == "[PERSON_2]話要覆診"
        assert scrub(k, "李志明醫生同陳大文講") == "[PERSON_2]同[PERSON_1]講"

    def test_cjk_traps_unchanged(self):
        k = KnownIdentifiers(full_name="陳大文", username="chan123", doctor_name="李志明")
        for t in ["花生", "醫生", "衛生", "何時發生", "阿媽", "陳皮", "李子"]:
            assert scrub(k, t) == t


class TestContactIdentifiers:

    def test_email_case_insensitive(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="u1", email="grace.tam@example.com")
        assert scrub(k, "mail Grace.Tam@Example.com please") == "mail [EMAIL_1] please"

    def test_phone_digit_normalised(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="u1", phone="91234567")
        assert scrub(k, "my number is 9123 4567") == "my number is [PHONE_1]"
        assert scrub(k, "you can reach me on 9123-4567") == "you can reach me on [PHONE_1]"
        assert scrub(k, "我手機 9123 4567") == "我手機 [PHONE_1]"
        assert scrub(k, "+852 9123 4567 or (852) 9123.4567") == "[PHONE_1] or [PHONE_1]"
        k = KnownIdentifiers(full_name="Grace Tam", username="u1", phone="+852 9123 4567")
        assert scrub(k, "91234567") == "[PHONE_1]"

    def test_phone_does_not_match_other_numbers(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="u1", phone="91234567")
        assert scrub(k, "pain 7/10 for 3 days and CA-125 was 35") == "pain 7/10 for 3 days and CA-125 was 35"

    def test_hospital_and_extra(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="u1", hospital="Ovis Demo Hospital", extra=["POLICY-XYZ-1"])
        assert scrub(k, "at ovis demo hospital, ref POLICY-XYZ-1") == "at [FACILITY_1], ref [ID_1]"
        assert scrub(k, "I went to the hospital for a demo") == "I went to the hospital for a demo"


class TestDob:

    @pytest.mark.parametrize("text", [
        "22/08/1966", "08/22/1966", "1966-08-22", "August 22, 1966", "22.08.1966", "1966年8月22日", "8月22日",
        "Aug 22 1966", "22 August 1966", "22/8/66", "1966/08/22", "66年8月22日", "22 Aug 1966",
    ])
    def test_dob_in_any_format_becomes_dob_token(self, text):
        k = KnownIdentifiers(full_name="Grace Tam", username="u1", dob=date(1966, 8, 22))
        assert scrub(k, f"born {text} ok") == "born [DOB] ok"

    def test_other_dates_are_not_dob(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="u1", dob=date(1966, 8, 22))
        assert scrub(k, "on 5 March 2024") == "on [DATE_1 · ~2 years ago]"
        assert scrub(k, "22 September") == "[DATE_1 · in 13 days]"

    def test_ambiguous_numeric_dob_checks_both_readings(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="u1", dob=date(1966, 5, 3))
        assert scrub(k, "05/03/1966") == "[DOB]"
        assert scrub(k, "03/05/1966") == "[DOB]"

    def test_bare_birth_year_becomes_age_band(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="u1", dob=date(1966, 8, 22))
        assert scrub(k, "I was born in 1966") == "I was born in [AGE · 60s]"
        assert scrub(k, "since 1966 I lived here") == "since [AGE · 60s] I lived here"
        assert scrub(k, "in 19660 units") == "in 19660 units"

    def test_dob_year_in_known_layer_uses_now(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="u1", dob=date(1966, 8, 22))
        assert scrub(k, "1966", now=datetime(2020, 1, 1)) == "[AGE · 50s]"


class TestLeakForms:

    def test_demo_user_forms(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="demo", email="grace.tam@example.com",
                             phone="+852 9123 4567", dob="08/22/1966", doctor_name="Dr. Amanda Lee",
                             hospital="Ovis Demo Hospital")
        forms = k.leak_forms()
        assert "Grace Tam" in forms and "Tam" in forms and "grace" not in [f.lower() for f in forms]
        assert "demo" not in [f.lower() for f in forms]           # stoplisted username
        assert "grace.tam@example.com" in forms and "91234567" in forms
        assert {"08/22/1966", "22/08/1966", "1966-08-22", "22/8/1966", "1966/08/22", "1966"} <= set(forms)
        assert "Dr. Amanda Lee" in forms and "Amanda Lee" in forms and "Lee" not in forms
        assert "Ovis Demo Hospital" in forms

    def test_fixture_user_forms_have_no_single_stoplisted_parts(self):
        k = KnownIdentifiers(full_name="Test Patient", username="testpatient", email="patient@test.com", doctor_name="Dr. Test")
        forms = k.leak_forms()
        assert "Test Patient" in forms and "testpatient" in forms and "Dr. Test" in forms
        assert "Test" not in forms and "Patient" not in forms

    def test_cjk_forms(self):
        k = KnownIdentifiers(full_name="陳大文", username="chan123", doctor_name="李志明")
        forms = k.leak_forms()
        assert "陳大文" in forms and "李志明" in forms and "chan123" in forms
        assert "陳生" not in forms and "大文" not in forms     # derived variants are not leak forms

    def test_invariant_everything_flagged_is_scrubbed(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="demo", email="grace.tam@example.com",
                             phone="+852 9123 4567", dob="08/22/1966", doctor_name="Dr. Amanda Lee",
                             hospital="Ovis Demo Hospital")
        ctx = ScrubContext(k)
        sentences = [
            "I am Grace Tam, Tam to friends, GraceTam online, born 22/08/1966 in 1966, doctor Dr. Amanda Lee",
            "grace.tam@example.com +852 9123 4567 Ovis Demo Hospital 1966-08-22 1966/08/22 08/22/1966 22/8/1966",
            "I went to the hospital for a demo of the machine and I take tamoxifen to demonstrate",
        ]
        for s in sentences:
            scrubbed = ctx.scrub(s, now=NOW).text
            assert leak_check([{"role": "user", "content": scrubbed}], k.leak_forms()) == [], scrubbed


class TestKnownMatcherResolve:

    def test_resolve_person_for_title_rule(self):
        m = KnownMatcher(KnownIdentifiers(full_name="Ho Long", username="u1", doctor_name="Dr. Amanda Lee"))
        assert m.resolve_person("Long") == "Ho Long"
        assert m.resolve_person("Ho Long") == "Ho Long"
        assert m.resolve_person("Lee") == "Dr. Amanda Lee"
        assert m.resolve_person("Chan") is None
