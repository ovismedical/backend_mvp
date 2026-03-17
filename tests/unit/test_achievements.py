"""
Tests for app.achievements — streak-based achievement unlocking.
"""

import pytest
from app.achievements import (
    ACHIEVEMENT_DEFINITIONS,
    check_and_unlock_achievements,
)


class TestAchievementDefinitions:

    def test_definitions_exist(self):
        assert len(ACHIEVEMENT_DEFINITIONS) == 8

    def test_all_have_required_fields(self):
        for defn in ACHIEVEMENT_DEFINITIONS:
            assert "id" in defn
            assert "type" in defn
            assert "threshold" in defn
            assert "title" in defn

    def test_thresholds_are_sorted(self):
        streak_thresholds = [
            d["threshold"] for d in ACHIEVEMENT_DEFINITIONS if d["type"] == "streak"
        ]
        assert streak_thresholds == sorted(streak_thresholds)


class TestCheckAndUnlockAchievements:

    def test_no_achievements_at_zero(self, mock_db):
        newly = check_and_unlock_achievements(mock_db, "alice", 0)
        assert newly == []

    def test_unlocks_7day_at_streak_7(self, mock_db):
        newly = check_and_unlock_achievements(mock_db, "alice", 7)
        assert "7daystreak" in newly

    def test_unlocks_multiple_at_streak_14(self, mock_db):
        newly = check_and_unlock_achievements(mock_db, "alice", 14)
        assert "7daystreak" in newly
        assert "day10" in newly
        assert "14daystreak" in newly

    def test_no_double_unlock(self, mock_db):
        # First call unlocks
        first = check_and_unlock_achievements(mock_db, "alice", 7)
        assert "7daystreak" in first
        # Second call should not re-unlock
        second = check_and_unlock_achievements(mock_db, "alice", 7)
        assert "7daystreak" not in second

    def test_unlocks_all_at_50(self, mock_db):
        newly = check_and_unlock_achievements(mock_db, "alice", 50)
        assert len(newly) == 8  # All 8 achievements

    def test_milestone_25logs(self, mock_db):
        newly = check_and_unlock_achievements(mock_db, "alice", 25)
        assert "25logs" in newly

    def test_streak_6_unlocks_nothing(self, mock_db):
        newly = check_and_unlock_achievements(mock_db, "alice", 6)
        assert newly == []

    def test_different_users_independent(self, mock_db):
        check_and_unlock_achievements(mock_db, "alice", 7)
        newly_bob = check_and_unlock_achievements(mock_db, "bob", 7)
        assert "7daystreak" in newly_bob  # Bob's first time
