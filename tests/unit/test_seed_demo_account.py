"""The demo persona seed scripts specific days; the alert level must follow the scripted severities."""

from datetime import datetime, timezone

from scripts import seed_demo
from scripts import seed_demo_account as demo


def test_explicit_severities_drive_the_alert_level():
    created = datetime(2026, 9, 1, 8, 15, tzinfo=timezone.utc)
    doc = seed_demo.florence_record("demo", created, baseline=2.0, in_remission=False,
                                    severities=dict(fatigue=4, nausea=4, lack_of_appetite=3, cough=1, pain=2))
    assert doc["alert_level"] == "ORANGE"
    assert doc["flag_for_oncologist"] is True
    assert doc["triage_assessment"]["recommended_timeline"] == "Same-day clinical review"
    assert doc["structured_assessment"]["symptoms"]["fatigue"]["severity_rating"] == 4
    assert doc["structured_assessment"]["symptoms"]["cough"]["severity_rating"] == 1


def test_demo_story_has_exactly_one_flare_and_nothing_today():
    levels = {}
    for days_ago, sev in demo.FLORENCE_DAYS.items():
        doc = seed_demo.florence_record("demo", seed_demo.utc(days_ago), 2.0, False, severities=sev)
        levels[days_ago] = doc["alert_level"]
    assert [d for d, lvl in levels.items() if lvl in ("ORANGE", "RED")] == [9]
    assert 0 not in demo.FLORENCE_DAYS and 0 not in demo.QUESTIONNAIRE_DAYS
    assert demo.DEMO["doctor"] == seed_demo.DOCTOR["username"]
