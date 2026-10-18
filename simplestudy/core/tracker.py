"""The tracker: ledger + pomodoro + clock, with no IO.

``Tracker`` owns the mutable study state and knows how to advance it in
response to the passage of time. It never touches files, D-Bus or GTK; callers
persist :attr:`Tracker.state` and forward the events it reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .clock import Clock
from .ledger import DayResult, settle
from .pomodoro import (
    Phase,
    PomodoroConfig,
    advance,
    phase_length,
)
from .schedule import WEEKDAY_NAMES, DayKind, Schedule


def logical_day_for(now: datetime, boundary: time) -> date:
    """The logical day for ``now`` given the roll-over ``boundary``.

    With the default 04:00 boundary, 03:30 on the 15th still belongs to the
    14th, while 04:00 on the 15th starts the 15th.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    shift = timedelta(
        hours=boundary.hour, minutes=boundary.minute, seconds=boundary.second
    )
    return (now - shift).date()


@dataclass
class TrackerState:
    """Serialisable snapshot of the tracker's mutable state."""

    logical_day: date
    debt_seconds: int = 0
    elapsed_seconds: int = 0
    study_seconds: int = 0
    break_seconds: int = 0
    phase: Phase = Phase.IDLE
    phase_elapsed_seconds: int = 0
    running: bool = False


@dataclass(frozen=True)
class SettledDay:
    """A completed logical day, with the study/break split it ended with."""

    result: DayResult
    quota_seconds: int
    day_kind: str
    weekday: str
    study_seconds: int
    break_seconds: int


@dataclass
class Snapshot:
    """A read-only view of the tracker, handy for UIs and D-Bus properties."""

    logical_day: date
    weekday: str
    day_kind: str
    debt_seconds: int
    elapsed_seconds: int
    required_seconds: int
    remaining_seconds: int
    optional_goal_seconds: int
    optional_done_seconds: int
    phase: str
    phase_elapsed_seconds: int
    phase_length_seconds: int
    phase_remaining_seconds: int
    running: bool
    day_complete: bool
    is_free_day: bool


class Tracker:
    def __init__(
        self,
        clock: Clock,
        schedule: Schedule,
        pomodoro: PomodoroConfig,
        boundary: time = time(4, 0),
        state: TrackerState | None = None,
    ) -> None:
        self.clock = clock
        self.schedule = schedule
        self.pomodoro = pomodoro
        self.boundary = boundary
        #: Fractional seconds not yet promoted to whole seconds.
        self._frac = 0.0
        self._last_monotonic = clock.monotonic()
        #: Settled days reported by the most recent :meth:`sync`.
        self.last_settled: list[SettledDay] = []
        if state is None:
            self.new_state()
        else:
            self.load_state(state)

    def load_state(self, state: TrackerState) -> None:
        """Replace the current state (e.g. restored from disk) and resync."""
        self.state = state
        self._frac = 0.0
        self._last_monotonic = self.clock.monotonic()
        self.sync()

    def new_state(self) -> None:
        today = logical_day_for(self.clock.now(), self.boundary)
        self.state = TrackerState(logical_day=today)
        self._frac = 0.0
        self._last_monotonic = self.clock.monotonic()

    # -- derived values -------------------------------------------------

    @property
    def policy(self):
        return self.schedule.policy(self.state.logical_day)

    @property
    def is_free_day(self) -> bool:
        return self.policy.kind is DayKind.FREE

    @property
    def required_seconds(self) -> int:
        if self.is_free_day:
            return 0
        return self.state.debt_seconds + self.policy.quota_seconds

    @property
    def remaining_seconds(self) -> int:
        return max(0, self.required_seconds - self.state.elapsed_seconds)

    @property
    def day_complete(self) -> bool:
        # A free day never "completes"; study on it is purely optional and only
        # serves to pay down debt.
        if self.is_free_day:
            return False
        return self.state.elapsed_seconds >= self.required_seconds

    @property
    def optional_goal_seconds(self) -> int:
        if self.policy.kind is DayKind.OPTIONAL:
            return self.policy.quota_seconds
        return 0

    @property
    def optional_done_seconds(self) -> int:
        if self.policy.kind is not DayKind.OPTIONAL:
            return 0
        beyond_debt = max(0, self.state.elapsed_seconds - self.state.debt_seconds)
        return min(self.optional_goal_seconds, beyond_debt)

    @property
    def phase_length_seconds(self) -> int:
        return phase_length(self.pomodoro, self.state.phase)

    @property
    def phase_remaining_seconds(self) -> int:
        return max(0, self.phase_length_seconds - self.state.phase_elapsed_seconds)

    # -- control --------------------------------------------------------

    def sync(self) -> list[SettledDay]:
        """Roll the ledger forward to the current logical day.

        Returns one :class:`SettledDay` per *completed* logical day that was
        settled since the state was last in sync (oldest first).
        """
        today = logical_day_for(self.clock.now(), self.boundary)
        results: list[SettledDay] = []
        if today <= self.state.logical_day:
            return results

        while self.state.logical_day < today:
            policy = self.schedule.policy(self.state.logical_day)
            result = settle(
                policy,
                self.state.logical_day,
                self.state.debt_seconds,
                self.state.elapsed_seconds,
            )
            results.append(
                SettledDay(
                    result=result,
                    quota_seconds=policy.quota_seconds,
                    day_kind=policy.kind.value,
                    weekday=WEEKDAY_NAMES[self.state.logical_day.weekday()],
                    study_seconds=self.state.study_seconds,
                    break_seconds=self.state.break_seconds,
                )
            )
            self.state.debt_seconds = result.debt_out
            self.state.elapsed_seconds = 0
            self.state.study_seconds = 0
            self.state.break_seconds = 0
            self.state.logical_day = self.state.logical_day + timedelta(days=1)
            self.state.phase_elapsed_seconds = 0

        # A new day resets the pomodoro loop and stops the session.
        self.state.phase = Phase.IDLE
        self.state.running = False
        self._frac = 0.0
        self.last_settled = results
        return results

    def current_result(self) -> DayResult:
        """The ledger result for the day in progress (not yet settled)."""
        return settle(
            self.policy,
            self.state.logical_day,
            self.state.debt_seconds,
            self.state.elapsed_seconds,
        )

    def start(self) -> list[Phase]:
        """Begin (or resume) a session. Returns any phase transitions."""
        self._accrue_to_now()
        self.sync()
        if self.day_complete:
            return []
        if self.state.phase in (Phase.IDLE, Phase.COMPLETE):
            self.state.phase = Phase.STUDY
            self.state.phase_elapsed_seconds = 0
        self.state.running = True
        self._last_monotonic = self.clock.monotonic()
        return []

    def pause(self) -> None:
        self._accrue_to_now()
        self.state.running = False

    def skip_phase(self) -> Phase:
        """Jump to the next pomodoro phase without crediting extra time."""
        self._accrue_to_now()
        if self.state.phase is Phase.STUDY:
            self.state.phase = Phase.BREAK
        elif self.state.phase is Phase.BREAK:
            self.state.phase = Phase.STUDY
        self.state.phase_elapsed_seconds = 0
        self._last_monotonic = self.clock.monotonic()
        return self.state.phase

    def accrue(self) -> list[Phase]:
        """Credit run-time up to the present without rolling the day."""
        return self._accrue_to_now()

    def tick(self) -> list[Phase]:
        """Advance time up to the present. Returns phase transitions."""
        transitions = self._accrue_to_now()
        self.sync()
        return transitions

    # -- internals ------------------------------------------------------

    def _accrue_to_now(self) -> list[Phase]:
        now = self.clock.monotonic()
        transitions: list[Phase] = []
        if self.state.running:
            delta = now - self._last_monotonic
            if delta > 0:
                transitions = self._accrue(delta)
        self._last_monotonic = now
        return transitions

    def _accrue(self, delta: float) -> list[Phase]:
        self._frac += delta
        whole = int(self._frac)
        if whole <= 0:
            return []
        self._frac -= whole

        result = advance(
            self.state.phase,
            self.state.phase_elapsed_seconds,
            whole,
            self.pomodoro,
        )
        self.state.phase = result.phase
        self.state.phase_elapsed_seconds = result.phase_elapsed
        self.state.elapsed_seconds += whole
        self.state.study_seconds += result.study_seconds
        self.state.break_seconds += result.break_seconds

        if self.day_complete:
            self.state.phase = Phase.COMPLETE
            self.state.running = False
        return list(result.transitions)

    def snapshot(self) -> Snapshot:
        return Snapshot(
            logical_day=self.state.logical_day,
            weekday=WEEKDAY_NAMES[self.state.logical_day.weekday()],
            day_kind=self.policy.kind.value,
            debt_seconds=self.state.debt_seconds,
            elapsed_seconds=self.state.elapsed_seconds,
            required_seconds=self.required_seconds,
            remaining_seconds=self.remaining_seconds,
            optional_goal_seconds=self.optional_goal_seconds,
            optional_done_seconds=self.optional_done_seconds,
            phase=self.state.phase.value,
            phase_elapsed_seconds=self.state.phase_elapsed_seconds,
            phase_length_seconds=self.phase_length_seconds,
            phase_remaining_seconds=self.phase_remaining_seconds,
            running=self.state.running,
            day_complete=self.day_complete,
            is_free_day=self.is_free_day,
        )
