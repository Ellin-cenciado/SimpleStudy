from __future__ import annotations

import unittest

from simplestudy.core.checklist import (
    SUBJECT_REST_SECONDS,
    ChecklistState,
    Item,
    Subject,
)

MINUTE = 60


def make_state() -> tuple[ChecklistState, str, str, str]:
    state = ChecklistState()
    subject = state.add_subject("Math")
    first = state.add_item(subject.id, "Algebra", 40 * MINUTE)
    second = state.add_item(subject.id, "Geometry", 40 * MINUTE)
    return state, subject.id, first.id, second.id


class SubjectAndItemTest(unittest.TestCase):
    def test_first_subject_becomes_active(self):
        state, subject_id, first_id, second_id = make_state()
        self.assertEqual(state.active_subject_id, subject_id)
        self.assertEqual(state.active_item().id, first_id)
        self.assertEqual(state.subject(subject_id).active_item.id, first_id)

    def test_empty_subject_is_not_complete(self):
        subject = Subject(id="s", name="Empty")
        self.assertFalse(subject.complete)
        self.assertIsNone(subject.active_item)

    def test_item_math(self):
        item = Item(id="i", text="x", base_seconds=600, carry_in_seconds=120, elapsed_seconds=300)
        self.assertEqual(item.budget_seconds, 720)
        self.assertEqual(item.remaining_seconds, 420)
        self.assertEqual(item.overrun_seconds, 0)

        item.elapsed_seconds = 800
        self.assertEqual(item.remaining_seconds, -80)
        self.assertEqual(item.overrun_seconds, 80)

    def test_add_item_rejects_bad_input(self):
        state, subject_id, _first, _second = make_state()
        with self.assertRaises(ValueError):
            state.add_item(subject_id, "   ", MINUTE)
        with self.assertRaises(ValueError):
            state.add_item(subject_id, "x", 0)
        with self.assertRaises(ValueError):
            state.add_subject("   ")

    def test_select_completed_subject_is_rejected(self):
        state, subject_id, first_id, second_id = make_state()
        state.check_item(first_id)
        state.check_item(second_id)
        with self.assertRaises(ValueError):
            state.select_subject(subject_id)


class CarryTest(unittest.TestCase):
    def test_early_check_carries_surplus(self):
        state, subject_id, first_id, second_id = make_state()
        state.advance(30 * MINUTE)
        result = state.check_item(first_id)

        self.assertTrue(result.subject_completed is False)
        self.assertEqual(result.carry_out_seconds, 10 * MINUTE)
        self.assertEqual(result.next_item_id, second_id)

        second = state.find_item(second_id)[1]
        self.assertEqual(second.carry_in_seconds, 10 * MINUTE)
        self.assertEqual(second.budget_seconds, 50 * MINUTE)
        self.assertEqual(state.active_item().id, second_id)

    def test_late_check_carries_debt(self):
        state, _subject_id, first_id, second_id = make_state()
        state.advance(48 * MINUTE)
        result = state.check_item(first_id)

        self.assertEqual(result.carry_out_seconds, -8 * MINUTE)
        second = state.find_item(second_id)[1]
        self.assertEqual(second.carry_in_seconds, -8 * MINUTE)
        self.assertEqual(second.budget_seconds, 32 * MINUTE)
        self.assertEqual(second.remaining_seconds, 32 * MINUTE)

    def test_inherited_debt_can_start_red(self):
        state, _subject_id, first_id, second_id = make_state()
        state.advance(90 * MINUTE)
        first = state.check_item(first_id)
        self.assertEqual(first.carry_out_seconds, -50 * MINUTE)

        second = state.find_item(second_id)[1]
        self.assertEqual(second.remaining_seconds, -10 * MINUTE)
        self.assertEqual(second.overrun_seconds, 10 * MINUTE)

        # It is already expired, so advancing does not fire a second expiry.
        result = state.advance(MINUTE)
        self.assertFalse(result.expired)
        self.assertEqual(result.overrun_seconds, 11 * MINUTE)

    def test_carry_is_discarded_at_subject_boundary(self):
        state, subject_id, first_id, second_id = make_state()
        state.advance(10 * MINUTE)
        state.check_item(first_id)
        state.check_item(second_id)

        self.assertTrue(state.subject(subject_id).complete)
        self.assertIsNone(state.active_item())

        other = state.add_subject("Physics")
        state.select_subject(other.id)
        item = state.add_item(other.id, "Mechanics", 40 * MINUTE)
        self.assertEqual(item.carry_in_seconds, 0)

    def test_expiry_fires_once(self):
        state, _subject_id, first_id, _second_id = make_state()
        state.advance(39 * MINUTE)
        result = state.advance(MINUTE)
        self.assertTrue(result.expired)
        self.assertEqual(result.remaining_seconds, 0)
        self.assertEqual(result.overrun_seconds, 0)

        again = state.advance(MINUTE)
        self.assertFalse(again.expired)
        self.assertEqual(again.overrun_seconds, MINUTE)

    def test_only_current_item_can_be_checked(self):
        state, _subject_id, first_id, second_id = make_state()
        with self.assertRaises(ValueError):
            state.check_item(second_id)
        state.check_item(first_id)
        with self.assertRaises(ValueError):
            state.check_item(first_id)

    def test_item_of_another_subject_cannot_be_checked(self):
        state, _subject_id, _first_id, _second_id = make_state()
        other = state.add_subject("Physics")
        other_item = state.add_item(other.id, "Mechanics", MINUTE)
        with self.assertRaises(ValueError):
            state.check_item(other_item.id)


class CorrectionTest(unittest.TestCase):
    def test_uncheck_last_resets_item_and_successor_carry(self):
        state, subject_id, first_id, second_id = make_state()
        state.advance(10 * MINUTE)
        state.check_item(first_id)
        self.assertEqual(state.find_item(second_id)[1].carry_in_seconds, 30 * MINUTE)

        item = state.uncheck_last(subject_id)
        self.assertEqual(item.id, first_id)
        self.assertFalse(item.checked)
        self.assertEqual(item.elapsed_seconds, 0)
        self.assertEqual(item.carry_out_seconds, 0)
        self.assertEqual(state.find_item(second_id)[1].carry_in_seconds, 0)
        self.assertEqual(state.active_item().id, first_id)

    def test_uncheck_last_without_checks_is_rejected(self):
        state, subject_id, _first, _second = make_state()
        with self.assertRaises(ValueError):
            state.uncheck_last(subject_id)

    def test_reset_subject_clears_everything(self):
        state, subject_id, first_id, second_id = make_state()
        state.advance(10 * MINUTE)
        state.check_item(first_id)
        state.reset_subject(subject_id)

        subject = state.subject(subject_id)
        self.assertFalse(subject.complete)
        for item in subject.items:
            self.assertFalse(item.checked)
            self.assertEqual(item.elapsed_seconds, 0)
            self.assertEqual(item.carry_in_seconds, 0)
            self.assertEqual(item.carry_out_seconds, 0)
        self.assertEqual(state.active_item().id, first_id)

    def test_remove_current_item_passes_carry_to_successor(self):
        state, subject_id, first_id, second_id = make_state()
        third = state.add_item(subject_id, "Trigonometry", 40 * MINUTE)
        state.advance(10 * MINUTE)
        state.check_item(first_id)
        self.assertEqual(state.find_item(second_id)[1].carry_in_seconds, 30 * MINUTE)

        state.remove_item(second_id)
        successor = state.find_item(third.id)[1]
        self.assertEqual(successor.carry_in_seconds, 30 * MINUTE)
        self.assertEqual(successor.elapsed_seconds, 0)

    def test_remove_checked_item_is_rejected(self):
        state, _subject_id, first_id, _second_id = make_state()
        state.check_item(first_id)
        with self.assertRaises(ValueError):
            state.remove_item(first_id)

    def test_set_duration_rejects_checked_item(self):
        state, _subject_id, first_id, _second_id = make_state()
        state.check_item(first_id)
        with self.assertRaises(ValueError):
            state.set_item_duration(first_id, 20 * MINUTE)

    def test_set_duration_changes_remaining(self):
        state, _subject_id, first_id, _second_id = make_state()
        state.advance(10 * MINUTE)
        state.set_item_duration(first_id, 20 * MINUTE)
        self.assertEqual(state.active_item().remaining_seconds, 10 * MINUTE)


class NavigationTest(unittest.TestCase):
    def test_next_incomplete_subject_wraps(self):
        state = ChecklistState()
        first = state.add_subject("First")
        second = state.add_subject("Second")
        third = state.add_subject("Third")

        self.assertEqual(state.next_incomplete_subject().id, first.id)
        self.assertEqual(state.next_incomplete_subject(first.id).id, second.id)
        self.assertEqual(state.next_incomplete_subject(third.id).id, first.id)

        state.add_item(first.id, "a", MINUTE)
        first_item_id = state.active_item().id
        state.check_item(first_item_id)
        self.assertEqual(state.next_incomplete_subject(first.id).id, second.id)

    def test_ensure_active_recovers_from_missing_subject(self):
        state, subject_id, first_id, _second_id = make_state()
        state.active_subject_id = "missing"
        state.ensure_active()
        self.assertEqual(state.active_subject_id, subject_id)
        self.assertEqual(state.active_item().id, first_id)

    def test_all_complete(self):
        state, _subject_id, first_id, second_id = make_state()
        self.assertFalse(state.all_complete)
        state.check_item(first_id)
        self.assertFalse(state.all_complete)
        state.check_item(second_id)
        self.assertTrue(state.all_complete)


class RestConstantTest(unittest.TestCase):
    def test_rest_is_five_minutes(self):
        self.assertEqual(SUBJECT_REST_SECONDS, 5 * MINUTE)


if __name__ == "__main__":
    unittest.main()
