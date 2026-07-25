from evanovation_db import manifest
from evanovation_db.restore import postgres
from evanovation_db.run import Result


def test_postgres_wait_requires_stable_readiness(monkeypatch):
    codes = iter([0, 1, 0, 0])
    times = iter([0.0, 0.1, 0.2, 0.3, 0.4])
    calls = []

    def fake_exec(*args, **kwargs):
        calls.append(args)
        return Result(("pg_isready",), next(codes), "", "")

    monkeypatch.setattr(postgres.docker, "exec", fake_exec)
    monkeypatch.setattr(postgres.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(postgres.time, "sleep", lambda value: None)

    postgres._wait("restore", timeout=10)

    assert len(calls) == 4


def test_postgres_candidate_uses_only_persistent_candidate_mount(config, tmp_path, monkeypatch):
    instance = config.get("postgres", "test-dev-01")
    folder = tmp_path / "backup"
    folder.mkdir()
    (folder / "globals.sql").write_text("-- globals\n")
    (folder / "databases").mkdir()
    manifest.write(
        folder,
        {
            "status": "complete",
            "facts": {"owners": {}, "objects": {}},
            "files": manifest.files(folder, ["globals.sql"]),
        },
    )
    candidate = tmp_path / "candidate"
    started = {}
    monkeypatch.setattr(postgres, "_wait", lambda name: None)
    monkeypatch.setattr(postgres.docker, "copy", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        postgres.docker,
        "exec",
        lambda *args, **kwargs: Result(("docker",), 0, "", ""),
    )

    def start(image, name, *args, **kwargs):
        started.update({"image": image, "name": name, **kwargs})
        return name

    monkeypatch.setattr(postgres.docker, "start", start)

    postgres.restore(config.host, instance, folder, "candidate-container", candidate)

    assert started["image"] == instance.image
    assert started["network"] == "none"
    assert started["mounts"] == [(candidate, "/var/lib/postgresql/data", False)]
    assert instance.data not in {source for source, _, _ in started["mounts"]}
    assert started["secrets"]
