"""The main timer page."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..daemon.service import format_hours
from .client import DaemonClient

CSS = """
.countdown {
    font-feature-settings: "tnum";
    font-size: 64px;
    font-weight: 800;
}
.phase-label {
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 2px;
}
.debt-badge {
    font-weight: 600;
}
"""


def format_clock(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class TimerPage(Gtk.Box):
    def __init__(self, client: DaemonClient) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.client = client
        self.set_margin_top(24)
        self.set_margin_bottom(24)
        self.set_margin_start(24)
        self.set_margin_end(24)
        self.set_valign(Gtk.Align.CENTER)
        self.set_vexpand(True)

        self._install_css()

        self.phase_label = Gtk.Label(label="READY")
        self.phase_label.add_css_class("phase-label")
        self.phase_label.add_css_class("dim-label")

        self.countdown_label = Gtk.Label(label="--:--")
        self.countdown_label.add_css_class("countdown")

        self.remaining_label = Gtk.Label(label="")
        self.remaining_label.add_css_class("dim-label")

        self.progress = Gtk.ProgressBar()
        self.progress.set_hexpand(True)
        self.progress.set_size_request(-1, 8)
        self.progress.add_css_class("osd")

        self.debt_label = Gtk.Label(label="")
        self.debt_label.add_css_class("debt-badge")
        self.debt_label.add_css_class("warning")
        self.debt_label.set_visible(False)

        # Saturday's optional goal gets its own row.
        self.optional_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.optional_label = Gtk.Label(label="")
        self.optional_label.add_css_class("dim-label")
        self.optional_bar = Gtk.ProgressBar()
        self.optional_box.append(self.optional_label)
        self.optional_box.append(self.optional_bar)
        self.optional_box.set_visible(False)

        # Buttons.
        self.primary_button = Gtk.Button(label="Start")
        self.primary_button.add_css_class("suggested-action")
        self.primary_button.add_css_class("pill")
        self.primary_button.set_size_request(160, -1)
        self.primary_button.connect("clicked", self._on_primary)

        self.skip_button = Gtk.Button(label="Skip phase")
        self.skip_button.add_css_class("pill")
        self.skip_button.connect("clicked", lambda *_: self.client.skip_phase())

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        buttons.set_halign(Gtk.Align.CENTER)
        buttons.append(self.primary_button)
        buttons.append(self.skip_button)

        self.status_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        self.status_icon.set_pixel_size(48)
        self.status_icon.set_visible(False)

        for widget in (
            self.phase_label,
            self.countdown_label,
            self.remaining_label,
            self.progress,
            self.optional_box,
            self.debt_label,
            buttons,
            self.status_icon,
        ):
            if isinstance(widget, (Gtk.ProgressBar,)):
                wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                wrapper.set_size_request(380, -1)
                wrapper.set_halign(Gtk.Align.CENTER)
                wrapper.append(widget)
                self.append(wrapper)
            else:
                self.append(widget)

        self._running = False

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        display = self.get_display()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    # -- callbacks ------------------------------------------------------

    def _on_primary(self, _button) -> None:
        if self._running:
            self.client.pause()
        else:
            self.client.start()

    # -- rendering ------------------------------------------------------

    def update(self, snap: dict | None) -> None:
        self.primary_button.set_sensitive(snap is not None)
        self.skip_button.set_sensitive(snap is not None)
        if snap is None:
            self.phase_label.set_label("DAEMON OFFLINE")
            self.countdown_label.set_label("--:--")
            self.remaining_label.set_label("Start the SimpleStudy daemon to begin.")
            self.progress.set_fraction(0)
            self.optional_box.set_visible(False)
            self.debt_label.set_visible(False)
            self.status_icon.set_visible(False)
            self.primary_button.set_label("Start")
            return

        self._running = bool(snap["Running"])
        complete = bool(snap["DayComplete"])
        free_day = bool(snap["IsFreeDay"])

        if complete:
            self.phase_label.set_label("DAY COMPLETE")
            self.countdown_label.set_label("Done")
            self.status_icon.set_visible(True)
            self.progress.set_fraction(1.0)
        elif free_day:
            self.phase_label.set_label("FREE DAY")
            self.countdown_label.set_label("Rest")
            self.status_icon.set_visible(False)
            self.progress.set_fraction(0.0)
        else:
            self.status_icon.set_visible(False)
            self.phase_label.set_label(snap["Phase"].upper())
            self.countdown_label.set_label(format_clock(snap["PhaseRemainingSeconds"]))
            required = max(1, snap["RequiredSeconds"])
            done = min(snap["ElapsedSeconds"], required)
            self.progress.set_fraction(done / required)

        if free_day:
            self.remaining_label.set_label("No study required today.")
        elif complete:
            self.remaining_label.set_label(
                f"Required {format_hours(snap['RequiredSeconds'])} - all done."
            )
        else:
            self.remaining_label.set_label(
                f"{format_hours(snap['RemainingSeconds'])} remaining of "
                f"{format_hours(snap['RequiredSeconds'])}"
            )

        if snap["CarryInSeconds"] and not complete:
            self.debt_label.set_label(
                f"Carried in: {format_hours(snap['CarryInSeconds'])} debt"
            )
            self.debt_label.set_visible(True)
        else:
            self.debt_label.set_visible(False)

        goal = snap["OptionalGoalSeconds"]
        if goal:
            done = snap["OptionalDoneSeconds"]
            self.optional_label.set_label(
                f"Saturday optional: {format_hours(done)} / {format_hours(goal)}"
            )
            self.optional_bar.set_fraction(done / goal)
            self.optional_box.set_visible(True)
        else:
            self.optional_box.set_visible(False)

        self.primary_button.set_label("Pause" if self._running else "Start")
        self.primary_button.set_sensitive(not complete and not free_day)
