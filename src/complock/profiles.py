"""Turning a configured profile into the exact hostnames and executables to act on."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources

from .config import Profile
from .errors import ProfileError

# A hostname: dot-separated labels, no leading or trailing hyphen, at least one
# dot so that a bare word in a profile is caught as a mistake rather than
# written into the hosts file.
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_BLOCKLIST_NAMES = ("social", "video", "news", "games", "shopping")


@dataclass(frozen=True)
class ResolvedProfile:
    name: str
    hostnames: tuple[str, ...]
    processes: tuple[str, ...]


def available_blocklists() -> tuple[str, ...]:
    return _BLOCKLIST_NAMES


def load_blocklist(name: str) -> tuple[str, ...]:
    """Read a bundled list, dropping comments and blank lines."""
    if name not in _BLOCKLIST_NAMES:
        known = ", ".join(_BLOCKLIST_NAMES)
        raise ProfileError(f"no blocklist named {name!r}; bundled lists are: {known}")
    path = resources.files("complock").joinpath("blocklists", f"{name}.txt")
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.append(line)
    return tuple(entries)


def resolve(profile: Profile) -> ResolvedProfile:
    """Expand @list references, validate hostnames, and normalise executable names."""
    hostnames: set[str] = set()
    for entry in profile.domains:
        if entry.startswith("@"):
            listed = load_blocklist(entry[1:])
            for host in listed:
                hostnames.update(_expand(host, f"blocklist {entry[1:]!r}"))
        else:
            hostnames.update(_expand(entry, f"profile {profile.name!r}"))

    processes = {_normalise_process(name, profile.name) for name in profile.processes}

    return ResolvedProfile(
        name=profile.name,
        hostnames=tuple(sorted(hostnames)),
        processes=tuple(sorted(processes)),
    )


def _expand(host: str, where: str) -> tuple[str, ...]:
    """Validate a hostname and pair it with its www. alias."""
    host = host.strip().rstrip(".").casefold()
    if not _HOSTNAME.match(host):
        raise ProfileError(f"{where} lists {host!r}, which is not a hostname")
    if host.startswith("www."):
        return (host,)
    return (host, f"www.{host}")


def _normalise_process(name: str, profile_name: str) -> str:
    """Accept either 'Steam' or 'Steam.exe' and store one canonical form."""
    name = name.strip().casefold()
    if "/" in name or "\\" in name:
        raise ProfileError(
            f"profile {profile_name!r} lists {name!r}; give the executable name only, "
            "not a path, because comp-lock matches on the file name"
        )
    if not name:
        raise ProfileError(f"profile {profile_name!r} lists an empty process name")
    if "." not in name:
        name += ".exe"
    return name
