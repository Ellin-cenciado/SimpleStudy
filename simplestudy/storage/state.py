"""Persistence of :class:`~simplestudy.core.tracker.TrackerState` as JSON.

Writes are atomic (temp file + ``os.replace``) so a crash mid-save can never
leave a corrupt state file behind.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ..core.pomodoro import Phase
from ..core.tracker import TrackerState
from .atomic import write_text_atomic

SCHEMA_VERSION = 2


def to_dict(state: TrackerState) -> dict:
    return {
        "version": SCHEMA_VERSION,
        "logical_day": state.logical_day.isoformat(),
        "debt_seconds": state.debt_seconds,
        "elapsed_seconds": state.elapsed_seconds,
        "study_seconds": state.study_seconds,
        "break_seconds": state.break_seconds,
        "phase": state.phase.value,
        "phase_elapsed_seconds": state.phase_elapsed_seconds,
        "break_override_seconds": state.break_override_seconds,
        "running": state.running,
    }


def from_dict(data: dict) -> TrackerState:
    return TrackerState(
        logical_day=date.fromisoformat(data["logical_day"]),
        debt_seconds=int(data.get("debt_seconds", 0)),
        elapsed_seconds=int(data.get("elapsed_seconds", 0)),
        study_seconds=int(data.get("study_seconds", 0)),
        break_seconds=int(data.get("break_seconds", 0)),
        phase=Phase(data.get("phase", Phase.IDLE.value)),
        phase_elapsed_seconds=int(data.get("phase_elapsed_seconds", 0)),
        break_override_seconds=int(data.get("break_override_seconds", 0)),
        running=bool(data.get("running", False)),
    )


def load(path: Path) -> TrackerState | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        return None
    return from_dict(data)


def save(path: Path, state: TrackerState) -> None:
    payload = json.dumps(to_dict(state), indent=2) + "\n"
    write_text_atomic(path, payload)
