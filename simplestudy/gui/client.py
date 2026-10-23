"""Client-side wrapper around the daemon's D-Bus interface.

The GUI never touches the state files directly: it asks the daemon for
snapshots and subscribes to the ``Changed`` signal. This keeps the daemon the
single source of truth even when several windows are open.
"""

from __future__ import annotations

import json

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("GObject", "2.0")
from gi.repository import Gio, GLib, GObject  # noqa: E402

from .. import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH
from ..daemon.app import INTROSPECTION_XML


class DaemonClient(GObject.Object):
    """Talks to ``org.simplestudy.Daemon`` on the session bus."""

    __gsignals__ = {
        # snapshot dict (or None when unavailable)
        "changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # the checklist structure changed; callers should refetch
        "checklist-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__()
        self._proxy: Gio.DBusProxy | None = None
        self._node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)

    def connect_bus(self) -> None:
        self._proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            self._node_info.interfaces[0],
            DBUS_NAME,
            DBUS_PATH,
            DBUS_INTERFACE,
            None,
        )
        self._proxy.connect("g-signal", self._on_signal)
        self._proxy.connect("notify::g-name-owner", lambda *_: self.refresh())

    @property
    def available(self) -> bool:
        return self._proxy is not None and self._proxy.get_name_owner() is not None

    # -- queries --------------------------------------------------------

    def refresh(self) -> None:
        if not self.available:
            self.emit("changed", None)
            return
        try:
            result = self._proxy.call_sync(
                "GetSnapshot", None, Gio.DBusCallFlags.NONE, -1, None
            )
        except GLib.Error:
            self.emit("changed", None)
            return
        self.emit("changed", result.unpack()[0])

    # -- actions --------------------------------------------------------

    def get_checklist(self) -> dict:
        """Fetch and decode the full checklist, or ``{}`` when unavailable."""
        if not self.available:
            return {}
        try:
            result = self._proxy.call_sync(
                "GetChecklist", None, Gio.DBusCallFlags.NONE, -1, None
            )
            payload = result.unpack()[0]
            data = json.loads(payload)
        except (GLib.Error, IndexError, TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _call(self, method: str, parameters: GLib.Variant | None = None):
        if not self.available:
            return None
        try:
            result = self._proxy.call_sync(
                method, parameters, Gio.DBusCallFlags.NONE, -1, None
            )
        except GLib.Error:
            return None
        self.refresh()
        return result

    def _call_for_id(self, method: str, parameters: GLib.Variant) -> str | None:
        result = self._call(method, parameters)
        if result is None:
            return None
        try:
            return str(result.unpack()[0])
        except (IndexError, TypeError):
            return None

    def start(self) -> None:
        self._call("Start")

    def pause(self) -> None:
        self._call("Pause")

    def skip_phase(self) -> None:
        self._call("SkipPhase")

    def reload_config(self) -> None:
        self._call("ReloadConfig")

    # -- checklist actions ---------------------------------------------

    def add_subject(self, name: str) -> str | None:
        return self._call_for_id("AddSubject", GLib.Variant("(s)", (name,)))

    def rename_subject(self, subject_id: str, name: str) -> None:
        self._call("RenameSubject", GLib.Variant("(ss)", (subject_id, name)))

    def remove_subject(self, subject_id: str) -> None:
        self._call("RemoveSubject", GLib.Variant("(s)", (subject_id,)))

    def select_subject(self, subject_id: str) -> None:
        self._call("SelectSubject", GLib.Variant("(s)", (subject_id,)))

    def add_item(
        self, subject_id: str, text: str, duration_seconds: int = 0
    ) -> str | None:
        return self._call_for_id(
            "AddItem", GLib.Variant("(ssu)", (subject_id, text, duration_seconds))
        )

    def rename_item(self, item_id: str, text: str) -> None:
        self._call("RenameItem", GLib.Variant("(ss)", (item_id, text)))

    def set_item_duration(self, item_id: str, seconds: int) -> None:
        self._call("SetItemDuration", GLib.Variant("(su)", (item_id, seconds)))

    def remove_item(self, item_id: str) -> None:
        self._call("RemoveItem", GLib.Variant("(s)", (item_id,)))

    def check_item(self, item_id: str) -> None:
        self._call("CheckItem", GLib.Variant("(s)", (item_id,)))

    def uncheck_last_item(self, subject_id: str) -> None:
        self._call("UncheckLastItem", GLib.Variant("(s)", (subject_id,)))

    def reset_subject(self, subject_id: str) -> None:
        self._call("ResetSubject", GLib.Variant("(s)", (subject_id,)))

    def _on_signal(self, _proxy, _sender, signal_name: str, parameters: GLib.Variant) -> None:
        if signal_name == "Changed":
            self.emit("changed", parameters.unpack()[0])
        elif signal_name == "ChecklistChanged":
            self.emit("checklist-changed")
