import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
import yaml

import evdb.config as config_module
import evdb.files as files_module
from evdb.config import (
    add_role,
    as_dict,
    dump,
    dump_secrets,
    load,
    protected,
    rclone_owner,
    reject_unsupported,
    repository_owner,
    repository_parts,
    require_valid,
    validate_image,
    write,
)
from evdb.errors import ConfigError
from evdb.models import RoleSecrets
from evdb.run import Result


def test_strict_two_file_round_trip_and_project_role_selectors(config):
    assert [item.identity for item in config.databases] == [
        "app-test-01/postgres",
        "app-test-01/kv",
    ]
    assert config.select("app-test-01/postgres").compose.name == "compose.yaml"
    assert config.host.data_roots == (config.paths.databases,)
    assert config.select("app-test-01/kv").data == (
        config.host.data_roots[0] / "app-test-01/kv/data"
    )
    assert config.paths.source.name == "config.yml"
    assert config.paths.secrets.name == "secrets.yml"
    assert config.host.backup.rclone_config.name == "rclone.conf"
    assert yaml.safe_load(dump(config)) == as_dict(config)
    assert "local-restic-password" not in dump(config)
    assert "local-restic-password" in dump_secrets(config.secrets)


def test_postgres_identity_is_required_without_compatibility_defaults(config):
    text = config.paths.source.read_text().replace("      username: default\n", "")
    config.paths.source.write_text(text)

    with pytest.raises(ConfigError, match="postgres.username is required"):
        load(config.paths.source, paths=config.paths)


def test_data_roots_are_required_without_compatibility_default(config):
    text = config.paths.source.read_text().replace(
        f"  data_roots:\n  - {config.paths.databases}\n", ""
    )
    config.paths.source.write_text(text)

    with pytest.raises(ConfigError, match="host.data_roots is required"):
        load(config.paths.source, paths=config.paths)


def test_role_data_root_is_required_without_compatibility_default(config):
    text = config.paths.source.read_text().replace(
        f"      data_root: {config.paths.databases}\n", "", 1
    )
    config.paths.source.write_text(text)

    with pytest.raises(ConfigError, match="postgres.data_root is required"):
        load(config.paths.source, paths=config.paths)


def test_custom_data_roots_are_explicit_and_derive_role_paths(config, tmp_path):
    parent = tmp_path / "database-volume"
    parent.mkdir()
    root = parent / "evdb"
    project = config.projects[0]
    selected = replace(
        config,
        host=replace(config.host, data_roots=(config.paths.databases, root)),
        projects=(replace(project, postgres=replace(project.postgres, data_root=root)),),
    )

    require_valid(selected)

    assert as_dict(selected)["host"]["data_roots"] == [str(config.paths.databases), str(root)]
    assert as_dict(selected)["projects"][project.id]["postgres"]["data_root"] == str(root)
    assert selected.select("app-test-01/postgres").data == (root / "app-test-01/postgres/data")


@pytest.mark.parametrize("root", [Path("relative/data"), Path("/")])
def test_data_root_rejects_relative_and_overlapping_paths(config, root):
    selected = replace(config, host=replace(config.host, data_roots=(root,)))

    with pytest.raises(ConfigError, match="host.data_root"):
        require_valid(selected)


def test_custom_data_root_requires_existing_immediate_parent(config, tmp_path):
    root = tmp_path / "missing" / "evdb"
    selected = replace(config, host=replace(config.host, data_roots=(root,)))

    with pytest.raises(ConfigError, match="parent is missing or unsafe"):
        require_valid(selected)


def test_canonical_data_root_accepts_operator_owned_private_parent(config, tmp_path, monkeypatch):
    parent = tmp_path / "data"
    parent.mkdir(mode=0o755)
    root = parent / "databases"
    selected = replace(
        config,
        host=replace(config.host, data_roots=(config.paths.databases, root)),
    )
    monkeypatch.setattr(config_module, "CONFIG_DIR", config.paths.config)

    require_valid(selected)


def test_canonical_data_root_rejects_group_writable_parent(config, tmp_path, monkeypatch):
    parent = tmp_path / "data"
    parent.mkdir(mode=0o775)
    parent.chmod(0o775)
    root = parent / "databases"
    selected = replace(
        config,
        host=replace(config.host, data_roots=(config.paths.databases, root)),
    )
    monkeypatch.setattr(config_module, "CONFIG_DIR", config.paths.config)

    with pytest.raises(ConfigError, match=r"group/world-writable.*mode=0775"):
        require_valid(selected)


def test_data_root_rejects_managed_backup_overlap(config):
    selected = replace(config, host=replace(config.host, data_roots=(config.paths.backups,)))

    with pytest.raises(ConfigError, match="overlaps an evdb managed path"):
        require_valid(selected)


def test_data_root_catalog_rejects_duplicates_and_overlap(config, tmp_path):
    parent = tmp_path / "database-volume"
    parent.mkdir()
    root = parent / "evdb"
    root.mkdir()
    nested = root / "fast"

    duplicate = replace(config, host=replace(config.host, data_roots=(root, root)))
    with pytest.raises(ConfigError, match="must not contain duplicates"):
        require_valid(duplicate)

    overlapping = replace(config, host=replace(config.host, data_roots=(root, nested)))
    with pytest.raises(ConfigError, match="must not overlap"):
        require_valid(overlapping)


def test_role_data_root_must_remain_in_host_catalog(config, tmp_path):
    root = tmp_path / "removed"
    project = config.projects[0]
    selected = replace(
        config,
        projects=(replace(project, kv=replace(project.kv, data_root=root)),),
    )

    with pytest.raises(ConfigError, match="app-test-01/kv: data_root is not configured"):
        require_valid(selected)


def test_atomic_writes_keep_source_and_secret_modes(config):
    write(config)

    assert config.paths.source.stat().st_mode & 0o777 == 0o600
    assert config.paths.secrets.stat().st_mode & 0o777 == 0o600
    assert not list(config.paths.config.glob(".*.yml.*"))


def test_secret_file_requires_exact_0600(config):
    config.paths.secrets.chmod(0o640)

    with pytest.raises(ConfigError, match="mode 0600"):
        load(config.paths.source, paths=config.paths)


def test_unsupported_host_and_machine_state_are_rejected(paths):
    paths.config.mkdir(parents=True)
    unsupported = paths.config / "host.yml"
    unsupported.write_text("host: {}\n")

    with pytest.raises(ConfigError, match="unsupported source configuration"):
        load(unsupported, paths=paths)
    with pytest.raises(ConfigError, match="provide current config.yml"):
        reject_unsupported(paths)

    unsupported.unlink()
    state = paths.state / "state/host.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}\n")
    with pytest.raises(ConfigError, match="unsupported source layout"):
        reject_unsupported(paths)


@pytest.mark.parametrize("image", ["postgres", "postgres:latest", " postgres:16"])
def test_images_require_direct_non_latest_references(image):
    with pytest.raises(ConfigError):
        validate_image(image)


def test_image_with_registry_port_is_supported():
    validate_image("registry.example.com:5000/postgres:16.4")


def test_rclone_is_referenced_in_place_and_not_rewritten(config):
    rclone = config.host.backup.rclone_config
    before = (rclone.stat().st_ino, rclone.read_bytes())

    write(config)

    assert (rclone.stat().st_ino, rclone.read_bytes()) == before
    assert as_dict(config)["host"]["backup"]["rclone_config"] == str(rclone)


def test_matching_secrets_are_required_and_extras_rejected(config):
    missing = replace(
        config,
        secrets=replace(config.secrets, projects=()),
    )
    with pytest.raises(ConfigError, match="matching secrets"):
        from evdb.config import require_valid

        require_valid(missing)

    role = config.select("app-test-01/postgres")
    added = add_role(
        config,
        "other-prod-01",
        "postgres",
        role.settings,
        RoleSecrets("other-password"),
    )
    assert added.select("other-prod-01/postgres").credentials.password == "other-password"


def test_protected_values_are_exact_and_encoded_while_repository_is_visible(config):
    config.host.backup.rclone_config.write_text(
        "[remote]\n"
        "type = local\n"
        'token = {"access_token":"nested access","refresh_token":"nested refresh",'
        '"nested":{"client_secret":"nested secret"}}\n'
    )
    values = protected(config)

    assert "local-kv-password" in values
    assert "nested access" in values
    assert "nested%20access" in values
    assert "nested refresh" in values
    assert "nested secret" in values
    assert config.host.backup.repository not in values


def test_rclone_defaults_only_credentials_are_protected(config):
    config.host.backup.rclone_config.write_text("[DEFAULT]\nclient_secret = defaults secret\n")

    assert "defaults secret" in protected(config)


@pytest.mark.parametrize(
    "text",
    [
        "[remote]\ntype = local\n[remote]\ntype = local\n",
        "[remote]\ntoken = first\ntoken = second\n",
    ],
)
def test_rclone_credential_extraction_rejects_ambiguous_or_malformed_config(config, text):
    config.host.backup.rclone_config.write_text(text)

    with pytest.raises(ConfigError, match="invalid rclone configuration"):
        protected(config)


def test_json_shaped_rclone_token_is_treated_as_opaque(config, monkeypatch):
    from evdb import backup

    config.host.backup.rclone_config.write_text("[remote]\ntoken = {broken\n")
    seen = {}

    def run(args, **kwargs):
        seen.update(secrets=kwargs["secrets"])
        return Result(tuple(args), 0, "{}", "")

    monkeypatch.setattr(backup, "run", run)

    backup._restic(config, ["cat", "config"])

    assert "{broken" in seen["secrets"]


def test_non_secret_scalars_reject_managed_values_and_encoded_forms(config):
    secret = config.secrets.restic_password
    repository = replace(
        config,
        host=replace(
            config.host,
            backup=replace(config.host.backup, repository=f"rclone:remote:{secret}"),
        ),
    )
    with pytest.raises(ConfigError, match="repository.*managed credential"):
        require_valid(repository)

    project = config.projects[0]
    image = replace(
        config,
        projects=(
            replace(project, postgres=replace(project.postgres, image=f"postgres:{secret}")),
        ),
    )
    with pytest.raises(ConfigError, match="image.*managed credential"):
        require_valid(image)

    encoded_secret = "managed secret"
    encoded = replace(
        config,
        host=replace(
            config.host,
            backup=replace(
                config.host.backup,
                repository=f"rclone:remote:{quote(encoded_secret, safe='')}",
            ),
        ),
        secrets=replace(config.secrets, restic_password=encoded_secret),
    )
    with pytest.raises(ConfigError, match="repository.*managed credential"):
        require_valid(encoded)

    domain = replace(config, secrets=replace(config.secrets, restic_password=config.host.domain))
    with pytest.raises(ConfigError, match="domain.*managed credential"):
        require_valid(domain)


def test_short_credentials_do_not_collide_with_ordinary_source_fields(config):
    short = replace(config, secrets=replace(config.secrets, restic_password="a"))

    require_valid(short)

    leaked = replace(
        short,
        host=replace(
            short.host,
            backup=replace(short.host.backup, repository="a"),
        ),
    )
    with pytest.raises(ConfigError, match="repository.*managed credential"):
        require_valid(leaked)


@pytest.mark.parametrize("role", ["postgres", "kv"])
def test_every_derived_native_database_domain_must_be_valid(config, role):
    base_domain = ".".join(("a" * 63, "b" * 63, "c" * 63, "d" * 47))
    project = config.projects[0]
    secret_project = config.secrets.projects[0]
    if role == "postgres":
        project = replace(project, kv=None)
        secret_project = replace(secret_project, kv=None)
    else:
        project = replace(
            project,
            postgres=None,
            kv=replace(project.kv, http=replace(project.kv.http, enabled=False)),
        )
        secret_project = replace(secret_project, postgres=None)
    invalid = replace(
        config,
        host=replace(config.host, domain=base_domain),
        projects=(project,),
        secrets=replace(config.secrets, projects=(secret_project,)),
    )

    with pytest.raises(ConfigError, match=rf"app-test-01/{role}: native domain is invalid"):
        require_valid(invalid)


def test_canonical_layout_enforces_owner_group_and_modes(config, monkeypatch):
    config.paths.config.chmod(0o700)
    config.paths.source.chmod(0o600)
    monkeypatch.setattr(config_module, "CONFIG_DIR", config.paths.config)
    real_stat = config_module.Path.stat

    def root_stat(path, *args, **kwargs):
        details = real_stat(path, *args, **kwargs)
        return SimpleNamespace(st_uid=0, st_gid=0, st_mode=details.st_mode)

    monkeypatch.setattr(config_module.Path, "stat", root_stat)

    load(config.paths.source, paths=config.paths)

    def foreign_stat(path, *args, **kwargs):
        details = root_stat(path, *args, **kwargs)
        if path == config.paths.source:
            details.st_uid = 1
        return details

    monkeypatch.setattr(config_module.Path, "stat", foreign_stat)
    with pytest.raises(ConfigError, match="owner, group, or mode"):
        load(config.paths.source, paths=config.paths)


def test_rclone_path_must_be_private_regular_and_external(config, tmp_path):
    rclone = config.host.backup.rclone_config
    rclone.chmod(0o640)
    with pytest.raises(ConfigError, match="mode 0600"):
        require_valid(config)

    rclone.unlink()
    target = tmp_path / "target.conf"
    target.write_text("[local]\ntype = local\n")
    target.chmod(0o600)
    rclone.symlink_to(target)
    with pytest.raises(ConfigError, match="unsafe"):
        require_valid(config)


@pytest.mark.parametrize("mode", [0o400, 0o640, 0o601])
def test_rclone_path_requires_exact_0600(config, mode):
    config.host.backup.rclone_config.chmod(mode)

    with pytest.raises(ConfigError, match="mode 0600"):
        require_valid(config)


def test_rclone_owner_derives_current_account_and_groups(config):
    owner = rclone_owner(config.host.backup.rclone_config)

    assert owner.uid == os.getuid()
    assert owner.gid == os.getgid()
    assert owner.name
    assert owner.home.is_dir()
    assert owner.gid not in owner.groups


def test_rclone_path_rejects_root_owner(config, monkeypatch):
    real_fstat = config_module.os.fstat

    def root_fstat(descriptor):
        details = real_fstat(descriptor)
        values = list(details)
        values[4] = 0
        return os.stat_result(values)

    monkeypatch.setattr(config_module.os, "fstat", root_fstat)

    with pytest.raises(ConfigError, match="non-root"):
        require_valid(config)


def test_rclone_path_rejects_unknown_owner(config, monkeypatch):
    monkeypatch.setattr(
        config_module.pwd,
        "getpwuid",
        lambda uid: (_ for _ in ()).throw(KeyError(uid)),
    )

    with pytest.raises(ConfigError, match="owner is unknown"):
        require_valid(config)


def test_rclone_path_rejects_unsafe_mutable_parent(config):
    parent = config.host.backup.rclone_config.parent
    parent.chmod(0o770)

    with pytest.raises(ConfigError, match="parent.*user-writable"):
        require_valid(config)


def test_local_repository_omits_rclone_and_derives_parent_owner(config, tmp_path):
    local = replace(
        config,
        host=replace(
            config.host,
            backup=replace(
                config.host.backup,
                repository=str(tmp_path / "restic"),
                rclone_config=None,
            ),
        ),
    )

    require_valid(local)

    assert repository_parts(local.host.backup.repository) is None
    assert repository_owner(local.host.backup).uid == os.getuid()
    assert "rclone_config" not in as_dict(local)["host"]["backup"]

    (tmp_path / "restic").mkdir()
    tmp_path.chmod(0o770)
    with pytest.raises(ConfigError, match="owner differs from its parent"):
        require_valid(local)


def test_rclone_repository_requires_configured_remote(config):
    invalid = replace(
        config,
        host=replace(
            config.host,
            backup=replace(config.host.backup, repository="rclone:missing:evdb/test"),
        ),
    )

    with pytest.raises(ConfigError, match="remote is not configured"):
        require_valid(invalid)


def test_rclone_credential_read_does_not_follow_symlink(config, tmp_path):
    rclone = config.host.backup.rclone_config
    target = tmp_path / "target.conf"
    target.write_text("[remote]\ntoken = must-not-be-read\n")
    target.chmod(0o600)
    rclone.unlink()
    rclone.symlink_to(target)

    with pytest.raises(ConfigError, match="missing or unsafe"):
        protected(config)


def test_canonical_atomic_writes_apply_final_owners_before_replace(config, monkeypatch):
    current_uid = os.getuid()
    current_gid = os.getgid()
    selected = config
    monkeypatch.setattr(config_module, "CONFIG_DIR", config.paths.config)
    real_fchown = files_module.os.fchown
    owners = []

    def fchown(descriptor, uid, gid):
        owners.append((uid, gid))
        real_fchown(descriptor, current_uid, current_gid)

    monkeypatch.setattr(files_module.os, "fchown", fchown)

    write(selected)

    assert owners == [
        (0, 0),
        (0, 0),
    ]
    assert selected.paths.source.stat().st_mode & 0o777 == 0o600
    assert selected.paths.secrets.stat().st_mode & 0o777 == 0o600


def test_canonical_secret_write_failure_does_not_restore_source(config, monkeypatch):
    selected = config
    monkeypatch.setattr(config_module, "CONFIG_DIR", config.paths.config)
    real_fchown = os.fchown
    monkeypatch.setattr(
        files_module.os,
        "fchown",
        lambda descriptor, uid, gid: real_fchown(descriptor, os.getuid(), os.getgid()),
    )
    real_write = config_module.write_text
    owners = []

    def fail_secret(path, text, *, mode, owner=None):
        owners.append((path, owner))
        if path == selected.paths.secrets:
            raise OSError("injected canonical secret failure")
        return real_write(path, text, mode=mode, owner=owner)

    monkeypatch.setattr(config_module, "write_text", fail_secret)

    with pytest.raises(OSError, match="injected canonical"):
        write(selected)

    assert selected.paths.source.read_text() == dump(selected)
    assert owners[0][1] == (0, 0)
