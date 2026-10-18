from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from simplestudy.core.clock import FakeClock
from simplestudy.core.pomodoro import PomodoroConfig
from simplestudy.core.schedule import DayPolicy, DayKind, SECONDS_PER_HOUR
from simplestudy.core.tracker import TrackerState
from simplestudy.daemon.service import Event, StudyService, format_hours
from simplestudy.storage.config import Config
from simplestudy.storage.state import load as load_state

HOUR = SECONDS_PER_HOUR
MINUTE = 60
TZ = timezone(timedelta(hours=2))
MONDAY = (2026, 10, 12)
TUESDAY = (2026, 10, 13)


def at(ymd, hour=10, minute=0) -> datetime:
    return datetime(*ymd, hour, minute, tzinfo=TZ)


class RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    def notify(self, summary: str, body: str, *, urgent: bool = False) -> None:
        self.calls.append((summary, body, urgent))


class ServiceTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.state_file = self.tmp / "state.json"
        self.history_file = self.tmp / "history.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_service(self, clock, *, config=None, notifier=None) -> StudyService:
        service = StudyService(
            config or Config(),
            clock,
            state_file=self.state_file,
            history_file=self.history_file,
            notifier=notifier,
            persist=True,
        )
        self.addCleanup(service.close)
        return service


class FormatHoursTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(format_hours(4 * HOUR), "4h 00m")
        self.assertEqual(format_hours(HOUR + 30 * MINUTE), "1h 30m")
        self.assertEqual(format_hours(0), "0h 00m")


class ServicePersistenceTest(ServiceTestBase):
    def test_state_is_persisted_on_start_and_tick(self):
        clock = FakeClock(at(MONDAY))
        service = self.make_service(clock)
        service.start()
        clock.advance(65)
        service.tick()

        saved = load_state(self.state_file)
        assert saved is not None
        self.assertEqual(saved.logical_day, at(MONDAY).date())
        self.assertEqual(saved.elapsed_seconds, 65)
        self.assertTrue(saved.running)

    def test_history_records_current_day(self):
        clock = FakeClock(at(MONDAY))
        service = self.make_service(clock)
        service.start()
        clock.advance(30 * MINUTE)
        service.tick()
        assert service.history is not None
        row = service.history.day(at(MONDAY).date())
        assert row is not None
        self.assertEqual(row.elapsed_seconds, 30 * MINUTE)
        self.assertEqual(row.study_seconds, 30 * MINUTE)
        self.assertEqual(row.debt_in_seconds, 0)
        self.assertEqual(row.required_seconds, 4 * HOUR)


class ServiceRolloverTest(ServiceTestBase):
    def _monday_study(self, service, clock, seconds):
        service.start()
        clock.advance(seconds)
        service.tick()
        service.pause()

    def test_history_split_recorded_across_break(self):
        big = Config()
        big.study_seconds = 40 * MINUTE
        big.break_seconds = 10 * MINUTE
        clock = FakeClock(at(MONDAY))
        service = self.make_service(clock, config=big)
        # 45 min => 40 min study + 5 min break.
        self._monday_study(service, clock, 45 * MINUTE)
        assert service.history is not None
        row = service.history.day(at(MONDAY).date())
        assert row is not None
        self.assertEqual(row.study_seconds, 40 * MINUTE)
        self.assertEqual(row.break_seconds, 5 * MINUTE)

    def test_offline_gap_settles_days(self):
        clock = FakeClock(at(MONDAY))
        service = self.make_service(clock)
        self._monday_study(service, clock, 0)  # no study
        service.close()

        # Daemon was off for a day; a new service a day later settles Monday.
        clock2 = FakeClock(at(TUESDAY))
        service2 = self.make_service(clock2)
        assert service2.history is not None
        monday = service2.history.day(at(MONDAY).date())
        assert monday is not None
        self.assertEqual(monday.debt_out_seconds, 4 * HOUR)
        self.assertEqual(service2.tracker.required_seconds, 8 * HOUR)


class ServiceEventTest(ServiceTestBase):
    def test_phase_change_emits_event_and_notification(self):
        notifier = RecordingNotifier()
        clock = FakeClock(at(MONDAY))
        service = self.make_service(clock, notifier=notifier)
        service.start()
        clock.advance(40 * MINUTE)  # 40m study -> break
        events = service.tick()
        self.assertTrue(any(e.kind == "phase" for e in events))
        self.assertEqual(notifier.calls[-1][0], "Break time")

    def test_day_complete_event(self):
        notifier = RecordingNotifier()
        cfg = Config()
        cfg.schedule = cfg.schedule.with_day(0, DayPolicy(2 * MINUTE, DayKind.CARRIED))
        clock = FakeClock(at(MONDAY))
        service = self.make_service(clock, config=cfg, notifier=notifier)
        service.start()
        clock.advance(2 * MINUTE)
        events = service.tick()
        self.assertTrue(any(e.kind == "day_complete" for e in events))
        self.assertTrue(any("complete" in c[0].lower() for c in notifier.calls))

    def test_notifications_can_be_disabled(self):
        notifier = RecordingNotifier()
        cfg = Config()
        cfg.notifications = False
        clock = FakeClock(at(MONDAY))
        service = self.make_service(clock, config=cfg, notifier=notifier)
        service.start()
        clock.advance(40 * MINUTE)
        service.tick()
        self.assertEqual(notifier.calls, [])

    def test_new_day_notification_mentions_carryover(self):
        notifier = RecordingNotifier()
        clock = FakeClock(at(MONDAY))
        service = self.make_service(clock, notifier=notifier)
        service.start()  # study 0 of 4h
        service.pause()
        clock.set_wall(at(TUESDAY))
        events = service.tick()
        self.assertTrue(any(e.kind == "day_rolled" for e in events))
        self.assertTrue(any("4h 00m carried over" in c[1] for c in notifier.calls))


class ReloadConfigTest(ServiceTestBase):
    def test_pomodoro_lengths_apply(self):
        clock = FakeClock(at(MONDAY))
        service = self.make_service(clock)
        new = Config()
        new.study_seconds = 25 * MINUTE
        new.break_seconds = 5 * MINUTE
        service.reload_config(new)
        self.assertEqual(service.tracker.pomodoro.study_seconds, 25 * MINUTE)


if __name__ == "__main__":
    unittest.main()
