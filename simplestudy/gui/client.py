"""Client-side wrapper around the daemon's D-Bus interface.

The GUI never touches the state files directly: it asks the daemon for
snapshots and subscribes to the ``Changed`` signal. This keeps the daemon the
single source of truth even when several windows are open.
"""

from __future__ import annotations

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

    def _call(self, method: str) -> None:
        if not self.available:
            return
        try:
            self._proxy.call_sync(method, None, Gio.DBusCallFlags.NONE, -1, None)
        except GLib.Error:
            pass
        self.refresh()

    def start(self) -> None:
        self._call("Start")

    def pause(self) -> None:
        self._call("Pause")

    def skip_phase(self) -> None:
        self._call("SkipPhase")

    def reload_config(self) -> None:
        self._call("ReloadConfig")

    def _on_signal(self, _proxy, _sender, signal_name: str, parameters: GLib.Variant) -> None:
        if signal_name == "Changed":
            self.emit("changed", parameters.unpack()[0])
