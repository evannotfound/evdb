from datetime import datetime, timezone

import pytest

from evanovation_db.errors import RestoreError
from evanovation_db.files import write_json
from evanovation_db.restore.main import _safe, due


def test_restore_rejects_live_path(config):
    instance = config.get("postgres", "test-dev-01")

    with pytest.raises(RestoreError, match="overlaps live data"):
        _safe(config, instance, instance.data)


def test_due_selects_instance_without_restore(config):
    first = config.instances[0]
    path = config.host.state_dir / "state" / first.group / f"{first.id}.json"
    write_json(path, {"restore": {"time": datetime.now(timezone.utc).isoformat()}})

    selected = due(config)

    assert (selected.group, selected.id) != (first.group, first.id)
