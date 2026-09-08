"""Streak day boundaries follow the patient's timezone (X-Timezone header), not UTC."""

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.achievements import update_daily_streak, streak_is_current, patient_now
from tests.conftest import MockDatabase
from tests.factories import make_user


@pytest.fixture
def db():
    d = MockDatabase()
    d["users"].insert_one(make_user())
    return d


def test_patient_now_uses_iana_zone_and_falls_back_to_utc():
    hk = patient_now("Asia/Hong_Kong")
    assert hk.tzinfo is not None and hk.utcoffset() == timedelta(hours=8)
    assert patient_now("Not/AZone").tzinfo == timezone.utc
    assert patient_now(None).tzinfo == timezone.utc


def test_streak_records_patient_local_date(db):
    tz = "Asia/Hong_Kong"
    streak, _ = update_daily_streak(db, "testpatient", tz)
    user = db["users"].find_one({"username": "testpatient"})
    assert streak == 1
    assert user["last_completion"] == datetime.now(ZoneInfo(tz)).strftime("%m/%d/%Y")


def test_same_day_double_submit_does_not_increment(db):
    update_daily_streak(db, "testpatient", "Asia/Hong_Kong")
    streak, unlocked = update_daily_streak(db, "testpatient", "Asia/Hong_Kong")
    assert streak == 1 and unlocked == []


def test_streak_is_current_evaluated_in_patient_zone():
    tz = ZoneInfo("Pacific/Kiritimati")  # UTC+14: "today" here can be tomorrow in UTC
    local_today = datetime.now(tz).strftime("%m/%d/%Y")
    assert streak_is_current(local_today, "Pacific/Kiritimati") is True
    two_days_ago = (datetime.now(tz) - timedelta(days=2)).strftime("%m/%d/%Y")
    assert streak_is_current(two_days_ago, "Pacific/Kiritimati") is False
    assert streak_is_current(None, "Asia/Hong_Kong") is False
    assert streak_is_current("garbage", "Asia/Hong_Kong") is False
