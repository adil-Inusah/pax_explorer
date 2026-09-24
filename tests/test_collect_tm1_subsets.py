from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scripts.collect_tm1_subsets as module


@dataclass
class Subset:
    name: str
    expression: str = ""
    elements: list[str] | None = None
    alias: str = ""


class Subsets:
    def __init__(self, data: dict[tuple[str, str], list[Subset]], failures: set[tuple[str, str, str]] | None = None) -> None:
        self.data = data
        self.failures = failures or set()

    def get_all_names(self, dimension: str, hierarchy: str, private: bool = False) -> list[str]:
        assert private is False
        return [subset.name for subset in self.data.get((dimension, hierarchy), [])]

    def get(self, name: str, dimension: str, hierarchy: str, private: bool = False) -> Subset:
        assert private is False
        if (dimension, hierarchy, name) in self.failures:
            raise RuntimeError("subset unavailable")
        return next(subset for subset in self.data[(dimension, hierarchy)] if subset.name == name)


class TM1:
    def __init__(self, subsets: Subsets) -> None:
        self.subsets = subsets


def when() -> datetime:
    return datetime(2026, 9, 24, 17, 0, tzinfo=timezone.utc)


def test_collects_public_static_and_mdx_subsets(tmp_path: Path) -> None:
    tm1 = TM1(Subsets({
        ("Material", "Material"): [
            Subset("Active", elements=["A", "B"]),
            Subset("Dynamic", expression="{TM1FILTERBYLEVEL(...) }"),
        ]
    }))
    manifest = module.collect_subsets(
        tm1,
        hierarchy_scope=[("Material", "Material")],
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    assert manifest["status"] == "COMPLETE"
    assert manifest["subset_count"] == 2
    assert manifest["relationship_count"] == 4
    assert manifest["validation_count"] == 4
    records = module.read_json(tmp_path / "current" / "subsets.json")
    assert {record["subset_kind"] for record in records} == {"STATIC", "MDX"}
    assert all(record["visibility"] == "PUBLIC" for record in records)


def test_identity_is_hierarchy_qualified(tmp_path: Path) -> None:
    tm1 = TM1(Subsets({
        ("Dim", "H1"): [Subset("Current", elements=[])],
        ("Dim", "H2"): [Subset("Current", elements=[])],
    }))
    module.collect_subsets(
        tm1,
        hierarchy_scope=[("Dim", "H1"), ("Dim", "H2")],
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    records = module.read_json(tmp_path / "current" / "subsets.json")
    assert len(records) == 2
    assert len({record["node_id"] for record in records}) == 2


def test_partial_run_preserves_current(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir(parents=True)
    module.write_json(current / "subsets.json", [{"sentinel": True}])
    tm1 = TM1(Subsets(
        {("Dim", "Dim"): [Subset("Good"), Subset("Bad")]},
        {("Dim", "Dim", "Bad")},
    ))
    manifest = module.collect_subsets(
        tm1,
        hierarchy_scope=[("Dim", "Dim")],
        snapshot_root=tmp_path / "snapshots",
        current_root=current,
        timestamp=when(),
    )
    assert manifest["status"] == "PARTIAL"
    assert module.read_json(current / "subsets.json") == [{"sentinel": True}]
    errors = module.read_json(tmp_path / "snapshots" / "20260924T170000Z" / "subset_collection_errors.json")
    assert len(errors) == 1


def test_empty_hierarchy_scope_completes(tmp_path: Path) -> None:
    manifest = module.collect_subsets(
        TM1(Subsets({})),
        hierarchy_scope=[],
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    assert manifest["status"] == "COMPLETE"
    assert manifest["subset_count"] == 0


def test_main_returns_zero(monkeypatch: Any, tmp_path: Path) -> None:
    class Connection:
        def __enter__(self) -> object:
            return object()
        def __exit__(self, *args: Any) -> None:
            return None
    catalog = tmp_path / "hierarchies.json"
    module.write_json(catalog, [])
    monkeypatch.setattr(module, "get_tm1_connection", lambda: Connection())
    monkeypatch.setattr(module, "collect_subsets", lambda *args, **kwargs: {
        "status": "COMPLETE", "snapshot_id": "x", "visibility_scope": "PUBLIC",
        "hierarchy_count": 0, "subset_count": 0, "relationship_count": 0,
        "validation_count": 0, "error_count": 0, "published_current": True,
    })
    assert module.main(["--hierarchy-catalog", str(catalog), "--quiet"]) == 0
