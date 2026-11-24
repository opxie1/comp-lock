"""Group-policy switches that stop a browser resolving names behind the OS.

Chromium and Firefox both ship with DNS-over-HTTPS on or set to automatic,
which means they ask their own resolver over HTTPS and never look at the hosts
file. Blocking a site has no effect in that state, so a lock turns the feature
off by policy and puts the previous values back when it ends.

Policy is read when the browser starts. A browser that is already open keeps
resolving the old way until it is restarted.
"""

from __future__ import annotations

import json
import logging
import winreg
from typing import Any

from . import paths

# (subkey under HKLM, value name, type, value that disables DoH)
_POLICIES: tuple[tuple[str, str, int, Any], ...] = (
    (r"SOFTWARE\Policies\Google\Chrome", "DnsOverHttpsMode", winreg.REG_SZ, "off"),
    (r"SOFTWARE\Policies\Microsoft\Edge", "DnsOverHttpsMode", winreg.REG_SZ, "off"),
    (r"SOFTWARE\Policies\BraveSoftware\Brave", "DnsOverHttpsMode", winreg.REG_SZ, "off"),
    (r"SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS", "Enabled", winreg.REG_DWORD, 0),
    (r"SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS", "Locked", winreg.REG_DWORD, 1),
)

CHROMIUM_PROCESSES = ("chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe")

_log = logging.getLogger(__name__)


def disable() -> int:
    """Switch DoH off. Returns how many registry values were changed."""
    backup = paths.doh_backup()
    # Only record the original state once. A second call, after a reboot for
    # instance, would otherwise save comp-lock's own values as the originals
    # and never restore what the user had.
    record_originals = not backup.exists()
    saved: list[dict[str, Any]] = []
    changed = 0

    for subkey, name, kind, wanted in _POLICIES:
        key_existed = _key_exists(subkey)
        previous = _read(subkey, name)
        if record_originals:
            saved.append(
                {"subkey": subkey, "name": name, "kind": kind,
                 "value": previous, "key_existed": key_existed}
            )
        if previous == wanted:
            continue
        try:
            _write(subkey, name, kind, wanted)
        except OSError as exc:
            _log.warning(r"could not set HKLM\%s\%s: %s", subkey, name, exc)
            continue
        changed += 1

    if record_originals:
        paths.ensure_data_dir()
        backup.write_text(json.dumps(saved, indent=2), encoding="utf-8")

    if changed:
        _log.info("turned DNS-over-HTTPS off in %d policy values", changed)
    return changed


def restore() -> int:
    """Put the recorded values back. Returns how many were restored."""
    backup = paths.doh_backup()
    try:
        saved = json.loads(backup.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 0
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("cannot read %s: %s; leaving browser policy alone", backup, exc)
        return 0

    restored = 0
    for item in saved:
        subkey = item["subkey"]
        name = item["name"]
        try:
            if item["value"] is None:
                _delete(subkey, name)
                if not item.get("key_existed", True):
                    _delete_key_if_empty(subkey)
            else:
                _write(subkey, name, item["kind"], item["value"])
            restored += 1
        except OSError as exc:
            _log.warning(r"could not restore HKLM\%s\%s: %s", subkey, name, exc)

    try:
        backup.unlink()
    except OSError as exc:
        _log.warning("could not remove %s: %s", backup, exc)

    return restored


def current_state() -> list[tuple[str, str, Any]]:
    """Every policy value comp-lock manages, for the doctor command."""
    return [(subkey, name, _read(subkey, name)) for subkey, name, _, _ in _POLICIES]


def _key_exists(subkey: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey):
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _read(subkey: str, name: str) -> Any:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _write(subkey: str, name: str, kind: int, value: Any) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, subkey, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, kind, value)


def _delete(subkey: str, name: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


def _delete_key_if_empty(subkey: str) -> None:
    """Tidy up a policy key that comp-lock created and nothing else uses."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
            subkeys, values, _ = winreg.QueryInfoKey(key)
        if subkeys or values:
            return
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, subkey)
    except OSError:
        pass
