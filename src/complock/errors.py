"""Errors that the command line reports as a message rather than a traceback."""


class CompLockError(Exception):
    """Base class for anything the user can fix."""


class ConfigError(CompLockError):
    """The configuration file is missing a value or carries one that is unusable."""


class ProfileError(CompLockError):
    """A profile names a blocklist or hostname that cannot be resolved."""


class SessionError(CompLockError):
    """The stored lock state is missing, unreadable, or contradicts the request."""


class PrivilegeError(CompLockError):
    """The operation needs an elevated prompt."""


class HostsError(CompLockError):
    """The hosts file could not be read or rewritten."""
