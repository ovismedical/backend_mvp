"""Florence memory end to end through /florence/* and /memories/*.

Every request the FakeProvider records is what would leave the building, so the assertions about
names and addresses are assertions about the wire.
"""

import re

import pytest

from app.florence_memory import MEMORIES
from app.inference import InferenceGateway, reset_gateway
from app.inference.router import Policy
from tests.integration.test_florence_endpoints import by_task, finish, install, say, start
from tests.mock_openai import FakeProvider, fake_gateway


@pytest.fixture(autouse=True)
def _memory_on(monkeypatch):
    monkeypatch.setenv("FLORENCE_MEMORY", "on")
    yield
    reset_gateway(InferenceGateway({}))


def add(text, category="pet", durability="long"):
    return {"op": "add", "ref": None, "text": text, "category": category, "durability": durability}


def provider(memory_ops=None) -> FakeProvider:
    fake = FakeProvider(name="openai")
    fake.memory_ops = memory_ops
    return install(fake)


def text_of(requests) -> str:
    return "\n".join(m.get("content", "") for r in requests for m in r.messages)


def daughter_token(request) -> str:
    return re.search(r"daughter (\[PERSON_\d+\])", text_of([request])).group(1)


async def checkin(client, headers, *messages):
    session_id = (await start(client, headers))["session_id"]
    for message in messages:
        await say(client, headers, session_id, message)
    return session_id, await finish(client, headers, session_id)


async def remember_biscuit_and_mei(client, headers):
    provider(lambda request: [add("Has a dog called Biscuit"),
                              add(f"Daughter {daughter_token(request)} made them congee", "food", "short")])
    return await checkin(client, headers, "My dog Biscuit kept me up. My daughter Mei made me congee.")


class TestCapture:

    async def test_a_checkin_is_remembered_in_clear_text(self, client, patient_headers, seeded_db):
        session_id, result = await remember_biscuit_and_mei(client, patient_headers)
        assert result["triage_status"] == "completed"
        [doc] = seeded_db[MEMORIES].find({"user_id": "testpatient"})
        by_text = {m["text"]: m for m in doc["memories"]}
        assert set(by_text) == {"Has a dog called Biscuit", "Daughter Mei made them congee"}
        assert by_text["Daughter Mei made them congee"]["entities"] == [{"cls": "PERSON", "original": "Mei"}]
        assert by_text["Daughter Mei made them congee"]["durability"] == "short"
        assert by_text["Has a dog called Biscuit"]["source_session_id"] == session_id

    async def test_the_extraction_itself_only_ever_sees_placeholders(self, client, patient_headers):
        await remember_biscuit_and_mei(client, patient_headers)
        # The FakeProvider is still installed: its requests are this check-in's.
        from app.inference import get_gateway
        fake = get_gateway().providers["openai"]
        [extraction] = by_task(fake, "memory_extraction")
        assert "Mei" not in text_of([extraction]) and "Test Patient" not in text_of([extraction])

    async def test_an_address_is_never_stored(self, client, patient_headers, seeded_db):
        def address_notes(request):
            token = re.search(r"\[ADDRESS_\d+\]", text_of([request])).group(0)
            return [add("Lives at 12 Nathan Road", "other"), add("Lives at " + token, "other")]
        provider(address_notes)
        await checkin(client, patient_headers, "I live at 12 Nathan Road")
        assert seeded_db[MEMORIES].find_one({"user_id": "testpatient"}) is None

    async def test_a_refused_extraction_leaves_the_clinical_record_alone(self, client, patient_headers, seeded_db):
        real = Policy.load()
        policy = Policy.from_dict({
            "version": 1, "flags": {},
            "providers": {"openai": {"requires": []}, "local": {"requires": []}},
            "tasks": {**{t: {"provider": "openai", "on_refuse": real.on_refuse_for(t)}
                         for t in ("chat_turn", "symptom_assessment", "triage")},
                      "memory_extraction": {"provider": "local", "on_refuse": "skip"}},   # local is not configured
            "tools": {name: {"requires": [], "discloses": spec.discloses} for name, spec in real.tools.items()},
        }, source="test")
        fake = FakeProvider(name="openai")
        fake.memory_ops = [add("Has a dog called Biscuit")]
        install(fake, policy=policy)
        _, result = await checkin(client, patient_headers, "My dog Biscuit kept me up")
        assert result["triage_status"] == "completed"
        assert by_task(fake, "memory_extraction") == []
        assert seeded_db[MEMORIES].find_one({"user_id": "testpatient"}) is None

    async def test_a_crashing_extraction_leaves_the_clinical_record_alone(self, client, patient_headers, seeded_db):
        def boom(request):
            raise RuntimeError("model returned garbage")
        provider(boom)
        _, result = await checkin(client, patient_headers, "My dog Biscuit kept me up")
        assert result["triage_status"] == "completed"
        assert seeded_db[MEMORIES].find_one({"user_id": "testpatient"}) is None

    async def test_forget_removes_a_note(self, client, patient_headers, seeded_db):
        await remember_biscuit_and_mei(client, patient_headers)

        def forget_biscuit(request):
            ref = re.search(r"(m\d+): Has a dog called Biscuit", text_of([request])).group(1)
            return [{"op": "forget", "ref": ref, "text": None, "category": None, "durability": None}]
        provider(forget_biscuit)
        await checkin(client, patient_headers, "Biscuit passed away last month")
        texts = [m["text"] for m in seeded_db[MEMORIES].find_one({"user_id": "testpatient"})["memories"]]
        assert texts == ["Daughter Mei made them congee"]


class TestRecall:

    async def test_the_next_checkin_opens_with_the_notes_and_names_stay_tokenised(self, client, patient_headers):
        await remember_biscuit_and_mei(client, patient_headers)
        fake = provider()
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "Mei came round again today")
        chats = by_task(fake, "chat_turn")
        assert len(chats) == 2
        for request in chats:
            first = request.messages[0]
            assert first["role"] == "developer"
            assert "Has a dog called Biscuit (today)" in first["content"]
            assert re.search(r"Daughter \[PERSON_\d+\] made them congee", first["content"])
        # Seeded, so the patient's new turn - which has no cue word - is tokenised too.
        assert "Mei" not in text_of(chats)

    async def test_assessment_and_triage_never_see_the_notes(self, client, patient_headers):
        await remember_biscuit_and_mei(client, patient_headers)
        fake = provider()
        await checkin(client, patient_headers, "Feeling a bit tired today")
        clinical = by_task(fake, "symptom_assessment") + by_task(fake, "triage")
        assert clinical and "Biscuit" not in text_of(clinical)
        assert all(m["role"] != "developer" for r in clinical for m in r.messages)

    async def test_the_notes_are_never_returned_with_the_session(self, client, patient_headers):
        await remember_biscuit_and_mei(client, patient_headers)
        provider()
        session_id = (await start(client, patient_headers))["session_id"]
        body = (await client.get(f"/florence/session/{session_id}", headers=patient_headers)).json()
        assert "memory_context" not in body and "Biscuit" not in str(body)

    async def test_capture_mode_stores_but_never_injects(self, client, patient_headers, seeded_db, monkeypatch):
        monkeypatch.setenv("FLORENCE_MEMORY", "capture")
        await remember_biscuit_and_mei(client, patient_headers)
        assert seeded_db[MEMORIES].find_one({"user_id": "testpatient"})
        fake = provider()
        await start(client, patient_headers)
        assert "Biscuit" not in text_of(by_task(fake, "chat_turn"))

    async def test_off_does_nothing(self, client, patient_headers, seeded_db, monkeypatch):
        monkeypatch.setenv("FLORENCE_MEMORY", "off")
        fake = provider([add("Has a dog called Biscuit")])
        await checkin(client, patient_headers, "My dog Biscuit kept me up")
        assert by_task(fake, "memory_extraction") == []
        assert seeded_db[MEMORIES].find_one({"user_id": "testpatient"}) is None

    async def test_a_patient_who_switched_memory_off_is_neither_captured_nor_reminded(self, client, patient_headers):
        await remember_biscuit_and_mei(client, patient_headers)
        assert (await client.put("/memories/settings", json={"enabled": False}, headers=patient_headers)).status_code == 200
        fake = provider([add("Likes mahjong", "hobby")])
        await checkin(client, patient_headers, "Played mahjong all afternoon")
        assert by_task(fake, "memory_extraction") == []
        assert "Biscuit" not in text_of(by_task(fake, "chat_turn"))


class TestPatientControls:

    async def test_list_delete_and_clear(self, client, patient_headers):
        await remember_biscuit_and_mei(client, patient_headers)
        body = (await client.get("/memories/me", headers=patient_headers)).json()
        assert body["enabled"] is True
        assert {m["text"] for m in body["memories"]} == {"Has a dog called Biscuit", "Daughter Mei made them congee"}
        assert all("entities" not in m for m in body["memories"])

        biscuit = next(m["memory_id"] for m in body["memories"] if "Biscuit" in m["text"])
        after = (await client.delete(f"/memories/{biscuit}", headers=patient_headers)).json()
        assert [m["text"] for m in after["memories"]] == ["Daughter Mei made them congee"]
        assert (await client.delete(f"/memories/{biscuit}", headers=patient_headers)).status_code == 404

        cleared = (await client.delete("/memories/me", headers=patient_headers)).json()
        assert cleared["memories"] == []

    async def test_another_patients_memory_is_not_found(self, client, patient_headers, seeded_db):
        seeded_db[MEMORIES].insert_one({"user_id": "someoneelse", "enabled": True, "version": 1,
                                        "memories": [{"memory_id": "mem_theirs", "text": "Has a parrot"}]})
        assert (await client.delete("/memories/mem_theirs", headers=patient_headers)).status_code == 404
        assert seeded_db[MEMORIES].find_one({"user_id": "someoneelse"})["memories"]

    async def test_clinicians_cannot_read_patient_memories(self, client, doctor_headers):
        assert (await client.get("/memories/me", headers=doctor_headers)).status_code == 403

    async def test_deleting_a_user_removes_their_memories(self, client, doctor_headers, seeded_db):
        seeded_db[MEMORIES].insert_one({"user_id": "testpatient", "enabled": True, "version": 1, "memories": []})
        body = (await client.delete("/admin/users/testpatient", headers=doctor_headers)).json()
        assert body["cleaned"][MEMORIES] == 1
