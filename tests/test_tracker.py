from __future__ import annotations

import unittest
from datetime import datetime, time, timedelta, timezone

from simplestudy.core.clock import FakeClock
from simplestudy.core.pomodoro import Phase, PomodoroConfig
from simplestudy.core.schedule import SECONDS_PER_HOUR, DayKind, DayPolicy, default_schedule
from simplestudy.core.tracker import Tracker, TrackerState, logical_day_for

HOUR = SECONDS_PER_HOUR
MINUTE = 60
TZ = timezone(timedelta(hours=2))  # fixed offset -> deterministic tests

# 2026-10-12 is a Monday.
MONDAY = (2026, 10, 12)
TUESDAY = (2026, 10, 13)
FRIDAY = (2026, 10, 16)
SATURDAY = (2026, 10, 17)
SUNDAY = (2026, 10, 18)
NEXT_MONDAY = (2026, 10, 19)
PREV_SUNDAY = (2026, 10, 11)


def at(ymd, hour=10, minute=0) -> datetime:
    return datetime(*ymd, hour, minute, tzinfo=TZ)


def make_tracker(clock, *, pomodoro=None, schedule=None, boundary=time(4, 0)) -> Tracker:
    return Tracker(
        clock=clock,
        schedule=schedule or default_schedule(),
        pomodoro=pomodoro or PomodoroConfig(),
        boundary=boundary,
    )


class LogicalDayTest(unittest.TestCase):
    def test_before_boundary_belongs_to_previous_day(self):
        # Monday 03:30 -> the previous Sunday (2026-10-11).
        self.assertEqual(logical_day_for(at(MONDAY, 3, 30), time(4, 0)),
                         at(PREV_SUNDAY).date())

    def test_at_boundary_starts_new_day(self):
        self.assertEqual(
            logical_day_for(at(MONDAY, 4, 0), time(4, 0)), at(MONDAY).date()
        )

    def test_midnight_boundary(self):
        self.assertEqual(
            logical_day_for(at(MONDAY, 0, 0), time(0, 0)), at(MONDAY).date()
        )


class CarryOverTest(unittest.TestCase):
    """The examples from the requirements."""

    def test_missed_day_adds_full_quota(self):
        clock = FakeClock(at(MONDAY))
        tracker = make_tracker(clock)
        self.assertEqual(tracker.required_seconds, 4 * HOUR)

        # Skip Monday entirely, arrive Tuesday.
        clock.set_wall(at(TUESDAY))
        tracker.sync()
        self.assertEqual(tracker.state.logical_day, at(TUESDAY).date())
        self.assertEqual(tracker.state.debt_seconds, 4 * HOUR)
        self.assertEqual(tracker.required_seconds, 8 * HOUR)

    def test_partial_day_carries_remainder(self):
        clock = FakeClock(at(MONDAY))
        tracker = make_tracker(clock)
        tracker.start()
        clock.advance(2 * HOUR)
        tracker.tick()
        self.assertEqual(tracker.state.elapsed_seconds, 2 * HOUR)

        tracker.pause()
        clock.set_wall(at(TUESDAY))
        tracker.sync()
        self.assertEqual(tracker.state.debt_seconds, 2 * HOUR)
        self.assertEqual(tracker.required_seconds, 6 * HOUR)


class WeekendTest(unittest.TestCase):
    def test_saturday_debt_priority_and_optional_discard(self):
        # Friday ends with 2h debt.
        tracker = make_tracker(FakeClock(at(FRIDAY)))
        tracker.start()
        clock = tracker.clock  # type: ignore[assignment]
        clock.advance(2 * HOUR)
        tracker.tick()
        tracker.pause()
        clock.set_wall(at(SATURDAY))
        tracker.sync()

        # Saturday requirement is debt(2h) + optional goal(2h).
        self.assertEqual(tracker.state.logical_day, at(SATURDAY).date())
        self.assertEqual(tracker.state.debt_seconds, 2 * HOUR)
        self.assertEqual(tracker.required_seconds, 4 * HOUR)
        self.assertEqual(tracker.optional_goal_seconds, 2 * HOUR)

        # Study exactly the debt -> debt cleared, optional goal discarded.
        tracker.start()
        clock.advance(2 * HOUR)
        tracker.tick()
        tracker.pause()

        clock.set_wall(at(SUNDAY))
        tracker.sync()
        clock.set_wall(at(NEXT_MONDAY))
        tracker.sync()

        self.assertEqual(tracker.state.logical_day, at(NEXT_MONDAY).date())
        self.assertEqual(tracker.state.debt_seconds, 0)
        self.assertEqual(tracker.required_seconds, 4 * HOUR)

    def test_saturday_unstudied_debt_carries_to_monday(self):
        # Friday: study 2h of 4h -> 2h debt carried into the weekend.
        clock = FakeClock(at(FRIDAY))
        tracker = make_tracker(clock)
        tracker.start()
        clock.advance(2 * HOUR)
        tracker.tick()
        tracker.pause()

        clock.set_wall(at(SATURDAY))
        tracker.sync()
        # Saturday: 2h debt (mandatory) + 2h optional goal.
        self.assertEqual(tracker.state.debt_seconds, 2 * HOUR)
        self.assertEqual(tracker.required_seconds, 4 * HOUR)

        clock.set_wall(at(SUNDAY))
        tracker.sync()
        self.assertEqual(tracker.state.debt_seconds, 2 * HOUR)  # preserved
        self.assertEqual(tracker.required_seconds, 0)

        clock.set_wall(at(NEXT_MONDAY))
        tracker.sync()
        self.assertEqual(tracker.state.logical_day, at(NEXT_MONDAY).date())
        self.assertEqual(tracker.required_seconds, 6 * HOUR)  # 4h + 2h debt

    def test_sunday_preserves_debt(self):
        clock = FakeClock(at(FRIDAY))
        tracker = make_tracker(clock)  # no study Fri -> 4h debt
        clock.set_wall(at(SATURDAY))
        tracker.sync()  # debt 4h (Fri) + optional Sat
        clock.set_wall(at(SUNDAY))
        tracker.sync()
        self.assertEqual(tracker.state.logical_day, at(SUNDAY).date())
        self.assertEqual(tracker.state.debt_seconds, 4 * HOUR)
        self.assertEqual(tracker.required_seconds, 0)
        self.assertTrue(tracker.is_free_day)


class PomodoroTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pomodoro = PomodoroConfig(study_seconds=40 * MINUTE, break_seconds=10 * MINUTE)
        self.clock = FakeClock(at(MONDAY))
        self.tracker = make_tracker(
            self.clock,
            pomodoro=self.pomodoro,
            schedule=default_schedule().with_day(
                0, DayPolicy(100 * HOUR, DayKind.CARRIED)
            ),
        )

    def test_starts_in_study_and_counts_breaks_toward_quota(self):
        self.tracker.start()
        self.assertEqual(self.tracker.state.phase, Phase.STUDY)

        self.clock.advance(45 * MINUTE)
        transitions = self.tracker.tick()
        # 40 min study + 5 min of break were consumed...
        self.assertEqual(self.tracker.state.elapsed_seconds, 45 * MINUTE)
        self.assertEqual(self.tracker.state.phase, Phase.BREAK)
        self.assertEqual(transitions, [Phase.BREAK])

    def test_break_leads_back_to_study(self):
        self.tracker.start()
        self.clock.advance(50 * MINUTE)
        transitions = self.tracker.tick()
        self.assertEqual(self.tracker.state.phase, Phase.STUDY)
        self.assertEqual(transitions, [Phase.BREAK, Phase.STUDY])

    def test_phase_remaining_counts_down(self):
        self.tracker.start()
        self.clock.advance(10 * MINUTE)
        self.tracker.tick()
        self.assertEqual(self.tracker.state.phase, Phase.STUDY)
        self.assertEqual(self.tracker.phase_remaining_seconds, 30 * MINUTE)

    def test_skip_phase(self):
        self.tracker.start()
        self.clock.advance(5 * MINUTE)
        self.tracker.tick()
        phase = self.tracker.skip_phase()
        self.assertEqual(phase, Phase.BREAK)
        self.assertEqual(self.tracker.state.phase_elapsed_seconds, 0)

    def test_completion_stops_the_session(self):
        small = PomodoroConfig(study_seconds=1, break_seconds=1)
        schedule = default_schedule().with_day(
            0, DayPolicy(2 * MINUTE, DayKind.CARRIED)
        )
        tracker = make_tracker(self.clock, pomodoro=small, schedule=schedule)
        tracker.start()
        self.clock.advance(2 * MINUTE)
        tracker.tick()
        self.assertTrue(tracker.day_complete)
        self.assertEqual(tracker.state.phase, Phase.COMPLETE)
        self.assertFalse(tracker.state.running)

    def test_paused_time_is_not_counted(self):
        self.tracker.start()
        self.clock.advance(10 * MINUTE)
        self.tracker.tick()
        self.tracker.pause()
        self.clock.advance(5 * HOUR)
        transitions = self.tracker.tick()
        self.assertEqual(self.tracker.state.elapsed_seconds, 10 * MINUTE)
        self.assertEqual(transitions, [])

    def test_study_and_break_seconds_are_split(self):
        self.tracker.start()
        self.clock.advance(45 * MINUTE)  # 40m study + 5m break
        self.tracker.tick()
        self.assertEqual(self.tracker.state.study_seconds, 40 * MINUTE)
        self.assertEqual(self.tracker.state.break_seconds, 5 * MINUTE)
        self.assertEqual(
            self.tracker.state.study_seconds + self.tracker.state.break_seconds,
            self.tracker.state.elapsed_seconds,
        )


class RestOverrideTest(unittest.TestCase):
    """One-shot break lengths used for the 5-minute subject rest."""

    def setUp(self) -> None:
        self.pomodoro = PomodoroConfig(
            study_seconds=40 * MINUTE, break_seconds=10 * MINUTE
        )
        self.clock = FakeClock(at(MONDAY))
        self.tracker = make_tracker(
            self.clock,
            pomodoro=self.pomodoro,
            schedule=default_schedule().with_day(
                0, DayPolicy(100 * HOUR, DayKind.CARRIED)
            ),
        )

    def test_rest_uses_override_then_returns_to_study(self):
        self.tracker.start()
        self.clock.advance(5 * MINUTE)
        self.tracker.tick()
        self.tracker.start_rest(5 * MINUTE)

        self.assertEqual(self.tracker.state.phase, Phase.BREAK)
        self.assertEqual(self.tracker.state.break_override_seconds, 5 * MINUTE)
        self.assertEqual(self.tracker.phase_remaining_seconds, 5 * MINUTE)
        self.assertTrue(self.tracker.state.running)

        self.clock.advance(5 * MINUTE)
        transitions = self.tracker.tick()
        self.assertEqual(transitions, [Phase.STUDY])
        self.assertEqual(self.tracker.state.phase, Phase.STUDY)
        self.assertEqual(self.tracker.state.break_override_seconds, 0)

    def test_override_does_not_affect_later_breaks(self):
        self.tracker.start()
        self.tracker.start_rest(5 * MINUTE)
        self.clock.advance(5 * MINUTE)
        self.tracker.tick()

        self.clock.advance(40 * MINUTE)
        self.tracker.tick()
        self.assertEqual(self.tracker.state.phase, Phase.BREAK)
        self.assertEqual(self.tracker.state.break_override_seconds, 0)
        self.assertEqual(self.tracker.phase_length_seconds, 10 * MINUTE)

    def test_skip_phase_clears_override(self):
        self.tracker.start()
        self.tracker.start_rest(5 * MINUTE)
        self.tracker.skip_phase()
        self.assertEqual(self.tracker.state.phase, Phase.STUDY)
        self.assertEqual(self.tracker.state.break_override_seconds, 0)

    def test_new_day_clears_override(self):
        self.tracker.start_rest(5 * MINUTE)
        self.clock.set_wall(at(TUESDAY))
        self.tracker.tick()
        self.assertEqual(self.tracker.state.break_override_seconds, 0)
        self.assertFalse(self.tracker.state.running)

    def test_queued_rest_waits_for_start(self):
        self.tracker.start_rest(5 * MINUTE)
        self.assertFalse(self.tracker.state.running)
        self.clock.advance(HOUR)
        self.tracker.tick()
        self.assertEqual(self.tracker.state.break_override_seconds, 5 * MINUTE)

        self.tracker.start()
        self.clock.advance(5 * MINUTE)
        self.tracker.tick()
        self.assertEqual(self.tracker.state.break_override_seconds, 0)
        self.assertEqual(self.tracker.state.phase, Phase.STUDY)


class PersistenceRoundTripTest(unittest.TestCase):
    def test_state_can_be_restored(self):
        clock = FakeClock(at(MONDAY))
        tracker = make_tracker(clock)
        tracker.start()
        clock.advance(1 * HOUR)
        tracker.tick()
        saved = tracker.state

        # New tracker restored from saved state; no time credited while down.
        clock2 = FakeClock(at(MONDAY, 12))
        restored = make_tracker(clock2, )
        restored.load_state(TrackerState(**vars(saved)))
        self.assertEqual(restored.state.elapsed_seconds, 1 * HOUR)


if __name__ == "__main__":
    unittest.main()
