from __future__ import annotations

import unittest
from datetime import date

from simplestudy.core.ledger import settle
from simplestudy.core.schedule import (
    SECONDS_PER_HOUR,
    DayKind,
    DayPolicy,
    default_schedule,
)

HOUR = SECONDS_PER_HOUR


class SettleCarriedTest(unittest.TestCase):
    """Mon-Fri days: the day's quota is added and unmet time becomes debt."""

    def setUp(self) -> None:
        self.policy = DayPolicy(4 * HOUR, DayKind.CARRIED)
        self.day = date(2026, 10, 12)  # Monday

    def test_full_study_clears_everything(self):
        result = settle(self.policy, self.day, debt_in=0, elapsed=4 * HOUR)
        self.assertEqual(result.required, 4 * HOUR)
        self.assertEqual(result.debt_out, 0)
        self.assertTrue(result.completed)

    def test_zero_study_carries_full_quota(self):
        result = settle(self.policy, self.day, debt_in=0, elapsed=0)
        self.assertEqual(result.required, 4 * HOUR)
        self.assertEqual(result.debt_out, 4 * HOUR)
        self.assertFalse(result.completed)

    def test_partial_study_carries_remainder(self):
        result = settle(self.policy, self.day, debt_in=0, elapsed=2 * HOUR)
        self.assertEqual(result.debt_out, 2 * HOUR)

    def test_existing_debt_is_added_to_requirement(self):
        result = settle(self.policy, self.day, debt_in=2 * HOUR, elapsed=0)
        self.assertEqual(result.required, 6 * HOUR)
        self.assertEqual(result.debt_out, 6 * HOUR)

    def test_study_overflow_does_not_create_negative_debt(self):
        result = settle(self.policy, self.day, debt_in=2 * HOUR, elapsed=10 * HOUR)
        self.assertEqual(result.debt_out, 0)


class SettleOptionalTest(unittest.TestCase):
    """Saturday: debt is mandatory, the quota is optional and discarded."""

    def setUp(self) -> None:
        self.policy = DayPolicy(2 * HOUR, DayKind.OPTIONAL)
        self.day = date(2026, 10, 17)  # Saturday

    def test_requirement_is_debt_plus_optional_goal(self):
        result = settle(self.policy, self.day, debt_in=2 * HOUR, elapsed=0)
        self.assertEqual(result.required, 4 * HOUR)
        self.assertEqual(result.debt_out, 2 * HOUR)
        self.assertEqual(result.optional_goal, 2 * HOUR)
        self.assertEqual(result.optional_done, 0)

    def test_study_pays_debt_first(self):
        result = settle(self.policy, self.day, debt_in=2 * HOUR, elapsed=2 * HOUR)
        self.assertEqual(result.debt_out, 0)
        self.assertEqual(result.optional_done, 0)
        self.assertEqual(result.remaining, 2 * HOUR)
        self.assertFalse(result.completed)

    def test_optional_goal_is_discarded_when_unmet(self):
        # Debt cleared, optional goal untouched -> only the optional goal is lost.
        result = settle(self.policy, self.day, debt_in=0, elapsed=0)
        self.assertEqual(result.debt_out, 0)
        self.assertEqual(result.optional_done, 0)

    def test_full_optional_completion(self):
        result = settle(self.policy, self.day, debt_in=2 * HOUR, elapsed=4 * HOUR)
        self.assertEqual(result.debt_out, 0)
        self.assertEqual(result.optional_done, 2 * HOUR)
        self.assertTrue(result.completed)

    def test_partial_optional_progress(self):
        result = settle(self.policy, self.day, debt_in=0, elapsed=1 * HOUR)
        self.assertEqual(result.optional_done, 1 * HOUR)


class SettleFreeTest(unittest.TestCase):
    """Sunday: no requirement, debt is preserved."""

    def setUp(self) -> None:
        self.policy = DayPolicy(0, DayKind.FREE)
        self.day = date(2026, 10, 18)  # Sunday

    def test_no_requirement(self):
        result = settle(self.policy, self.day, debt_in=5 * HOUR, elapsed=0)
        self.assertEqual(result.required, 0)
        self.assertEqual(result.debt_out, 5 * HOUR)
        self.assertTrue(result.completed)

    def test_optional_study_pays_debt(self):
        result = settle(self.policy, self.day, debt_in=5 * HOUR, elapsed=2 * HOUR)
        self.assertEqual(result.debt_out, 3 * HOUR)


class DefaultScheduleTest(unittest.TestCase):
    def test_weekday_kinds(self):
        schedule = default_schedule()
        for weekday in range(5):
            policy = schedule.days[weekday]
            self.assertEqual(policy.quota_seconds, 4 * HOUR)
            self.assertEqual(policy.kind, DayKind.CARRIED)
        saturday = schedule.days[5]
        self.assertEqual(saturday.quota_seconds, 2 * HOUR)
        self.assertEqual(saturday.kind, DayKind.OPTIONAL)
        sunday = schedule.days[6]
        self.assertEqual(sunday.quota_seconds, 0)
        self.assertEqual(sunday.kind, DayKind.FREE)

    def test_free_day_cannot_have_quota(self):
        with self.assertRaises(ValueError):
            DayPolicy(HOUR, DayKind.FREE)

    def test_with_day_replaces_one(self):
        schedule = default_schedule().with_day(5, DayPolicy(0, DayKind.FREE))
        self.assertEqual(schedule.days[5].kind, DayKind.FREE)
        self.assertEqual(schedule.days[0].kind, DayKind.CARRIED)


if __name__ == "__main__":
    unittest.main()
