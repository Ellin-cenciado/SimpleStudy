"""Per-weekday study policy.

Every weekday maps to a :class:`DayPolicy` describing how much study the day
carries and what happens to time that is not studied:

* ``CARRIED``  - a normal day. The quota is fixed; any amount not studied is
  added to the running debt for the following days (Mon-Fri by default).
* ``OPTIONAL`` - a light day. The quota is only a goal; it is *not* added to
  the debt if unmet. Debt from earlier days still has to be cleared first
  (Saturday by default).
* ``FREE``     - no requirement at all. Debt is preserved, not forgiven
  (Sunday by default).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Mapping

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 60 * SECONDS_PER_MINUTE


class DayKind(Enum):
    CARRIED = "carried"
    OPTIONAL = "optional"
    FREE = "free"


@dataclass(frozen=True)
class DayPolicy:
    """How one weekday behaves."""

    quota_seconds: int
    kind: DayKind

    def __post_init__(self) -> None:
        if self.quota_seconds < 0:
            raise ValueError("quota_seconds must not be negative")
        if self.kind is DayKind.FREE and self.quota_seconds != 0:
            raise ValueError("a FREE day must have a zero quota")


#: Python's ``date.weekday()``: Monday == 0 ... Sunday == 6.
WEEKDAY_NAMES: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

WEEKDAY_INDEX: Mapping[str, int] = {name: i for i, name in enumerate(WEEKDAY_NAMES)}


def default_schedule() -> "Schedule":
    """Mon-Fri 4h carried, Saturday 2h optional, Sunday free."""
    return Schedule(
        {
            0: DayPolicy(4 * SECONDS_PER_HOUR, DayKind.CARRIED),
            1: DayPolicy(4 * SECONDS_PER_HOUR, DayKind.CARRIED),
            2: DayPolicy(4 * SECONDS_PER_HOUR, DayKind.CARRIED),
            3: DayPolicy(4 * SECONDS_PER_HOUR, DayKind.CARRIED),
            4: DayPolicy(4 * SECONDS_PER_HOUR, DayKind.CARRIED),
            5: DayPolicy(2 * SECONDS_PER_HOUR, DayKind.OPTIONAL),
            6: DayPolicy(0, DayKind.FREE),
        }
    )


@dataclass(frozen=True)
class Schedule:
    """A mapping of weekday index (Monday==0) to :class:`DayPolicy`."""

    days: Mapping[int, DayPolicy]

    def __post_init__(self) -> None:
        missing = set(range(7)) - set(self.days)
        if missing:
            raise ValueError(f"schedule is missing weekdays: {sorted(missing)}")

    def policy(self, day: date) -> DayPolicy:
        return self.days[day.weekday()]

    def with_day(self, weekday: int, policy: DayPolicy) -> "Schedule":
        """Return a copy with a single weekday replaced."""
        if weekday not in range(7):
            raise ValueError("weekday must be in 0..6")
        days = dict(self.days)
        days[weekday] = policy
        return Schedule(days)
