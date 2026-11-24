"""The handful of Win32 calls comp-lock needs, bound through ctypes.

Process enumeration and termination are the only parts that genuinely need the
platform API. Keeping them here means the rest of the package stays importable
and testable without a Windows machine.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
from collections.abc import Iterator
from ctypes import wintypes

if sys.platform != "win32":  # pragma: no cover - the tool only targets Windows
    raise ImportError("complock.win32 requires Windows")

_log = logging.getLogger(__name__)

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)

TH32CS_SNAPPROCESS = 0x0002
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_MAX_PATH = 260


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * _MAX_PATH),
    ]


_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
_kernel32.Process32FirstW.restype = wintypes.BOOL
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
_kernel32.Process32NextW.restype = wintypes.BOOL
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_kernel32.TerminateProcess.restype = wintypes.BOOL
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


def is_admin() -> bool:
    """True when the current process holds administrator rights."""
    try:
        return bool(_shell32.IsUserAnAdmin())
    except OSError:
        return False


def iter_processes() -> Iterator[tuple[int, str]]:
    """Yield (pid, executable name) for every process the caller can see."""
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        if not _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return
        while True:
            yield int(entry.th32ProcessID), entry.szExeFile
            if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                return
    finally:
        _kernel32.CloseHandle(snapshot)


def terminate(pid: int) -> None:
    """Stop a process. Raises OSError when the handle or the kill is refused."""
    handle = _kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not _kernel32.TerminateProcess(handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _kernel32.CloseHandle(handle)


def process_name(pid: int) -> str | None:
    """The executable name for a pid, or None if it is gone or inaccessible."""
    for candidate, name in iter_processes():
        if candidate == pid:
            return name
    return None


def is_running(pid: int, expect_name: str | None = None) -> bool:
    """Check that a pid is live, optionally that it is still the same program.

    Windows reuses process ids, so a stored pid on its own is not enough to
    decide whether a worker from before a reboot is still running.
    """
    if pid <= 0:
        return False
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        if code.value != STILL_ACTIVE:
            return False
    finally:
        _kernel32.CloseHandle(handle)
    if expect_name is None:
        return True
    actual = process_name(pid)
    return actual is not None and actual.casefold() == expect_name.casefold()


def flush_dns_cache() -> bool:
    """Drop the resolver cache so names looked up before the lock stop resolving.

    DnsFlushResolverCache is not in the documented API surface, so a failure
    falls back to the command that ships with Windows.
    """
    try:
        dnsapi = ctypes.WinDLL("dnsapi", use_last_error=True)
        if dnsapi.DnsFlushResolverCache():
            return True
    except (OSError, AttributeError) as exc:
        _log.debug("DnsFlushResolverCache unavailable: %s", exc)
    try:
        subprocess.run(
            ["ipconfig", "/flushdns"],
            check=True,
            capture_output=True,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        return True
    except (OSError, subprocess.CalledProcessError) as exc:
        _log.warning("could not flush the DNS cache: %s", exc)
        return False
