"""Closing the applications a profile names."""

from __future__ import annotations

import logging
import os
from collections.abc import Collection, Mapping, Sequence

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


def sweep(
    names: Collection[str],
    command_line: Mapping[str, Sequence[str]] | None = None,
) -> list[tuple[int, str]]:
    """Terminate every running process whose name is in *names*.

    An executable named in *command_line* is only closed when its command line
    or its own path contains one of the fragments given for it. That is there
    for the executables that host more than one program: javaw.exe is every
    Java application on the machine, so closing it on sight would take
    unrelated work down alongside Minecraft.

    Returns the processes that were closed. Ones that could not be opened are
    logged and skipped, since a protected or already-exiting process is normal
    and should not stop the sweep.
    """
    if not names:
        return []

    wanted = {name.casefold() for name in names} - PROTECTED
    if not wanted:
        return []

    guards = {
        exe.casefold(): tuple(fragment.casefold() for fragment in fragments)
        for exe, fragments in (command_line or {}).items()
    }

    own_pid = os.getpid()
    closed: list[tuple[int, str]] = []

    for pid, name in win32.iter_processes():
        if pid == own_pid or pid <= 4:
            continue
        lowered = name.casefold()
        if lowered not in wanted:
            continue

        fragments = guards.get(lowered)
        if fragments is not None:
            matched = _matches(pid, fragments)
            if matched is None:
                _log.debug(
                    "leaving %s (pid %d) alone: its command line could not be read",
                    name,
                    pid,
                )
                continue
            if not matched:
                continue

        try:
            win32.terminate(pid)
        except OSError as exc:
            _log.debug("could not close %s (pid %d): %s", name, pid, exc)
            continue
        _log.info("closed %s (pid %d)", name, pid)
        closed.append((pid, name))

    return closed


def _matches(pid: int, fragments: Sequence[str]) -> bool | None:
    """Whether a process looks like one of *fragments*.

    None means the question could not be answered, which is deliberately not
    the same as False: a process that cannot be inspected is left running
    rather than killed on a guess.
    """
    command = win32.process_command_line(pid)
    image = win32.process_image_path(pid)
    if command is None and image is None:
        return None
    haystack = f"{command or ''}\n{image or ''}".casefold()
    return any(fragment in haystack for fragment in fragments)


def running(names: Collection[str]) -> list[tuple[int, str]]:
    """The processes from *names* that are currently running."""
    wanted = {name.casefold() for name in names}
    return [(pid, name) for pid, name in win32.iter_processes() if name.casefold() in wanted]
