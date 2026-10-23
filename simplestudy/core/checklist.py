"""The checklist: subjects, items, and the per-item study budget.

This module is pure: it owns the mutable checklist state and the arithmetic
that turns study seconds into countdowns, surplus, and debt. It never touches
files, D-Bus, or GTK.

Model
-----
A subject is an ordered list of items. Only the *first unchecked* item of the
active subject is running. Each item has a signed ``carry_in_seconds``: a
positive value is surplus from the previous item, a negative value is debt.

    budget    = base_seconds + carry_in_seconds
    remaining = budget - elapsed_seconds

When an item is checked, its remaining time becomes the next item's carry-in::

    carry_out = remaining       # +surplus / -debt
    next.carry_in = carry_out

The last item of a subject discards its carry: every subject starts fresh.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

#: Rest granted when the last item of a subject is checked.
SUBJECT_REST_SECONDS = 5 * 60


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Item:
    id: str
    text: str
    base_seconds: int
    elapsed_seconds: int = 0
    carry_in_seconds: int = 0
    checked: bool = False
    #: The carry this item produced when it was checked (0 until then).
    carry_out_seconds: int = 0

    @property
    def budget_seconds(self) -> int:
        return self.base_seconds + self.carry_in_seconds

    @property
    def remaining_seconds(self) -> int:
        """Seconds left; negative once the item is in overrun."""
        return self.budget_seconds - self.elapsed_seconds

    @property
    def overrun_seconds(self) -> int:
        return max(0, -self.remaining_seconds)


@dataclass
class Subject:
    id: str
    name: str
    items: list[Item] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        # An empty subject is not complete; it is simply not started.
        return bool(self.items) and all(item.checked for item in self.items)

    @property
    def checked_count(self) -> int:
        return sum(1 for item in self.items if item.checked)

    @property
    def first_unchecked_index(self) -> int | None:
        for index, item in enumerate(self.items):
            if not item.checked:
                return index
        return None

    @property
    def active_item(self) -> Item | None:
        index = self.first_unchecked_index
        if index is None:
            return None
        return self.items[index]


@dataclass(frozen=True)
class AdvanceResult:
    """Outcome of advancing the active item by some study seconds."""

    expired: bool
    item_id: str | None
    remaining_seconds: int
    overrun_seconds: int


@dataclass(frozen=True)
class CheckResult:
    """Outcome of checking an item."""

    item_id: str
    carry_out_seconds: int
    subject_completed: bool
    next_item_id: str | None


@dataclass
class ChecklistState:
    subjects: list[Subject] = field(default_factory=list)
    active_subject_id: str | None = None

    # -- lookups -------------------------------------------------------

    def subject(self, subject_id: str) -> Subject:
        for subject in self.subjects:
            if subject.id == subject_id:
                return subject
        raise ValueError(f"unknown subject {subject_id!r}")

    def find_item(self, item_id: str) -> tuple[Subject, Item, int]:
        for subject in self.subjects:
            for index, item in enumerate(subject.items):
                if item.id == item_id:
                    return subject, item, index
        raise ValueError(f"unknown item {item_id!r}")

    def active_subject(self) -> Subject | None:
        if self.active_subject_id is None:
            return None
        for subject in self.subjects:
            if subject.id == self.active_subject_id:
                return subject
        return None

    def active_item(self) -> Item | None:
        subject = self.active_subject()
        return subject.active_item if subject is not None else None

    @property
    def all_complete(self) -> bool:
        return bool(self.subjects) and all(subject.complete for subject in self.subjects)

    # -- subject operations -------------------------------------------

    def add_subject(self, name: str, *, subject_id: str | None = None) -> Subject:
        name = name.strip()
        if not name:
            raise ValueError("subject name must not be empty")
        subject = Subject(id=subject_id or _new_id(), name=name)
        self.subjects.append(subject)
        if self.active_subject_id is None:
            self.active_subject_id = subject.id
        return subject

    def rename_subject(self, subject_id: str, name: str) -> Subject:
        name = name.strip()
        if not name:
            raise ValueError("subject name must not be empty")
        subject = self.subject(subject_id)
        subject.name = name
        return subject

    def remove_subject(self, subject_id: str) -> None:
        subject = self.subject(subject_id)
        self.subjects.remove(subject)
        if self.active_subject_id == subject_id:
            self.active_subject_id = None

    def select_subject(self, subject_id: str) -> Subject:
        subject = self.subject(subject_id)
        if subject.complete:
            raise ValueError("cannot select a completed subject")
        self.active_subject_id = subject.id
        return subject

    def next_incomplete_subject(self, after_id: str | None = None) -> Subject | None:
        """The next incomplete subject after ``after_id``, wrapping around."""
        if not self.subjects:
            return None
        start = 0
        if after_id is not None:
            ids = [subject.id for subject in self.subjects]
            if after_id in ids:
                start = ids.index(after_id) + 1
        for offset in range(len(self.subjects)):
            subject = self.subjects[(start + offset) % len(self.subjects)]
            if not subject.complete:
                return subject
        return None

    def ensure_active(self) -> Subject | None:
        """Pick a sensible active subject if none is selected or it vanished."""
        current = self.active_subject()
        if current is not None:
            return current
        next_subject = self.next_incomplete_subject()
        if next_subject is not None:
            self.active_subject_id = next_subject.id
        else:
            self.active_subject_id = None
        return next_subject

    # -- item operations ----------------------------------------------

    def add_item(
        self,
        subject_id: str,
        text: str,
        base_seconds: int,
        *,
        item_id: str | None = None,
    ) -> Item:
        text = text.strip()
        if not text:
            raise ValueError("item text must not be empty")
        if base_seconds <= 0:
            raise ValueError("item duration must be positive")
        subject = self.subject(subject_id)
        item = Item(
            id=item_id or _new_id(),
            text=text,
            base_seconds=int(base_seconds),
        )
        subject.items.append(item)
        return item

    def rename_item(self, item_id: str, text: str) -> Item:
        text = text.strip()
        if not text:
            raise ValueError("item text must not be empty")
        _subject, item, _index = self.find_item(item_id)
        item.text = text
        return item

    def set_item_duration(self, item_id: str, base_seconds: int) -> Item:
        if base_seconds <= 0:
            raise ValueError("item duration must be positive")
        _subject, item, _index = self.find_item(item_id)
        if item.checked:
            raise ValueError("cannot change the duration of a completed item")
        item.base_seconds = int(base_seconds)
        return item

    def remove_item(self, item_id: str) -> None:
        subject, item, index = self.find_item(item_id)
        if item.checked:
            raise ValueError("cannot remove a completed item; reset the subject instead")
        was_current = subject.first_unchecked_index == index
        inherited_carry = item.carry_in_seconds
        del subject.items[index]
        # If the removed item was the current one, its carry belongs to the
        # item that becomes current next.
        if was_current and index < len(subject.items):
            successor = subject.items[index]
            successor.carry_in_seconds = inherited_carry
            successor.elapsed_seconds = 0

    def uncheck_last(self, subject_id: str) -> Item:
        """Undo the most recent check in a subject."""
        subject = self.subject(subject_id)
        checked = [index for index, item in enumerate(subject.items) if item.checked]
        if not checked:
            raise ValueError("subject has no checked items")
        last_index = checked[-1]
        item = subject.items[last_index]
        item.checked = False
        item.elapsed_seconds = 0
        item.carry_out_seconds = 0
        if last_index + 1 < len(subject.items):
            successor = subject.items[last_index + 1]
            successor.carry_in_seconds = 0
            successor.elapsed_seconds = 0
        return item

    def reset_subject(self, subject_id: str) -> Subject:
        subject = self.subject(subject_id)
        for item in subject.items:
            item.elapsed_seconds = 0
            item.carry_in_seconds = 0
            item.checked = False
            item.carry_out_seconds = 0
        return subject

    # -- running the clock --------------------------------------------

    def advance(self, seconds: int) -> AdvanceResult:
        """Credit ``seconds`` of study time to the active item."""
        if seconds <= 0:
            return AdvanceResult(False, None, 0, 0)
        item = self.active_item()
        if item is None:
            return AdvanceResult(False, None, 0, 0)
        before = item.remaining_seconds
        item.elapsed_seconds += int(seconds)
        after = item.remaining_seconds
        return AdvanceResult(
            expired=before > 0 and after <= 0,
            item_id=item.id,
            remaining_seconds=after,
            overrun_seconds=max(0, -after),
        )

    def check_item(self, item_id: str) -> CheckResult:
        """Check the current item and pass its remaining time to the next one."""
        subject, item, index = self.find_item(item_id)
        if self.active_subject_id != subject.id:
            raise ValueError("only the active subject's items can be checked")
        if item.checked:
            raise ValueError("item is already checked")
        if subject.first_unchecked_index != index:
            raise ValueError("only the current item can be checked")

        carry_out = item.remaining_seconds
        item.checked = True
        item.carry_out_seconds = carry_out

        next_item_id: str | None = None
        if index + 1 < len(subject.items):
            successor = subject.items[index + 1]
            successor.carry_in_seconds = carry_out
            successor.elapsed_seconds = 0
            next_item_id = successor.id

        return CheckResult(
            item_id=item.id,
            carry_out_seconds=carry_out,
            subject_completed=subject.complete,
            next_item_id=next_item_id,
        )
