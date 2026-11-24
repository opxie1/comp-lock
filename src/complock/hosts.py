"""Reading and rewriting the Windows hosts file.

comp-lock owns one fenced region and never touches a line outside it, so a
machine that already redirects hosts for development keeps those entries.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from .errors import HostsError

BEGIN = "# >>> comp-lock begin >>>"
END = "# <<< comp-lock end <<<"
NOTE = "# Managed region. Edits are reverted while a lock is running."

# 0.0.0.0 is refused immediately. 127.0.0.1 would instead open a connection to
# a local port that is usually not listening, which reads as a slow page rather
# than a blocked one.
SINKHOLE = "0.0.0.0"

_log = logging.getLogger(__name__)


def render(hostnames: Sequence[str]) -> str:
    """Build the managed region for *hostnames*."""
    lines = [BEGIN, NOTE]
    lines.extend(f"{SINKHOLE} {name}" for name in hostnames)
    lines.append(END)
    return "\n".join(lines)


def strip(text: str) -> str:
    """Return *text* with any managed region removed."""
    kept: list[str] = []
    inside = False
    for line in text.splitlines():
        marker = line.strip()
        if marker == BEGIN:
            inside = True
            continue
        if marker == END:
            inside = False
            continue
        if not inside:
            kept.append(line)
    # A region left open means an earlier write was cut short. Everything after
    # the opening marker was ours, so dropping it to the end of the file is the
    # right repair.
    return "\n".join(kept)


def extract(text: str) -> list[str] | None:
    """The hostnames inside the managed region, or None if there is no region.

    An empty list and None mean different things: the first is a region that
    blocks nothing, the second is a file comp-lock has not touched.
    """
    found: list[str] = []
    seen_region = False
    inside = False
    for line in text.splitlines():
        marker = line.strip()
        if marker == BEGIN:
            inside = True
            seen_region = True
            continue
        if marker == END:
            inside = False
            continue
        if not inside or not marker or marker.startswith("#"):
            continue
        parts = marker.split()
        if len(parts) >= 2:
            found.extend(parts[1:])
    return found if seen_region else None


def apply(text: str, hostnames: Sequence[str]) -> str:
    """Replace any managed region in *text* with one covering *hostnames*."""
    base = strip(text).rstrip("\n")
    region = render(hostnames)
    if not base.strip():
        return region + "\n"
    return f"{base}\n\n{region}\n"


class HostsFile:
    """The hosts file on disk, with a one-time backup of its original content."""

    def __init__(self, path: Path, backup: Path | None = None) -> None:
        self.path = path
        self.backup = backup

    def read(self) -> str:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return ""
        except OSError as exc:
            raise HostsError(f"cannot read {self.path}: {exc}") from exc
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HostsError(
                f"{self.path} is not ASCII or UTF-8; refusing to rewrite it"
            ) from exc
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def write(self, text: str) -> None:
        self._save_backup()
        payload = text.replace("\n", "\r\n").encode("utf-8")
        try:
            # Written in place rather than swapped in from a temporary file:
            # the hosts file carries its own ACLs, and a replacement would
            # inherit the directory default instead.
            with open(self.path, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except PermissionError as exc:
            raise HostsError(
                f"cannot write {self.path}; run from an elevated prompt and check "
                "that no security product is holding the file open"
            ) from exc
        except OSError as exc:
            raise HostsError(f"cannot write {self.path}: {exc}") from exc

    def block(self, hostnames: Sequence[str]) -> bool:
        """Install the managed region. True when the file changed."""
        current = self.read()
        wanted = apply(current, hostnames)
        if current == wanted:
            return False
        self.write(wanted)
        _log.info("wrote %d hostnames to %s", len(hostnames), self.path)
        return True

    def unblock(self) -> bool:
        """Remove the managed region. True when the file changed."""
        current = self.read()
        if BEGIN not in current:
            return False
        remainder = strip(current).rstrip("\n")
        self.write(remainder + "\n" if remainder.strip() else "")
        _log.info("removed the managed region from %s", self.path)
        return True

    def blocked(self) -> list[str] | None:
        return extract(self.read())

    def _save_backup(self) -> None:
        """Keep a copy of the file as it was before comp-lock first touched it."""
        if self.backup is None or self.backup.exists() or not self.path.exists():
            return
        try:
            self.backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path, self.backup)
        except OSError as exc:
            _log.warning("could not back up %s: %s", self.path, exc)
