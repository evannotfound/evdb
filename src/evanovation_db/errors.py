class Error(Exception):
    """Base application error."""


class ConfigError(Error):
    pass


class CommandError(Error):
    pass


class LockError(Error):
    pass


class BackupError(Error):
    pass


class RestoreError(Error):
    pass


class ResticError(Error):
    pass
