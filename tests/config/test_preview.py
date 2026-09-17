import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = yaml.load((ROOT / ".github/workflows/preview.yml").read_text(), Loader=yaml.BaseLoader)
PUBLISH = WORKFLOW["jobs"]["publish"]["steps"][-1]["run"]
COMMIT = "b" * 40
PREVIOUS = "a" * 40
VERSION = "1.2.4.dev17+gabc1234"

# Execute the real publication shell against a disposable release, with no GitHub writes.
FAKE_GH = """\
import json, os, pathlib, re, sys
path = pathlib.Path(os.environ['RELEASE_STATE'])
state = json.loads(path.read_text())
args = sys.argv[1:]
state['calls'].append(args)
code = 0
def option(name):
    return args[args.index(name) + 1]
if args[:2] == ['release', 'view']:
    if not state['exists']:
        code = 1
    elif option('--json') == 'name,body':
        marker = re.search(r'<!-- preview-run: ([0-9]+) -->', state['body'])
        print(marker.group(1) if marker else state['title'].removeprefix('Preview run '))
    else:
        print('\\n'.join(state['assets']))
elif args[:2] == ['release', 'create']:
    state.update(exists=True, title=option('--title'), body=option('--notes'), draft=True)
elif args[:2] == ['release', 'edit']:
    if '--title' in args:
        state['title'] = option('--title')
    if '--notes-file' in args:
        state['body'] = pathlib.Path(option('--notes-file')).read_text()
    if '--draft=false' in args:
        state['draft'] = False
elif args[:2] == ['release', 'download']:
    folder = pathlib.Path(option('--dir'))
    folder.mkdir()
    (folder / 'preview.txt').write_text(state['assets']['preview.txt'])
elif args[:2] == ['release', 'upload']:
    for value in args[3:]:
        if value.startswith('--'):
            continue
        asset = pathlib.Path(value)
        if asset.name in state['assets'] and '--clobber' not in args:
            code = 1
            break
        if '--clobber' in args:
            state['assets'].pop(asset.name, None)
        if asset.name == os.environ.get('FAIL_UPLOAD'):
            code = 1
            break
        state['assets'][asset.name] = asset.read_text()
elif args[:2] == ['release', 'delete-asset']:
    del state['assets'][args[3]]
elif args[0] == 'api':
    # First publication must publish its draft before updating the newly created tag.
    assert not state['draft']
    state['tag'] = option('-f').removeprefix('sha=')
else:
    raise AssertionError(args)
path.write_text(json.dumps(state))
sys.exit(code)
"""


def _state(tmp_path, *, exists=True, title="Preview run 16", body=""):
    old = f"evdb_linux_amd64_{PREVIOUS}_16_1"
    ancient = f"evdb_linux_amd64_{'c' * 40}_15_1"
    assets = {
        "preview.txt": f"{PREVIOUS} 1.2.4.dev16+gdef1234 16 1\n",
        "install.sh": "old installer",
        old: "previous executable",
        old + ".sha256": "previous checksum",
        ancient: "ancient executable",
        ancient + ".sha256": "ancient checksum",
    }
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "exists": exists,
                "title": title,
                "body": body,
                "draft": False,
                "assets": assets if exists else {},
                "calls": [],
            }
        )
    )
    return path


def _publish(tmp_path, state, *, run=17, attempt=1, fail_upload=""):
    work = tmp_path / f"run-{run}-{attempt}"
    work.mkdir()
    binary_dir = work / "bin"
    binary_dir.mkdir()
    gh = binary_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n" + FAKE_GH)
    gh.chmod(0o755)
    release = work / "release"
    release.mkdir()
    for arch in ["amd64", "arm64"]:
        asset = release / f"evdb_linux_{arch}_{COMMIT}_{run}_{attempt}"
        asset.write_text("new executable")
        asset.with_name(asset.name + ".sha256").write_text("new checksum")
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
            "VERSION": VERSION,
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


def test_publication_switches_manifest_after_assets_and_keeps_previous_build(tmp_path):
    state = _state(tmp_path)

    result, published = _publish(tmp_path, state)

    assert result.returncode == 0, result.stderr
    assert published["title"] == "v1.2.4.dev17"
    assert f"Latest successful main build: {VERSION}" in published["body"]
    assert "<!-- preview-run: 17 -->" in published["body"]
    assert published["tag"] == COMMIT
    assert published["assets"]["preview.txt"] == f"{COMMIT} {VERSION} 17 1\n"
    assert f"evdb_linux_amd64_{PREVIOUS}_16_1" in published["assets"]
    assert not any("c" * 40 in name for name in published["assets"])
    uploads = [call for call in published["calls"] if call[:2] == ["release", "upload"]]
    assert len([arg for arg in uploads[0] if arg.startswith("release/evdb_linux_")]) == 4
    assert "--clobber" not in uploads[0]
    assert uploads[-1][3] == "release/preview.txt"


def test_first_publication_uses_one_draft_prerelease(tmp_path):
    result, published = _publish(tmp_path, _state(tmp_path, exists=False))

    assert result.returncode == 0, result.stderr
    creates = [call for call in published["calls"] if call[:2] == ["release", "create"]]
    assert len(creates) == 1
    assert "--prerelease" in creates[0] and "--latest=false" in creates[0]
    assert published["title"] == "v1.2.4.dev17"
    assert not published["draft"]


def test_older_workflow_cannot_modify_newer_preview(tmp_path):
    state = _state(tmp_path, title="v1.2.4.dev18", body="<!-- preview-run: 18 -->")
    before = json.loads(state.read_text())

    result, after = _publish(tmp_path, state)

    assert result.returncode == 0, result.stderr
    assert after["assets"] == before["assets"]
    assert after["title"] == before["title"]
    assert len(after["calls"]) == 1


def test_interrupted_manifest_switch_blocks_older_run_and_allows_retry(tmp_path):
    state = _state(tmp_path)
    failed, partial = _publish(tmp_path, state, fail_upload="preview.txt")
    assert failed.returncode != 0
    assert partial["title"] == "v1.2.4.dev17"
    assert "<!-- preview-run: 17 -->" in partial["body"]
    assert "preview.txt" not in partial["assets"]

    skipped, older = _publish(tmp_path, state, run=16)
    assert skipped.returncode == 0, skipped.stderr
    assert older["assets"] == partial["assets"]
    retried, published = _publish(tmp_path, state, attempt=2)
    assert retried.returncode == 0, retried.stderr
    assert published["assets"]["preview.txt"] == f"{COMMIT} {VERSION} 17 2\n"


def test_rerun_keeps_previous_attempt_assets_immutable(tmp_path):
    state = _state(tmp_path)
    first, before = _publish(tmp_path, state)
    assert first.returncode == 0, first.stderr

    retried, after = _publish(tmp_path, state, attempt=2)

    assert retried.returncode == 0, retried.stderr
    name = f"evdb_linux_amd64_{COMMIT}_17_1"
    assert after["assets"][name] == before["assets"][name]
    assert after["assets"]["preview.txt"] == f"{COMMIT} {VERSION} 17 2\n"
