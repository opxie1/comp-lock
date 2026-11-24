"""The loop that holds a lock in place for its duration."""

from __future__ import annotations

import logging
import signal
import time
from types import FrameType

from . import apps, browsers, hosts, paths, win32
from .config import Config
from .profiles import ResolvedProfile
from .session import Session, SessionStore, format_duration

_log = logging.getLogger(__name__)


class _Interrupted(Exception):
    """Raised out of a signal handler so the loop can unwind through one path."""


def teardown(store: SessionStore) -> None:
    """Undo everything a lock puts in place.

    Every step runs even if an earlier one fails, and each is safe to call when
    there is nothing to undo. The browser policy and the DNS cache are handled
    regardless of configuration: a setting that changed mid-lock should not
    leave a registry value behind.
    """
    steps = (
        ("hosts file", hosts.HostsFile(paths.hosts_file(), paths.hosts_backup()).unblock),
        ("browser policy", browsers.restore),
        ("dns cache", win32.flush_dns_cache),
        ("lock record", store.clear),
    )
    for label, action in steps:
        try:
            action()
        except Exception:
            _log.exception("could not restore the %s", label)


class Watchdog:
    """Engages a lock, keeps it engaged, and releases it on time.

    The loop does three things on each pass: repair the hosts file if something
    edited it, close any blocked application that has appeared, and check
    whether the deadline has arrived.
    """

    def __init__(
        self,
        config: Config,
        profile: ResolvedProfile,
        session: Session,
        store: SessionStore | None = None,
    ) -> None:
        self.config = config
        self.profile = profile
        self.session = session
        self.store = store if store is not None else SessionStore()
        self.hosts = hosts.HostsFile(paths.hosts_file(), paths.hosts_backup())

    def run(self) -> int:
        self._install_signal_handlers()

        try:
            self.engage()
        except Exception:
            _log.exception("could not engage the lock; undoing whatever was applied")
            teardown(self.store)
            raise

        try:
            self._loop()
        except _Interrupted:
            if self.session.is_active() and self.session.hard:
                _log.warning(
                    "interrupted with %s left on a hard lock; the block stays in place",
                    format_duration(self.session.remaining),
                )
                return 1
            _log.info("interrupted; releasing the lock")
            self.release()
            return 130
        except Exception:
            _log.exception("the watchdog stopped unexpectedly")
            if self.session.hard:
                # A hard lock is meant to outlive its worker, and resume will
                # start another one. Releasing here would defeat the point.
                return 1
            self.release()
            raise

        self.release()
        return 0

    def engage(self) -> None:
        if self.config.disable_browser_doh and browsers.disable():
            self._warn_about_open_browsers()

        self.hosts.block(self.profile.hostnames)

        if self.config.flush_dns:
            win32.flush_dns_cache()

        closed = apps.sweep(self.profile.processes)

        # Recorded last, so the record never claims a lock that is not actually
        # in place. `complock start` waits on this file to know the worker is up.
        self.store.save(self.session)

        _log.info(
            "lock %r engaged: %d hostnames, %d applications, %s remaining, %d closed now",
            self.profile.name,
            len(self.profile.hostnames),
            len(self.profile.processes),
            format_duration(self.session.remaining),
            len(closed),
        )

    def release(self) -> None:
        teardown(self.store)
        _log.info("lock %r released", self.profile.name)

    def _loop(self) -> None:
        while True:
            remaining = self.session.remaining
            if remaining <= 0:
                return
            self._repair_hosts()
            apps.sweep(self.profile.processes)
            time.sleep(min(self.config.poll_interval, remaining))

    def _repair_hosts(self) -> None:
        """Rewrite the managed region if anything has altered it."""
        try:
            present = self.hosts.blocked()
        except Exception:
            _log.exception("could not read the hosts file")
            return
        if present is not None and sorted(present) == list(self.profile.hostnames):
            return
        reason = "removed" if present is None else "edited"
        _log.warning("the managed hosts region was %s; putting it back", reason)
        try:
            self.hosts.block(self.profile.hostnames)
            if self.config.flush_dns:
                win32.flush_dns_cache()
        except Exception:
            _log.exception("could not repair the hosts file")

    def _warn_about_open_browsers(self) -> None:
        open_now = apps.running(browsers.CHROMIUM_PROCESSES)
        if not open_now:
            return
        names = sorted({name for _, name in open_now})
        _log.warning(
            "%s %s already running; restart %s so the DNS policy applies, "
            "or blocked sites will keep loading",
            ", ".join(names),
            "is" if len(names) == 1 else "are",
            "it" if len(names) == 1 else "them",
        )

    def _install_signal_handlers(self) -> None:
        def handler(signum: int, frame: FrameType | None) -> None:
            raise _Interrupted(signum)

        for name in ("SIGINT", "SIGBREAK", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, handler)
            except (OSError, ValueError):
                # Not every signal can be caught off the main thread.
                pass
