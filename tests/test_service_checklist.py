from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from simplestudy.core.clock import FakeClock
from simplestudy.core.pomodoro import Phase
from simplestudy.core.schedule import SECONDS_PER_HOUR, DayKind, DayPolicy
from simplestudy.daemon.service import StudyService
from simplestudy.storage.config import Config

HOUR = SECONDS_PER_HOUR
MINUTE = 60
TZ = timezone(timedelta(hours=2))
MONDAY = (2026, 10, 12)


def at(ymd, hour=10, minute=0) -> datetime:
    return datetime(*ymd, hour, minute, tzinfo=TZ)


class RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    def notify(self, summary: str, body: str, *, urgent: bool = False) -> None:
        self.calls.append((summary, body, urgent))


class ChecklistServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.state_file = self.tmp / "state.json"
        self.history_file = self.tmp / "history.db"
        self.checklist_file = self.tmp / "checklist.json"
        self.clock = FakeClock(at(MONDAY))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_service(self, *, notifier=None, config=None) -> StudyService:
        service = StudyService(
            config or Config(),
            self.clock,
            state_file=self.state_file,
            history_file=self.history_file,
            checklist_file=self.checklist_file,
            notifier=notifier,
            persist=True,
        )
        self.addCleanup(service.close)
        return service

    def add_subject(self, service, name="Math", items=("Algebra",)):
        subject_id = service.add_subject(name)
        for text in items:
            service.add_item(subject_id, text)
        return subject_id

    def data(self, service) -> dict:
        return json.loads(service.checklist_json())

    # -- basics ---------------------------------------------------------

    def test_add_subject_and_item_uses_config_duration(self):
        service = self.make_service()
        subject_id = service.add_subject("Math")
        item_id = service.add_item(subject_id, "Algebra")

        data = self.data(service)
        self.assertEqual(data["active_subject_id"], subject_id)
        subject = data["subjects"][0]
        self.assertEqual(subject["name"], "Math")
        self.assertEqual(subject["items"][0]["id"], item_id)
        self.assertEqual(subject["items"][0]["base_seconds"], 40 * MINUTE)

        summary = service.checklist_summary()
        self.assertEqual(summary.active_subject_id, subject_id)
        self.assertEqual(summary.active_item_id, item_id)
        self.assertEqual(summary.remaining_seconds, 40 * MINUTE)
        self.assertGreaterEqual(summary.revision, 2)

    def test_revision_changes_on_mutation(self):
        service = self.make_service()
        before = service.checklist_revision()
        subject_id = service.add_subject("Math")
        item_id = service.add_item(subject_id, "Algebra")
        service.set_item_duration(item_id, 20 * MINUTE)
        service.rename_item(item_id, "Algebra II")
        after = service.checklist_revision()
        self.assertGreater(after, before)
        self.assertEqual(service.checklist_summary().revision, after)

    def test_active_item_accrues_only_during_study(self):
        notifier = RecordingNotifier()
        service = self.make_service(notifier=notifier)
        subject_id = self.add_subject(service)
        service.start()

        self.clock.advance(10 * MINUTE)
        service.tick()
        self.assertEqual(service.checklist_summary().remaining_seconds, 30 * MINUTE)

        # Reaches exactly zero at 40 minutes: red, and one expiry event.
        self.clock.advance(30 * MINUTE)
        events = service.tick()
        summary = service.checklist_summary()
        self.assertEqual(summary.remaining_seconds, 0)
        self.assertEqual(summary.overrun_seconds, 0)
        self.assertTrue(any(event.kind == "item_expired" for event in events))
        self.assertTrue(any("Algebra" in call[0] for call in notifier.calls))

        # Break time does not count against the item.
        self.clock.advance(5 * MINUTE)
        service.tick()
        self.assertEqual(service.checklist_summary().remaining_seconds, 0)
        self.assertEqual(service.checklist_summary().overrun_seconds, 0)

        # One more study minute after the break becomes overrun.
        self.clock.advance(6 * MINUTE)
        service.tick()
        summary = service.checklist_summary()
        self.assertEqual(summary.remaining_seconds, -MINUTE)
        self.assertEqual(summary.overrun_seconds, MINUTE)

    def test_paused_time_is_not_credited_to_item(self):
        service = self.make_service()
        self.add_subject(service)
        service.start()
        self.clock.advance(5 * MINUTE)
        service.tick()
        service.pause()
        self.clock.advance(2 * HOUR)
        service.tick()
        self.assertEqual(service.checklist_summary().remaining_seconds, 35 * MINUTE)

    # -- carry ----------------------------------------------------------

    def test_early_check_carries_surplus(self):
        notifier = RecordingNotifier()
        service = self.make_service(notifier=notifier)
        subject_id = service.add_subject("Math")
        first = service.add_item(subject_id, "Algebra")
        second = service.add_item(subject_id, "Geometry")

        service.start()
        self.clock.advance(10 * MINUTE)
        service.tick()
        service.check_item(first)

        data = self.data(service)
        items = data["subjects"][0]["items"]
        self.assertTrue(items[0]["checked"])
        self.assertEqual(items[0]["carry_out_seconds"], 30 * MINUTE)
        self.assertEqual(items[1]["carry_in_seconds"], 30 * MINUTE)

        summary = service.checklist_summary()
        self.assertEqual(summary.active_item_id, second)
        self.assertEqual(summary.remaining_seconds, 70 * MINUTE)
        self.assertEqual(summary.rest_remaining_seconds, 0)
        # Only completing the last item earns a rest.
        self.assertFalse(any("complete" in call[0].lower() for call in notifier.calls))

    def test_overrun_becomes_debt_for_next_item(self):
        service = self.make_service()
        subject_id = service.add_subject("Math")
        first = service.add_item(subject_id, "Algebra", 30 * MINUTE)
        second = service.add_item(subject_id, "Geometry")

        service.start()
        self.clock.advance(38 * MINUTE)
        service.tick()
        service.check_item(first)

        summary = service.checklist_summary()
        self.assertEqual(summary.active_item_id, second)
        self.assertEqual(summary.remaining_seconds, 32 * MINUTE)

    def test_checking_a_foreign_item_is_rejected(self):
        service = self.make_service()
        first_subject = service.add_subject("Math")
        service.add_item(first_subject, "Algebra")
        second_subject = service.add_subject("Physics")
        foreign_item = service.add_item(second_subject, "Mechanics")

        with self.assertRaises(ValueError):
            service.check_item(foreign_item)

    # -- rest -----------------------------------------------------------

    def test_completing_subject_starts_rest_and_selects_next(self):
        notifier = RecordingNotifier()
        service = self.make_service(notifier=notifier)
        first_subject = service.add_subject("Math")
        first_item = service.add_item(first_subject, "Algebra")
        second_subject = service.add_subject("Physics")
        second_item = service.add_item(second_subject, "Mechanics")

        service.start()
        self.clock.advance(5 * MINUTE)
        service.tick()
        service.check_item(first_item)

        summary = service.checklist_summary()
        self.assertEqual(summary.active_subject_id, first_subject)
        self.assertEqual(summary.active_item_id, "")
        self.assertEqual(summary.rest_remaining_seconds, 5 * MINUTE)
        self.assertEqual(service.tracker.state.phase, Phase.BREAK)
        self.assertEqual(service.tracker.state.break_override_seconds, 5 * MINUTE)
        self.assertTrue(any("Math complete" in call[0] for call in notifier.calls))

        self.clock.advance(5 * MINUTE)
        events = service.tick()

        self.assertTrue(any(event.kind == "rest_over" for event in events))
        summary = service.checklist_summary()
        self.assertEqual(summary.active_subject_id, second_subject)
        self.assertEqual(summary.active_item_id, second_item)
        self.assertEqual(summary.rest_remaining_seconds, 0)
        self.assertEqual(service.tracker.state.break_override_seconds, 0)
        self.assertEqual(service.tracker.state.phase, Phase.STUDY)

    def test_rest_counts_toward_daily_quota(self):
        service = self.make_service()
        subject_id = service.add_subject("Math")
        item = service.add_item(subject_id, "Algebra")
        service.start()
        self.clock.advance(5 * MINUTE)
        service.tick()
        service.check_item(item)

        before = service.snapshot()
        break_before = service.tracker.state.break_seconds
        study_before = service.tracker.state.study_seconds
        self.clock.advance(5 * MINUTE)
        service.tick()
        after = service.snapshot()

        self.assertEqual(after.elapsed_seconds, before.elapsed_seconds + 5 * MINUTE)
        self.assertEqual(
            service.tracker.state.break_seconds, break_before + 5 * MINUTE
        )
        self.assertEqual(service.tracker.state.study_seconds, study_before)

    def test_no_rest_when_daily_goal_already_complete(self):
        cfg = Config()
        cfg.schedule = cfg.schedule.with_day(0, DayPolicy(1 * MINUTE, DayKind.CARRIED))
        service = self.make_service(config=cfg)
        subject_id = service.add_subject("Math")
        item = service.add_item(subject_id, "Algebra")
        service.start()
        self.clock.advance(1 * MINUTE)
        service.tick()
        self.assertTrue(service.tracker.day_complete)

        service.check_item(item)

        self.assertEqual(service.checklist_summary().rest_remaining_seconds, 0)
        self.assertEqual(service.tracker.state.break_override_seconds, 0)
        self.assertFalse(service.tracker.state.running)

    def test_rest_can_be_queued_while_paused(self):
        service = self.make_service()
        subject_id = service.add_subject("Math")
        item = service.add_item(subject_id, "Algebra")
        service.pause()
        service.check_item(item)

        summary = service.checklist_summary()
        self.assertEqual(summary.rest_remaining_seconds, 5 * MINUTE)
        self.assertEqual(service.tracker.state.break_override_seconds, 5 * MINUTE)
        self.assertFalse(service.tracker.state.running)

        self.clock.advance(HOUR)
        service.tick()
        self.assertEqual(service.checklist_summary().rest_remaining_seconds, 5 * MINUTE)

        service.start()
        self.clock.advance(5 * MINUTE)
        service.tick()
        self.assertEqual(service.checklist_summary().rest_remaining_seconds, 0)

    def test_skip_phase_finishes_rest(self):
        service = self.make_service()
        subject_id = service.add_subject("Math")
        item = service.add_item(subject_id, "Algebra")
        service.start()
        service.check_item(item)

        service.skip_phase()

        self.assertEqual(service.tracker.state.phase, Phase.STUDY)
        self.assertEqual(service.tracker.state.break_override_seconds, 0)
        self.assertEqual(service.checklist_summary().rest_remaining_seconds, 0)

    def test_uncheck_last_cancels_rest(self):
        service = self.make_service()
        subject_id = service.add_subject("Math")
        item = service.add_item(subject_id, "Algebra")
        service.start()
        service.check_item(item)
        self.assertEqual(service.checklist_summary().rest_remaining_seconds, 5 * MINUTE)

        service.uncheck_last_item(subject_id)

        self.assertEqual(service.checklist_summary().rest_remaining_seconds, 0)
        self.assertEqual(service.checklist_summary().active_item_id, item)
        self.assertEqual(service.tracker.state.phase, Phase.STUDY)
        self.assertEqual(service.tracker.state.break_override_seconds, 0)

    def test_canceling_rest_keeps_rest_time_as_break(self):
        service = self.make_service()
        subject_id = service.add_subject("Math")
        item = service.add_item(subject_id, "Algebra")
        service.start()
        self.clock.advance(5 * MINUTE)
        service.tick()
        service.check_item(item)
        self.assertEqual(service.tracker.state.phase, Phase.BREAK)

        # Spend 2 minutes of the rest, then reopen the subject by adding an item.
        self.clock.advance(2 * MINUTE)
        new_item = service.add_item(subject_id, "Geometry")
        self.clock.advance(1 * MINUTE)
        service.tick()

        # The 2 rest minutes stay break time; only the last minute is study.
        self.assertEqual(service.tracker.state.break_seconds, 2 * MINUTE)
        self.assertEqual(service.tracker.state.study_seconds, 6 * MINUTE)
        data = self.data(service)
        by_id = {entry["id"]: entry for entry in data["subjects"][0]["items"]}
        self.assertEqual(by_id[new_item]["elapsed_seconds"], 1 * MINUTE)

    def test_adding_item_cancels_rest(self):
        service = self.make_service()
        subject_id = service.add_subject("Math")
        item = service.add_item(subject_id, "Algebra")
        service.start()
        service.check_item(item)

        new_item = service.add_item(subject_id, "Geometry")
        summary = service.checklist_summary()
        self.assertEqual(summary.rest_remaining_seconds, 0)
        self.assertEqual(summary.active_subject_id, subject_id)
        self.assertEqual(summary.active_item_id, new_item)
        self.assertEqual(service.tracker.state.phase, Phase.STUDY)

    # -- subject management --------------------------------------------

    def test_add_item_or_reset_reactivates_a_completed_checklist(self):
        service = self.make_service()
        subject_id = service.add_subject("Math")
        item = service.add_item(subject_id, "Algebra")
        service.start()
        service.check_item(item)
        service.skip_phase()
        self.assertEqual(service.checklist_summary().active_subject_id, "")

        added = service.add_item(subject_id, "Geometry")
        self.assertEqual(service.checklist_summary().active_subject_id, subject_id)
        self.assertEqual(service.checklist_summary().active_item_id, added)

        service.check_item(added)
        service.skip_phase()
        self.assertEqual(service.checklist_summary().active_subject_id, "")

        service.reset_subject(subject_id)
        self.assertEqual(service.checklist_summary().active_subject_id, subject_id)
        self.assertEqual(service.checklist_summary().active_item_id, item)

    def test_removing_active_subject_selects_next(self):
        service = self.make_service()
        first = service.add_subject("Math")
        service.add_item(first, "Algebra")
        second = service.add_subject("Physics")
        second_item = service.add_item(second, "Mechanics")

        service.remove_subject(first)

        summary = service.checklist_summary()
        self.assertEqual(summary.active_subject_id, second)
        self.assertEqual(summary.active_item_id, second_item)

    def test_reset_subject_resets_active_item(self):
        service = self.make_service()
        subject_id = self.add_subject(service, items=("Algebra", "Geometry"))
        service.start()
        self.clock.advance(10 * MINUTE)
        service.tick()
        service.check_item(service.checklist_summary().active_item_id)

        service.reset_subject(subject_id)

        summary = service.checklist_summary()
        self.assertEqual(summary.remaining_seconds, 40 * MINUTE)
        self.assertFalse(any(item["checked"] for item in self.data(service)["subjects"][0]["items"]))

    def test_rest_is_restored_after_restart(self):
        service = self.make_service()
        subject_id = service.add_subject("Math")
        item = service.add_item(subject_id, "Algebra")
        service.start()
        service.check_item(item)
        self.assertEqual(service.checklist_summary().rest_remaining_seconds, 5 * MINUTE)
        service.close()

        restored = self.make_service()
        self.assertEqual(
            restored.checklist_summary().rest_remaining_seconds, 5 * MINUTE
        )
        self.assertEqual(restored.tracker.state.phase, Phase.BREAK)

        self.clock.advance(5 * MINUTE)
        restored.tick()
        self.assertEqual(restored.checklist_summary().rest_remaining_seconds, 0)
        self.assertEqual(restored.tracker.state.phase, Phase.STUDY)

    def test_persisted_checklist_is_restored(self):
        service = self.make_service()
        subject_id = service.add_subject("Math")
        service.add_item(subject_id, "Algebra")
        service.start()
        self.clock.advance(10 * MINUTE)
        service.tick()
        service.pause()
        service.close()

        restored = self.make_service()
        data = self.data(restored)
        self.assertEqual(data["active_subject_id"], subject_id)
        item = data["subjects"][0]["items"][0]
        self.assertEqual(item["elapsed_seconds"], 10 * MINUTE)
        self.assertEqual(restored.checklist_summary().remaining_seconds, 30 * MINUTE)

    def test_checklist_persisted_on_mutation(self):
        service = self.make_service()
        subject_id = service.add_subject("Math")
        service.add_item(subject_id, "Algebra")
        self.assertTrue(self.checklist_file.exists())

        payload = json.loads(self.checklist_file.read_text(encoding="utf-8"))
        self.assertEqual(payload["subjects"][0]["name"], "Math")


if __name__ == "__main__":
    unittest.main()
