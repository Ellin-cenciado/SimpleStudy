"""The carry-over ledger.

The ledger is the heart of the app. Given a day's policy, the study time
elapsed during that day, and the debt carried in, it decides how much was
required and how much debt carries out.

Let ``D`` be the debt carried in and ``E`` the seconds studied during the day:

==============  ==========================  ================================
Day kind        required                    debt out
==============  ==========================  ================================
FREE            ``0``                       ``max(0, D - E)``
CARRIED         ``D + quota``               ``max(0, D + quota - E)``
OPTIONAL        ``D + quota``               ``max(0, D - E)``
==============  ==========================  ================================

On a CARRIED day the day's own quota becomes part of the requirement and, if
unmet, is added to the debt. On an OPTIONAL day the quota is only a goal that is
discarded when unmet, so only the pre-existing debt ``D`` survives; study time
is applied to that debt first. A FREE day has no requirement but study time (if
any) still pays down debt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .schedule import DayKind, DayPolicy


@dataclass(frozen=True)
class DayResult:
    """Outcome of settling a single logical day."""

    logical_date: date
    debt_in: int
    required: int
    elapsed: int
    debt_out: int
    #: For OPTIONAL days, the size of the optional goal (0 otherwise).
    optional_goal: int
    #: How much of the optional goal was reached (0 otherwise).
    optional_done: int

    @property
    def completed(self) -> bool:
        return self.elapsed >= self.required

    @property
    def remaining(self) -> int:
        return max(0, self.required - self.elapsed)


def settle(
    policy: DayPolicy, logical_date: date, debt_in: int, elapsed: int
) -> DayResult:
    """Settle ``logical_date`` and return the resulting :class:`DayResult`."""
    if debt_in < 0:
        raise ValueError("debt_in must not be negative")
    if elapsed < 0:
        raise ValueError("elapsed must not be negative")

    if policy.kind is DayKind.FREE:
        required = 0
        debt_out = max(0, debt_in - elapsed)
        optional_goal = 0
        optional_done = 0
    elif policy.kind is DayKind.CARRIED:
        required = debt_in + policy.quota_seconds
        debt_out = max(0, required - elapsed)
        optional_goal = 0
        optional_done = 0
    else:  # DayKind.OPTIONAL
        required = debt_in + policy.quota_seconds
        debt_out = max(0, debt_in - elapsed)
        optional_goal = policy.quota_seconds
        optional_done = min(optional_goal, max(0, elapsed - debt_in))

    return DayResult(
        logical_date=logical_date,
        debt_in=debt_in,
        required=required,
        elapsed=elapsed,
        debt_out=debt_out,
        optional_goal=optional_goal,
        optional_done=optional_done,
    )
