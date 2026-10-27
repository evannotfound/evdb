import json
import os
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = yaml.load((ROOT / ".github/workflows/preview.yml").read_text(), Loader=yaml.BaseLoader)
PUBLISH = WORKFLOW["jobs"]["publish"]["steps"][-1]["run"]
COMMIT = "b" * 40
VERSION = "1.2.4.dev17+gabc1234"

# Execute the real publication shell against disposable immutable releases, with no GitHub writes.
FAKE_GH = """\
import json, os, pathlib, sys
path = pathlib.Path(os.environ['RELEASE_STATE'])
state = json.loads(path.read_text())
args = sys.argv[1:]
state['calls'].append(args)
assert args[0] == 'release', args
tag = args[2]
release = state['releases'].get(tag)
code = 0
def option(name):
    return args[args.index(name) + 1]
if args[1] == 'view':
    assert option('--json') == 'isDraft'
    assert option('--jq') == '.isDraft'
    if release is None:
        code = 1
    else:
        print(json.dumps(release['draft']))
elif args[1] == 'create':
    if release is not None:
        code = 1
    else:
        state['releases'][tag] = {
            'title': option('--title'), 'body': option('--notes'),
            'target': option('--target'), 'draft': '--draft' in args,
            'prerelease': '--prerelease' in args, 'latest': '--latest=false' not in args,
            'assets': {},
        }
elif release is not None and not release['draft']:
    print('HTTP 422: published release is immutable', file=sys.stderr)
    code = 1
elif args[1] == 'upload':
    for value in args[3:]:
        if value.startswith('--'):
            continue
        asset = pathlib.Path(value)
        if asset.name in release['assets'] and '--clobber' not in args:
            code = 1
            break
        if '--clobber' in args:
            release['assets'].pop(asset.name, None)
        if asset.name == os.environ.get('FAIL_UPLOAD'):
            code = 1
            break
        release['assets'][asset.name] = asset.read_text()
elif args[1] == 'edit':
    assert '--draft=false' in args
    release['draft'] = False
    release['prerelease'] = '--prerelease' in args
    release['latest'] = '--latest=false' not in args
    release['published_assets'] = release['assets'].copy()
else:
    raise AssertionError(args)
path.write_text(json.dumps(state))
sys.exit(code)
"""


def _state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"releases": {}, "calls": []}))
    return path


def _assets(run=17, attempt=1, version=VERSION):
    assets = {
        "install.sh": "new installer",
        "preview.txt": f"{COMMIT} {version} {run} {attempt}\n",
    }
    for arch in ["amd64", "arm64"]:
        name = f"evdb_linux_{arch}_{COMMIT}_{run}_{attempt}"
        assets[name] = "new executable"
        assets[name + ".sha256"] = "new checksum"
    return assets


def _publish(tmp_path, state, *, run=17, attempt=1, fail_upload="", version=VERSION):
    work = tmp_path / f"run-{run}-{attempt}"
    work.mkdir(exist_ok=True)
    binary_dir = work / "bin"
    binary_dir.mkdir(exist_ok=True)
    gh = binary_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n" + FAKE_GH)
    gh.chmod(0o755)
    release = work / "release"
    release.mkdir(exist_ok=True)
    for name, content in _assets(run, attempt, version).items():
        if name.startswith("evdb_linux_"):
            (release / name).write_text(content)
    (work / "install.sh").write_text("new installer")
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", PUBLISH],
        cwd=work,
        env={
            **os.environ,
            "PATH": f"{binary_dir}:{os.environ['PATH']}",
            "RELEASE_STATE": str(state),
            "GITHUB_SHA": COMMIT,
            "GITHUB_RUN_NUMBER": str(run),
            "GITHUB_RUN_ATTEMPT": str(attempt),
            "GITHUB_REPOSITORY": "example/evdb",
            "VERSION": version,
            "FAIL_UPLOAD": fail_upload,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    return result, json.loads(state.read_text())


def test_preview_workflow_requires_both_native_builds_and_attests_them():
    assert WORKFLOW["on"] == {"push": {"branches": ["main"]}}
    jobs = WORKFLOW["jobs"]
    assert jobs["build"]["needs"] == "check"
    assert jobs["build"]["strategy"]["matrix"]["include"] == [
        {"runner": "ubuntu-22.04", "arch": "amd64"},
        {"runner": "ubuntu-22.04-arm", "arch": "arm64"},
    ]
    assert jobs["publish"]["needs"] == ["check", "build"]
    assert jobs["publish"]["concurrency"] == {
        "group": "preview-publication",
        "cancel-in-progress": "false",
        "queue": "max",
    }
    assert any(step.get("uses") == "actions/attest@v4" for step in jobs["publish"]["steps"])
    stable = yaml.load((ROOT / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader)
    assert "branches" not in stable["on"]["push"]


def test_first_publication_attaches_all_assets_to_draft_before_publishing(tmp_path):
    result, state = _publish(tmp_path, _state(tmp_path))

    assert result.returncode == 0, result.stderr
    assert list(state["releases"]) == ["v1.2.4.dev17"]
    release = state["releases"]["v1.2.4.dev17"]
    assert release["title"] == "v1.2.4.dev17"
    assert VERSION in release["body"]
    assert release["target"] == COMMIT
    assert release["prerelease"] and not release["latest"]
    assert not release["draft"]
    assert release["assets"] == release["published_assets"] == _assets()
    assert [call[1] for call in state["calls"]] == ["view", "create", "upload", "edit"]
    create = state["calls"][1]
    assert all(flag in create for flag in ["--draft", "--prerelease", "--latest=false"])


@pytest.mark.parametrize("run", [18, 16])
def test_new_version_leaves_previous_release_unchanged(tmp_path, run):
    state = _state(tmp_path)
    first, before = _publish(tmp_path, state)
    assert first.returncode == 0, first.stderr

    version = f"1.2.4.dev{run}+gabc1234"
    result, after = _publish(tmp_path, state, run=run, version=version)

    assert result.returncode == 0, result.stderr
    assert len(after["releases"]) == 2
    assert after["releases"]["v1.2.4.dev17"] == before["releases"]["v1.2.4.dev17"]
    release = after["releases"][f"v1.2.4.dev{run}"]
    assert not release["draft"]
    assert release["assets"] == release["published_assets"] == _assets(run, version=version)


@pytest.mark.parametrize(("run", "attempt"), [(17, 1), (17, 2), (18, 1)])
def test_published_version_is_reused_across_runs_and_attempts(tmp_path, run, attempt):
    state = _state(tmp_path)
    first, before = _publish(tmp_path, state)
    assert first.returncode == 0, first.stderr

    result, after = _publish(tmp_path, state, run=run, attempt=attempt)

    assert result.returncode == 0, result.stderr
    assert after["releases"] == before["releases"]
    assert after["calls"][len(before["calls"]) :] == [
        ["release", "view", "v1.2.4.dev17", "--json", "isDraft", "--jq", ".isDraft"]
    ]


@pytest.mark.parametrize(
    "asset", [f"evdb_linux_arm64_{COMMIT}_17_1.sha256", "install.sh", "preview.txt"]
)
def test_failed_upload_keeps_draft_and_same_attempt_recovers(tmp_path, asset):
    state = _state(tmp_path)
    failed, partial = _publish(tmp_path, state, fail_upload=asset)
    assert failed.returncode != 0
    draft = partial["releases"]["v1.2.4.dev17"]
    assert draft["draft"]
    assert draft["assets"] and asset not in draft["assets"]
    assert "published_assets" not in draft

    result, after = _publish(tmp_path, state)

    assert result.returncode == 0, result.stderr
    release = after["releases"]["v1.2.4.dev17"]
    assert not release["draft"]
    assert release["assets"] == release["published_assets"] == _assets()
    calls = after["calls"][len(partial["calls"]) :]
    assert [call[1] for call in calls] == ["view", "upload", "edit"]
    assert "--clobber" in calls[1]


@pytest.mark.parametrize("operation", ["upload", "edit", "delete-asset"])
def test_fake_github_rejects_changes_to_published_releases(tmp_path, operation):
    state = _state(tmp_path)
    first, before = _publish(tmp_path, state)
    assert first.returncode == 0, first.stderr

    result = subprocess.run(
        [sys.executable, "-c", FAKE_GH, "release", operation, "v1.2.4.dev17"],
        env={**os.environ, "RELEASE_STATE": str(state)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "HTTP 422" in result.stderr
    assert json.loads(state.read_text())["releases"] == before["releases"]


def test_stable_source_version_does_not_publish_a_preview(tmp_path):
    result, state = _publish(tmp_path, _state(tmp_path), version="1.2.3")

    assert result.returncode == 0, result.stderr
    assert not state["releases"]
    assert not state["calls"]


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v1.2.3", True),
        ("v1.2.4.dev17", False),
        ("v1.2.4rc1.dev2", False),
    ],
)
def test_stable_release_trigger_excludes_development_tags(tag, expected):
    stable = yaml.load((ROOT / ".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader)
    matches = False
    for pattern in stable["on"]["push"]["tags"]:
        if fnmatchcase(tag, pattern.removeprefix("!")):
            matches = not pattern.startswith("!")
    assert matches is expected
