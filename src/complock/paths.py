"""Filesystem locations.

These are functions rather than module constants so that the environment
overrides below can be set after import. The test suite points both of them at
a temporary directory; without that it would have to write to the real hosts
file to cover anything useful.
"""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Where configuration, lock state, logs and backups live."""
    override = os.environ.get("COMPLOCK_DATA_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "comp-lock"


def hosts_file() -> Path:
    override = os.environ.get("COMPLOCK_HOSTS")
    if override:
        return Path(override)
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return root / "System32" / "drivers" / "etc" / "hosts"


def config_file() -> Path:
    return data_dir() / "config.toml"


def session_file() -> Path:
    return data_dir() / "session.json"


def log_file() -> Path:
    return data_dir() / "complock.log"


def hosts_backup() -> Path:
    return data_dir() / "hosts.backup"


def doh_backup() -> Path:
    return data_dir() / "doh-policy.json"


def ensure_data_dir() -> Path:
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory
