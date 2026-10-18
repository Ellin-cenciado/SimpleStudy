from __future__ import annotations

import unittest
from datetime import date

from simplestudy.core.tracker import Snapshot
from simplestudy.daemon import app as app_mod
from simplestudy.daemon.service import format_hours


def make_snapshot(**overrides) -> Snapshot:
    base = dict(
        logical_day=date(2026, 10, 15),
        weekday="thursday",
        day_kind="carried",
        debt_seconds=3600,
        elapsed_seconds=600,
        required_seconds=14400,
        remaining_seconds=13800,
        optional_goal_seconds=0,
        optional_done_seconds=0,
        phase="study",
        phase_elapsed_seconds=120,
        phase_length_seconds=2400,
        phase_remaining_seconds=2280,
        running=True,
        day_complete=False,
        is_free_day=False,
    )
    base.update(overrides)
    return Snapshot(**base)


class SnapshotVariantTest(unittest.TestCase):
    def test_variant_contains_all_fields(self):
        variant = app_mod.snapshot_to_variant(make_snapshot())
        self.assertEqual(variant.get_type_string(), "a{sv}")
        data = variant.unpack()
        self.assertEqual(data["LogicalDay"], "2026-10-15")
        self.assertEqual(data["Weekday"], "thursday")
        self.assertEqual(data["RequiredSeconds"], 14400)
        self.assertEqual(data["CarryInSeconds"], 3600)
        self.assertEqual(data["Phase"], "study")
        self.assertTrue(data["Running"])

    def test_variant_is_wrappable_in_a_tuple(self):
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        variant = app_mod.snapshot_to_variant(make_snapshot())
        tuple_variant = GLib.Variant.new_tuple(variant)
        self.assertEqual(tuple_variant.get_type_string(), "(a{sv})")


class IntrospectionTest(unittest.TestCase):
    def test_xml_parses_and_declares_interface(self):
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        node = Gio.DBusNodeInfo.new_for_xml(app_mod.INTROSPECTION_XML)
        interfaces = {i.name for i in node.interfaces}
        self.assertIn("org.simplestudy.Daemon", interfaces)

    def test_property_getter_covers_declared_properties(self):
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        node = Gio.DBusNodeInfo.new_for_xml(app_mod.INTROSPECTION_XML)
        iface = next(i for i in node.interfaces if i.name == "org.simplestudy.Daemon")
        declared = {p.name for p in iface.properties}
        # Build the mapping the daemon would return without needing a live daemon.
        daemon = object.__new__(app_mod.Daemon)

        class FakeService:
            def snapshot(self_inner):
                return make_snapshot()

        daemon.service = FakeService()
        provided = set()
        for prop in declared:
            value = app_mod.Daemon._on_get_property(
                daemon, None, "", "", "org.simplestudy.Daemon", prop
            )
            if value is not None:
                provided.add(prop)
        self.assertEqual(declared, provided)


if __name__ == "__main__":
    unittest.main()
