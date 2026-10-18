"""User configuration: parsing and writing ``config.toml``.

The file is intentionally human-editable. A missing file is not an error - the
defaults are used and can be persisted on demand.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from ..core.schedule import (
    WEEKDAY_INDEX,
    WEEKDAY_NAMES,
    DayKind,
    DayPolicy,
    Schedule,
    default_schedule,
)

_MODE_TO_KIND = {
    "carried": DayKind.CARRIED,
    "optional": DayKind.OPTIONAL,
    "free": DayKind.FREE,
}
_KIND_TO_MODE = {v: k for k, v in _MODE_TO_KIND.items()}


@dataclass
class TransparencyConfig:
    idle_timeout: int = 5
    idle_opacity: float = 0.08
    active_opacity: float = 0.90
    fade_ms: int = 300

    def __post_init__(self) -> None:
        if self.idle_timeout < 0:
            raise ValueError("idle_timeout must be >= 0")
        if not (0.0 <= self.idle_opacity <= 1.0):
            raise ValueError("idle_opacity must be between 0.0 and 1.0")
        if not (0.0 <= self.active_opacity <= 1.0):
            raise ValueError("active_opacity must be between 0.0 and 1.0")
        if self.fade_ms < 0:
            raise ValueError("fade_ms must be >= 0")


@dataclass
class Config:
    boundary: time = field(default_factory=lambda: time(4, 0))
    auto_start: bool = False
    study_seconds: int = 40 * 60
    break_seconds: int = 10 * 60
    notifications: bool = True
    schedule: Schedule = field(default_factory=default_schedule)
    transparency: TransparencyConfig = field(default_factory=TransparencyConfig)


def parse_boundary(value: str) -> time:
    """Parse ``"HH:MM"`` into a :class:`datetime.time`."""
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid day_boundary {value!r}, expected HH:MM")
    hour, minute = (int(p) for p in parts)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"invalid day_boundary {value!r}")
    return time(hour, minute)


def format_boundary(boundary: time) -> str:
    return f"{boundary.hour:02d}:{boundary.minute:02d}"


def loads(text: str) -> Config:
    data = tomllib.loads(text) if text.strip() else {}

    general = data.get("general", {})
    pomodoro = data.get("pomodoro", {})
    notifications = data.get("notifications", {})
    weekday = data.get("weekday", {})

    defaults = default_schedule()
    days: dict[int, DayPolicy] = {}
    for name in WEEKDAY_NAMES:
        idx = WEEKDAY_INDEX[name]
        base = defaults.days[idx]
        section = weekday.get(name, {})
        quota_minutes = int(section.get("quota_minutes", base.quota_seconds // 60))
        mode = str(section.get("mode", _KIND_TO_MODE[base.kind]))

        kind = _MODE_TO_KIND.get(mode)
        if kind is None:
            raise ValueError(f"unknown mode {mode!r} for weekday {name!r}")

        quota_seconds = 0 if kind is DayKind.FREE else quota_minutes * 60
        days[idx] = DayPolicy(quota_seconds, kind)

    transparency_section = data.get("transparency", {}) or {}
    transparency = TransparencyConfig(
        idle_timeout=int(transparency_section.get("idle_timeout", 5)),
        idle_opacity=float(transparency_section.get("idle_opacity", 0.08)),
        active_opacity=float(transparency_section.get("active_opacity", 0.90)),
        fade_ms=int(transparency_section.get("fade_ms", 300)),
    )

    return Config(
        boundary=parse_boundary(str(general.get("day_boundary", "04:00"))),
        auto_start=bool(general.get("auto_start", False)),
        study_seconds=int(pomodoro.get("study_minutes", 40)) * 60,
        break_seconds=int(pomodoro.get("break_minutes", 10)) * 60,
        notifications=bool(notifications.get("enabled", True)),
        schedule=Schedule(days),
        transparency=transparency,
    )


def load(path: Path | None = None) -> Config:
    from .paths import config_path

    target = path or config_path()
    if not target.exists():
        return Config()
    return loads(target.read_text(encoding="utf-8"))


def dumps(config: Config) -> str:
    lines: list[str] = []
    lines.append("[general]")
    lines.append(f'day_boundary = "{format_boundary(config.boundary)}"')
    lines.append(f"auto_start = {_toml_bool(config.auto_start)}")
    lines.append("")
    lines.append("[pomodoro]")
    lines.append(f"study_minutes = {config.study_seconds // 60}")
    lines.append(f"break_minutes = {config.break_seconds // 60}")
    lines.append("")
    lines.append("[notifications]")
    lines.append(f"enabled = {_toml_bool(config.notifications)}")
    lines.append("")
    lines.append("[transparency]")
    lines.append(f"idle_timeout = {config.transparency.idle_timeout}")
    lines.append(f"idle_opacity = {config.transparency.idle_opacity}")
    lines.append(f"active_opacity = {config.transparency.active_opacity}")
    lines.append(f"fade_ms = {config.transparency.fade_ms}")
    for name in WEEKDAY_NAMES:
        policy = config.schedule.days[WEEKDAY_INDEX[name]]
        lines.append("")
        lines.append(f"[weekday.{name}]")
        lines.append(f"quota_minutes = {policy.quota_seconds // 60}")
        lines.append(f'mode = "{_KIND_TO_MODE[policy.kind]}"')
    lines.append("")
    return "\n".join(lines)


def save(config: Config, path: Path | None = None) -> None:
    from .paths import config_path

    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dumps(config), encoding="utf-8")


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"
