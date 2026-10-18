"""Clocks used by the tracker.

Two notions of time are needed:

* wall time (``now``) decides which *logical day* it is and when a day rolls
  over, so it must be timezone-aware local time.
* monotonic time (``monotonic``) measures how long a study session has been
  running. Using a monotonic source means a laptop that suspends is **not**
  credited as study time, because ``CLOCK_MONOTONIC`` does not advance while
  the machine sleeps.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """A source of wall time and monotonic time."""

    def now(self) -> datetime:
        """Timezone-aware local wall time."""
        ...

    def monotonic(self) -> float:
        """Seconds from an arbitrary reference; never goes backwards and does
        not advance while the machine is suspended."""
        ...


class SystemClock:
    """Real clock backed by :mod:`time` and :mod:`datetime`."""

    def now(self) -> datetime:
        return datetime.now().astimezone()

    def monotonic(self) -> float:
        return time.monotonic()


class FakeClock:
    """Deterministic clock for tests.

    ``advance`` moves both wall and monotonic time by the same amount, which is
    what happens during normal operation. ``advance_wall_only`` simulates events
    like the system clock being corrected or a DST jump without spending any
    study time.
    """

    def __init__(self, start: datetime, monotonic: float = 0.0) -> None:
        if start.tzinfo is None:
            raise ValueError("FakeClock requires a timezone-aware datetime")
        self._now = start
        self._monotonic = monotonic

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        """Advance both wall and monotonic time (time spent studying/elapsed)."""
        self._now = self._now + timedelta(seconds=seconds)
        self._monotonic += seconds

    def advance_wall_only(self, seconds: float) -> None:
        """Move the wall clock without moving monotonic time."""
        self._now = self._now + timedelta(seconds=seconds)

    def set_wall(self, when: datetime) -> None:
        if when.tzinfo is None:
            raise ValueError("expected a timezone-aware datetime")
        self._now = when
