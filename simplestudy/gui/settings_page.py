"""Settings page: edits config.toml and asks the daemon to reload it."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("GObject", "2.0")
from gi.repository import Adw, GObject, Gtk  # noqa: E402

from ..core.schedule import WEEKDAY_NAMES, DayKind
from ..storage import config as config_mod
from .client import DaemonClient

_MODES = ["carried", "optional", "free"]


class SettingsPage(Adw.PreferencesPage):
    __gsignals__ = {
        # Emitted after the config file is written. ``object`` = Config.
        "config-saved": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, client: DaemonClient, path=None) -> None:
        super().__init__()
        self.client = client
        self.path = path
        self._loading = False
        self._config = config_mod.load(path)
        self._weekday_rows: dict[int, tuple[object, Adw.ComboRow]] = {}
        self._build()
        self._populate(self._config)

    # -- construction ---------------------------------------------------

    def _build(self) -> None:
        general = Adw.PreferencesGroup(title="General")
        general.set_description("How the day and its roll-over behave.")

        self.boundary_row = Adw.EntryRow(title="Day boundary (HH:MM)")
        self.boundary_row.connect("changed", self._on_changed)
        general.add(self.boundary_row)

        self.autostart_row = Adw.SwitchRow(title="Start studying automatically")
        self.autostart_row.set_subtitle("Begin the session as soon as the daemon starts.")
        self.autostart_row.connect("notify::active", self._on_changed)
        general.add(self.autostart_row)

        self.notifications_row = Adw.SwitchRow(title="Desktop notifications")
        self.notifications_row.set_subtitle("Notify on phase changes and day completion.")
        self.notifications_row.connect("notify::active", self._on_changed)
        general.add(self.notifications_row)
        self.add(general)

        pomodoro = Adw.PreferencesGroup(title="Pomodoro")
        pomodoro.set_description("Breaks count towards the daily goal.")
        self.study_row = self._spin_row("Study length", "Minutes of focused study.", 1, 240)
        self.break_row = self._spin_row("Break length", "Minutes of break.", 1, 120)
        pomodoro.add(self.study_row)
        pomodoro.add(self.break_row)
        self.add(pomodoro)

        self._build_appearance()

        weekdays = Adw.PreferencesGroup(
            title="Weekly schedule",
            description=(
                "carried = unmet time becomes debt. optional = a goal only, "
                "discarded if unmet. free = rest day."
            ),
        )
        for index, name in enumerate(WEEKDAY_NAMES):
            quota_row = self._spin_row(name.capitalize(), "", 0, 24 * 60)
            mode_row = Adw.ComboRow(title=f"{name.capitalize()} mode")
            mode_row.set_model(Gtk.StringList.new(_MODES))
            mode_row.connect("notify::selected", self._on_changed)
            weekdays.add(quota_row)
            weekdays.add(mode_row)
            self._weekday_rows[index] = (quota_row, mode_row)
        self.add(weekdays)

    def _build_appearance(self) -> None:
        appearance = Adw.PreferencesGroup(title="Appearance")
        appearance.set_description(
            "Fade the window when it is left idle or moved to the background. "
            "Opacity is a percentage (higher = more visible)."
        )

        self.idle_timeout_row = self._spin_row(
            "Idle delay", "Seconds without interaction before fading.", 0, 600
        )
        self.idle_timeout_row.get_adjustment().set_step_increment(1)
        self.idle_opacity_row = self._spin_row(
            "Idle opacity", "Opacity percentage when faded.", 0, 100
        )
        self.idle_opacity_row.get_adjustment().set_step_increment(1)
        self.active_opacity_row = self._spin_row(
            "Active opacity", "Opacity percentage while in use.", 0, 100
        )
        self.active_opacity_row.get_adjustment().set_step_increment(1)
        self.fade_row = self._spin_row(
            "Fade duration", "Milliseconds for the fade-out animation.", 0, 2000
        )
        self.fade_row.get_adjustment().set_step_increment(50)

        for row in (
            self.idle_timeout_row,
            self.idle_opacity_row,
            self.active_opacity_row,
            self.fade_row,
        ):
            appearance.add(row)
        self.add(appearance)

    def _spin_row(self, title: str, subtitle: str, lower: int, upper: int) -> Adw.SpinRow:
        adjustment = Gtk.Adjustment(value=0, lower=lower, upper=upper, step_increment=5)
        row = Adw.SpinRow(title=title, subtitle=subtitle, adjustment=adjustment)
        row.connect("notify::value", self._on_changed)
        return row

    # -- data binding ---------------------------------------------------

    def _populate(self, config) -> None:
        self._loading = True
        self.boundary_row.set_text(config_mod.format_boundary(config.boundary))
        self.autostart_row.set_active(config.auto_start)
        self.notifications_row.set_active(config.notifications)
        self.study_row.set_value(config.study_seconds // 60)
        self.break_row.set_value(config.break_seconds // 60)
        transparency = config.transparency
        self.idle_timeout_row.set_value(transparency.idle_timeout)
        self.idle_opacity_row.set_value(round(transparency.idle_opacity * 100))
        self.active_opacity_row.set_value(round(transparency.active_opacity * 100))
        self.fade_row.set_value(transparency.fade_ms)
        for index, (quota_row, mode_row) in self._weekday_rows.items():
            policy = config.schedule.days[index]
            quota_row.set_value(policy.quota_seconds // 60)
            mode_row.set_selected(_MODES.index(_kind_to_mode(policy.kind)))
            quota_row.set_sensitive(policy.kind is not DayKind.FREE)
        self._loading = False

    def _on_changed(self, *_args) -> None:
        if self._loading:
            return
        try:
            config = self._collect()
        except ValueError:
            return
        self._config = config
        config_mod.save(config, self.path)
        self.emit("config-saved", config)
        self.client.reload_config()

    def _collect(self):
        boundary = config_mod.parse_boundary(self.boundary_row.get_text())
        days = dict(self._config.schedule.days)
        for index, (quota_row, mode_row) in self._weekday_rows.items():
            mode = _MODES[mode_row.get_selected()]
            kind = _mode_to_kind(mode)
            quota_seconds = 0 if kind is DayKind.FREE else int(quota_row.get_value()) * 60
            days[index] = DayPolicy(quota_seconds, kind)

        from ..core.schedule import Schedule

        transparency = config_mod.TransparencyConfig(
            idle_timeout=int(self.idle_timeout_row.get_value()),
            idle_opacity=float(self.idle_opacity_row.get_value()) / 100.0,
            active_opacity=float(self.active_opacity_row.get_value()) / 100.0,
            fade_ms=int(self.fade_row.get_value()),
        )

        return config_mod.Config(
            boundary=boundary,
            auto_start=self.autostart_row.get_active(),
            study_seconds=int(self.study_row.get_value()) * 60,
            break_seconds=int(self.break_row.get_value()) * 60,
            notifications=self.notifications_row.get_active(),
            schedule=Schedule(days),
            transparency=transparency,
        )


def _kind_to_mode(kind: DayKind) -> str:
    return {
        DayKind.CARRIED: "carried",
        DayKind.OPTIONAL: "optional",
        DayKind.FREE: "free",
    }[kind]


def _mode_to_kind(mode: str) -> DayKind:
    return {
        "carried": DayKind.CARRIED,
        "optional": DayKind.OPTIONAL,
        "free": DayKind.FREE,
    }[mode]
