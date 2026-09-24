from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scripts.collect_tm1_chore_tasks as module


@dataclass
class Task:
    step: int
    process_name: str
    parameters: list[dict[str, str]]


@dataclass
class Chore:
    name: str
    active: bool
    tasks: list[Task]


class Chores:
    def __init__(self, chores: list[Chore]) -> None:
        self.chores = chores

    def get_all(self) -> list[Chore]:
        return list(self.chores)


class TM1:
    def __init__(self, chores: list[Chore]) -> None:
        self.chores = Chores(chores)


def when() -> datetime:
    return datetime(2026, 9, 24, 19, 0, tzinfo=timezone.utc)


def test_collects_tasks_calls_and_bindings(tmp_path: Path) -> None:
    tm1 = TM1([
        Chore("Nightly", True, [
            Task(2, "Load B", [{"Name": "pYear", "Value": "2026"}]),
            Task(1, "Load A", []),
        ])
    ])
    manifest = module.collect_chore_tasks(
        tm1,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    assert manifest["status"] == "COMPLETE"
    assert manifest["task_count"] == 2
    assert manifest["binding_count"] == 1
    assert manifest["relationship_count"] == 5
    assert manifest["validation_count"] == 5
    tasks = module.read_json(tmp_path / "current" / "chore_tasks.json")
    assert [task["task_step"] for task in tasks] == [1, 2]


def test_missing_process_is_validated(tmp_path: Path) -> None:
    objects = tmp_path / "objects.json"
    module.write_json(objects, [])
    tm1 = TM1([Chore("C", True, [Task(1, "Missing", [])])])
    module.collect_chore_tasks(
        tm1,
        object_catalog_path=objects,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    validations = module.read_json(tmp_path / "current" / "chore_relationship_validations.json")
    assert any(item["validation_status"] == "PROCESS_NOT_IN_CATALOG" for item in validations)


def test_unknown_parameter_is_validated(tmp_path: Path) -> None:
    parameters = tmp_path / "parameters.json"
    module.write_json(parameters, [{"process_name": "P", "parameter_name": "Known"}])
    tm1 = TM1([Chore("C", True, [Task(1, "P", [{"Name": "Unknown", "Value": "x"}])])])
    module.collect_chore_tasks(
        tm1,
        process_parameters_path=parameters,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    validations = module.read_json(tmp_path / "current" / "chore_relationship_validations.json")
    assert any(item["validation_status"] == "UNKNOWN_PROCESS_PARAMETER" for item in validations)


def test_duplicate_task_identity_is_partial_and_preserves_current(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir(parents=True)
    module.write_json(current / "chore_tasks.json", [{"sentinel": True}])
    tm1 = TM1([Chore("C", True, [Task(1, "P", []), Task(1, "P", [])])])
    manifest = module.collect_chore_tasks(
        tm1,
        snapshot_root=tmp_path / "snapshots",
        current_root=current,
        timestamp=when(),
    )
    assert manifest["status"] == "PARTIAL"
    assert module.read_json(current / "chore_tasks.json") == [{"sentinel": True}]


def test_empty_chore_inventory_completes(tmp_path: Path) -> None:
    manifest = module.collect_chore_tasks(
        TM1([]),
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    assert manifest["status"] == "COMPLETE"
    assert manifest["task_count"] == 0


def test_main_returns_zero(monkeypatch: Any) -> None:
    class Connection:
        def __enter__(self) -> object:
            return object()
        def __exit__(self, *args: Any) -> None:
            return None
    monkeypatch.setattr(module, "get_tm1_connection", lambda: Connection())
    monkeypatch.setattr(module, "collect_chore_tasks", lambda *args, **kwargs: {
        "status": "COMPLETE", "snapshot_id": "x", "chore_count": 0,
        "task_count": 0, "binding_count": 0, "relationship_count": 0,
        "validation_count": 0, "error_count": 0, "published_current": True,
    })
    assert module.main([]) == 0
