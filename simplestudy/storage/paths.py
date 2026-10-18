"""Filesystem locations following the XDG base directory spec."""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR = "simplestudy"


def _base(env_var: str, fallback: str) -> Path:
    value = os.environ.get(env_var)
    if value:
        return Path(value)
    return Path.home() / fallback


def config_dir() -> Path:
    return _base("XDG_CONFIG_HOME", ".config") / APP_DIR


def state_dir() -> Path:
    return _base("XDG_STATE_HOME", ".local/state") / APP_DIR


def config_path() -> Path:
    return config_dir() / "config.toml"


def state_path() -> Path:
    return state_dir() / "state.json"


def history_path() -> Path:
    return state_dir() / "history.db"


def log_path() -> Path:
    return state_dir() / "simplestudy.log"


def ensure_dirs() -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    state_dir().mkdir(parents=True, exist_ok=True)
