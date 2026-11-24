"""The record of a lock in progress.

Kept on disk so that a reboot, or a worker that dies, does not quietly end a
lock that still has time left on it.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import paths
from .errors import SessionError

MAX_DURATION = 24 * 3600

_DURATION = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$", re.IGNORECASE)


def parse_duration(text: str) -> int:
    """Read '90m', '2h', '1h30m' or a bare number of minutes into seconds."""
    cleaned = text.strip().replace(" ", "")
    if not cleaned:
        raise SessionError("no duration given")
    if cleaned.isdigit():
        total = int(cleaned) * 60
    else:
        match = _DURATION.match(cleaned)
        if match is None or not any(match.groups()):
            raise SessionError(
                f"cannot read {text!r} as a duration; use 90m, 2h, 1h30m, or a plain "
                "number of minutes"
            )
        hours, minutes, seconds = (int(part or 0) for part in match.groups())
        total = hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        raise SessionError("a lock has to run for longer than zero seconds")
    if total > MAX_DURATION:
        raise SessionError("a lock cannot run for longer than 24h")
    return total


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


@dataclass(frozen=True)
class Session:
    profile: str
    started_at: float
    ends_at: float
    hard: bool
    pid: int

    @property
    def remaining(self) -> float:
        return self.ends_at - time.time()

    def is_active(self) -> bool:
        return self.remaining > 0

    def replace_pid(self, pid: int) -> Session:
        return Session(self.profile, self.started_at, self.ends_at, self.hard, pid)


class SessionStore:
    """Reads and writes the lock record."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else paths.session_file()

    def load(self) -> Session | None:
        try:
            body = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SessionError(f"cannot read {self.path}: {exc}") from exc
        try:
            data = json.loads(body)
            return Session(
                profile=str(data["profile"]),
                started_at=float(data["started_at"]),
                ends_at=float(data["ends_at"]),
                hard=bool(data["hard"]),
                pid=int(data["pid"]),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise SessionError(
                f"{self.path} is not a readable lock record ({exc}); delete it to start over"
            ) from exc

    def save(self, session: Session) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        scratch = self.path.with_suffix(".json.new")
        try:
            scratch.write_text(json.dumps(asdict(session), indent=2), encoding="utf-8")
            os.replace(scratch, self.path)
        except OSError as exc:
            raise SessionError(f"cannot write {self.path}: {exc}") from exc

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise SessionError(f"cannot remove {self.path}: {exc}") from exc
