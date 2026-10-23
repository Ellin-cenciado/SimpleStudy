"""SimpleStudy GUI: a libadwaita window backed by the daemon.

The window is a thin client. It shows a live timer, a stats page and a settings
page, and it reflects whatever the daemon reports over D-Bus.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import APP_ID, APP_NAME
from ..storage import config as config_mod
from .checklist_page import ChecklistPage
from .client import DaemonClient
from .opacity import OpacityController
from .settings_page import SettingsPage
from .stats_page import StatsPage
from .timer_page import TimerPage

log = logging.getLogger("simplestudy.gui")


class SimpleStudyWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, client: DaemonClient) -> None:
        super().__init__(application=application)
        self.client = client
        self.set_title(APP_NAME)
        self.set_default_size(460, 620)

        self.toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        self.view_switcher = Adw.ViewSwitcher()
        self.view_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(self.view_switcher)

        # A small refresh action so the user can force a sync.
        refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_button.set_tooltip_text("Refresh")
        refresh_button.connect("clicked", lambda *_: self.client.refresh())
        header.pack_end(refresh_button)
        self.toolbar_view.add_top_bar(header)

        self.banner = Adw.Banner(title="The SimpleStudy daemon is not running.")
        self.banner.set_revealed(False)
        self.toolbar_view.add_top_bar(self.banner)

        self.stack = Adw.ViewStack()
        self.view_switcher.set_stack(self.stack)

        self.timer_page = TimerPage(client)
        self.stats_page = StatsPage(client)
        self.settings_page = SettingsPage(client)
        self.checklist_page = ChecklistPage(client)

        self.stack.add_titled_with_icon(self.timer_page, "timer", "Timer", "alarm-symbolic")
        self.stack.add_titled_with_icon(self.checklist_page, "checklist", "Checklist", "view-list-symbolic")
        self.stack.add_titled_with_icon(self.stats_page, "stats", "Stats", "org.gnome.Software-symbolic")
        self.stack.add_titled_with_icon(self.settings_page, "settings", "Settings", "emblem-system-symbolic")

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.stack)
        self.toolbar_view.set_content(self.toast_overlay)

        self.set_content(self.toolbar_view)

        # Auto-transparency after idle / when in the background.
        config = config_mod.load()
        self.opacity_controller = OpacityController(self, config.transparency)

        client.connect("changed", self._on_changed)
        client.connect("checklist-changed", lambda *_: self.checklist_page.refetch())
        self.settings_page.connect("config-saved", self._on_config_saved)

    def _on_config_saved(self, _page, config) -> None:
        self.opacity_controller.apply_config(config.transparency)

    def _on_changed(self, _client: DaemonClient, snap: object) -> None:
        snapshot = snap if isinstance(snap, dict) else None
        self.banner.set_revealed(snapshot is None)
        self.timer_page.update(snapshot)
        self.checklist_page.update(snapshot)


class SimpleStudyApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.client = DaemonClient()
        self.window: SimpleStudyWindow | None = None

    def do_activate(self) -> None:
        if self.window is None:
            self.client.connect_bus()
            self.client.connect("changed", lambda *_: self._poll_stats())
            self.window = SimpleStudyWindow(self, self.client)
        self.window.present()
        self.client.refresh()

    def _poll_stats(self) -> None:
        # Cheap enough to refresh the stats view whenever the daemon reports.
        if self.window is not None:
            self.window.stats_page.refresh()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    app = SimpleStudyApplication()
    return app.run(argv if argv is not None else [])
