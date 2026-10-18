from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from simplestudy.core.ledger import DayResult
from simplestudy.storage.history import History


def make_result(logical_date: date, elapsed: int, required: int, debt_out: int) -> DayResult:
    return DayResult(
        logical_date=logical_date,
        debt_in=0,
        required=required,
        elapsed=elapsed,
        debt_out=debt_out,
        optional_goal=0,
        optional_done=0,
    )


class HistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.history = History(Path(self._tmp.name) / "history.db")

    def tearDown(self) -> None:
        self.history.close()
        self._tmp.cleanup()

    def test_upsert_and_read_back(self):
        day = date(2026, 10, 12)
        result = make_result(day, elapsed=2 * 3600, required=4 * 3600, debt_out=2 * 3600)
        self.history.upsert_result(
            result,
            weekday="monday",
            day_kind="carried",
            quota_seconds=4 * 3600,
            study_seconds=6000,
            break_seconds=1200,
        )
        row = self.history.day(day)
        assert row is not None
        self.assertEqual(row.elapsed_seconds, 2 * 3600)
        self.assertEqual(row.debt_out_seconds, 2 * 3600)
        self.assertEqual(row.study_seconds, 6000)
        self.assertEqual(row.break_seconds, 1200)
        self.assertFalse(row.completed)

    def test_upsert_is_idempotent_on_same_day(self):
        day = date(2026, 10, 12)
        for elapsed in (3600, 2 * 3600, 3 * 3600):
            result = make_result(day, elapsed=elapsed, required=4 * 3600, debt_out=4 * 3600 - elapsed)
            self.history.upsert_result(
                result, weekday="monday", day_kind="carried", quota_seconds=4 * 3600
            )
        self.assertEqual(len(self.history.all_days()), 1)
        row = self.history.day(day)
        assert row is not None
        self.assertEqual(row.elapsed_seconds, 3 * 3600)

    def test_range_query_is_ordered(self):
        for offset, day in enumerate(
            [date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)]
        ):
            self.history.upsert_result(
                make_result(day, elapsed=offset * 3600, required=4 * 3600, debt_out=0),
                weekday="monday",
                day_kind="carried",
                quota_seconds=4 * 3600,
            )
        rows = self.history.range(date(2026, 10, 12), date(2026, 10, 13))
        self.assertEqual([r.logical_date for r in rows], [date(2026, 10, 12), date(2026, 10, 13)])


if __name__ == "__main__":
    unittest.main()
