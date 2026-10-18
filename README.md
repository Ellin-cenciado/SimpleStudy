# SimpleStudy

A lightweight study tracker for Linux. Every day grants study time; time you
don't study **carries over** as debt, with configurable per-weekday rules.

## The rules

| Day      | Quota | Unmet quota                         |
|----------|-------|-------------------------------------|
| Mon–Fri  | 4h    | added to debt (carries forward)     |
| Saturday | 2h    | discarded (optional goal)           |
| Sunday   | free  | nothing required; debt is preserved |

Let `D` be the debt carried into a day and `E` the seconds studied that day:

```
Sunday   (free)     required = 0            debt out = max(0, D - E)
Weekdays (carried)  required = D + quota    debt out = max(0, D + quota - E)
Saturday (optional) required = D + quota    debt out = max(0, D - E)
```

Saturday works the mandatory **debt first**, then the optional 2h goal. The
optional goal never becomes debt.

Examples:

* No study Monday → Tuesday requires `4h + 4h = 8h`.
* 2h on Monday → Tuesday requires `4h + 2h = 6h`.
* 2h debt going into Saturday → Saturday requires `2h debt + 2h optional`;
  studying the 2h debt leaves Monday at just `4h`.

The daily requirement measures **wall-clock session time, breaks included**.
Paused time is never counted, and suspend time is not credited (a monotonic
clock is used).

## Window transparency

The GUI window fades to a low opacity after a period of inactivity, or as soon
as it moves to the background, and snaps back to its active opacity on any
mouse or keyboard interaction. It never fades while fullscreen or minimized.
Tune it in Settings -> Appearance, or in `config.toml`:

```toml
[transparency]
idle_timeout = 5      # seconds without interaction before fading
idle_opacity = 0.08   # opacity when faded (0.08 = 8%)
active_opacity = 0.90 # opacity while in use (0.90 = 90%)
fade_ms = 300         # fade-out animation duration
```

## Layout

```
simplestudy/
  core/        pure logic (no GTK / D-Bus / SQLite), unit-tested with a fake clock
    clock.py     wall + monotonic clocks, FakeClock for tests
    schedule.py  per-weekday DayPolicy and Schedule
    ledger.py    the carry-over math (settle)
    pomodoro.py  study/break phase machine
    tracker.py   ledger + pomodoro + clock
  storage/
    paths.py     XDG locations
    config.py    config.toml read/write
    state.py     atomic state.json read/write
    history.py   SQLite day history for stats
  daemon/        D-Bus service, notifications, systemd entry point
  gui/           GTK4 + libadwaita client (timer, stats, settings, opacity)
  client.py      simplestudy-ctl command-line client
tests/         stdlib unittest suite
```

## Running the tests

```sh
python3 -m unittest discover -s tests
```
