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
