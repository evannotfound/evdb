class Error(Exception):
    """Base application error."""


class ConfigError(Error):
    pass


class CommandError(Error):
    pass


class ProtocolError(Error):
    pass


class RuntimeUnavailableError(ProtocolError):
    pass


class ProtocolMismatchError(ProtocolError):
    pass


class DeploymentError(Error):
    pass


class LockError(Error):
    pass


class BackupError(Error):
    pass


class RestoreError(Error):
    pass


class ResticError(Error):
    pass
