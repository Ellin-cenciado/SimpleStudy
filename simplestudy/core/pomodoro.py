"""The pomodoro phase machine.

Note that breaks count towards the daily requirement (the requirement is
wall-clock *session* time, breaks included), so the pomodoro phases exist only
to pace the session and to drive phase-change notifications. They do not change
how time is accounted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Phase(Enum):
    IDLE = "idle"
    STUDY = "study"
    BREAK = "break"
    COMPLETE = "complete"


@dataclass(frozen=True)
class PomodoroConfig:
    study_seconds: int = 40 * 60
    break_seconds: int = 10 * 60

    def __post_init__(self) -> None:
        if self.study_seconds <= 0:
            raise ValueError("study_seconds must be positive")
        if self.break_seconds <= 0:
            raise ValueError("break_seconds must be positive")


@dataclass(frozen=True)
class PhaseAdvance:
    """Result of advancing the phase machine by some number of seconds."""

    phase: Phase
    phase_elapsed: int
    transitions: tuple[Phase, ...]
    study_seconds: int
    break_seconds: int


def phase_length(config: PomodoroConfig, phase: Phase) -> int:
    """Length in seconds of ``phase`` (0 for IDLE/COMPLETE)."""
    if phase is Phase.STUDY:
        return config.study_seconds
    if phase is Phase.BREAK:
        return config.break_seconds
    return 0


def advance(
    phase: Phase, phase_elapsed: int, delta: int, config: PomodoroConfig
) -> PhaseAdvance:
    """Advance the phase machine by ``delta`` seconds.

    The returned :class:`PhaseAdvance` reports the new phase, how far into it we
    are, which phases were *entered* (e.g. ``(BREAK, STUDY)`` for a delta that
    spans a full cycle), and how the ``delta`` split between study and break.
    """
    if delta < 0:
        raise ValueError("delta must not be negative")

    transitions: list[Phase] = []
    study = 0
    brk = 0
    remaining = delta
    while remaining > 0 and phase in (Phase.STUDY, Phase.BREAK):
        length = phase_length(config, phase)
        left = length - phase_elapsed
        take = min(remaining, left)
        if phase is Phase.STUDY:
            study += take
        else:
            brk += take
        phase_elapsed += take
        remaining -= take
        if phase_elapsed >= length:
            phase = Phase.BREAK if phase is Phase.STUDY else Phase.STUDY
            phase_elapsed = 0
            transitions.append(phase)

    return PhaseAdvance(
        phase=phase,
        phase_elapsed=phase_elapsed,
        transitions=tuple(transitions),
        study_seconds=study,
        break_seconds=brk,
    )
