"""leak_check: whole-string layer-1 matching on outbound messages (A9/A16)."""

from pathlib import Path

from app.inference.scrub import KnownIdentifiers, ScrubContext, TokenMap, leak_check
from app.inference.scrub.tokens import PERSON, PHONE

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def msgs(*contents):
    return [{"role": "user", "content": c} for c in contents]


class TestMatching:

    def test_positive_whole_string(self):
        assert leak_check(msgs("I am Test Patient"), ["Test Patient"]) == ["Test Patient"]
        assert leak_check(msgs("i am test  patient"), ["Test Patient"]) == ["Test Patient"]
        assert leak_check(msgs("Test-Patient here"), ["Test Patient"]) == ["Test Patient"]

    def test_no_splitting_into_parts(self):
        assert leak_check(msgs("the patient had a test"), ["Test Patient"]) == []
        assert leak_check(msgs("I cannot sleep, bleeding stopped"), ["Dr. Amanda Lee"]) == []

    def test_word_boundaries_not_plain_in(self):
        assert leak_check(msgs("I take tamoxifen to demonstrate"), ["Tam", "demo"]) == []
        assert leak_check(msgs("Tam is here"), ["Tam"]) == ["Tam"]
        assert leak_check(msgs("demographics"), ["demo"]) == []

    def test_cjk_exact_substring(self):
        assert leak_check(msgs("我叫陳大文呀"), ["陳大文"]) == ["陳大文"]
        assert leak_check(msgs("陳大 文"), ["陳大文"]) == []

    def test_phones_digit_normalised(self):
        assert leak_check(msgs("call me on 9123 4567"), ["+852 9123 4567"]) == ["+852 9123 4567"]
        assert leak_check(msgs("9123-4567"), ["91234567"]) == ["91234567"]
        assert leak_check(msgs("+852-9123-4567"), ["91234567"]) == ["91234567"]
        assert leak_check(msgs("91234568"), ["91234567"]) == []

    def test_dates_and_years(self):
        assert leak_check(msgs("born 08/22/1966"), ["08/22/1966", "1966"]) == ["08/22/1966", "1966"]
        assert leak_check(msgs("1966年"), ["1966"]) == ["1966"]
        assert leak_check(msgs("19660 units"), ["1966"]) == []

    def test_prompt_templates_pass_for_fixture_identifiers(self):
        for name in ("triage_prompt_eng.txt", "assessment_prompt_eng.txt", "prompt_eng.txt"):
            text = (APP_DIR / name).read_text(encoding="utf-8")
            assert leak_check(msgs(text), ["Test Patient", "testpatient", "patient@test.com"]) == [], name

    def test_demo_user_sentence_passes(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="demo", email="grace.tam@example.com", phone="+852 9123 4567",
                             dob="08/22/1966", doctor_name="Dr. Amanda Lee", hospital="Ovis Demo Hospital")
        assert leak_check(msgs("I went to the hospital for a demo of the machine and I take tamoxifen to demonstrate"),
                          k.leak_forms()) == []
        assert leak_check(msgs("my name is Grace Tam"), k.leak_forms()) != []

    def test_assistant_and_system_turns_are_checked(self):
        assert leak_check([{"role": "assistant", "content": "Hello Test Patient"}], ["Test Patient"]) == ["Test Patient"]
        assert leak_check([{"role": "system", "content": "Test Patient"}], ["Test Patient"]) == ["Test Patient"]

    def test_content_parts_and_odd_shapes(self):
        parts = [{"role": "user", "content": [{"type": "text", "text": "I am Test Patient"}, "and more"]}]
        assert leak_check(parts, ["Test Patient"]) == ["Test Patient"]
        assert leak_check([{"role": "user", "content": None}], ["x"]) == []
        assert leak_check([], ["x"]) == []
        assert leak_check(msgs("hello"), None) == []
        assert leak_check(msgs("hello"), ["", "   ", None]) == []

    def test_return_preserves_input_order_without_duplicates(self):
        found = leak_check(msgs("Grace Tam and grace tam again"), ["Grace Tam", "grace tam", "Nobody"])
        assert found == ["Grace Tam"]


class TestTokenMapOriginals:

    def test_token_map_originals_are_checked_except_age_date_dob(self):
        tm = TokenMap()
        tm.token_for(PERSON, "Mary")
        tm.token_for(PHONE, "9123 4567")
        tm.token_for("DATE", "5 March")
        tm.token_for("AGE", "59", fixed="[AGE · 50s]")
        assert leak_check(msgs("Mary called 91234567 on 5 March aged 59"), [], tm) == ["Mary", "9123 4567"]

    def test_invariant_scrubbed_messages_pass(self):
        k = KnownIdentifiers(full_name="Grace Tam", username="gracetam", email="grace.tam@example.com",
                             phone="91234567", dob="08/22/1966", doctor_name="Dr. Amanda Lee", hospital="Ovis Demo Hospital")
        ctx = ScrubContext(k)
        history = [
            {"role": "assistant", "content": "Hello Grace Tam, Dr. Amanda Lee sends regards from Ovis Demo Hospital."},
            {"role": "user", "content": "my daughter Mei Ling (call 9123 4567 or grace.tam@example.com) born 22/08/1966 in 1966"},
            {"role": "assistant", "content": "How kind of Mei Ling!"},
        ]
        out = ctx.scrub_messages(history)
        assert leak_check(out, ctx.leak_forms()) == []
        assert leak_check(history, ctx.leak_forms()) != []
