"""Pure, UI- and IO-free logic for SimpleStudy.

Nothing in this package may import GTK, D-Bus or SQLite. Keeping it pure lets us
unit-test the tricky day/ledger math with an injected clock.
"""
