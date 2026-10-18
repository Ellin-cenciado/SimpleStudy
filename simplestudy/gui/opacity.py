"""Window auto-transparency.

Makes a window fade to a low opacity when it is left alone for a while or when
it moves to the background, and jump back to its active opacity on any
interaction. All parameters come from
:class:`~simplestudy.storage.config.TransparencyConfig`.

Activity is detected with GTK event controllers (no polling); fades are driven
by :class:`Adw.TimedAnimation` on the window's ``opacity`` property.
"""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from ..storage.config import TransparencyConfig


class OpacityController:
    """Drives a window's opacity based on focus and idle time.

    Rules:

    * fade out after ``idle_timeout`` seconds without interaction, or
      immediately when the window is not the active window;
    * return to ``active_opacity`` immediately on any interaction or when the
      window becomes active again;
    * never fade while fullscreen or minimized.
    """

    def __init__(self, window: Gtk.Window, config: TransparencyConfig) -> None:
        self.window = window
        self.config = config
        self._idle_source: int | None = None
        self._animation: Adw.Animation | None = None
        self._faded = False
        # Avoid fading during the brief moment before the window is first
        # focused after being mapped.
        self._ever_active = False

        window.set_opacity(config.active_opacity)

        self._install_activity_controllers()
        window.connect("notify::is-active", lambda *_: self._sync())
        window.connect("notify::fullscreened", lambda *_: self._sync())
        window.connect("map", lambda *_: self._sync())
        window.connect("unmap", lambda *_: self._cancel_idle_timer())

    # -- public api -----------------------------------------------------

    def apply_config(self, config: TransparencyConfig) -> None:
        """Update parameters, e.g. after the user edited settings."""
        self.config = config
        self._sync()

    def close(self) -> None:
        self._cancel_idle_timer()
        self._cancel_animation()

    # -- activity tracking ----------------------------------------------

    def _install_activity_controllers(self) -> None:
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda *_: self._mark_activity())
        self.window.add_controller(motion)

        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: self._mark_activity())
        self.window.add_controller(click)

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key_pressed)
        self.window.add_controller(key)

    def _on_key_pressed(self, _controller, _keyval, _keycode, _state) -> bool:
        self._mark_activity()
        return False

    def _mark_activity(self) -> None:
        self._restore_immediate()
        self._arm_idle_timer()

    # -- idle handling --------------------------------------------------

    def _arm_idle_timer(self) -> None:
        self._cancel_idle_timer()
        if not self._fade_allowed() or not self.window.is_active():
            return
        timeout = self.config.idle_timeout
        if timeout <= 0:
            self._fade_out()
            return
        self._idle_source = GLib.timeout_add_seconds(timeout, self._on_idle_timeout)

    def _on_idle_timeout(self) -> bool:
        self._idle_source = None
        self._fade_out()
        return GLib.SOURCE_REMOVE

    def _cancel_idle_timer(self) -> None:
        if self._idle_source is not None:
            GLib.source_remove(self._idle_source)
            self._idle_source = None

    # -- focus / state changes ------------------------------------------

    def _sync(self) -> None:
        """Reconcile opacity with the current window state."""
        if self.window.is_active():
            self._ever_active = True
        if not self._fade_allowed():
            self._restore_immediate()
            return
        if not self.window.is_active():
            # Background: fade straight away (but not before the window has
            # ever been focused, to avoid a flash on startup).
            if self._ever_active:
                self._cancel_idle_timer()
                self._fade_out()
            return
        self._restore_immediate()
        self._arm_idle_timer()

    def _fade_allowed(self) -> bool:
        return not self.window.is_fullscreen() and not self._is_minimized()

    def _is_minimized(self) -> bool:
        surface = self.window.get_surface()
        if not isinstance(surface, Gdk.Toplevel):
            return False
        try:
            return bool(surface.get_state() & Gdk.ToplevelState.MINIMIZED)
        except (TypeError, AttributeError):
            return False

    # -- opacity transitions --------------------------------------------

    def _fade_out(self) -> None:
        if self._faded or not self._fade_allowed():
            return
        self._faded = True
        self._animate_to(self.config.idle_opacity)

    def _restore_immediate(self) -> None:
        self._faded = False
        self._cancel_animation()
        self.window.set_opacity(self.config.active_opacity)

    def _animate_to(self, target: float) -> None:
        self._cancel_animation()
        duration = max(0, self.config.fade_ms)
        if duration == 0:
            self.window.set_opacity(target)
            return
        animation = Adw.TimedAnimation.new(
            self.window,
            self.window.get_opacity(),
            target,
            duration,
            Adw.PropertyAnimationTarget.new(self.window, "opacity"),
        )
        animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        animation.play()
        self._animation = animation

    def _cancel_animation(self) -> None:
        if self._animation is not None:
            self._animation.pause()
            self._animation = None
