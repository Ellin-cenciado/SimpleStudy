"""Application service: wires the pure tracker to config, state, history.

``StudyService`` is intentionally free of GTK and D-Bus so it can be unit
tested; the D-Bus/GLib layer in :mod:`simplestudy.daemon.app` drives it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..core.checklist import (
    SUBJECT_REST_SECONDS,
    AdvanceResult,
    ChecklistState,
)
from ..core.clock import Clock, SystemClock
from ..core.ledger import DayResult
from ..core.pomodoro import Phase, PomodoroConfig
from ..core.schedule import SECONDS_PER_HOUR, WEEKDAY_NAMES
from ..core.tracker import SettledDay, Snapshot, Tracker
from ..storage import checklist as checklist_mod
from ..storage import state as state_mod
from ..storage.config import Config
from ..storage.history import History
from ..storage.paths import checklist_path, history_path, state_path
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
    kind: str  # "phase" | "day_complete" | "day_rolled" | "item_expired" | ...
    summary: str
    body: str
    urgent: bool = False


@dataclass(frozen=True)
class ChecklistSummary:
    """Small live view of the checklist for the D-Bus snapshot."""

    active_subject_id: str = ""
    active_item_id: str = ""
    remaining_seconds: int = 0
    overrun_seconds: int = 0
    rest_remaining_seconds: int = 0
    revision: int = 0


class StudyService:
    def __init__(
        self,
        config: Config,
        clock: Clock | None = None,
        *,
        state_file: Path | None = None,
        history_file: Path | None = None,
        checklist_file: Path | None = None,
        notifier: Notifier | None = None,
        persist: bool = True,
        persist_interval: float = 15.0,
    ) -> None:
        self.config = config
        self.clock = clock or SystemClock()
        self.state_file = state_file or state_path()
        self.history_file = history_file or history_path()
        self.checklist_file = checklist_file or checklist_path()
        self.notifier = notifier or NullNotifier()
        self.persist = persist
        self.persist_interval = persist_interval

        self.history: History | None = None
        self._last_persist = 0.0
        self._was_complete = False
        self._checklist_revision = 0
        self._resting = False
        self._rest_subject_id: str | None = None

        self._build_tracker()
        self._build_checklist()
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

    def _build_checklist(self) -> None:
        if self.persist:
            self.checklist = checklist_mod.load(
                self.checklist_file, default_seconds=self.config.study_seconds
            )
        else:
            self.checklist = ChecklistState()
        self.checklist.ensure_active()
        # A persisted one-shot break means a subject rest was in progress (or
        # queued while paused) when the daemon stopped.
        self._resting = self.tracker.state.break_override_seconds > 0
        if self._resting:
            active = self.checklist.active_subject()
            self._rest_subject_id = active.id if active is not None else None

    def _save_checklist(self) -> None:
        if self.persist:
            checklist_mod.save(self.checklist_file, self.checklist)

    def _bump_checklist(self) -> None:
        self._checklist_revision += 1

    # -- checklist API --------------------------------------------------

    def checklist_json(self) -> str:
        data = checklist_mod.to_dict(self.checklist)
        # The revision is service-level (not persisted) but lets a client avoid
        # a second fetch when it receives both ChecklistChanged and Changed.
        data["revision"] = self._checklist_revision
        return json.dumps(data)

    def checklist_revision(self) -> int:
        return self._checklist_revision

    def checklist_summary(self) -> ChecklistSummary:
        subject = self.checklist.active_subject()
        item = self.checklist.active_item()
        rest = 0
        if self._resting or self.tracker.state.break_override_seconds > 0:
            if self.tracker.state.phase is Phase.BREAK:
                rest = self.tracker.phase_remaining_seconds
            else:
                rest = self.tracker.state.break_override_seconds
        return ChecklistSummary(
            active_subject_id=subject.id if subject is not None else "",
            active_item_id=item.id if item is not None else "",
            remaining_seconds=item.remaining_seconds if item is not None else 0,
            overrun_seconds=item.overrun_seconds if item is not None else 0,
            rest_remaining_seconds=max(0, rest),
            revision=self._checklist_revision,
        )

    def add_subject(self, name: str) -> str:
        subject = self.checklist.add_subject(name)
        self.checklist.ensure_active()
        self._checklist_changed()
        return subject.id

    def rename_subject(self, subject_id: str, name: str) -> None:
        self.checklist.rename_subject(subject_id, name)
        self._checklist_changed()

    def remove_subject(self, subject_id: str) -> None:
        if self._rest_subject_id == subject_id:
            self._cancel_rest()
        self.checklist.remove_subject(subject_id)
        self.checklist.ensure_active()
        self._checklist_changed()

    def select_subject(self, subject_id: str) -> None:
        self.checklist.select_subject(subject_id)
        self._checklist_changed()

    def add_item(self, subject_id: str, text: str, duration_seconds: int = 0) -> str:
        seconds = duration_seconds or self.config.study_seconds
        item = self.checklist.add_item(subject_id, text, seconds)
        if self._rest_subject_id == subject_id:
            # The subject is no longer complete; the pending rest is moot.
            self._cancel_rest()
        self.checklist.ensure_active()
        self._checklist_changed()
        return item.id

    def rename_item(self, item_id: str, text: str) -> None:
        self.checklist.rename_item(item_id, text)
        self._checklist_changed()

    def set_item_duration(self, item_id: str, seconds: int) -> None:
        self.checklist.set_item_duration(item_id, seconds)
        self._checklist_changed()

    def remove_item(self, item_id: str) -> None:
        self.checklist.remove_item(item_id)
        self._checklist_changed()

    def check_item(self, item_id: str) -> None:
        self._accrue_checklist()
        subject, _item, _index = self.checklist.find_item(item_id)
        if self.checklist.active_subject_id != subject.id:
            raise ValueError("item does not belong to the active subject")
        result = self.checklist.check_item(item_id)

        events: list[Event] = []
        if result.subject_completed:
            if not self.tracker.day_complete:
                self.tracker.start_rest(SUBJECT_REST_SECONDS)
                self._resting = True
                self._rest_subject_id = subject.id
                events.append(
                    Event(
                        kind="subject_complete",
                        summary=f"{subject.name} complete",
                        body="Take a 5-minute rest.",
                        urgent=True,
                    )
                )
            else:
                # The daily goal is already done; a rest has nothing to pause.
                next_subject = self.checklist.next_incomplete_subject(subject.id)
                self.checklist.active_subject_id = (
                    next_subject.id if next_subject is not None else None
                )
                events.append(
                    Event(
                        kind="subject_complete",
                        summary=f"{subject.name} complete",
                        body="Nice work - today's goal is done.",
                        urgent=True,
                    )
                )
        self._checklist_changed()
        self._emit(events)

    def uncheck_last_item(self, subject_id: str) -> None:
        self.checklist.uncheck_last(subject_id)
        if self._rest_subject_id == subject_id:
            self._cancel_rest()
        self.checklist.ensure_active()
        self._checklist_changed()

    def reset_subject(self, subject_id: str) -> None:
        self.checklist.reset_subject(subject_id)
        if self._rest_subject_id == subject_id:
            self._cancel_rest()
        self.checklist.ensure_active()
        self._checklist_changed()

    def _checklist_changed(self) -> None:
        self._bump_checklist()
        self._persist(force=True)

    def _cancel_rest(self) -> None:
        self._resting = False
        self._rest_subject_id = None
        if self.tracker.state.break_override_seconds:
            # Account for the rest time that has already passed *as break time*
            # before switching the phase back to study.
            self._accrue_checklist()
            self.tracker.state.break_override_seconds = 0
            if self.tracker.state.phase is Phase.BREAK:
                self.tracker.state.phase = Phase.STUDY
                self.tracker.state.phase_elapsed_seconds = 0

    def _finish_rest(self) -> None:
        if not self._resting:
            return
        self._resting = False
        rest_subject_id = self._rest_subject_id
        self._rest_subject_id = None
        # If the day completed mid-rest, the tracker may still hold the override.
        self.tracker.state.break_override_seconds = 0
        current = self.checklist.active_subject()
        if current is None or current.complete:
            next_subject = self.checklist.next_incomplete_subject(
                current.id if current is not None else rest_subject_id
            )
            self.checklist.active_subject_id = (
                next_subject.id if next_subject is not None else None
            )
        self._checklist_changed()

    # -- control API ----------------------------------------------------

    def snapshot(self) -> Snapshot:
        return self.tracker.snapshot()

    def start(self) -> None:
        self._accrue_checklist()
        self.tracker.start()
        self._was_complete = self.tracker.day_complete
        self._record_current()
        self._persist(force=True)

    def pause(self) -> None:
        self._accrue_checklist()
        self.tracker.pause()
        self.tracker.sync()
        self._record_current()
        self._persist(force=True)

    def skip_phase(self) -> None:
        self._accrue_checklist()
        self.tracker.skip_phase()
        if self._resting and self.tracker.state.break_override_seconds == 0:
            self._finish_rest()
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

    def _accrue_checklist(self) -> tuple[list[Phase], AdvanceResult | None]:
        """Accrue tracker time and credit the study part to the active item."""
        before = self.tracker.state.study_seconds
        transitions = self.tracker.accrue()
        delta = max(0, self.tracker.state.study_seconds - before)
        result = self.checklist.advance(delta) if delta > 0 else None
        return transitions, result

    def tick(self) -> list[Event]:
        """Advance to the present, persist, and return any events."""
        transitions, advance_result = self._accrue_checklist()
        settled = self.tracker.sync()

        events: list[Event] = []
        if advance_result is not None and advance_result.expired:
            events.append(self._item_expired_event(advance_result.item_id))
        if settled:
            self._record_settled(settled)
            for day in settled:
                events.append(self._day_rolled_event(day))

        rest_finished = False
        if self._resting and (
            self.tracker.state.break_override_seconds == 0
            or self.tracker.day_complete
        ):
            self._finish_rest()
            rest_finished = True
            events.append(
                Event(
                    kind="rest_over",
                    summary="Rest over",
                    body="The 5-minute rest is finished.",
                )
            )

        for phase in transitions:
            # The rest-over notification already covers this transition.
            if rest_finished and phase is Phase.STUDY:
                continue
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

    def _item_expired_event(self, item_id: str | None) -> Event:
        text = "Item"
        if item_id is not None:
            try:
                _subject, item, _index = self.checklist.find_item(item_id)
                text = item.text
            except ValueError:
                pass
        return Event(
            kind="item_expired",
            summary=f"Time's up: {text}",
            body="The item is now in overrun; it is counting up.",
        )

    # -- persistence / history -----------------------------------------

    def _persist(self, *, force: bool = False) -> None:
        if not self.persist:
            return
        if force or (self.clock.monotonic() - self._last_persist) >= self.persist_interval:
            state_mod.save(self.state_file, self.tracker.state)
            self._save_checklist()
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
