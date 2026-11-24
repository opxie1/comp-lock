"""Closing the applications a profile names."""

from __future__ import annotations

import logging
import os
from collections.abc import Collection

from . import win32

# Terminating any of these either takes the session down or takes comp-lock
# down with it. A profile that names one is treated as a mistake and skipped
# rather than obeyed.
PROTECTED = frozenset(
    {
        "system",
        "system idle process",
        "registry",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "winlogon.exe",
        "services.exe",
        "lsass.exe",
        "svchost.exe",
        "explorer.exe",
        "dwm.exe",
        "fontdrvhost.exe",
        "python.exe",
        "pythonw.exe",
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "conhost.exe",
        "windowsterminal.exe",
    }
)

_log = logging.getLogger(__name__)


def sweep(names: Collection[str]) -> list[tuple[int, str]]:
    """Terminate every running process whose name is in *names*.

    Returns the processes that were closed. Ones that could not be opened are
    logged and skipped: a protected or already-exiting process is normal and
    should not stop the sweep.
    """
    if not names:
        return []

    wanted = {name.casefold() for name in names} - PROTECTED
    if not wanted:
        return []

    own_pid = os.getpid()
    closed: list[tuple[int, str]] = []

    for pid, name in win32.iter_processes():
        if pid == own_pid or pid <= 4:
            continue
        if name.casefold() not in wanted:
            continue
        try:
            win32.terminate(pid)
        except OSError as exc:
            _log.debug("could not close %s (pid %d): %s", name, pid, exc)
            continue
        _log.info("closed %s (pid %d)", name, pid)
        closed.append((pid, name))

    return closed


def running(names: Collection[str]) -> list[tuple[int, str]]:
    """The processes from *names* that are currently running."""
    wanted = {name.casefold() for name in names}
    return [(pid, name) for pid, name in win32.iter_processes() if name.casefold() in wanted]
