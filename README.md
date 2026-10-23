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

## Checklists

The **Checklist** tab holds any number of subjects; each subject is an ordered
list of items. Only the first unchecked item of the active subject is current,
and only the current item's checkbox is enabled.

Every item has its own countdown, editable inline by clicking the time. New
items start with the configured study length (40 minutes by default); editing
an item changes only that item, not the global setting. The item clock counts
**study time only** — pomodoro breaks, pauses, and completed days do not advance
it.

The arithmetic is deliberately simple and additive:

```
budget    = item length + carry from the previous item
remaining = budget - study time spent on the item
```

* Checking an item early carries its remaining surplus into the next item:
  a 40-minute item checked with 10 minutes left makes the next item 50 minutes.
* Running past zero turns the item red and counts **up**. Checking it then
  carries the overrun as debt, so the next item's budget is reduced.
* If inherited debt is larger than the next item's length, that item starts red
  and counts up immediately.
* Carry belongs to one subject. Completing the last item discards any surplus or
  debt, and the next subject starts fresh.

Completing a subject forces a **5-minute rest**. It uses the normal break phase,
counts toward the daily quota, works while paused (it is queued until you
resume), and can be skipped with the rest row's **Skip** button or *Skip phase*
on the Timer tab. When the rest ends, the next incomplete subject is selected
automatically. Resetting or unchecking the subject cancels a pending rest.

Mistakes are corrected with the subject's **Reset** button, or with
`UncheckLastItem` over D-Bus, which reopens the most recently checked item and
clears the carry it passed on. Completed items cannot be edited or deleted;
reset the subject instead.

The checklist is persisted to `$XDG_STATE_HOME/simplestudy/checklist.json`
(usually `~/.local/state/simplestudy/checklist.json`) with atomic writes, so
item text, durations, progress, and carry survive daemon restarts.

## Control interface

The daemon exports `org.simplestudy.Daemon` on the session bus. Besides the
timer controls (`Start`, `Pause`, `SkipPhase`, `ReloadConfig`, `GetSnapshot`),
the checklist API is:

```
GetChecklist() -> JSON
AddSubject(name) -> id            RenameSubject(id, name)
RemoveSubject(id)                 SelectSubject(id)
AddItem(subject_id, text, seconds) -> id
RenameItem(id, text)              SetItemDuration(id, seconds)
RemoveItem(id)                    CheckItem(id)
UncheckLastItem(subject_id)       ResetSubject(subject_id)
```

`ChecklistChanged` is emitted when the structure changes; `Changed` carries the
live snapshot every second, including the active item's signed remaining time,
its overrun, and the rest countdown.

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
    checklist.py subjects, items, and per-item carry arithmetic
  storage/
    paths.py     XDG locations
    config.py    config.toml read/write
    state.py     atomic state.json read/write
    checklist.py atomic checklist.json read/write
    atomic.py    shared atomic-write helper
    history.py   SQLite day history for stats
  daemon/        D-Bus service, notifications, systemd entry point
  gui/           GTK4 + libadwaita client (timer, checklist, stats, settings)
  client.py      simplestudy-ctl command-line client
tests/         stdlib unittest suite
```

## Running the tests

```sh
python3 -m unittest discover -s tests
```
