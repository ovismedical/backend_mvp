"""Florence memory: the scrubber decides what may be remembered, and the store is written whole.

The screening rule is the point of the design - a pet's name and a meal are kept, an address or a
hospital never is, however the model phrased it - so each blocked class gets a case of its own.
"""

from datetime import datetime, timedelta, timezone

import pytest
from pymongo.errors import DuplicateKeyError

from app.florence_memory import (
    MAX_MEMORIES, MEMORIES, PatientMemories, PlannedOp, ScreenedMemory, apply_memory_ops, memory_messages,
    mutate_memories, plan_memory_ops, screen_memory, seed_token_map,
)
from app.florence_utils import MemoryOp
from app.inference.scrub import KnownIdentifiers, ScrubContext
from tests.conftest import MockDatabase

NOW = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)


def context() -> ScrubContext:
    return ScrubContext(KnownIdentifiers(full_name="Grace Tam", username="demo", email="grace@example.com",
                                         phone="91234567"), tz="Asia/Hong_Kong")


def planned(text, *, category="pet", durability="long", entities=()) -> PlannedOp:
    return PlannedOp("add", memory=ScreenedMemory(text=text, entities=tuple(entities)),
                     category=category, durability=durability)


class TestWhatIsKept:

    def test_a_pet_name_is_kept(self):
        screened = screen_memory("Has a dog called Biscuit", context(), now=NOW)
        assert screened is not None
        assert screened.text == "Has a dog called Biscuit"

    def test_a_meal_is_kept(self):
        screened = screen_memory("Had congee with fish for dinner", context(), now=NOW)
        assert screened.text == "Had congee with fish for dinner"

    def test_a_family_member_is_restored_and_recorded_for_seeding(self):
        ctx = context()
        ctx.scrub("my daughter Mei visits on Sundays", now=NOW)
        token = next(t for t, cls, original in ctx.token_map.entries() if original == "Mei")
        screened = screen_memory(f"Daughter {token} visits most Sundays", ctx, now=NOW)
        assert screened.text == "Daughter Mei visits most Sundays"
        assert ("PERSON", "Mei") in screened.entities

    def test_the_patient_is_never_named_in_a_note(self):
        ctx = context()
        screened = screen_memory(f"{ctx.patient_token} loves gardening", ctx, now=NOW)
        assert screened.text == "the patient loves gardening"
        assert all(original != "Grace Tam" for _, original in screened.entities)

    def test_screening_never_changes_the_session_map(self):
        ctx = context()
        version = ctx.token_map.version
        screen_memory("Sister Ah Mei came over from Tai Po", ctx, now=NOW)
        screen_memory("Has a dog called Biscuit", ctx, now=NOW)
        assert ctx.token_map.version == version


class TestWhatIsNeverKept:

    @pytest.mark.parametrize("text", [
        "Lives at 12 Nathan Road",
        "Lives in Tai Po",
        "Is treated at Queen Mary Hospital",
        "Phone number is 9876 5432",
        "Email is ah.ming@example.com",
        "HKID is A123456(7)",
    ])
    def test_identifying_details_written_out_in_full(self, text):
        assert screen_memory(text, context(), now=NOW) is None

    def test_a_blocked_placeholder_rejects_the_note(self):
        ctx = context()
        scrubbed = ctx.scrub("I live at 12 Nathan Road", now=NOW).text
        token = scrubbed.split("at ", 1)[1].rstrip(".")
        assert token.startswith("[ADDRESS_")
        assert screen_memory(f"Lives at {token}", ctx, now=NOW) is None

    def test_a_placeholder_the_model_invented_rejects_the_note(self):
        assert screen_memory("Sister [PERSON_9] visited", context(), now=NOW) is None

    def test_the_patients_own_contact_details(self):
        assert screen_memory("Can be reached on 91234567", context(), now=NOW) is None
        assert screen_memory("Uses grace@example.com", context(), now=NOW) is None

    def test_empty_text(self):
        assert screen_memory("   ", context(), now=NOW) is None


class TestPlanning:

    def op(self, kind="add", ref=None, text=None, category="pet", durability="long"):
        return MemoryOp(op=kind, ref=ref, text=text, category=category, durability=durability)

    def test_refs_the_model_was_not_given_are_ignored(self):
        ops = [self.op("forget", ref="m7"), self.op("update", ref="m9", text="Has two dogs")]
        assert plan_memory_ops(ops, context(), {"m1": "mem_a"}, now=NOW) == []

    def test_forget_and_add_and_a_dropped_note(self):
        ops = [self.op("forget", ref="m1", text=None, category=None, durability=None),
               self.op("add", text="Has a cat called Mochi"),
               self.op("add", text="Lives at 12 Nathan Road")]
        plan = plan_memory_ops(ops, context(), {"m1": "mem_a"}, now=NOW)
        assert [(p.op, p.memory_id) for p in plan] == [("forget", "mem_a"), ("add", None)]
        assert plan[1].memory.text == "Has a cat called Mochi"


class TestRecall:

    def test_notes_render_as_one_developer_message_with_relative_ages(self):
        snapshot = [{"text": "Has a dog called Biscuit", "captured_at": (NOW - timedelta(days=8)).isoformat()},
                    {"text": "Had congee for dinner", "captured_at": (NOW - timedelta(days=1)).isoformat()}]
        [message] = memory_messages(snapshot, NOW, "en")
        assert message["role"] == "developer"
        assert "- Has a dog called Biscuit (8 days ago)" in message["content"]
        assert "- Had congee for dinner (yesterday)" in message["content"]

    def test_cantonese_ages(self):
        snapshot = [{"text": "養咗隻狗叫 Biscuit", "captured_at": (NOW - timedelta(days=21)).isoformat()}]
        assert "3個星期前" in memory_messages(snapshot, NOW, "zh-HK")[0]["content"]

    def test_no_notes_no_message(self):
        assert memory_messages([], NOW, "en") == []

    def test_seeding_teaches_the_session_a_name_with_no_cue(self):
        ctx = context()
        assert "Mei Ling" in ctx.scrub("Mei Ling came over", now=NOW).text   # no cue word: not caught
        seed_token_map(ctx.token_map, [{"entities": [{"cls": "PERSON", "original": "Mei Ling"},
                                                     {"cls": "DATE", "original": "Sunday"},
                                                     {"cls": "NONSENSE", "original": "x"}]}])
        assert "Mei Ling" not in ctx.scrub("Mei Ling came over", now=NOW).text
        assert "Mei Ling" in ctx.leak_forms()
        assert all(cls in ("PERSON",) for _, cls, _ in ctx.token_map.entries())


class TestStorage:

    def test_one_document_per_patient_and_the_version_moves(self):
        db = MockDatabase()
        mutate_memories(db, "demo", lambda m: m.add(planned("Has a dog called Biscuit"), source_session_id="s1", now=NOW), now=NOW)
        mutate_memories(db, "demo", lambda m: m.add(planned("Has a cat called Mochi"), source_session_id="s2", now=NOW), now=NOW)
        assert db[MEMORIES].count_documents({"user_id": "demo"}) == 1
        doc = db[MEMORIES].find_one({"user_id": "demo"})
        assert doc["version"] == 2
        assert [m["text"] for m in doc["memories"]] == ["Has a dog called Biscuit", "Has a cat called Mochi"]

    def test_saying_it_again_refreshes_rather_than_duplicates(self):
        db = MockDatabase()
        for session in ("s1", "s2"):
            mutate_memories(db, "demo", lambda m: m.add(planned("Has a dog called Biscuit"), source_session_id=session, now=NOW), now=NOW)
        assert len(PatientMemories.load(db, "demo").memories) == 1

    def test_expired_notes_are_not_recalled_and_are_pruned_on_write(self):
        db = MockDatabase()
        past = NOW - timedelta(days=8)
        mutate_memories(db, "demo", lambda m: m.add(planned("Had congee", durability="short"), source_session_id="s1", now=past), now=past)
        memories = PatientMemories.load(db, "demo")
        assert memories.recent(NOW) == []
        mutate_memories(db, "demo", lambda m: m.add(planned("Has a dog"), source_session_id="s2", now=NOW), now=NOW)
        assert [m["text"] for m in PatientMemories.load(db, "demo").memories] == ["Has a dog"]

    def test_the_store_is_capped_keeping_the_most_recent(self):
        db = MockDatabase()

        def many(m):
            for i in range(MAX_MEMORIES + 5):
                m.add(planned(f"Fact {i}"), source_session_id="s", now=NOW + timedelta(seconds=i))
        mutate_memories(db, "demo", many, now=NOW)
        texts = [m["text"] for m in PatientMemories.load(db, "demo").memories]
        assert len(texts) == MAX_MEMORIES
        assert "Fact 0" not in texts and f"Fact {MAX_MEMORIES + 4}" in texts

    def test_a_lost_race_reloads_and_keeps_both_writes(self):
        db = MockDatabase()
        mutate_memories(db, "demo", lambda m: m.add(planned("Has a dog"), source_session_id="s0", now=NOW), now=NOW)
        attempts = []

        def change(m):
            if not attempts:   # another check-in finishes in between
                other = PatientMemories.load(db, "demo")
                other.add(planned("Likes mahjong", category="hobby"), source_session_id="s1", now=NOW)
                assert other.save(db, now=NOW)
            attempts.append(1)
            m.add(planned("Has a cat"), source_session_id="s2", now=NOW)

        assert mutate_memories(db, "demo", change, now=NOW) is not None
        assert len(attempts) == 2
        assert {m["text"] for m in PatientMemories.load(db, "demo").memories} == {"Has a dog", "Likes mahjong", "Has a cat"}

    def test_a_first_write_that_loses_the_insert_race_reports_it(self):
        class Collection:
            def insert_one(self, doc):
                raise DuplicateKeyError("dup")

        class Database:
            def __getitem__(self, name):
                return Collection()

        assert PatientMemories("demo").save(Database(), now=NOW) is False

    def test_nothing_is_applied_once_the_patient_switched_memory_off(self):
        memories = PatientMemories("demo", enabled=False)
        apply_memory_ops(memories, [planned("Has a dog")], source_session_id="s1", now=NOW)
        assert memories.memories == []
