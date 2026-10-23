from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from simplestudy.core.pomodoro import Phase
from simplestudy.core.tracker import TrackerState
from simplestudy.storage import state as state_mod


class StateRoundTripTest(unittest.TestCase):
    def test_dict_round_trip(self):
        original = TrackerState(
            logical_day=date(2026, 10, 15),
            debt_seconds=3600,
            elapsed_seconds=1200,
            phase=Phase.BREAK,
            phase_elapsed_seconds=90,
            break_override_seconds=300,
            running=True,
        )
        restored = state_mod.from_dict(state_mod.to_dict(original))
        self.assertEqual(restored, original)

    def test_save_and_load_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            original = TrackerState(logical_day=date(2026, 10, 15), debt_seconds=42)
            state_mod.save(path, original)
            restored = state_mod.load(path)
            self.assertEqual(restored, original)

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(state_mod.load(Path(tmp) / "nope.json"))

    def test_corrupt_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{ not valid json", encoding="utf-8")
            self.assertIsNone(state_mod.load(path))

    def test_save_is_atomic_no_leftover_tempfiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state_mod.save(path, TrackerState(logical_day=date(2026, 10, 15)))
            leftovers = [p for p in Path(tmp).iterdir() if p.name.startswith(".state-")]
            self.assertEqual(leftovers, [])
            # Valid JSON payload on disk.
            json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
