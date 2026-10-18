from __future__ import annotations

import tempfile
import unittest
from datetime import time
from pathlib import Path

from simplestudy.core.schedule import SECONDS_PER_HOUR, DayKind, DayPolicy
from simplestudy.storage import config as config_mod


class ConfigParseTest(unittest.TestCase):
    def test_defaults_when_empty(self):
        cfg = config_mod.loads("")
        self.assertEqual(cfg.boundary, time(4, 0))
        self.assertEqual(cfg.study_seconds, 40 * 60)
        self.assertEqual(cfg.break_seconds, 10 * 60)
        self.assertFalse(cfg.auto_start)
        self.assertTrue(cfg.notifications)
        self.assertEqual(cfg.schedule.days[0].quota_seconds, 4 * SECONDS_PER_HOUR)

    def test_parses_general_and_pomodoro(self):
        cfg = config_mod.loads(
            """
            [general]
            day_boundary = "05:30"
            auto_start = true

            [pomodoro]
            study_minutes = 25
            break_minutes = 5
            """
        )
        self.assertEqual(cfg.boundary, time(5, 30))
        self.assertTrue(cfg.auto_start)
        self.assertEqual(cfg.study_seconds, 25 * 60)
        self.assertEqual(cfg.break_seconds, 5 * 60)

    def test_parses_weekday_overrides(self):
        cfg = config_mod.loads(
            """
            [weekday.saturday]
            quota_minutes = 120
            mode = "carried"

            [weekday.sunday]
            quota_minutes = 60
            mode = "optional"
            """
        )
        self.assertEqual(cfg.schedule.days[5].kind, DayKind.CARRIED)
        self.assertEqual(cfg.schedule.days[6].kind, DayKind.OPTIONAL)
        self.assertEqual(cfg.schedule.days[6].quota_seconds, 60 * 60)

    def test_free_mode_forces_zero_quota(self):
        cfg = config_mod.loads(
            """
            [weekday.monday]
            quota_minutes = 240
            mode = "free"
            """
        )
        self.assertEqual(cfg.schedule.days[0].quota_seconds, 0)

    def test_invalid_mode_raises(self):
        with self.assertRaises(ValueError):
            config_mod.loads("[weekday.monday]\nmode = \"nonsense\"\n")

    def test_invalid_boundary_raises(self):
        with self.assertRaises(ValueError):
            config_mod.loads('[general]\nday_boundary = "25:00"\n')

    def test_transparency_defaults(self):
        cfg = config_mod.loads("")
        self.assertEqual(cfg.transparency.idle_timeout, 5)
        self.assertAlmostEqual(cfg.transparency.idle_opacity, 0.08)
        self.assertAlmostEqual(cfg.transparency.active_opacity, 0.90)
        self.assertEqual(cfg.transparency.fade_ms, 300)

    def test_transparency_overrides(self):
        cfg = config_mod.loads(
            """
            [transparency]
            idle_timeout = 12
            idle_opacity = 0.2
            active_opacity = 1.0
            fade_ms = 500
            """
        )
        self.assertEqual(cfg.transparency.idle_timeout, 12)
        self.assertAlmostEqual(cfg.transparency.idle_opacity, 0.2)
        self.assertAlmostEqual(cfg.transparency.active_opacity, 1.0)
        self.assertEqual(cfg.transparency.fade_ms, 500)

    def test_transparency_validation(self):
        with self.assertRaises(ValueError):
            config_mod.TransparencyConfig(idle_opacity=1.5)
        with self.assertRaises(ValueError):
            config_mod.TransparencyConfig(idle_timeout=-1)


class ConfigRoundTripTest(unittest.TestCase):
    def test_dumps_then_loads(self):
        original = config_mod.Config(
            boundary=time(3, 15),
            auto_start=True,
            study_seconds=50 * 60,
            break_seconds=15 * 60,
            notifications=False,
            schedule=config_mod.default_schedule().with_day(
                6, DayPolicy(2 * SECONDS_PER_HOUR, DayKind.OPTIONAL)
            ),
        )
        text = config_mod.dumps(original)
        restored = config_mod.loads(text)
        self.assertEqual(restored.boundary, original.boundary)
        self.assertEqual(restored.auto_start, original.auto_start)
        self.assertEqual(restored.study_seconds, original.study_seconds)
        self.assertEqual(restored.break_seconds, original.break_seconds)
        self.assertEqual(restored.notifications, original.notifications)
        self.assertEqual(restored.schedule.days[6].kind, DayKind.OPTIONAL)
        self.assertEqual(restored.schedule.days[6].quota_seconds, 2 * SECONDS_PER_HOUR)

    def test_transparency_round_trip(self):
        original = config_mod.Config(
            transparency=config_mod.TransparencyConfig(
                idle_timeout=7, idle_opacity=0.1, active_opacity=0.95, fade_ms=450
            )
        )
        restored = config_mod.loads(config_mod.dumps(original))
        self.assertEqual(restored.transparency, original.transparency)

    def test_save_and_load_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            cfg = config_mod.Config(auto_start=True)
            config_mod.save(cfg, path)
            self.assertTrue(path.exists())
            loaded = config_mod.load(path)
            self.assertTrue(loaded.auto_start)

    def test_missing_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = config_mod.load(Path(tmp) / "does-not-exist.toml")
            self.assertEqual(loaded.boundary, time(4, 0))


if __name__ == "__main__":
    unittest.main()
