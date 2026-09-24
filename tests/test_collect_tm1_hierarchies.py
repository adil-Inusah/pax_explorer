from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scripts.collect_tm1_hierarchies as module


class Dimensions:
    def __init__(self, names: list[str]) -> None:
        self.names = names

    def get_all_names(self) -> list[str]:
        return list(self.names)


class Hierarchies:
    def __init__(
        self,
        names: dict[str, list[str]],
        failures: set[str] | None = None,
    ) -> None:
        self.names = names
        self.failures = failures or set()

    def get_all_names(self, dimension_name: str) -> list[str]:
        if dimension_name in self.failures:
            raise RuntimeError("hierarchy list unavailable")
        return list(self.names.get(dimension_name, []))


class TM1:
    def __init__(
        self,
        dimensions: list[str],
        hierarchies: dict[str, list[str]],
        failures: set[str] | None = None,
    ) -> None:
        self.dimensions = Dimensions(dimensions)
        self.hierarchies = Hierarchies(hierarchies, failures)


def fixed_time() -> datetime:
    return datetime(2026, 9, 24, 16, 0, tzinfo=timezone.utc)


def test_collects_hierarchies_and_relationships(tmp_path: Path) -> None:
    tm1 = TM1(
        ["Material", "ML Document"],
        {
            "Material": ["Material"],
            "ML Document": ["Company", "ML Document", "company"],
        },
    )
    manifest = module.collect_hierarchies(
        tm1,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=fixed_time(),
    )
    assert manifest["status"] == "COMPLETE"
    assert manifest["dimension_count"] == 2
    assert manifest["hierarchy_count"] == 3
    assert manifest["relationship_count"] == 3
    assert manifest["validation_count"] == 3

    records = module.read_json(tmp_path / "current" / "hierarchies.json")
    assert [record["qualified_name"] for record in records] == [
        "Material::Material",
        "ML Document::Company",
        "ML Document::ML Document",
    ]
    relationships = module.read_json(
        tmp_path / "current" / "hierarchy_relationships.json"
    )
    assert all(
        item["relationship_type"] == "BELONGS_TO_DIMENSION"
        for item in relationships
    )


def test_marks_control_and_default_hierarchy(tmp_path: Path) -> None:
    tm1 = TM1(["}Clients"], {"}Clients": ["}Clients"]})
    module.collect_hierarchies(
        tm1,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=fixed_time(),
    )
    records = module.read_json(tmp_path / "current" / "hierarchies.json")
    assert len(records) == 1
    assert records[0]["is_control"] is True
    assert records[0]["is_default_hierarchy"] is True


def test_partial_run_preserves_current(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir(parents=True)
    module.write_json(current / "hierarchies.json", [{"sentinel": True}])
    tm1 = TM1(
        ["Good", "Bad"],
        {"Good": ["Good"], "Bad": ["Bad"]},
        {"Bad"},
    )
    manifest = module.collect_hierarchies(
        tm1,
        snapshot_root=tmp_path / "snapshots",
        current_root=current,
        timestamp=fixed_time(),
    )
    assert manifest["status"] == "PARTIAL"
    assert module.read_json(current / "hierarchies.json") == [
        {"sentinel": True}
    ]
    snapshot = tmp_path / "snapshots" / "20260924T160000Z"
    assert (snapshot / "hierarchies.json").is_file()
    errors = module.read_json(snapshot / "hierarchy_collection_errors.json")
    assert len(errors) == 1


def test_empty_inventory_completes(tmp_path: Path) -> None:
    manifest = module.collect_hierarchies(
        TM1([], {}),
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=fixed_time(),
    )
    assert manifest["status"] == "COMPLETE"
    assert manifest["dimension_count"] == 0
    assert manifest["hierarchy_count"] == 0


def test_main_default_contract(monkeypatch: Any) -> None:
    class Connection:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr(module, "get_tm1_connection", lambda: Connection())
    monkeypatch.setattr(
        module,
        "collect_hierarchies",
        lambda *args, **kwargs: {
            "status": "COMPLETE",
            "snapshot_id": "x",
            "scope": "all",
            "dimension_count": 0,
            "hierarchy_count": 0,
            "relationship_count": 0,
            "validation_count": 0,
            "error_count": 0,
            "published_current": True,
        },
    )
    assert module.main([]) == 0
