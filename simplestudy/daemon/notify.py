"""Desktop notification backend.

The daemon talks to ``org.freedesktop.Notifications`` over the session bus via
Gio, so notifications work even when no GUI window is open. A :class:`NullNotifier`
is used in tests.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402


@runtime_checkable
class Notifier(Protocol):
    def notify(self, summary: str, body: str, *, urgent: bool = False) -> None: ...


class NullNotifier:
    """Does nothing; used in tests and when notifications are disabled."""

    def notify(self, summary: str, body: str, *, urgent: bool = False) -> None:
        return None


class GioNotifier:
    """Sends a desktop notification through the session bus."""

    def __init__(self, app_name: str = "SimpleStudy") -> None:
        self._app_name = app_name
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._counter = 1

    def notify(self, summary: str, body: str, *, urgent: bool = False) -> None:
        try:
            self._bus.call_sync(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
                GLib.Variant(
                    "(susssasa{sv}i)",
                    (
                        self._app_name,
                        0,  # replaces_id
                        "dialog-information" if not urgent else "dialog-warning",
                        summary,
                        body,
                        [],  # actions
                        {"urgency": GLib.Variant("y", 2 if urgent else 1)},
                        -1,  # timeout: default
                    ),
                ),
                GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except GLib.Error:
            # The notification service may be unavailable; never crash for this.
            pass
