"""Persistence of :class:`~simplestudy.core.checklist.ChecklistState` as JSON.

The checklist is user content plus runtime progress, so it lives in the state
directory next to ``state.json`` and is written atomically like everything
else. Loading is deliberately forgiving: a malformed file never prevents the
daemon from starting; it simply yields an empty checklist.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from ..core.checklist import ChecklistState, Item, Subject
from .atomic import write_text_atomic

SCHEMA_VERSION = 1
DEFAULT_ITEM_SECONDS = 40 * 60


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def to_dict(state: ChecklistState) -> dict:
    return {
        "version": SCHEMA_VERSION,
        "active_subject_id": state.active_subject_id,
        "subjects": [
            {
                "id": subject.id,
                "name": subject.name,
                "items": [
                    {
                        "id": item.id,
                        "text": item.text,
                        "base_seconds": item.base_seconds,
                        "elapsed_seconds": item.elapsed_seconds,
                        "carry_in_seconds": item.carry_in_seconds,
                        "checked": item.checked,
                        "carry_out_seconds": item.carry_out_seconds,
                    }
                    for item in subject.items
                ],
            }
            for subject in state.subjects
        ],
    }


def from_dict(data: dict, *, default_seconds: int = DEFAULT_ITEM_SECONDS) -> ChecklistState:
    subjects: list[Subject] = []
    for raw_subject in data.get("subjects", []) or []:
        if not isinstance(raw_subject, dict):
            continue
        subject = Subject(
            id=str(raw_subject.get("id") or uuid.uuid4().hex),
            name=str(raw_subject.get("name") or "Untitled"),
            items=[],
        )
        for raw_item in raw_subject.get("items", []) or []:
            if not isinstance(raw_item, dict):
                continue
            base = _as_int(raw_item.get("base_seconds", default_seconds), default_seconds)
            if base <= 0:
                base = default_seconds
            item = Item(
                id=str(raw_item.get("id") or uuid.uuid4().hex),
                text=str(raw_item.get("text") or "Untitled item"),
                base_seconds=base,
                elapsed_seconds=max(
                    0, _as_int(raw_item.get("elapsed_seconds", 0), 0)
                ),
                carry_in_seconds=_as_int(raw_item.get("carry_in_seconds", 0), 0),
                checked=bool(raw_item.get("checked", False)),
                carry_out_seconds=_as_int(
                    raw_item.get("carry_out_seconds", 0), 0
                ),
            )
            subject.items.append(item)

        _sanitize_unchecked(subject)
        subjects.append(subject)

    active = data.get("active_subject_id")
    active_id = str(active) if active else None
    state = ChecklistState(subjects=subjects, active_subject_id=active_id)
    if active_id is not None and not any(s.id == active_id for s in subjects):
        state.active_subject_id = None
    state.ensure_active()
    return state


def _sanitize_unchecked(subject: Subject) -> None:
    """Enforce the invariant that only the first unchecked item has a carry."""
    first = subject.first_unchecked_index
    if first is None:
        return
    for item in subject.items[first + 1 :]:
        if not item.checked:
            item.carry_in_seconds = 0
            item.elapsed_seconds = 0


def load(path: Path, *, default_seconds: int = DEFAULT_ITEM_SECONDS) -> ChecklistState:
    if not path.exists():
        return ChecklistState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return ChecklistState()
    if not isinstance(data, dict):
        return ChecklistState()
    try:
        return from_dict(data, default_seconds=default_seconds)
    except (TypeError, ValueError):
        return ChecklistState()


def save(path: Path, state: ChecklistState) -> None:
    payload = json.dumps(to_dict(state), indent=2) + "\n"
    write_text_atomic(path, payload)
