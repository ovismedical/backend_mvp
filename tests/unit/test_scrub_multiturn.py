"""Multi-turn behaviour (A0/A11): originals learned earlier stay scrubbed on
every later turn, including re-identified assistant echoes, and the same token
is reused. TokenMap persistence across turns via to_dict/from_dict."""

from datetime import datetime

from app.inference.scrub import KnownIdentifiers, ScrubContext, TokenMap, leak_check, reidentify

NOW = datetime(2026, 9, 9, 10, 0)
KNOWN = KnownIdentifiers(full_name="Test Patient", username="testpatient", email="patient@test.com", doctor_name="Dr. Test")


class TestSessionKnownLayer:

    def test_kinship_name_sticks_without_cue(self):
        ctx = ScrubContext(KNOWN)
        assert ctx.scrub("my daughter Mei Ling brought soup", now=NOW).text == "my daughter [PERSON_2] brought soup"
        assert ctx.scrub("How kind of Mei Ling!", now=NOW).text == "How kind of [PERSON_2]!"
        assert ctx.scrub("mei-ling again", now=NOW).text == "[PERSON_2] again"
        assert ctx.scrub("Meiling again", now=NOW).text == "[PERSON_2] again"

    def test_cued_phone_sticks_without_cue(self):
        ctx = ScrubContext(KNOWN)
        assert ctx.scrub("WhatsApp me at 9123 4567", now=NOW).text == "WhatsApp me at [PHONE_1]"
        assert ctx.scrub("I've noted 9123 4567", now=NOW).text == "I've noted [PHONE_1]"
        assert ctx.scrub("I've noted 91234567 and 9123-4567", now=NOW).text == "I've noted [PHONE_1] and [PHONE_1]"

    def test_cache_invalidation_when_a_name_is_learned(self):
        ctx = ScrubContext(KNOWN)
        first = [{"role": "user", "content": "Mei Ling is coming"}]
        assert ctx.scrub_messages(first, now=NOW)[0]["content"] == "Mei Ling is coming"
        ctx.scrub("my daughter Mei Ling", now=NOW)
        assert ctx.scrub_messages(first, now=NOW)[0]["content"] == "[PERSON_2] is coming"

    def test_ages_and_dates_are_not_session_originals(self):
        ctx = ScrubContext(KNOWN)
        ctx.scrub("I'm 59, it started 5 March", now=NOW)
        assert ctx.scrub("59 tablets on March 5 shelf 59", now=NOW).text == "59 tablets on [DATE_1 · 188 days ago] shelf 59"

    def test_cjk_session_originals_exact(self):
        ctx = ScrubContext(KnownIdentifiers(full_name="陳大文", username="chan123"))
        assert ctx.scrub("我個女美玲帶我去", now=NOW).text == "我個女[PERSON_2]帶我去"
        assert ctx.scrub("美玲好叻", now=NOW).text == "[PERSON_2]好叻"
        assert ctx.scrub("美玲玲", now=NOW).text == "[PERSON_2]玲"   # exact substring, no word boundaries in CJK


class TestA11Scenario:

    def test_friend_mary_and_queen_mary_hospital_across_turns(self):
        ctx = ScrubContext(KNOWN)
        res = ctx.scrub("my friend Mary drove me to Queen Mary Hospital", now=NOW)
        assert res.text == "my friend [PERSON_2] drove me to [FACILITY_1]"

        reply = reidentify("That was kind of [PERSON_2].", ctx.token_map)
        assert reply == "That was kind of Mary."

        out = ctx.scrub_messages([
            {"role": "assistant", "content": reply, "timestamp": "2026-09-09T10:00:01"},
            {"role": "user", "content": "Mary is visiting tomorrow and Queen Mary Hospital is far"},
        ], now=NOW)
        assert out == [
            {"role": "assistant", "content": "That was kind of [PERSON_2]."},
            {"role": "user", "content": "[PERSON_2] is visiting tomorrow and [FACILITY_1] is far"},
        ]
        assert all("Mary" not in m["content"] for m in out)
        assert ctx.token_map.get("[PERSON_2]") == "Mary" and ctx.token_map.get("[FACILITY_1]") == "Queen Mary Hospital"

        # the extended leak list flags an unscrubbed echo, and passes for the scrubbed turns
        assert leak_check([{"role": "user", "content": "Mary is visiting"}], KNOWN.leak_forms(), ctx.token_map) == ["Mary"]
        assert leak_check(out, ctx.leak_forms()) == []

    def test_hospital_stays_one_token_even_after_mary_is_known(self):
        ctx = ScrubContext(KNOWN)
        ctx.scrub("my friend Mary", now=NOW)
        assert ctx.scrub("then Queen Mary Hospital called", now=NOW).text == "then [FACILITY_1] called"


class TestPersistence:

    def test_token_map_round_trip_between_turns(self):
        ctx = ScrubContext(KNOWN)
        ctx.scrub("my daughter Mei Ling took me to Queen Mary on Tuesday, I'm 59", now=NOW)
        saved = ctx.token_map.to_dict()

        ctx2 = ScrubContext(KNOWN, token_map=TokenMap.from_dict(saved))
        res = ctx2.scrub("Mei Ling says Queen Mary was busy; my friend Peter came", now=NOW)
        assert res.text == "[PERSON_2] says [FACILITY_1] was busy; my friend [PERSON_3] came"
        assert reidentify(res.text, ctx2.token_map) == "Mei Ling says Queen Mary was busy; my friend Peter came"
        assert reidentify("[PERSON_1] is [AGE · 50s]", ctx2.token_map) == "Test Patient is 59"

    def test_patient_name_never_leaks_through_history(self):
        ctx = ScrubContext(KNOWN)
        history = [
            {"role": "assistant", "content": "Hello Test Patient, I'm Florence."},
            {"role": "user", "content": "Hi, it's testpatient, I had a blood test today, pain 7/10"},
            {"role": "assistant", "content": "Thanks Test Patient. Dr. Test will see the patient leaflet."},
        ]
        out = ctx.scrub_messages(history, now=NOW)
        joined = " ".join(m["content"] for m in out)
        assert "Test Patient" not in joined and "testpatient" not in joined and "Dr. Test" not in joined
        assert "blood test" in joined and "patient leaflet" in joined and "pain 7/10" in joined
        assert leak_check(out, KNOWN.leak_forms()) == []
