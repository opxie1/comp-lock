"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__, apps, browsers, config as config_mod, hosts, paths, profiles, win32
from .errors import CompLockError, PrivilegeError, SessionError
from .session import Session, SessionStore, format_duration, parse_duration
from .watchdog import Watchdog, teardown

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000

# Must match the name scripts/install-task.ps1 registers.
TASK_NAME = "comp-lock resume"

_log = logging.getLogger("complock")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args)
    try:
        return args.handler(args)
    except CompLockError as exc:
        print(f"complock: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="complock",
        description="Block distracting sites and applications for a fixed stretch of time.",
    )
    parser.add_argument("--version", action="version", version=f"comp-lock {__version__}")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="write debug detail to the console"
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    init = sub.add_parser("init", help="write the default configuration file")
    init.add_argument("--force", action="store_true", help="overwrite an existing file")
    init.set_defaults(handler=cmd_init)

    listing = sub.add_parser("profiles", help="show the configured profiles")
    listing.set_defaults(handler=cmd_profiles)

    start = sub.add_parser("start", help="begin a lock and return to the prompt")
    _add_lock_arguments(start)
    start.set_defaults(handler=cmd_start)

    run = sub.add_parser("run", help="begin a lock and hold the console until it ends")
    _add_lock_arguments(run)
    run.add_argument(
        "--resume",
        action="store_true",
        help="pick up the stored lock instead of starting a new one",
    )
    run.set_defaults(handler=cmd_run)

    status = sub.add_parser("status", help="report the lock in progress")
    status.set_defaults(handler=cmd_status)

    stop = sub.add_parser("stop", help="end a lock before its deadline")
    stop.add_argument("--force", action="store_true", help="confirm ending a lock early")
    stop.set_defaults(handler=cmd_stop)

    resume = sub.add_parser(
        "resume", help="re-engage an unfinished lock (run from the scheduled task)"
    )
    resume.set_defaults(handler=cmd_resume)

    doctor = sub.add_parser("doctor", help="check privileges, the hosts file and browser policy")
    doctor.set_defaults(handler=cmd_doctor)

    return parser


def _add_lock_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-p", "--profile", help="which profile to apply")
    parser.add_argument(
        "-f", "--for", dest="duration", help="how long to run: 90m, 2h, 1h30m, or minutes"
    )
    parser.add_argument(
        "--hard",
        action="store_true",
        help="keep the block in place if the worker is interrupted",
    )


def cmd_init(args: argparse.Namespace) -> int:
    target = paths.config_file()
    if target.exists() and not args.force:
        print(f"{target} already exists; pass --force to overwrite it")
        return 1
    paths.ensure_data_dir()
    target.write_text(config_mod.default_text(), encoding="utf-8")
    print(f"Wrote {target}")
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    config = config_mod.load()
    source = config.source or "the packaged default"
    print(f"Profiles from {source}:")
    for name in sorted(config.profiles):
        resolved = profiles.resolve(config.profiles[name])
        print(f"\n  {name}")
        print(f"    {len(resolved.hostnames)} hostnames from {_sources(config.profiles[name])}")

        # Kept apart so the listing does not imply that javaw.exe is closed on
        # sight, which would mean every Java program on the machine.
        plain = [p for p in resolved.processes if p not in config.command_line]
        guarded = [p for p in resolved.processes if p in config.command_line]
        if plain:
            print(f"    {len(plain)} applications: {', '.join(plain)}")
        if guarded:
            print(f"    {len(guarded)} only on a command-line match: {', '.join(guarded)}")
        if not resolved.processes:
            print("    no applications")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    config = config_mod.load()
    profile_name, duration = _require_lock_arguments(args)
    config.profile(profile_name)
    seconds = parse_duration(duration)
    _require_admin()

    store = SessionStore()
    current = store.load()
    if current is not None and current.is_active():
        raise SessionError(
            f"a {current.profile!r} lock is already running with "
            f"{format_duration(current.remaining)} left"
        )

    child = _spawn_worker(["run", "-p", profile_name, "-f", f"{seconds}s"]
                          + (["--hard"] if args.hard else []))
    session = _await_session(store, timeout=15.0)
    if session is None:
        print(
            f"complock: the worker (pid {child.pid}) did not engage the lock; "
            f"see {paths.log_file()}",
            file=sys.stderr,
        )
        return 1

    ends = time.strftime("%H:%M", time.localtime(session.ends_at))
    mode = "hard" if session.hard else "normal"
    print(f"Locked on {session.profile} ({mode}) until {ends}, {format_duration(seconds)} from now.")
    if config.disable_browser_doh and apps.running(browsers.CHROMIUM_PROCESSES):
        print("Restart your browser so the DNS policy takes effect.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = config_mod.load()
    store = SessionStore()

    if args.resume:
        if args.profile or args.duration:
            raise SessionError("--resume takes the stored lock; drop --profile and --for")
        session = store.load()
        if session is None:
            raise SessionError("there is no stored lock to resume")
        if not session.is_active():
            teardown(store)
            print("The stored lock had already expired; everything is unblocked.")
            return 0
        session = session.replace_pid(os.getpid())
    else:
        profile_name, duration = _require_lock_arguments(args)
        seconds = parse_duration(duration)
        now = time.time()
        session = Session(
            profile=profile_name,
            started_at=now,
            ends_at=now + seconds,
            hard=args.hard,
            pid=os.getpid(),
        )

    profile = config.profile(session.profile)
    _require_admin()
    return Watchdog(config, profiles.resolve(profile), session, store).run()


def cmd_status(args: argparse.Namespace) -> int:
    store = SessionStore()
    session = store.load()
    if session is None or not session.is_active():
        if session is not None:
            print("The last lock has expired but was never released. Run: complock stop")
            return 1
        print("No lock is running.")
        return 1

    blocked = hosts.HostsFile(paths.hosts_file()).blocked()
    worker = "running" if win32.is_running(session.pid) else "not running"

    print(f"Profile  {session.profile}")
    print(f"Started  {time.strftime('%H:%M:%S', time.localtime(session.started_at))}")
    print(
        f"Ends     {time.strftime('%H:%M:%S', time.localtime(session.ends_at))}"
        f"  ({format_duration(session.remaining)} left)"
    )
    print(f"Mode     {'hard' if session.hard else 'normal'}")
    print(f"Worker   pid {session.pid}, {worker}")
    print(f"Hosts    {len(blocked) if blocked is not None else 0} hostnames blocked")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    config = config_mod.load()
    store = SessionStore()
    session = store.load()
    if session is None:
        print("No lock is running.")
        return 1

    if session.is_active():
        if session.hard and not config.allow_early_stop:
            raise SessionError(
                f"this is a hard lock and allow_early_stop is off; "
                f"{format_duration(session.remaining)} left"
            )
        if not args.force:
            raise SessionError(
                f"{format_duration(session.remaining)} left on the {session.profile!r} lock; "
                "pass --force to end it anyway"
            )

    _require_admin()
    if win32.is_running(session.pid):
        try:
            win32.terminate(session.pid)
        except OSError as exc:
            _log.warning("could not stop the worker (pid %d): %s", session.pid, exc)

    teardown(store)
    print(f"Released the {session.profile!r} lock.")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """Called by the scheduled task at startup and once a minute afterwards."""
    store = SessionStore()
    session = store.load()
    if session is None:
        return 0
    if not session.is_active():
        _log.info("the stored lock expired while nothing was watching; releasing it")
        teardown(store)
        return 0
    if win32.is_running(session.pid, Path(sys.executable).name):
        return 0
    _log.warning(
        "no worker is holding the %r lock and %s remains; starting one",
        session.profile,
        format_duration(session.remaining),
    )
    _spawn_worker(["run", "--resume"])
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    findings: list[tuple[str, str, str]] = []

    elevated = win32.is_admin()
    findings.append(
        ("ok" if elevated else "fail", "administrator rights",
         "held" if elevated else "start an elevated prompt")
    )

    hosts_path = paths.hosts_file()
    try:
        handle = hosts.HostsFile(hosts_path)
        text = handle.read()
        region = hosts.extract(text)
        findings.append(("ok", "hosts file", f"{len(text.splitlines())} lines at {hosts_path}"))
        findings.append(
            ("ok", "managed region",
             f"{len(region)} hostnames blocked" if region else "not present")
        )
    except CompLockError as exc:
        findings.append(("fail", "hosts file", str(exc)))

    try:
        config = config_mod.load()
        findings.append(
            ("ok", "configuration",
             f"{len(config.profiles)} profiles from {config.source or 'the packaged default'}")
        )
    except CompLockError as exc:
        findings.append(("fail", "configuration", str(exc)))

    for subkey, name, value in browsers.current_state():
        browser = subkey.split("\\")[-1] if "DNSOverHTTPS" not in subkey else "Firefox"
        label = f"DoH policy ({browser} {name})"
        findings.append(
            ("ok", label, repr(value)) if value is not None
            else ("warn", label, "not set; this browser may bypass the hosts file")
        )

    try:
        session = SessionStore().load()
        if session is None:
            findings.append(("ok", "lock state", "no lock stored"))
        elif session.is_active():
            findings.append(
                ("ok", "lock state",
                 f"{session.profile!r} with {format_duration(session.remaining)} left")
            )
        else:
            findings.append(("warn", "lock state", "expired but not released; run complock stop"))
    except CompLockError as exc:
        findings.append(("fail", "lock state", str(exc)))

    findings.append(_scheduled_task_finding())

    width = max(len(label) for _, label, _ in findings)
    for level, label, detail in findings:
        print(f"{level:<5} {label:<{width}}  {detail}")

    return 1 if any(level == "fail" for level, _, _ in findings) else 0


def _scheduled_task_finding() -> tuple[str, str, str]:
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME],
            capture_output=True,
            text=True,
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError as exc:
        return ("warn", "reboot task", f"could not query the task scheduler: {exc}")

    if result.returncode == 0:
        return ("ok", "reboot task", "registered")

    # schtasks exits 1 whether the task is missing or merely unreadable, and a
    # task that runs as SYSTEM is not readable by an ordinary user. Reporting
    # that as "not registered" told people their reboot persistence was broken
    # while it was in fact working, so the two cases are kept apart.
    output = ((result.stdout or "") + (result.stderr or "")).casefold()
    if "access is denied" in output:
        return (
            "warn",
            "reboot task",
            "needs administrator rights to check, so this says nothing either way",
        )
    return ("warn", "reboot task", "not registered; a lock will not survive a reboot")


def _require_lock_arguments(args: argparse.Namespace) -> tuple[str, str]:
    if not args.profile:
        raise SessionError("name a profile with --profile; see complock profiles")
    if not args.duration:
        raise SessionError("say how long with --for, for instance --for 90m")
    return args.profile, args.duration


def _require_admin() -> None:
    if not win32.is_admin():
        raise PrivilegeError(
            "comp-lock rewrites the hosts file and closes other processes, so it has to "
            "run elevated; open an administrator prompt and try again"
        )


def _sources(profile: config_mod.Profile) -> str:
    named = [entry[1:] if entry.startswith("@") else entry for entry in profile.domains]
    return ", ".join(named) if named else "nothing"


def _spawn_worker(arguments: list[str]) -> subprocess.Popen[bytes]:
    """Start the watchdog in its own process group, detached from this console."""
    environment = dict(os.environ)
    # Keeps `-m complock` working when the package is being run out of a source
    # tree rather than from an installed copy.
    source_root = Path(__file__).resolve().parent.parent
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(source_root), environment.get("PYTHONPATH", "")) if part
    )
    return subprocess.Popen(
        [sys.executable, "-m", "complock", *arguments],
        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        cwd=os.environ.get("SystemRoot", "C:\\"),
        env=environment,
    )


def _await_session(store: SessionStore, timeout: float) -> Session | None:
    """Wait for the worker to record the lock it has engaged."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            session = store.load()
        except CompLockError:
            session = None
        if session is not None and session.is_active():
            return session
        time.sleep(0.2)
    return None


def _configure_logging(args: argparse.Namespace) -> None:
    root = logging.getLogger("complock")
    root.setLevel(logging.DEBUG)

    # Closed rather than just dropped: a discarded handler keeps the log file
    # open until it is collected, which on Windows is enough to block the
    # rotation of that same file.
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        try:
            handler.close()
        except OSError:
            pass

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    # Only `run` holds the console, so only it shows progress there. Everything
    # else keeps stderr for warnings and prints its own output.
    if args.verbose:
        console.setLevel(logging.DEBUG)
    elif args.command == "run":
        console.setLevel(logging.INFO)
    else:
        console.setLevel(logging.WARNING)
    root.addHandler(console)

    try:
        paths.ensure_data_dir()
        to_file = RotatingFileHandler(
            paths.log_file(), maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
        )
        to_file.setFormatter(formatter)
        to_file.setLevel(logging.DEBUG if args.verbose else logging.INFO)
        root.addHandler(to_file)
    except OSError:
        # An unwritable log directory should not stop a lock from starting.
        pass
