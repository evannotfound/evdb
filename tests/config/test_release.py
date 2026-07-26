from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github/workflows/release.yml"


def _workflow():
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def test_release_runs_only_for_semantic_version_tags_with_read_only_default():
    workflow = _workflow()

    assert workflow["on"] == {"push": {"tags": ["v[0-9]*.[0-9]*.[0-9]*"]}}
    assert workflow["permissions"] == {"contents": "read"}
    assert "pull_request" not in workflow["on"]


def test_release_checks_tag_and_repository_before_native_builds():
    workflow = _workflow()
    jobs = workflow["jobs"]
    check_steps = "\n".join(str(step) for step in jobs["check"]["steps"])

    assert jobs["check"]["runs-on"] == "ubuntu-22.04"
    assert "uv sync --locked" in check_steps
    assert "uv run evdb --version" in check_steps
    assert "GITHUB_REF_NAME" in check_steps
    assert "make check" in check_steps
    assert jobs["build"]["needs"] == "check"
    assert jobs["build"]["strategy"]["matrix"]["include"] == [
        {"runner": "ubuntu-22.04", "arch": "amd64"},
        {"runner": "ubuntu-22.04-arm", "arch": "arm64"},
    ]


def test_release_builds_one_file_archives_with_checksums_and_units():
    text = WORKFLOW.read_text()

    for phrase in (
        "make binary",
        "dist/evdb --version",
        'ASSET="evdb_linux_${{ matrix.arch }}.tar.gz"',
        "release/bin/evdb",
        "src/evanovation_db/units/*.service",
        "src/evanovation_db/units/*.timer",
        "tar -czf",
        "sha256sum",
        "tools/check_release.py",
        "actions/upload-artifact@v7",
    ):
        assert phrase in text
    assert "uv tool install" not in text
    assert "pypi" not in text.lower()


def test_release_publishes_only_after_both_builds_with_provenance():
    workflow = _workflow()
    publish = workflow["jobs"]["publish"]
    steps = "\n".join(str(step) for step in publish["steps"])

    assert publish["needs"] == "build"
    assert publish["permissions"] == {
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert "actions/download-artifact@v8" in steps
    assert "install.sh release/install.sh" in steps
    assert "actions/attest@v4" in steps
    assert "release/evdb_linux_*.tar.gz" in steps
    assert "gh release create" in steps
    assert "--verify-tag" in steps
