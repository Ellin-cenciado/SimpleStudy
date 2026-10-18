"""Stats page: reads the SQLite history and summarises progress."""

from __future__ import annotations

from datetime import date, timedelta

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk  # noqa: E402

from ..core.schedule import DayKind
from ..storage.history import DayRow, History
from ..storage.paths import history_path
from .client import DaemonClient


def current_streak(rows: list[DayRow]) -> int:
    """Number of consecutive completed study days, ignoring free days."""
    streak = 0
    for row in sorted(rows, key=lambda r: r.logical_date, reverse=True):
        if row.day_kind == DayKind.FREE.value:
            continue
        if row.required_seconds == 0:
            continue
        if row.completed:
            streak += 1
        else:
            break
    return streak


def compliance(rows: list[DayRow]) -> tuple[int, int]:
    """(completed, total) over non-free days with a requirement."""
    relevant = [r for r in rows if r.required_seconds > 0]
    return sum(1 for r in relevant if r.completed), len(relevant)


class StatsPage(Adw.PreferencesPage):
    def __init__(self, client: DaemonClient) -> None:
        super().__init__()
        self.client = client

        self.summary = Adw.PreferencesGroup(title="Progress")
        self.streak_row = Adw.ActionRow(title="Current streak")
        self.compliance_row = Adw.ActionRow(title="Completed (last 30 days)")
        self.debt_row = Adw.ActionRow(title="Debt carried")
        for row in (self.streak_row, self.compliance_row, self.debt_row):
            self.summary.add(row)
        self.add(self.summary)

        self.history_group = Adw.PreferencesGroup(title="Recent days")
        self.add(self.history_group)
        self._rows: list[Adw.ActionRow] = []

        self.connect("map", lambda *_: self.refresh())

    def refresh(self) -> None:
        path = history_path()
        if not path.exists():
            self._set_summary(0, 0, 0, 0)
            self._clear_rows()
            return

        history = History(path)
        try:
            rows = history.all_days()
        finally:
            history.close()

        recent = [r for r in rows if r.logical_date >= date.today() - timedelta(days=30)]
        completed, total = compliance(recent)
        debt = rows[-1].debt_out_seconds if rows else 0
        self._set_summary(current_streak(rows), completed, total, debt)
        self._render_rows(rows[-14:][::-1])

    def _set_summary(self, streak: int, completed: int, total: int, debt: int) -> None:
        self.streak_row.set_subtitle(f"{streak} day{'s' if streak != 1 else ''}")
        self.compliance_row.set_subtitle(f"{completed} / {total}" if total else "no data yet")
        self.debt_row.set_subtitle(_format_hours(debt))

    def _clear_rows(self) -> None:
        for row in self._rows:
            self.history_group.remove(row)
        self._rows = []

    def _render_rows(self, rows: list[DayRow]) -> None:
        self._clear_rows()
        if not rows:
            empty = Adw.ActionRow(title="No history yet")
            self.history_group.add(empty)
            self._rows.append(empty)
            return
        for row in rows:
            mark = "✓" if row.completed else ("–" if row.required_seconds == 0 else "✗")
            title = f"{row.logical_date.isoformat()}  {row.weekday.capitalize()}"
            subtitle = (
                f"{mark}  {_format_hours(row.elapsed_seconds)} / "
                f"{_format_hours(row.required_seconds)} studied"
            )
            if row.study_seconds or row.break_seconds:
                subtitle += (
                    f"  ·  {_format_hours(row.study_seconds)} study / "
                    f"{_format_hours(row.break_seconds)} break"
                )
            if row.debt_out_seconds:
                subtitle += f"  ·  debt {_format_hours(row.debt_out_seconds)}"
            action = Adw.ActionRow(title=title, subtitle=subtitle)
            self.history_group.add(action)
            self._rows.append(action)


def _format_hours(seconds: int) -> str:
    sign = "-" if seconds < 0 else ""
    seconds = abs(int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    return f"{sign}{hours}h {minutes:02d}m"
