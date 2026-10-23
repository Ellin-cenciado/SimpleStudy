"""The checklist page: subjects, items, and inline item timers.

The daemon owns all checklist state. This page is a thin view that rebuilds
itself whenever the checklist revision changes and updates only the active
item's countdown on every one-second snapshot.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .client import DaemonClient
from .timer_page import format_clock

CSS = """
.checklist-time {
    font-feature-settings: "tnum";
    font-weight: 600;
}
.checklist-overrun {
    color: #e01b24;
}
.checklist-active {
    font-weight: 700;
}
.checklist-done {
    opacity: 0.6;
}
.checklist-future {
    opacity: 0.55;
}
"""


def _subject_active_item(subject: dict) -> dict | None:
    for item in subject.get("items", []) or []:
        if not item.get("checked", False):
            return item
    return None


class ItemRow(Adw.ActionRow):
    """One checklist item with a checkbox, inline clock, and duration editor."""

    def __init__(
        self,
        page: "ChecklistPage",
        subject: dict,
        item: dict,
        active_item_id: str,
    ) -> None:
        super().__init__()
        self.page = page
        self.subject = subject
        self.item = item
        self.item_id = str(item.get("id", ""))
        self.base_seconds = int(item.get("base_seconds", 0))
        self.checked = bool(item.get("checked", False))
        self.is_active = self.item_id == active_item_id

        self.set_title(str(item.get("text", "Untitled item")))
        self.set_activatable(False)

        carry = int(item.get("carry_in_seconds", 0))
        if self.checked:
            carry_out = int(item.get("carry_out_seconds", 0))
            if carry_out > 0:
                self.set_subtitle(f"Surplus {format_clock(carry_out)} passed on")
            elif carry_out < 0:
                self.set_subtitle(
                    f"Overrun {format_clock(-carry_out)} deducted from next"
                )
            else:
                self.set_subtitle("Completed")
            self.add_css_class("checklist-done")
        elif carry:
            budget = max(0, self.base_seconds + carry)
            if carry > 0:
                self.set_subtitle(
                    f"Budget {format_clock(budget)} (+{format_clock(carry)} carried)"
                )
            else:
                self.set_subtitle(
                    f"Budget {format_clock(budget)} (-{format_clock(-carry)} debt)"
                )
        else:
            self.set_subtitle(f"{format_clock(self.base_seconds)} planned")

        if self.is_active and not self.checked:
            self.add_css_class("checklist-active")
        elif not self.checked and not self.is_active:
            self.add_css_class("checklist-future")

        self._updating = False

        self.checkbox = Gtk.CheckButton()
        self.checkbox.set_valign(Gtk.Align.CENTER)
        self._updating = True
        self.checkbox.set_active(self.checked)
        self._updating = False
        self.checkbox.set_sensitive(self.is_active and not self.checked)
        self.checkbox.set_tooltip_text(
            "Check the current item" if self.is_active else None
        )
        self.checkbox.connect("toggled", self._on_toggled)
        self.add_prefix(self.checkbox)

        self.active_icon = Gtk.Image.new_from_icon_name("media-record-symbolic")
        self.active_icon.set_pixel_size(9)
        self.active_icon.add_css_class("accent")
        self.active_icon.set_visible(self.is_active and not self.checked)

        self.time_button = Gtk.Button()
        self.time_button.add_css_class("flat")
        self.time_button.add_css_class("checklist-time")
        self.time_button.set_valign(Gtk.Align.CENTER)
        self.time_button.set_halign(Gtk.Align.END)
        self.time_button.set_tooltip_text("Click to change the planned duration")
        self.time_button.connect("clicked", lambda *_: self._begin_edit())

        self.editor = Gtk.SpinButton.new_with_range(1, 24 * 60, 1)
        self.editor.set_numeric(True)
        self.editor.set_width_chars(3)
        self.editor.set_valign(Gtk.Align.CENTER)
        self.editor.set_halign(Gtk.Align.END)
        self.editor.connect("activate", lambda *_: self._commit_edit())
        self.editor.connect("notify::has-focus", self._on_editor_focus)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.editor.add_controller(key_controller)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_valign(Gtk.Align.CENTER)
        self.stack.add_named(self.time_button, "label")
        self.stack.add_named(self.editor, "editor")

        self.delete_button = Gtk.Button(icon_name="user-trash-symbolic")
        self.delete_button.add_css_class("flat")
        self.delete_button.set_valign(Gtk.Align.CENTER)
        self.delete_button.set_tooltip_text("Remove this item")
        self.delete_button.set_visible(not self.checked)
        self.delete_button.connect(
            "clicked", lambda *_: self.page.client.remove_item(self.item_id)
        )

        suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        suffix.set_valign(Gtk.Align.CENTER)
        suffix.append(self.active_icon)
        suffix.append(self.stack)
        suffix.append(self.delete_button)
        self.add_suffix(suffix)

        if self.checked:
            self.time_button.set_label("Done")
            self.time_button.set_sensitive(False)
        else:
            remaining = self.base_seconds + carry - int(item.get("elapsed_seconds", 0))
            self.set_live(remaining)

    # -- live countdown -------------------------------------------------

    def set_live(self, remaining_seconds: int) -> None:
        if self.checked:
            return
        if remaining_seconds > 0:
            self.time_button.set_label(format_clock(remaining_seconds))
            self.time_button.remove_css_class("checklist-overrun")
        else:
            # Red and counting up, exactly like an overrun item.
            self.time_button.set_label("+" + format_clock(-remaining_seconds))
            self.time_button.add_css_class("checklist-overrun")

    # -- inline duration editing ---------------------------------------

    def _begin_edit(self) -> None:
        if self.checked or self.stack.get_visible_child_name() == "editor":
            return
        if (
            self.page._editing_item_id is not None
            and self.page._editing_item_id != self.item_id
        ):
            return
        self.page._editing_item_id = self.item_id
        self.editor.set_value(max(1, round(self.base_seconds / 60)))
        self.stack.set_visible_child_name("editor")
        self.editor.grab_focus()
        self.editor.select_region(0, -1)

    def _commit_edit(self) -> None:
        if self.stack.get_visible_child_name() != "editor":
            return
        minutes = int(self.editor.get_value())
        seconds = minutes * 60
        self._end_edit()
        if seconds != self.base_seconds:
            self.page.client.set_item_duration(self.item_id, seconds)

    def _cancel_edit(self) -> None:
        self._end_edit()

    def _end_edit(self) -> None:
        self.stack.set_visible_child_name("label")
        if self.page._editing_item_id == self.item_id:
            self.page._editing_item_id = None

    def _on_editor_focus(self, _widget, _pspec) -> None:
        if not self.editor.has_focus():
            self._commit_edit()

    def _on_key_pressed(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval == 0xFF1B:  # Escape
            self._cancel_edit()
            return True
        return False

    # -- checking -------------------------------------------------------

    def _on_toggled(self, button: Gtk.CheckButton) -> None:
        if self._updating:
            return
        if button.get_active() and self.is_active:
            self.page.client.check_item(self.item_id)


class SubjectRow(Adw.ExpanderRow):
    """One subject: its items and an inline add-item entry."""

    def __init__(
        self,
        page: "ChecklistPage",
        subject: dict,
        active_subject_id: str,
        active_item_id: str,
    ) -> None:
        super().__init__()
        self.page = page
        self.subject = subject
        self.subject_id = str(subject.get("id", ""))
        self.item_rows: dict[str, ItemRow] = {}

        items = subject.get("items", []) or []
        checked = sum(1 for item in items if item.get("checked", False))
        complete = bool(items) and checked == len(items)
        is_active = self.subject_id == active_subject_id

        self.set_title(str(subject.get("name", "Untitled subject")))
        progress = f"{checked}/{len(items)} done" if items else "No items yet"
        self.set_subtitle(("Active · " if is_active else "") + progress)
        if complete:
            self.add_css_class("checklist-done")

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        buttons.set_valign(Gtk.Align.CENTER)

        select_button = Gtk.Button(icon_name="go-next-symbolic")
        select_button.add_css_class("flat")
        select_button.set_valign(Gtk.Align.CENTER)
        select_button.set_tooltip_text("Make this the active subject")
        select_button.set_sensitive(not is_active and not complete)
        select_button.connect(
            "clicked", lambda *_: page.client.select_subject(self.subject_id)
        )
        buttons.append(select_button)

        reset_button = Gtk.Button(icon_name="edit-undo-symbolic")
        reset_button.add_css_class("flat")
        reset_button.set_valign(Gtk.Align.CENTER)
        reset_button.set_tooltip_text("Reset this subject")
        reset_button.set_sensitive(checked > 0)
        reset_button.connect(
            "clicked", lambda *_: page.client.reset_subject(self.subject_id)
        )
        buttons.append(reset_button)

        delete_button = Gtk.Button(icon_name="user-trash-symbolic")
        delete_button.add_css_class("flat")
        delete_button.set_valign(Gtk.Align.CENTER)
        delete_button.set_tooltip_text("Delete this subject")
        delete_button.connect(
            "clicked", lambda *_: page.client.remove_subject(self.subject_id)
        )
        buttons.append(delete_button)
        self.add_suffix(buttons)

        for item in items:
            row = ItemRow(page, subject, item, active_item_id)
            self.item_rows[row.item_id] = row
            self.add_row(row)

        self.add_entry = Adw.EntryRow(title="Add an item")
        add_button = Gtk.Button(icon_name="list-add-symbolic")
        add_button.add_css_class("flat")
        add_button.set_valign(Gtk.Align.CENTER)
        add_button.set_tooltip_text("Add item")
        add_button.connect("clicked", lambda *_: self._on_add_item())
        self.add_entry.add_suffix(add_button)
        self.add_entry.connect("apply", lambda *_: self._on_add_item())
        self.add_row(self.add_entry)

        self.set_expanded(self.subject_id in page._expanded)
        self.connect("notify::expanded", page._on_subject_expanded)

    def _on_add_item(self) -> None:
        text = self.add_entry.get_text().strip()
        if not text:
            return
        self.add_entry.set_text("")
        # Duration 0 asks the daemon to use the configured study length.
        self.page.client.add_item(self.subject_id, text, 0)


class ChecklistPage(Adw.PreferencesPage):
    """The Checklist tab."""

    def __init__(self, client: DaemonClient) -> None:
        super().__init__()
        self.client = client
        self._data: dict = {}
        self._revision = -1
        self._active_item_id = ""
        self._editing_item_id: str | None = None
        self._pending_refetch = False
        self._building = False
        self._expanded: set[str] = set()
        self._item_rows: dict[str, ItemRow] = {}
        self._subject_rows: dict[str, SubjectRow] = {}
        self._online = False

        self._install_css()

        self.rest_group = Adw.PreferencesGroup(title="Rest")
        self.rest_row = Adw.ActionRow(title="Resting", subtitle="")
        self.rest_skip = Gtk.Button(label="Skip")
        self.rest_skip.add_css_class("flat")
        self.rest_skip.set_valign(Gtk.Align.CENTER)
        self.rest_skip.set_tooltip_text("Skip the subject rest")
        self.rest_skip.connect("clicked", lambda *_: self.client.skip_phase())
        self.rest_row.add_suffix(self.rest_skip)
        self.rest_group.add(self.rest_row)
        self.rest_group.set_visible(False)
        self.add(self.rest_group)

        self.subjects_group = Adw.PreferencesGroup(title="Subjects")
        self.add(self.subjects_group)

        self._rebuild()

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        display = self.get_display()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    # -- data flow ------------------------------------------------------

    def refetch(self) -> None:
        if self._editing_item_id is not None:
            self._pending_refetch = True
            return
        self._pending_refetch = False
        self._data = self.client.get_checklist()
        revision = self._data.get("revision")
        if isinstance(revision, int):
            self._revision = revision
        self._rebuild()

    def update(self, snap: dict | None) -> None:
        if snap is None:
            self._online = False
            self.subjects_group.set_sensitive(False)
            self.rest_group.set_visible(False)
            return

        if not self._online:
            # The daemon may have restarted with a fresh revision counter;
            # force a refetch on every reconnect.
            self._revision = -1
        self._online = True
        self.subjects_group.set_sensitive(True)
        revision = int(snap.get("ChecklistRevision", 0))
        if self._editing_item_id is None and (
            self._pending_refetch or revision != self._revision
        ):
            self._revision = revision
            self.refetch()
        elif self._editing_item_id is not None and revision != self._revision:
            self._pending_refetch = True

        self._active_item_id = str(snap.get("ActiveItemId") or "")
        row = self._item_rows.get(self._active_item_id)
        if row is not None:
            row.set_live(int(snap.get("ActiveItemRemainingSeconds", 0)))

        rest = int(snap.get("RestRemainingSeconds", 0))
        self.rest_group.set_visible(rest > 0)
        if rest > 0:
            self.rest_row.set_subtitle(f"{format_clock(rest)} remaining")

    # -- rebuilding -----------------------------------------------------

    def _rebuild(self) -> None:
        # Replacing the group resets the page's scroll position; remember it so
        # checking an item deep in a long list doesn't jump back to the top.
        scroll = self.get_first_child()
        adjustment = (
            scroll.get_vadjustment() if isinstance(scroll, Gtk.ScrolledWindow) else None
        )
        scroll_value = adjustment.get_value() if adjustment is not None else 0.0

        self._building = True
        try:
            self.remove(self.subjects_group)
            self.subjects_group = Adw.PreferencesGroup(title="Subjects")
            self.add(self.subjects_group)
            self._item_rows.clear()
            self._subject_rows.clear()

            subjects = self._data.get("subjects", []) or []
            active_subject_id = str(self._data.get("active_subject_id") or "")
            valid_ids = {str(subject.get("id", "")) for subject in subjects}
            self._expanded &= valid_ids
            if active_subject_id:
                self._expanded.add(active_subject_id)

            active_item_id = ""
            active_subject = next(
                (s for s in subjects if str(s.get("id", "")) == active_subject_id),
                None,
            )
            if active_subject is not None:
                active_item = _subject_active_item(active_subject)
                if active_item is not None:
                    active_item_id = str(active_item.get("id", ""))

            self._active_item_id = active_item_id

            add_subject = Adw.EntryRow(title="Add a subject")
            add_subject_button = Gtk.Button(icon_name="list-add-symbolic")
            add_subject_button.add_css_class("flat")
            add_subject_button.set_valign(Gtk.Align.CENTER)
            add_subject_button.set_tooltip_text("Add subject")
            add_subject_button.connect(
                "clicked", lambda *_: self._on_add_subject(add_subject)
            )
            add_subject.add_suffix(add_subject_button)
            add_subject.connect(
                "apply", lambda *_: self._on_add_subject(add_subject)
            )
            self.subjects_group.add(add_subject)

            for subject in subjects:
                row = SubjectRow(
                    self,
                    subject,
                    active_subject_id,
                    active_item_id,
                )
                self._subject_rows[row.subject_id] = row
                self._item_rows.update(row.item_rows)
                self.subjects_group.add(row)
        finally:
            self._building = False

        if adjustment is not None:

            def restore(adj=adjustment, target=scroll_value) -> bool:
                upper = adj.get_upper() - adj.get_page_size()
                adj.set_value(min(target, max(0.0, upper)))
                return GLib.SOURCE_REMOVE

            GLib.idle_add(restore)

    def _on_add_subject(self, row: Adw.EntryRow) -> None:
        text = row.get_text().strip()
        if not text:
            return
        row.set_text("")
        self.client.add_subject(text)

    def _on_subject_expanded(self, row: SubjectRow, _pspec) -> None:
        if self._building:
            return
        if row.get_expanded():
            self._expanded.add(row.subject_id)
        else:
            self._expanded.discard(row.subject_id)
