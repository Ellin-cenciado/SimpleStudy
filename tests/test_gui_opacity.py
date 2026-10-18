from __future__ import annotations

import unittest

try:
    import gi

    gi.require_version("Adw", "1")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Adw, Gdk, Gtk  # noqa: F401

    from simplestudy.gui.opacity import OpacityController
    from simplestudy.storage.config import TransparencyConfig

    HAVE_GI = True
except (ImportError, ValueError):  # pragma: no cover - environment dependent
    HAVE_GI = False


def display_available() -> bool:
    if not HAVE_GI:
        return False
    try:
        return Gdk.Display.get_default() is not None
    except Exception:  # pragma: no cover
        return False


@unittest.skipUnless(display_available(), "no graphical display available")
class OpacityControllerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = Gtk.Window()

    def tearDown(self) -> None:
        self.window.destroy()

    def test_starts_at_active_opacity(self):
        controller = OpacityController(
            self.window,
            TransparencyConfig(active_opacity=0.9, fade_ms=0),
        )
        self.assertAlmostEqual(self.window.get_opacity(), 0.9, places=2)
        controller.close()

    def test_instant_fade_and_restore(self):
        controller = OpacityController(
            self.window,
            TransparencyConfig(idle_opacity=0.1, active_opacity=0.9, fade_ms=0),
        )
        controller._fade_out()
        self.assertTrue(controller._faded)
        self.assertAlmostEqual(self.window.get_opacity(), 0.1, places=2)

        controller._restore_immediate()
        self.assertFalse(controller._faded)
        self.assertAlmostEqual(self.window.get_opacity(), 0.9, places=2)
        controller.close()

    def test_apply_config_updates_targets(self):
        controller = OpacityController(
            self.window,
            TransparencyConfig(active_opacity=0.9, idle_opacity=0.1, fade_ms=0),
        )
        controller.apply_config(
            TransparencyConfig(active_opacity=0.75, idle_opacity=0.2, fade_ms=0)
        )
        self.assertAlmostEqual(controller.config.active_opacity, 0.75)
        self.assertAlmostEqual(controller.config.idle_opacity, 0.2)

        # The new targets are honoured on the next restore / fade.
        controller._fade_out()
        self.assertAlmostEqual(self.window.get_opacity(), 0.2, places=2)
        controller._restore_immediate()
        self.assertAlmostEqual(self.window.get_opacity(), 0.75, places=2)
        controller.close()


if __name__ == "__main__":
    unittest.main()
