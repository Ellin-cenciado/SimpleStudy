"""SQLite history for stats.

Two things are persisted here:

* one row per *logical day* summarising the ledger outcome, and
* the study/break split for the day (breaks count towards the requirement but
  are tracked separately so the stats view can show real study time).

This module is deliberately thin - it stores and returns rows; all decisions
live in the core.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ..core.ledger import DayResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS days (
    logical_date        TEXT PRIMARY KEY,
    weekday             TEXT    NOT NULL,
    day_kind            TEXT    NOT NULL,
    quota_seconds       INTEGER NOT NULL,
    debt_in_seconds     INTEGER NOT NULL,
    required_seconds    INTEGER NOT NULL,
    elapsed_seconds     INTEGER NOT NULL,
    debt_out_seconds    INTEGER NOT NULL,
    optional_goal_seconds INTEGER NOT NULL DEFAULT 0,
    optional_done_seconds INTEGER NOT NULL DEFAULT 0,
    study_seconds       INTEGER NOT NULL DEFAULT 0,
    break_seconds       INTEGER NOT NULL DEFAULT 0,
    completed           INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT    NOT NULL
);
"""


@dataclass(frozen=True)
class DayRow:
    logical_date: date
    weekday: str
    day_kind: str
    quota_seconds: int
    debt_in_seconds: int
    required_seconds: int
    elapsed_seconds: int
    debt_out_seconds: int
    optional_goal_seconds: int
    optional_done_seconds: int
    study_seconds: int
    break_seconds: int
    completed: bool


class History:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "History":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def upsert_result(
        self,
        result: DayResult,
        *,
        weekday: str,
        day_kind: str,
        quota_seconds: int,
        study_seconds: int = 0,
        break_seconds: int = 0,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO days (
                logical_date, weekday, day_kind, quota_seconds,
                debt_in_seconds, required_seconds, elapsed_seconds,
                debt_out_seconds, optional_goal_seconds, optional_done_seconds,
                study_seconds, break_seconds, completed, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(logical_date) DO UPDATE SET
                weekday=excluded.weekday,
                day_kind=excluded.day_kind,
                quota_seconds=excluded.quota_seconds,
                debt_in_seconds=excluded.debt_in_seconds,
                required_seconds=excluded.required_seconds,
                elapsed_seconds=excluded.elapsed_seconds,
                debt_out_seconds=excluded.debt_out_seconds,
                optional_goal_seconds=excluded.optional_goal_seconds,
                optional_done_seconds=excluded.optional_done_seconds,
                study_seconds=excluded.study_seconds,
                break_seconds=excluded.break_seconds,
                completed=excluded.completed,
                updated_at=excluded.updated_at
            """,
            (
                result.logical_date.isoformat(),
                weekday,
                day_kind,
                quota_seconds,
                result.debt_in,
                result.required,
                result.elapsed,
                result.debt_out,
                result.optional_goal,
                result.optional_done,
                study_seconds,
                break_seconds,
                1 if result.completed else 0,
                datetime.now().astimezone().isoformat(timespec="seconds"),
            ),
        )
        self._conn.commit()

    def day(self, logical_date: date) -> DayRow | None:
        row = self._conn.execute(
            "SELECT * FROM days WHERE logical_date = ?",
            (logical_date.isoformat(),),
        ).fetchone()
        return _row_to_day(row) if row else None

    def range(self, start: date, end: date) -> list[DayRow]:
        rows = self._conn.execute(
            "SELECT * FROM days WHERE logical_date BETWEEN ? AND ? "
            "ORDER BY logical_date",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [_row_to_day(r) for r in rows]

    def all_days(self) -> list[DayRow]:
        rows = self._conn.execute(
            "SELECT * FROM days ORDER BY logical_date"
        ).fetchall()
        return [_row_to_day(r) for r in rows]


def _row_to_day(row: sqlite3.Row) -> DayRow:
    return DayRow(
        logical_date=date.fromisoformat(row["logical_date"]),
        weekday=row["weekday"],
        day_kind=row["day_kind"],
        quota_seconds=row["quota_seconds"],
        debt_in_seconds=row["debt_in_seconds"],
        required_seconds=row["required_seconds"],
        elapsed_seconds=row["elapsed_seconds"],
        debt_out_seconds=row["debt_out_seconds"],
        optional_goal_seconds=row["optional_goal_seconds"],
        optional_done_seconds=row["optional_done_seconds"],
        study_seconds=row["study_seconds"],
        break_seconds=row["break_seconds"],
        completed=bool(row["completed"]),
    )
