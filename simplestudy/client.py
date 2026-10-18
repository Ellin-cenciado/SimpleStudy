"""A tiny command-line client for the SimpleStudy daemon.

Useful for scripting and for checking on the daemon without the GUI:

    simplestudy-ctl status
    simplestudy-ctl start
    simplestudy-ctl pause
    simplestudy-ctl skip
"""

from __future__ import annotations

import argparse
import sys

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import DBUS_INTERFACE, DBUS_NAME, DBUS_PATH
from .daemon.service import format_hours

_METHOD = f"{DBUS_INTERFACE}"


def _bus() -> Gio.DBusConnection:
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def _call(method: str, parameters: GLib.Variant | None = None) -> GLib.Variant:
    return _bus().call_sync(
        DBUS_NAME,
        DBUS_PATH,
        _METHOD,
        method,
        parameters,
        None,
        Gio.DBusCallFlags.NONE,
        -1,
        None,
    )


def _get_snapshot() -> dict:
    result = _call("GetSnapshot")
    return result.unpack()[0]


def cmd_status(_args) -> int:
    snap = _get_snapshot()
    complete = "yes" if snap["DayComplete"] else "no"
    print(f"Day:        {snap['LogicalDay']} ({snap['Weekday']}, {snap['DayKind']})")
    print(f"Required:   {format_hours(snap['RequiredSeconds'])}")
    print(f"Elapsed:    {format_hours(snap['ElapsedSeconds'])}")
    print(f"Remaining:  {format_hours(snap['RemainingSeconds'])}")
    if snap["CarryInSeconds"]:
        print(f"Carried in: {format_hours(snap['CarryInSeconds'])}")
    if snap["OptionalGoalSeconds"]:
        print(
            f"Optional:   {format_hours(snap['OptionalDoneSeconds'])} / "
            f"{format_hours(snap['OptionalGoalSeconds'])}"
        )
    print(f"Phase:      {snap['Phase']} ({format_hours(snap['PhaseRemainingSeconds'])} left)")
    print(f"Running:    {'yes' if snap['Running'] else 'no'}")
    print(f"Complete:   {complete}")
    return 0


def cmd_start(_args) -> int:
    _call("Start")
    print("started")
    return 0


def cmd_pause(_args) -> int:
    _call("Pause")
    print("paused")
    return 0


def cmd_skip(_args) -> int:
    _call("SkipPhase")
    print("skipped")
    return 0


def cmd_reload(_args) -> int:
    _call("ReloadConfig")
    print("config reloaded")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="simplestudy-ctl", description="Control the SimpleStudy daemon")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("start").set_defaults(func=cmd_start)
    sub.add_parser("pause").set_defaults(func=cmd_pause)
    sub.add_parser("skip").set_defaults(func=cmd_skip)
    sub.add_parser("reload").set_defaults(func=cmd_reload)
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except GLib.Error as error:
        if "ServiceUnknown" in error.message or "NameHasNoOwner" in error.message:
            print("SimpleStudy daemon is not running.", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
