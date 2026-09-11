"""Tolerant re-identification of strings and nested outputs (A8)."""

import pytest
from pydantic import BaseModel

from app.inference.scrub import KnownIdentifiers, ScrubContext, TokenMap, reidentify, reidentify_obj, reidentify_text
from app.inference.scrub.tokens import AGE, DATE, DOB, FACILITY, PERSON


@pytest.fixture
def tm():
    m = TokenMap()
    m.token_for(PERSON, "Grace Tam")
    m.token_for(PERSON, "Mary")
    m.token_for(FACILITY, "Queen Mary Hospital")
    m.token_for(DATE, "5 March")
    m.token_for(AGE, "59", fixed="[AGE · 50s]")
    m.token_for(DOB, "22/08/1966", fixed="[DOB]")
    return m


class TestRoundTrips:

    @pytest.mark.parametrize("text,expected", [
        ("[PERSON_1]!", "Grace Tam!"),
        ("[PERSON_1]，", "Grace Tam，"),
        ("【PERSON_1】", "Grace Tam"),
        ("［PERSON_1］", "Grace Tam"),
        ("「PERSON_1」", "Grace Tam"),
        ("〔PERSON_2〕", "Mary"),
        ("[person 1]", "Grace Tam"),
        ("[ PERSON_1 ]", "Grace Tam"),
        ("[PERSON-1]", "Grace Tam"),
        ("[PERSON1]", "Grace Tam"),
        ("PERSON_1", "Grace Tam"),
        ("PERSON 2 and person_1", "Mary and Grace Tam"),
        ("[PERSON_1's]", "Grace Tam's"),
        ("[PERSON_1]'s", "Grace Tam's"),
        ("[DATE_1 · 188 days ago]", "5 March"),
        ("[DATE_1]", "5 March"),
        ("[AGE · 50s]", "59"),
        ("[AGE]", "59"),
        ("[DOB]", "22/08/1966"),
        ("[FACILITY_1] is near [PERSON_2]", "Queen Mary Hospital is near Mary"),
        ("你好，【PERSON_1】", "你好，Grace Tam"),
        ("How kind of [PERSON_2]!", "How kind of Mary!"),
    ])
    def test_text(self, tm, text, expected):
        assert reidentify(text, tm) == expected
        assert reidentify_text(text, tm)[1] == 0

    def test_unknown_tokens_left_and_counted(self, tm):
        assert reidentify_text("[PATIENT_1] met [PERSON_1]", tm) == ("[PATIENT_1] met Grace Tam", 1)
        assert reidentify_text("[PERSON_9]", tm) == ("[PERSON_9]", 1)
        assert reidentify_text("[AGE · 60s]", tm) == ("[AGE · 60s]", 1)
        assert reidentify_text("【PLACE_3】 and [ORG 2]", tm) == ("【PLACE_3】 and [ORG 2]", 2)
        assert reidentify_text("PERSON_7 spoke", tm) == ("PERSON_7 spoke", 0)   # bareless unknown: untouched

    def test_cjk_quotes_in_ordinary_text_are_not_touched(self, tm):
        assert reidentify("佢話「你好」同【注意】", tm) == "佢話「你好」同【注意】"

    def test_empty_map_and_empty_text(self):
        assert reidentify("hello [PERSON_1]", TokenMap()) == "hello [PERSON_1]"
        assert reidentify_text("hello [PERSON_1]", TokenMap()) == ("hello [PERSON_1]", 1)
        assert reidentify("", TokenMap()) == ""
        assert reidentify_text("", TokenMap()) == ("", 0)

    def test_case_insensitive_class(self, tm):
        assert reidentify("[facility_1]", tm) == "Queen Mary Hospital"
        assert reidentify("[Person_2]", tm) == "Mary"

    def test_no_false_positive_on_plain_words(self, tm):
        assert reidentify("the person in the date of the age", tm) == "the person in the date of the age"
        assert reidentify("PERSONAL_1 and DATE_10X", tm) == "PERSONAL_1 and DATE_10X"


class Inner(BaseModel):
    note: str
    tags: list[str] = []


class Outer(BaseModel):
    summary: str
    level: str = "GREEN"
    score: int = 3
    inner: Inner
    extras: dict = {}
    maybe: str | None = None


class TestReidentifyObj:

    def test_nested_dict_list_tuple(self, tm):
        obj = {"a": "[PERSON_1] is [AGE · 50s]", "b": ["[PERSON_2]", ("[DATE_1 · 3 days ago]", 5)], "c": 7, "d": None}
        out, unresolved = reidentify_obj(obj, tm)
        assert out == {"a": "Grace Tam is 59", "b": ["Mary", ("5 March", 5)], "c": 7, "d": None}
        assert unresolved == 0
        assert obj["a"] == "[PERSON_1] is [AGE · 50s]"       # input untouched

    def test_pydantic_model_copy(self, tm):
        model = Outer(summary="[PERSON_1] saw [PERSON_2] at [FACILITY_1]", inner=Inner(note="[PATIENT_1] rests", tags=["[DATE_1]", "ok"]),
                      extras={"k": "[AGE · 50s]"})
        out, unresolved = reidentify_obj(model, tm)
        assert isinstance(out, Outer) and out is not model
        assert out.summary == "Grace Tam saw Mary at Queen Mary Hospital"
        assert out.inner.note == "[PATIENT_1] rests" and out.inner.tags == ["5 March", "ok"]
        assert out.extras == {"k": "59"} and out.level == "GREEN" and out.score == 3
        assert unresolved == 1
        assert model.summary.startswith("[PERSON_1]")      # original model untouched

    def test_model_without_tokens_returned_unchanged(self, tm):
        model = Outer(summary="plain", inner=Inner(note="plain"))
        out, unresolved = reidentify_obj(model, tm)
        assert out is model and unresolved == 0

    def test_scalars_pass_through(self, tm):
        assert reidentify_obj(5, tm) == (5, 0)
        assert reidentify_obj(None, tm) == (None, 0)


class TestEndToEnd:

    def test_scrub_then_reidentify_restores_originals(self):
        ctx = ScrubContext(KnownIdentifiers(full_name="Grace Tam", username="demo"))
        text = "my daughter Mei Ling took Grace Tam to Queen Mary Hospital on 5 March; I'm 59"
        scrubbed = ctx.scrub(text, now=None).text
        assert "Mei Ling" not in scrubbed and "Grace" not in scrubbed
        assert reidentify(scrubbed, ctx.token_map) == text
