"""TokenMap: stable tokens per original, reverse index, versioning, round-trip."""

import pytest

from app.inference.scrub import KnownIdentifiers, ScrubContext, ScrubError, TokenMap, reidentify
from app.inference.scrub.tokens import (
    AGE, DATE, DOB, PERSON, PHONE, flexible_literal, normalise_key, normalise_phone, sentence_initial, token_surface,
)


class TestTokenMap:

    def test_same_original_same_token_case_and_whitespace_insensitive(self):
        tm = TokenMap()
        t1 = tm.token_for(PERSON, "Mei Ling")
        assert t1 == "[PERSON_1]"
        assert tm.token_for(PERSON, "mei  ling") == t1
        assert tm.token_for(PERSON, "Mei-Ling") == t1
        assert tm.token_for(PERSON, "Mary") == "[PERSON_2]"
        assert tm.get("[PERSON_1]") == "Mei Ling"

    def test_cjk_originals_are_exact(self):
        tm = TokenMap()
        assert tm.token_for(PERSON, "陳大文") == "[PERSON_1]"
        assert tm.token_for(PERSON, "陳大") == "[PERSON_2]"

    def test_indices_are_per_class(self):
        tm = TokenMap()
        assert tm.token_for(PERSON, "a") == "[PERSON_1]"
        assert tm.token_for("FACILITY", "b") == "[FACILITY_1]"
        assert tm.token_for(PHONE, "91234567") == "[PHONE_1]"
        assert tm.token_for(DATE, "5 March") == "[DATE_1]"

    def test_unindexed_classes_use_fixed_surface(self):
        tm = TokenMap()
        assert tm.token_for(AGE, "59", fixed="[AGE · 50s]") == "[AGE · 50s]"
        assert tm.token_for(AGE, "62", fixed="[AGE · 60s]") == "[AGE · 60s]"
        assert tm.token_for(DOB, "22/08/1966", fixed="[DOB]") == "[DOB]"
        assert tm.lookup(AGE, None, "50s") == "59"
        assert tm.lookup(AGE, None, "60s") == "62"
        assert tm.lookup(DOB, None) == "22/08/1966"
        # a second original in the same band keeps the first mapping
        assert tm.token_for(AGE, "55", fixed="[AGE · 50s]") == "[AGE · 50s]"
        assert tm.lookup(AGE, None, "50s") == "59"

    def test_single_age_resolves_without_band(self):
        tm = TokenMap()
        tm.token_for(AGE, "59", fixed="[AGE · 50s]")
        assert tm.lookup(AGE, None, None) == "59"
        tm.token_for(AGE, "62", fixed="[AGE · 60s]")
        assert tm.lookup(AGE, None, None) is None   # ambiguous

    def test_version_increments_only_when_a_token_is_added(self):
        tm = TokenMap()
        assert tm.version == 0
        tm.token_for(PERSON, "Mary")
        assert tm.version == 1
        tm.token_for(PERSON, "mary")
        assert tm.version == 1
        tm.token_for(PHONE, "91234567")
        assert tm.version == 2

    def test_unknown_class_rejected(self):
        with pytest.raises(ValueError):
            TokenMap().token_for("PATIENT", "x")

    def test_originals_and_exclusions(self):
        tm = TokenMap()
        tm.token_for(PERSON, "Mary")
        tm.token_for(DATE, "5 March")
        tm.token_for(AGE, "59", fixed="[AGE · 50s]")
        tm.token_for(DOB, "22/08/1966", fixed="[DOB]")
        assert tm.originals() == ["Mary", "5 March", "59", "22/08/1966"]
        assert tm.originals(exclude={AGE, DATE, DOB}) == ["Mary"]

    def test_round_trip_to_dict_from_dict(self):
        tm = TokenMap()
        tm.token_for(PERSON, "Grace Tam")
        tm.token_for(PERSON, "Mary")
        tm.token_for(DATE, "5 March")
        tm.token_for(AGE, "59", fixed="[AGE · 50s]")
        data = tm.to_dict()
        assert isinstance(data["entries"], list) and data["next_index"] == {"PERSON": 2, "DATE": 1}
        restored = TokenMap.from_dict(data)
        assert restored.get("[PERSON_2]") == "Mary"
        assert restored.lookup(AGE, None, "50s") == "59"
        assert restored.version == tm.version
        # the counter continues after the restored tokens
        assert restored.token_for(PERSON, "Someone Else") == "[PERSON_3]"
        assert restored.token_for(PERSON, "mary") == "[PERSON_2]"

    def test_from_dict_tolerates_missing_or_partial_data(self):
        assert len(TokenMap.from_dict(None)) == 0
        assert len(TokenMap.from_dict({})) == 0
        tm = TokenMap.from_dict({"entries": [["[PERSON_3]", "PERSON", "X"]]})
        assert tm.get("[PERSON_3]") == "X"
        assert tm.token_for(PERSON, "Y") == "[PERSON_4]"
        bad = TokenMap.from_dict({"entries": [["[PATIENT_1]", "PATIENT", "X"]]})
        assert len(bad) == 0

    def test_membership_and_len(self):
        tm = TokenMap()
        tm.token_for(PERSON, "Mary")
        assert "[PERSON_1]" in tm and "[PERSON_2]" not in tm and len(tm) == 1
        assert tm.class_of("[PERSON_1]") == PERSON


class TestReservedPatientToken:

    def test_display_name_reserved_as_person_1_even_if_it_never_appears(self):
        ctx = ScrubContext(KnownIdentifiers(full_name="Grace Tam", username="demo"))
        assert ctx.patient_token == "[PERSON_1]"
        assert ctx.token_map.get("[PERSON_1]") == "Grace Tam"
        res = ctx.scrub("my friend Mary drove me")
        assert res.text == "my friend [PERSON_2] drove me"

    def test_display_name_falls_back_to_username(self):
        ctx = ScrubContext(KnownIdentifiers(full_name=None, username="testpatient"))
        assert reidentify("Hello [PERSON_1]!", ctx.token_map) == "Hello testpatient!"
        ctx = ScrubContext(KnownIdentifiers(full_name="", username="testpatient"))
        assert reidentify("Hello [PERSON_1]!", ctx.token_map) == "Hello testpatient!"
        ctx = ScrubContext(KnownIdentifiers(full_name="   ", username="testpatient"))
        assert ctx.token_map.get("[PERSON_1]") == "testpatient"

    def test_empty_display_name_is_a_scrub_error(self):
        with pytest.raises(ScrubError) as exc:
            ScrubContext(KnownIdentifiers(full_name="", username=""))
        assert str(exc.value) == "context: ValueError"

    def test_reloaded_token_map_keeps_person_1(self):
        ctx = ScrubContext(KnownIdentifiers(full_name="Grace Tam", username="demo"))
        ctx.scrub("my friend Mary drove me")
        data = ctx.token_map.to_dict()
        ctx2 = ScrubContext(KnownIdentifiers(full_name="Grace Tam", username="demo"), token_map=TokenMap.from_dict(data))
        assert ctx2.patient_token == "[PERSON_1]"
        assert ctx2.scrub("Mary again").text == "[PERSON_2] again"


class TestHelpers:

    def test_normalise_key(self):
        assert normalise_key("  Mei   Ling ") == "mei ling"
        assert normalise_key("Mei-Ling") == "mei ling"
        assert normalise_key("陳大文") == "陳大文"

    def test_normalise_phone(self):
        assert normalise_phone("+852 9123 4567") == "91234567"
        assert normalise_phone("(852) 9123-4567") == "91234567"
        assert normalise_phone("9123.4567") == "91234567"
        assert normalise_phone("91234567") == "91234567"

    def test_flexible_literal_matches_spacing_and_hyphen_variants(self):
        import re
        rx = re.compile(flexible_literal("Mei Ling"), re.IGNORECASE)
        assert rx.search("my daughter Mei-Ling came")
        assert rx.search("Meiling came")
        assert rx.search("MEI LING")
        assert not rx.search("Meilinger")

    def test_sentence_initial(self):
        assert sentence_initial("Long came", 0)
        assert sentence_initial("Hi. Long came", 4)
        assert sentence_initial("你好。陳生", 3)
        assert not sentence_initial("and Long came", 4)
        assert not sentence_initial("Hi, Long", 4)

    def test_token_surface(self):
        assert token_surface("[DATE_2]", "3 days ago") == "[DATE_2 · 3 days ago]"
        assert token_surface("[PERSON_1]", None) == "[PERSON_1]"
