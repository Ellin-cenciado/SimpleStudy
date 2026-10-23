"""GLib/D-Bus front-end for the daemon.

Owns the well-known bus name ``org.simplestudy.Daemon``, registers the object,
ticks the service once a second and emits change signals for the GUI.
"""

from __future__ import annotations

import logging
import signal

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .. import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH
from ..core.tracker import Snapshot
from ..storage import config as config_mod
from ..storage import paths
from .notify import GioNotifier
from .service import ChecklistSummary, StudyService

log = logging.getLogger("simplestudy.daemon")

INTROSPECTION_XML = """
<node>
  <interface name="org.simplestudy.Daemon">
    <method name="Start"/>
    <method name="Pause"/>
    <method name="SkipPhase"/>
    <method name="ReloadConfig"/>
    <method name="GetSnapshot">
      <arg type="a{sv}" name="snapshot" direction="out"/>
    </method>
    <method name="GetChecklist">
      <arg type="s" name="json" direction="out"/>
    </method>
    <method name="AddSubject">
      <arg type="s" name="name" direction="in"/>
      <arg type="s" name="id" direction="out"/>
    </method>
    <method name="RenameSubject">
      <arg type="s" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
    </method>
    <method name="RemoveSubject">
      <arg type="s" name="id" direction="in"/>
    </method>
    <method name="SelectSubject">
      <arg type="s" name="id" direction="in"/>
    </method>
    <method name="AddItem">
      <arg type="s" name="subject_id" direction="in"/>
      <arg type="s" name="text" direction="in"/>
      <arg type="u" name="duration_seconds" direction="in"/>
      <arg type="s" name="id" direction="out"/>
    </method>
    <method name="RenameItem">
      <arg type="s" name="id" direction="in"/>
      <arg type="s" name="text" direction="in"/>
    </method>
    <method name="SetItemDuration">
      <arg type="s" name="id" direction="in"/>
      <arg type="u" name="seconds" direction="in"/>
    </method>
    <method name="RemoveItem">
      <arg type="s" name="id" direction="in"/>
    </method>
    <method name="CheckItem">
      <arg type="s" name="id" direction="in"/>
    </method>
    <method name="UncheckLastItem">
      <arg type="s" name="subject_id" direction="in"/>
    </method>
    <method name="ResetSubject">
      <arg type="s" name="subject_id" direction="in"/>
    </method>
    <property name="LogicalDay" type="s" access="read"/>
    <property name="Weekday" type="s" access="read"/>
    <property name="DayKind" type="s" access="read"/>
    <property name="RequiredSeconds" type="u" access="read"/>
    <property name="ElapsedSeconds" type="u" access="read"/>
    <property name="RemainingSeconds" type="u" access="read"/>
    <property name="DebtSeconds" type="u" access="read"/>
    <property name="CarryInSeconds" type="u" access="read"/>
    <property name="OptionalGoalSeconds" type="u" access="read"/>
    <property name="OptionalDoneSeconds" type="u" access="read"/>
    <property name="Phase" type="s" access="read"/>
    <property name="PhaseRemainingSeconds" type="u" access="read"/>
    <property name="Running" type="b" access="read"/>
    <property name="DayComplete" type="b" access="read"/>
    <property name="IsFreeDay" type="b" access="read"/>
    <property name="ActiveSubjectId" type="s" access="read"/>
    <property name="ActiveItemId" type="s" access="read"/>
    <property name="ActiveItemRemainingSeconds" type="i" access="read"/>
    <property name="ActiveItemOverrunSeconds" type="u" access="read"/>
    <property name="RestRemainingSeconds" type="u" access="read"/>
    <property name="ChecklistRevision" type="u" access="read"/>
    <signal name="Changed">
      <arg type="a{sv}" name="snapshot"/>
    </signal>
    <signal name="PhaseChanged">
      <arg type="s" name="phase"/>
    </signal>
    <signal name="DayCompleted"/>
    <signal name="DayRolled">
      <arg type="s" name="date"/>
    </signal>
    <signal name="ChecklistChanged"/>
  </interface>
</node>
"""


def snapshot_to_variant(
    snapshot: Snapshot, checklist: ChecklistSummary | None = None
) -> GLib.Variant:
    """Convert a :class:`Snapshot` to a D-Bus ``a{sv}`` dictionary."""
    checklist = checklist or ChecklistSummary()
    fields: dict[str, GLib.Variant] = {
        "LogicalDay": GLib.Variant("s", snapshot.logical_day.isoformat()),
        "Weekday": GLib.Variant("s", snapshot.weekday),
        "DayKind": GLib.Variant("s", snapshot.day_kind),
        "RequiredSeconds": GLib.Variant("u", snapshot.required_seconds),
        "ElapsedSeconds": GLib.Variant("u", snapshot.elapsed_seconds),
        "RemainingSeconds": GLib.Variant("u", snapshot.remaining_seconds),
        "DebtSeconds": GLib.Variant("u", snapshot.debt_seconds),
        "CarryInSeconds": GLib.Variant("u", snapshot.debt_seconds),
        "OptionalGoalSeconds": GLib.Variant("u", snapshot.optional_goal_seconds),
        "OptionalDoneSeconds": GLib.Variant("u", snapshot.optional_done_seconds),
        "Phase": GLib.Variant("s", snapshot.phase),
        "PhaseRemainingSeconds": GLib.Variant("u", snapshot.phase_remaining_seconds),
        "Running": GLib.Variant("b", snapshot.running),
        "DayComplete": GLib.Variant("b", snapshot.day_complete),
        "IsFreeDay": GLib.Variant("b", snapshot.is_free_day),
        "ActiveSubjectId": GLib.Variant("s", checklist.active_subject_id),
        "ActiveItemId": GLib.Variant("s", checklist.active_item_id),
        "ActiveItemRemainingSeconds": GLib.Variant("i", checklist.remaining_seconds),
        "ActiveItemOverrunSeconds": GLib.Variant("u", checklist.overrun_seconds),
        "RestRemainingSeconds": GLib.Variant("u", checklist.rest_remaining_seconds),
        "ChecklistRevision": GLib.Variant("u", checklist.revision),
    }
    return GLib.Variant("a{sv}", fields)


class Daemon:
    def __init__(self, config_path=None, *, persist: bool = True) -> None:
        self.config_path = config_path or paths.config_path()
        self.persist = persist
        self.loop = GLib.MainLoop()
        self.connection: Gio.DBusConnection | None = None
        self.service: StudyService | None = None
        self._tick_source: int | None = None

        self.config = config_mod.load(self.config_path)
        if not self.config_path.exists():
            config_mod.save(self.config, self.config_path)

        self._node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        self._iface_info = self._node_info.interfaces[0]

    # -- lifecycle ------------------------------------------------------

    def run(self) -> int:
        Gio.bus_own_name(
            Gio.BusType.SESSION,
            DBUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            self._on_name_acquired,
            self._on_name_lost,
        )
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._quit)
        self.loop.run()
        return 0

    def _quit(self) -> bool:
        log.info("shutting down")
        if self.service is not None:
            self.service.pause()
            self.service.close()
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def _on_bus_acquired(self, connection: Gio.DBusConnection, name: str) -> None:
        self.connection = connection
        notifier = GioNotifier()
        self.service = StudyService(
            self.config,
            notifier=notifier,
            persist=self.persist,
        )
        connection.register_object(
            DBUS_PATH,
            self._iface_info,
            self._on_method_call,
            self._on_get_property,
            None,
        )
        if self.config.auto_start:
            self.service.start()
        self._tick_source = GLib.timeout_add_seconds(1, self._on_tick)
        log.info("daemon ready on %s", DBUS_NAME)

    def _on_name_acquired(self, connection: Gio.DBusConnection, name: str) -> None:
        log.info("acquired bus name %s", name)

    def _on_name_lost(self, connection: Gio.DBusConnection | None, name: str) -> None:
        if connection is None:
            log.error("could not connect to the session bus")
        else:
            log.error("another %s instance is already running", name)
        self.loop.quit()

    def _on_tick(self) -> bool:
        assert self.service is not None
        revision_before = self.service.checklist_revision()
        events = self.service.tick()
        if self.service.checklist_revision() != revision_before:
            self._emit_signal("ChecklistChanged", None)
        self._emit_changed()
        for event in events:
            if event.kind == "phase":
                self._emit_signal("PhaseChanged", GLib.Variant("(s)", (self.service.snapshot().phase,)))
            elif event.kind == "day_complete":
                self._emit_signal("DayCompleted", None)
            elif event.kind == "day_rolled":
                self._emit_signal(
                    "DayRolled",
                    GLib.Variant("(s)", (self.service.snapshot().logical_day.isoformat(),)),
                )
        return GLib.SOURCE_CONTINUE

    # -- D-Bus plumbing -------------------------------------------------

    def _emit_signal(self, name: str, parameters: GLib.Variant | None) -> None:
        assert self.connection is not None
        self.connection.emit_signal(None, DBUS_PATH, DBUS_INTERFACE, name, parameters)

    def _emit_changed(self) -> None:
        assert self.service is not None
        snapshot = snapshot_to_variant(
            self.service.snapshot(), self.service.checklist_summary()
        )
        self._emit_signal("Changed", GLib.Variant.new_tuple(snapshot))

    def _on_method_call(
        self,
        connection: Gio.DBusConnection,
        sender: str,
        object_path: str,
        interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        assert self.service is not None
        revision_before = self.service.checklist_revision()
        try:
            if method_name == "Start":
                self.service.start()
                invocation.return_value(None)
            elif method_name == "Pause":
                self.service.pause()
                invocation.return_value(None)
            elif method_name == "SkipPhase":
                self.service.skip_phase()
                invocation.return_value(None)
            elif method_name == "ReloadConfig":
                self.config = config_mod.load(self.config_path)
                self.service.reload_config(self.config)
                invocation.return_value(None)
            elif method_name == "GetSnapshot":
                snapshot = snapshot_to_variant(
                    self.service.snapshot(), self.service.checklist_summary()
                )
                invocation.return_value(GLib.Variant.new_tuple(snapshot))
            elif method_name == "GetChecklist":
                invocation.return_value(
                    GLib.Variant.new_tuple(
                        GLib.Variant("s", self.service.checklist_json())
                    )
                )
            elif method_name == "AddSubject":
                (name,) = parameters.unpack()
                new_id = self.service.add_subject(name)
                invocation.return_value(
                    GLib.Variant.new_tuple(GLib.Variant("s", new_id))
                )
            elif method_name == "RenameSubject":
                subject_id, name = parameters.unpack()
                self.service.rename_subject(subject_id, name)
                invocation.return_value(None)
            elif method_name == "RemoveSubject":
                (subject_id,) = parameters.unpack()
                self.service.remove_subject(subject_id)
                invocation.return_value(None)
            elif method_name == "SelectSubject":
                (subject_id,) = parameters.unpack()
                self.service.select_subject(subject_id)
                invocation.return_value(None)
            elif method_name == "AddItem":
                subject_id, text, duration = parameters.unpack()
                new_id = self.service.add_item(subject_id, text, int(duration))
                invocation.return_value(
                    GLib.Variant.new_tuple(GLib.Variant("s", new_id))
                )
            elif method_name == "RenameItem":
                item_id, text = parameters.unpack()
                self.service.rename_item(item_id, text)
                invocation.return_value(None)
            elif method_name == "SetItemDuration":
                item_id, seconds = parameters.unpack()
                self.service.set_item_duration(item_id, int(seconds))
                invocation.return_value(None)
            elif method_name == "RemoveItem":
                (item_id,) = parameters.unpack()
                self.service.remove_item(item_id)
                invocation.return_value(None)
            elif method_name == "CheckItem":
                (item_id,) = parameters.unpack()
                self.service.check_item(item_id)
                invocation.return_value(None)
            elif method_name == "UncheckLastItem":
                (subject_id,) = parameters.unpack()
                self.service.uncheck_last_item(subject_id)
                invocation.return_value(None)
            elif method_name == "ResetSubject":
                (subject_id,) = parameters.unpack()
                self.service.reset_subject(subject_id)
                invocation.return_value(None)
            else:
                invocation.return_dbus_error(
                    DBUS_INTERFACE + ".UnknownMethod", f"Unknown method {method_name}"
                )
        except ValueError as error:
            invocation.return_dbus_error(DBUS_INTERFACE + ".InvalidArgument", str(error))
        finally:
            if self.service.checklist_revision() != revision_before:
                self._emit_signal("ChecklistChanged", None)
            self._emit_changed()

    def _on_get_property(
        self,
        connection: Gio.DBusConnection,
        sender: str,
        object_path: str,
        interface_name: str,
        property_name: str,
        error: GLib.Error | None = None,
    ) -> GLib.Variant | None:
        assert self.service is not None
        snapshot = self.service.snapshot()
        checklist = self.service.checklist_summary()
        mapping = {
            "LogicalDay": GLib.Variant("s", snapshot.logical_day.isoformat()),
            "Weekday": GLib.Variant("s", snapshot.weekday),
            "DayKind": GLib.Variant("s", snapshot.day_kind),
            "RequiredSeconds": GLib.Variant("u", snapshot.required_seconds),
            "ElapsedSeconds": GLib.Variant("u", snapshot.elapsed_seconds),
            "RemainingSeconds": GLib.Variant("u", snapshot.remaining_seconds),
            "DebtSeconds": GLib.Variant("u", snapshot.debt_seconds),
            "CarryInSeconds": GLib.Variant("u", snapshot.debt_seconds),
            "OptionalGoalSeconds": GLib.Variant("u", snapshot.optional_goal_seconds),
            "OptionalDoneSeconds": GLib.Variant("u", snapshot.optional_done_seconds),
            "Phase": GLib.Variant("s", snapshot.phase),
            "PhaseRemainingSeconds": GLib.Variant("u", snapshot.phase_remaining_seconds),
            "Running": GLib.Variant("b", snapshot.running),
            "DayComplete": GLib.Variant("b", snapshot.day_complete),
            "IsFreeDay": GLib.Variant("b", snapshot.is_free_day),
            "ActiveSubjectId": GLib.Variant("s", checklist.active_subject_id),
            "ActiveItemId": GLib.Variant("s", checklist.active_item_id),
            "ActiveItemRemainingSeconds": GLib.Variant(
                "i", checklist.remaining_seconds
            ),
            "ActiveItemOverrunSeconds": GLib.Variant(
                "u", checklist.overrun_seconds
            ),
            "RestRemainingSeconds": GLib.Variant(
                "u", checklist.rest_remaining_seconds
            ),
            "ChecklistRevision": GLib.Variant("u", checklist.revision),
        }
        return mapping.get(property_name)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="simplestudy-daemon", description="SimpleStudy daemon")
    parser.add_argument("--config", type=str, default=None, help="path to config.toml")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    paths.ensure_dirs()
    daemon = Daemon(config_path=args.config)
    return daemon.run()
