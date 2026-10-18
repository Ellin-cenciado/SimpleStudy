"""Application service: wires the pure tracker to config, state, history.

``StudyService`` is intentionally free of GTK and D-Bus so it can be unit
tested; the D-Bus/GLib layer in :mod:`simplestudy.daemon.app` drives it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.clock import Clock, SystemClock
from ..core.ledger import DayResult
from ..core.pomodoro import Phase, PomodoroConfig
from ..core.schedule import SECONDS_PER_HOUR, WEEKDAY_NAMES
from ..core.tracker import SettledDay, Snapshot, Tracker
from ..storage import state as state_mod
from ..storage.config import Config
from ..storage.history import History
from ..storage.paths import history_path, state_path
from .notify import Notifier, NullNotifier


def format_hours(seconds: int) -> str:
    """Format seconds as e.g. ``4h 00m`` or ``1h 30m``."""
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    hours, rem = divmod(seconds, SECONDS_PER_HOUR)
    minutes = rem // 60
    return f"{sign}{hours}h {minutes:02d}m"


@dataclass(frozen=True)
class Event:
    kind: str  # "phase" | "day_complete" | "day_rolled"
    summary: str
    body: str
    urgent: bool = False


class StudyService:
    def __init__(
        self,
        config: Config,
        clock: Clock | None = None,
        *,
        state_file: Path | None = None,
        history_file: Path | None = None,
        notifier: Notifier | None = None,
        persist: bool = True,
        persist_interval: float = 15.0,
    ) -> None:
        self.config = config
        self.clock = clock or SystemClock()
        self.state_file = state_file or state_path()
        self.history_file = history_file or history_path()
        self.notifier = notifier or NullNotifier()
        self.persist = persist
        self.persist_interval = persist_interval

        self.history: History | None = None
        self._last_persist = 0.0
        self._was_complete = False

        self._build_tracker()
        # Record any days that were settled while the daemon was not running.
        self._record_settled(self.tracker.last_settled)
        self._record_current()
        self._was_complete = self.tracker.day_complete

    # -- construction ---------------------------------------------------

    def _build_tracker(self) -> None:
        saved = state_mod.load(self.state_file) if self.persist else None
        self.tracker = Tracker(
            clock=self.clock,
            schedule=self.config.schedule,
            pomodoro=PomodoroConfig(
                study_seconds=self.config.study_seconds,
                break_seconds=self.config.break_seconds,
            ),
            boundary=self.config.boundary,
            state=saved,
        )
        if self.persist:
            self._ensure_history()

    def _ensure_history(self) -> None:
        if self.history is None:
            self.history = History(self.history_file)

    # -- control API ----------------------------------------------------

    def snapshot(self) -> Snapshot:
        return self.tracker.snapshot()

    def start(self) -> None:
        self.tracker.start()
        self._was_complete = self.tracker.day_complete
        self._record_current()
        self._persist(force=True)

    def pause(self) -> None:
        self.tracker.pause()
        self.tracker.sync()
        self._record_current()
        self._persist(force=True)

    def skip_phase(self) -> None:
        self.tracker.skip_phase()
        self._record_current()
        self._persist(force=True)

    def reload_config(self, config: Config) -> None:
        """Apply a new configuration.

        Pomodoro lengths and the day boundary take effect immediately; schedule
        changes apply to the day in progress as well (the simplest behaviour -
        note this can change today's remaining time).
        """
        self.config = config
        self.tracker.schedule = config.schedule
        self.tracker.pomodoro = PomodoroConfig(
            study_seconds=config.study_seconds,
            break_seconds=config.break_seconds,
        )
        self.tracker.boundary = config.boundary
        self.tracker.sync()
        self._record_current()
        self._persist(force=True)

    def tick(self) -> list[Event]:
        """Advance to the present, persist, and return any events."""
        transitions = self.tracker.accrue()
        settled = self.tracker.sync()

        events: list[Event] = []
        if settled:
            self._record_settled(settled)
            for day in settled:
                events.append(self._day_rolled_event(day))

        for phase in transitions:
            events.append(self._phase_event(phase))

        now_complete = self.tracker.day_complete
        if now_complete and not self._was_complete:
            events.append(
                Event(
                    kind="day_complete",
                    summary="Day complete",
                    body="You've finished today's study goal. Nice work!",
                    urgent=True,
                )
            )
        self._was_complete = now_complete

        self._record_current()
        self._maybe_persist()
        self._emit(events)
        return events

    # -- persistence / history -----------------------------------------

    def _persist(self, *, force: bool = False) -> None:
        if not self.persist:
            return
        if force or (self.clock.monotonic() - self._last_persist) >= self.persist_interval:
            state_mod.save(self.state_file, self.tracker.state)
            self._last_persist = self.clock.monotonic()

    def _maybe_persist(self) -> None:
        self._persist(force=False)

    def _record_current(self) -> None:
        if not self.persist:
            return
        result = self.tracker.current_result()
        policy = self.tracker.policy
        self._upsert(
            result,
            weekday=WEEKDAY_NAMES[self.tracker.state.logical_day.weekday()],
            day_kind=policy.kind.value,
            quota_seconds=policy.quota_seconds,
            study_seconds=self.tracker.state.study_seconds,
            break_seconds=self.tracker.state.break_seconds,
        )

    def _record_settled(self, settled: list[SettledDay]) -> None:
        if not self.persist:
            return
        for day in settled:
            self._upsert(
                day.result,
                weekday=day.weekday,
                day_kind=day.day_kind,
                quota_seconds=day.quota_seconds,
                study_seconds=day.study_seconds,
                break_seconds=day.break_seconds,
            )

    def _upsert(
        self,
        result: DayResult,
        *,
        weekday: str,
        day_kind: str,
        quota_seconds: int,
        study_seconds: int,
        break_seconds: int,
    ) -> None:
        self._ensure_history()
        assert self.history is not None
        self.history.upsert_result(
            result,
            weekday=weekday,
            day_kind=day_kind,
            quota_seconds=quota_seconds,
            study_seconds=study_seconds,
            break_seconds=break_seconds,
        )

    # -- events / notifications ----------------------------------------

    def _phase_event(self, phase: Phase) -> Event:
        if phase is Phase.BREAK:
            length = self.tracker.pomodoro.break_seconds // 60
            return Event(
                kind="phase",
                summary="Break time",
                body=f"Step away for {length} minutes.",
            )
        return Event(
            kind="phase",
            summary="Back to studying",
            body="Break's over - let's go.",
        )

    def _day_rolled_event(self, day: SettledDay) -> Event:
        debt = day.result.debt_out
        if debt > 0:
            body = f"{format_hours(debt)} carried over."
        else:
            body = "Nothing carried over."
        return Event(
            kind="day_rolled",
            summary="A new day has started",
            body=f"Today's goal: {format_hours(self.tracker.required_seconds)}. {body}",
        )

    def _emit(self, events: list[Event]) -> None:
        if not self.config.notifications:
            return
        for event in events:
            self.notifier.notify(event.summary, event.body, urgent=event.urgent)

    def close(self) -> None:
        if self.history is not None:
            self.history.close()
            self.history = None
