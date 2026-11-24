"""Loading and validating the configuration file.

Unknown keys are rejected rather than ignored. A typo in a setting that
silently does nothing is worse here than a startup error, because the setting
it was meant to be is what keeps the lock honest.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from . import paths
from .errors import ConfigError

_TOP_LEVEL = {
    "poll_interval",
    "allow_early_stop",
    "flush_dns",
    "disable_browser_doh",
    "profiles",
}
_PROFILE_KEYS = {"domains", "processes"}

MIN_POLL_INTERVAL = 0.5
MAX_POLL_INTERVAL = 60.0


@dataclass(frozen=True)
class Profile:
    name: str
    domains: tuple[str, ...]
    processes: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    poll_interval: float
    allow_early_stop: bool
    flush_dns: bool
    disable_browser_doh: bool
    profiles: Mapping[str, Profile]
    source: Path | None

    def profile(self, name: str) -> Profile:
        try:
            return self.profiles[name]
        except KeyError:
            known = ", ".join(sorted(self.profiles)) or "none"
            raise ConfigError(f"no profile named {name!r}; defined profiles: {known}") from None


def default_text() -> str:
    """The commented configuration that `complock init` writes out."""
    return resources.files("complock").joinpath("config.default.toml").read_text(encoding="utf-8")


def load(path: Path | None = None) -> Config:
    """Read the configuration, falling back to the packaged default."""
    path = path if path is not None else paths.config_file()
    if path.exists():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
        return _build(raw, path)
    return _build(tomllib.loads(default_text()), None)


def _build(raw: Mapping[str, Any], source: Path | None) -> Config:
    _reject_unknown(raw, _TOP_LEVEL, "the top level")

    interval = _number(raw, "poll_interval", 2.0)
    if not MIN_POLL_INTERVAL <= interval <= MAX_POLL_INTERVAL:
        raise ConfigError(
            f"poll_interval must be between {MIN_POLL_INTERVAL} and {MAX_POLL_INTERVAL} "
            f"seconds, got {interval}"
        )

    profiles_raw = raw.get("profiles", {})
    if not isinstance(profiles_raw, Mapping):
        raise ConfigError("profiles must be a table of named profiles")
    if not profiles_raw:
        raise ConfigError("no profiles are defined; a lock needs at least one")

    profiles = {name: _profile(name, body) for name, body in profiles_raw.items()}

    return Config(
        poll_interval=float(interval),
        allow_early_stop=_flag(raw, "allow_early_stop", True),
        flush_dns=_flag(raw, "flush_dns", True),
        disable_browser_doh=_flag(raw, "disable_browser_doh", True),
        profiles=profiles,
        source=source,
    )


def _profile(name: str, body: Any) -> Profile:
    where = f"profile {name!r}"
    if not isinstance(body, Mapping):
        raise ConfigError(f"{where} must be a table")
    _reject_unknown(body, _PROFILE_KEYS, where)
    domains = _string_list(body, "domains", where)
    processes = _string_list(body, "processes", where)
    if not domains and not processes:
        raise ConfigError(f"{where} blocks nothing; give it domains, processes, or both")
    return Profile(name=name, domains=domains, processes=processes)


def _reject_unknown(body: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise ConfigError(
            f"unrecognised {'key' if len(unknown) == 1 else 'keys'} at {where}: "
            + ", ".join(unknown)
        )


def _string_list(body: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = body.get(key, [])
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ConfigError(f"{where}: {key} must be a list of strings")
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{where}: {key} contains an entry that is not a name: {item!r}")
        out.append(item.strip())
    return tuple(out)


def _flag(body: Mapping[str, Any], key: str, default: bool) -> bool:
    value = body.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be true or false, got {value!r}")
    return value


def _number(body: Mapping[str, Any], key: str, default: float) -> float:
    value = body.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} must be a number, got {value!r}")
    return float(value)
