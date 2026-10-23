from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from simplestudy.core.checklist import ChecklistState
from simplestudy.storage import checklist as checklist_mod

MINUTE = 60


class ChecklistStorageTest(unittest.TestCase):
    def test_round_trip(self):
        state = ChecklistState()
        math = state.add_subject("Math")
        algebra = state.add_item(math.id, "Algebra", 40 * MINUTE)
        geometry = state.add_item(math.id, "Geometry", 30 * MINUTE)
        state.advance(10 * MINUTE)
        state.check_item(algebra.id)

        restored = checklist_mod.from_dict(checklist_mod.to_dict(state))
        self.assertEqual(restored.active_subject_id, math.id)
        self.assertEqual(len(restored.subjects), 1)
        subject = restored.subjects[0]
        self.assertEqual(subject.name, "Math")
        self.assertEqual([item.text for item in subject.items], ["Algebra", "Geometry"])
        self.assertTrue(subject.items[0].checked)
        self.assertEqual(subject.items[0].carry_out_seconds, 30 * MINUTE)
        self.assertEqual(subject.items[1].carry_in_seconds, 30 * MINUTE)
        self.assertEqual(restored.active_item().id, geometry.id)

    def test_save_and_load_file(self):
        state, subject_id, item_id = self._state()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checklist.json"
            checklist_mod.save(path, state)
            self.assertTrue(path.exists())
            loaded = checklist_mod.load(path)
            self.assertEqual(loaded.active_subject_id, subject_id)
            self.assertEqual(loaded.active_item().id, item_id)

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = checklist_mod.load(Path(tmp) / "missing.json")
            self.assertEqual(loaded.subjects, [])
            self.assertIsNone(loaded.active_subject_id)

    def test_corrupt_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checklist.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(checklist_mod.load(path).subjects, [])

    def test_missing_fields_get_defaults(self):
        data = {
            "subjects": [
                {
                    "name": "Loose",
                    "items": [
                        {"text": "One", "base_seconds": 0},
                        {"base_seconds": "bad"},
                    ],
                }
            ]
        }
        loaded = checklist_mod.from_dict(data, default_seconds=25 * MINUTE)
        self.assertEqual(len(loaded.subjects), 1)
        subject = loaded.subjects[0]
        self.assertEqual(subject.name, "Loose")
        self.assertEqual(subject.items[0].text, "One")
        self.assertEqual(subject.items[0].base_seconds, 25 * MINUTE)
        self.assertEqual(subject.items[1].text, "Untitled item")

    def test_only_first_unchecked_item_keeps_carry(self):
        data = {
            "active_subject_id": "s",
            "subjects": [
                {
                    "id": "s",
                    "name": "S",
                    "items": [
                        {"id": "a", "text": "A", "base_seconds": 600, "checked": True},
                        {"id": "b", "text": "B", "base_seconds": 600, "carry_in_seconds": 120},
                        {
                            "id": "c",
                            "text": "C",
                            "base_seconds": 600,
                            "carry_in_seconds": 999,
                            "elapsed_seconds": 50,
                        },
                    ],
                }
            ],
        }
        loaded = checklist_mod.from_dict(data)
        second, third = loaded.subjects[0].items[1:]
        self.assertEqual(second.carry_in_seconds, 120)
        self.assertEqual(third.carry_in_seconds, 0)
        self.assertEqual(third.elapsed_seconds, 0)

    def test_unknown_active_subject_falls_back(self):
        data = {
            "active_subject_id": "missing",
            "subjects": [
                {
                    "id": "s",
                    "name": "S",
                    "items": [{"id": "a", "text": "A", "base_seconds": 600}],
                }
            ],
        }
        loaded = checklist_mod.from_dict(data)
        self.assertEqual(loaded.active_subject_id, "s")

    def test_non_dict_json_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checklist.json"
            path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
            self.assertEqual(checklist_mod.load(path).subjects, [])

    def _state(self) -> tuple[ChecklistState, str, str]:
        state = ChecklistState()
        subject = state.add_subject("Math")
        item = state.add_item(subject.id, "Algebra", 40 * MINUTE)
        return state, subject.id, item.id


if __name__ == "__main__":
    unittest.main()
