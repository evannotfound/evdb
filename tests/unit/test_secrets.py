from urllib.parse import unquote, urlparse

import pytest

from evdb.errors import ConfigError
from evdb.secrets import Credentials, credentials, ensure, protected, render


def test_credentials_are_generated_once_and_all_files_are_private(config):
    target = config.select("app-test-01/kv")
    values = iter(["p@ss/word", "http-token"])

    first = ensure(config, target, generate=lambda: next(values))
    second = ensure(config, target, generate=lambda: "changed")

    assert first == second == Credentials("p@ss/word", "http-token")
    root = config.paths.role_secrets(target.project, target.role)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in root.iterdir())


def test_http_environment_uses_unique_backend_and_encoded_password(config):
    target = config.select("app-test-01/kv")
    files = render(config, target, Credentials("p@ss/word", "token"))
    env = next(item.content for item in files if item.path.name == "http.env")
    connection = env.split("SRH_CONNECTION_STRING=", 1)[1].strip().strip('"')
    parsed = urlparse(connection)

    assert unquote(parsed.password) == "p@ss/word"
    assert parsed.hostname == "evdb-app-test-01-kv-primary"
    assert 'SRH_TOKEN="token"' in env


def test_postgres_and_engine_files_are_explicit(config):
    from dataclasses import replace

    postgres = config.select("app-test-01/postgres")
    postgres = replace(
        postgres,
        settings=replace(
            postgres.settings,
            pgbouncer=replace(postgres.settings.pgbouncer, enabled=True),
        ),
    )
    files = {item.path.name: item.content for item in render(config, postgres, Credentials("pw"))}

    assert files["password"] == "pw\n"
    assert files["pgbouncer-users"] == '"default" "pw"\n'

    kv = config.select("app-test-01/kv")
    files = {item.path.name: item.content for item in render(config, kv, Credentials("pw", "t"))}
    assert 'requirepass "pw"' in files["redis.conf"]
    assert 'save ""' in files["redis.conf"]

    dragonfly = replace(
        kv,
        settings=replace(kv.settings, engine="dragonfly", memory="256mb", threads=1),
    )
    files = {
        item.path.name: item.content for item in render(config, dragonfly, Credentials("pw", "t"))
    }
    assert "--dbfilename=dump" in files["dragonfly.flags"]
    assert "--requirepass=pw" in files["dragonfly.flags"]
    assert "--nodf_snapshot_format" not in files["dragonfly.flags"]


def test_missing_credential_fails_without_partial_result(config):
    target = config.select("app-test-01/kv")
    root = config.paths.role_secrets(target.project, target.role)
    root.mkdir(parents=True)
    (root / "password").write_text("pw\n")
    (root / "password").chmod(0o600)

    with pytest.raises(ConfigError, match="http-token"):
        credentials(config, target)


def test_unsafe_or_public_secret_file_is_rejected(config, tmp_path):
    target = config.select("app-test-01/kv")
    root = config.paths.role_secrets(target.project, target.role)
    root.mkdir(parents=True)
    password = root / "password"
    password.write_text("pw\n")
    password.chmod(0o644)

    with pytest.raises(ConfigError, match="not private"):
        credentials(config, target)

    password.unlink()
    password.symlink_to(tmp_path / "other")
    with pytest.raises(ConfigError, match="unsafe"):
        credentials(config, target)


def test_repr_and_protected_values_do_not_expose_credentials():
    values = Credentials("private-password", "private-token")

    assert "private" not in repr(values)
    expanded = protected((values.password, values.http_token))
    assert "private-password" in expanded
    assert "private-token" in expanded
