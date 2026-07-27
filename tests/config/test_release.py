import json
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
    for name in ("check", "build"):
        assert jobs[name]["steps"][0] == {
            "uses": "actions/checkout@v7",
            "with": {"fetch-depth": "0"},
        }
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
        "src/evdb/units/*.service",
        "src/evdb/units/*.timer",
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

    assert publish["needs"] == ["build", "notes"]
    assert publish["if"] == "${{ !cancelled() && needs.build.result == 'success' }}"
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


def test_release_note_agent_uses_full_history_and_exact_ci_dependencies():
    notes = _workflow()["jobs"]["notes"]
    checkout = notes["steps"][0]
    node = next(step for step in notes["steps"] if step.get("id") == "node")
    opencode = next(step for step in notes["steps"] if step.get("id") == "opencode")

    assert notes["needs"] == "build"
    assert notes["permissions"] == {"contents": "read"}
    assert checkout == {
        "uses": "actions/checkout@v7",
        "with": {"fetch-depth": "0", "persist-credentials": "false"},
    }
    assert node["uses"] == "actions/setup-node@v7"
    assert node["with"] == {"node-version": "24"}
    assert node["continue-on-error"] == "true"
    assert node["timeout-minutes"] == "5"
    assert opencode["run"] == "npm install --global opencode-ai@1.18.6"
    assert opencode["if"] == "steps.node.outcome == 'success'"
    assert opencode["continue-on-error"] == "true"
    assert opencode["timeout-minutes"] == "5"


def test_release_note_provider_uses_environment_interpolation_and_model_limits():
    job = _workflow()["jobs"]["notes"]
    generate = next(step for step in job["steps"] if step.get("id") == "generate")
    config = json.loads(generate["env"]["OPENCODE_CONFIG_CONTENT"])
    provider = config["provider"]["openai"]
    model = provider["models"]["gpt-5.6-sol"]

    assert provider["options"] == {
        "baseURL": "{env:RELEASE_LLM_URL}",
        "apiKey": "{env:RELEASE_LLM_KEY}",
    }
    assert model["limit"] == {"context": 353000, "output": 128000}
    assert model["variants"] == {"high": {"reasoningEffort": "high"}}
    assert generate["env"]["RELEASE_LLM_URL"] == "${{ secrets.RELEASE_LLM_URL }}"
    assert generate["env"]["RELEASE_LLM_KEY"] == "${{ secrets.RELEASE_LLM_KEY }}"
    assert "GH_TOKEN" not in generate["env"]
    assert "https://ai.evanovation.com" not in WORKFLOW.read_text()


def test_release_note_failures_select_fallback_without_auto_approval():
    jobs = _workflow()["jobs"]
    notes = jobs["notes"]
    publish = jobs["publish"]
    generate = next(step for step in notes["steps"] if step.get("id") == "generate")
    upload = next(step for step in notes["steps"] if step.get("id") == "upload")
    result = next(step for step in notes["steps"] if step.get("id") == "result")
    download = next(step for step in publish["steps"] if step.get("id") == "notes-download")
    validate = next(step for step in publish["steps"] if step.get("id") == "notes")
    agent = next(step for step in publish["steps"] if "with agent notes" in step.get("name", ""))
    fallback = next(
        step for step in publish["steps"] if "with automatic notes" in step.get("name", "")
    )

    assert generate["continue-on-error"] == "true"
    assert generate["timeout-minutes"] == "10"
    assert 'test -n "${RELEASE_LLM_URL}"' in generate["run"]
    assert 'test -n "${RELEASE_LLM_KEY}"' in generate["run"]
    assert "tools/release_notes.py input" in generate["run"]
    assert '--target "${GITHUB_SHA}"' in generate["run"]
    assert "opencode run --pure --command changelog" in generate["run"]
    assert "tools/release_notes.py check" in generate["run"]
    assert "--auto" not in generate["run"]
    assert upload["if"] == "steps.generate.outcome == 'success'"
    assert upload["continue-on-error"] == "true"
    assert upload["with"]["name"] == "agent-release-notes"
    assert result["if"] == "always()"
    assert "available=false" in result["run"]
    assert download["if"] == "needs.notes.outputs.available == 'true'"
    assert download["continue-on-error"] == "true"
    assert validate["if"] == "steps.notes-download.outcome == 'success'"
    assert validate["continue-on-error"] == "true"
    verify_tag = next(
        step for step in publish["steps"] if step.get("name") == "Verify release tag is unchanged"
    )
    assert "refs/tags/${GITHUB_REF_NAME}^{commit}" in verify_tag["run"]
    assert "${GITHUB_SHA}" in verify_tag["run"]
    assert "continue-on-error" not in verify_tag
    assert agent["if"] == "steps.notes.outcome == 'success'"
    assert fallback["if"] == "steps.notes.outcome != 'success'"
    assert "--notes-file release-notes.md" in agent["run"]
    assert "--generate-notes" in fallback["run"]
    for step in (agent, fallback):
        assert 'gh release create "${GITHUB_REF_NAME}" release/*' in step["run"]
        assert "--verify-tag" in step["run"]
        assert '--title "${GITHUB_REF_NAME}"' in step["run"]


def test_release_note_job_cannot_access_publication_or_release_assets():
    jobs = _workflow()["jobs"]
    notes_text = "\n".join(str(step) for step in jobs["notes"]["steps"])
    publish_text = "\n".join(str(step) for step in jobs["publish"]["steps"])

    assert "GH_TOKEN" not in notes_text
    assert "release/*" not in notes_text
    assert "actions/download-artifact" not in notes_text
    assert "RELEASE_LLM_KEY" not in publish_text
    assert jobs["publish"]["steps"][0] == {
        "uses": "actions/checkout@v7",
        "with": {"fetch-depth": "0", "persist-credentials": "false"},
    }
