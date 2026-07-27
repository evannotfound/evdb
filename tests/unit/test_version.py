from importlib.metadata import version

import pytest

from evdb import __version__, _version, cli, host, status


def test_version_is_consistent_with_package_cli_and_status(capsys):
    assert _version.__version__ == __version__
    assert version("evdb") == __version__
    assert status._version() == __version__

    with pytest.raises(SystemExit) as caught:
        cli.parser().parse_args(["--version"])

    assert caught.value.code == 0
    assert capsys.readouterr().out == f"evdb {__version__}\n"


@pytest.mark.parametrize(
    "value",
    ["0.1.0", "1.2.3-alpha.1", "1.2.3-1a", "1.2.3+build.01", "1.2.3-rc.1+build.2"],
)
def test_release_semver_accepts_valid_versions(value):
    assert host.VERSION.fullmatch(value)
