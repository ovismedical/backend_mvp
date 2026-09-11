"""Tool items are a second channel: they must survive the scrub replay and be visible to leak_check.

A Responses API `function_call` / `function_call_output` carries no "role" and no "content", so
before this both the scrubber (which flattened them away) and the leak check (which reads only
"content") were blind to them.
"""

from app.inference.scrub import KnownIdentifiers, ScrubContext, leak_check, tool_item_field

KNOWN = KnownIdentifiers(full_name="Test Patient", username="testpatient", email="patient@test.com",
                         doctor_name="Dr. Test")


def call(name="record_symptom", arguments='{"symptom": "fatigue"}', call_id="call_1"):
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}


def output(payload='{"recorded": "fatigue"}', call_id="call_1"):
    return {"type": "function_call_output", "call_id": call_id, "output": payload}


class TestToolItemField:

    def test_identifies_tool_items(self):
        assert tool_item_field(call()) == "arguments"
        assert tool_item_field(output()) == "output"

    def test_ordinary_turns_and_junk_are_not_tool_items(self):
        assert tool_item_field({"role": "user", "content": "hello"}) is None
        assert tool_item_field({"type": "message", "content": "hello"}) is None
        assert tool_item_field("just a string") is None
        assert tool_item_field(None) is None


class TestLeakCheckSeesToolPayloads:

    def test_identifier_in_tool_output_is_caught(self):
        # This is the red-team case: data fetched server-side and handed back to the model.
        leaked = output('{"rows": [{"patient": "Test Patient", "severity": 4}]}')
        assert leak_check([leaked], ["Test Patient"]) == ["Test Patient"]

    def test_identifier_in_tool_arguments_is_caught(self):
        leaked = call(arguments='{"evidence": "Test Patient said she was tired"}')
        assert leak_check([leaked], ["Test Patient"]) == ["Test Patient"]

    def test_clean_tool_traffic_passes(self):
        assert leak_check([call(), output()], ["Test Patient"]) == []

    def test_still_matches_whole_strings_only(self):
        assert leak_check([output('{"note": "the patient had a test"}')], ["Test Patient"]) == []


class TestScrubMessagesPreservesToolItems:

    def test_tool_item_keeps_its_keys(self):
        ctx = ScrubContext(KNOWN)
        out = ctx.scrub_messages([call()])
        assert out[0]["type"] == "function_call"
        assert out[0]["call_id"] == "call_1"
        assert out[0]["name"] == "record_symptom"
        assert "role" not in out[0]

    def test_tool_payload_is_scrubbed(self):
        ctx = ScrubContext(KNOWN)
        out = ctx.scrub_messages([call(arguments='{"evidence": "Test Patient is tired"}')])
        assert "Test Patient" not in out[0]["arguments"]
        assert ctx.patient_token in out[0]["arguments"]

    def test_tool_output_payload_is_scrubbed(self):
        ctx = ScrubContext(KNOWN)
        out = ctx.scrub_messages([output('{"note": "Test Patient"}')])
        assert "Test Patient" not in out[0]["output"]

    def test_non_string_payload_does_not_raise(self):
        ctx = ScrubContext(KNOWN)
        out = ctx.scrub_messages([{"type": "function_call_output", "call_id": "c", "output": None}])
        assert out[0]["output"] == ""

    def test_scrubbed_payload_is_leak_clean(self):
        ctx = ScrubContext(KNOWN)
        out = ctx.scrub_messages([call(arguments='{"evidence": "Test Patient is tired"}')])
        assert leak_check(out, ctx.leak_forms()) == []


class TestReplayIntegrity:
    """The regression test for the correctness half: a turn's own call history must come back."""

    def test_mixed_conversation_replays_intact(self):
        ctx = ScrubContext(KNOWN)
        history = [
            {"role": "assistant", "content": "How have your energy levels been?"},
            {"role": "user", "content": "I'm Test Patient and I've been tired"},
            call(arguments='{"symptom": "fatigue", "severity": 3}'),
            output('{"recorded": "fatigue", "remaining": ["appetite"]}'),
            {"role": "assistant", "content": "And how has your appetite been?"},
        ]
        out = ctx.scrub_messages(history)
        assert len(out) == 5
        assert [o.get("type") for o in out] == [None, None, "function_call", "function_call_output", None]
        assert out[2]["call_id"] == "call_1" and out[3]["call_id"] == "call_1"
        assert "fatigue" in out[2]["arguments"]
        assert "Test Patient" not in out[1]["content"]

    def test_ordinary_turns_are_unchanged_in_shape(self):
        ctx = ScrubContext(KNOWN)
        out = ctx.scrub_messages([{"role": "user", "content": "hello"}])
        assert out == [{"role": "user", "content": "hello"}]
