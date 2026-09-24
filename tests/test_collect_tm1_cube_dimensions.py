from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scripts.collect_tm1_cube_dimensions as module


@dataclass
class Cube:
    dimensions: list[str]


class Cubes:
    def __init__(
        self,
        cubes: dict[str, Cube],
        failures: set[str] | None = None,
    ) -> None:
        self.cubes = cubes
        self.failures = failures or set()

    def get_all_names(self) -> list[str]:
        return list(self.cubes)

    def get(self, cube_name: str) -> Cube:
        if cube_name in self.failures:
            raise RuntimeError("cube unavailable")
        return self.cubes[cube_name]


class TM1:
    def __init__(
        self,
        cubes: dict[str, Cube],
        failures: set[str] | None = None,
    ) -> None:
        self.cubes = Cubes(cubes, failures)


def when() -> datetime:
    return datetime(2026, 9, 24, 21, 0, tzinfo=timezone.utc)


def write_catalog(path: Path) -> None:
    module.write_json(
        path,
        [
            {"object_type": "cube", "object_name": "Sales", "is_control": False},
            {"object_type": "cube", "object_name": "}Control", "is_control": True},
            {"object_type": "dimension", "object_name": "Version"},
            {"object_type": "dimension", "object_name": "Month"},
            {"object_type": "dimension", "object_name": "Measures"},
        ],
    )


def test_collects_ordered_cube_dimensions(tmp_path: Path) -> None:
    catalog = tmp_path / "objects.json"
    write_catalog(catalog)
    tm1 = TM1({"Sales": Cube(["Version", "Month", "Measures"])})
    manifest = module.collect_cube_dimensions(
        tm1,
        cubes=["Sales"],
        object_catalog_path=catalog,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    assert manifest["status"] == "COMPLETE"
    assert manifest["relationship_count"] == 3
    relationships = module.read_json(
        tmp_path / "current" / "cube_dimension_relationships.json"
    )
    assert [item["dimension_position"] for item in relationships] == [1, 2, 3]
    assert [item["target_name"] for item in relationships] == [
        "Version",
        "Month",
        "Measures",
    ]


def test_missing_dimension_is_validated(tmp_path: Path) -> None:
    catalog = tmp_path / "objects.json"
    write_catalog(catalog)
    module.collect_cube_dimensions(
        TM1({"Sales": Cube(["Missing"])}),
        cubes=["Sales"],
        object_catalog_path=catalog,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    validations = module.read_json(
        tmp_path / "current" / "cube_dimension_relationship_validations.json"
    )
    assert validations[0]["validation_status"] == "DIMENSION_NOT_IN_CATALOG"


def test_scope_filters_regular_and_control_cubes(tmp_path: Path) -> None:
    catalog = tmp_path / "objects.json"
    write_catalog(catalog)
    tm1 = TM1(
        {
            "Sales": Cube(["Version"]),
            "}Control": Cube(["Version"]),
        }
    )
    manifest = module.collect_cube_dimensions(
        tm1,
        scope="regular",
        object_catalog_path=catalog,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    assert manifest["cube_count"] == 1
    relationships = module.read_json(
        tmp_path / "current" / "cube_dimension_relationships.json"
    )
    assert relationships[0]["source_name"] == "Sales"


def test_partial_run_preserves_current(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()
    module.write_json(
        current / "cube_dimension_relationships.json",
        [{"sentinel": True}],
    )
    tm1 = TM1(
        {"Good": Cube(["Version"]), "Bad": Cube(["Month"])},
        {"Bad"},
    )
    manifest = module.collect_cube_dimensions(
        tm1,
        cubes=["Good", "Bad"],
        object_catalog_path=None,
        snapshot_root=tmp_path / "snapshots",
        current_root=current,
        timestamp=when(),
    )
    assert manifest["status"] == "PARTIAL"
    assert module.read_json(
        current / "cube_dimension_relationships.json"
    ) == [{"sentinel": True}]


def test_duplicate_dimension_is_partial(tmp_path: Path) -> None:
    manifest = module.collect_cube_dimensions(
        TM1({"Sales": Cube(["Month", "month"])}),
        cubes=["Sales"],
        object_catalog_path=None,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    assert manifest["status"] == "PARTIAL"
    assert manifest["error_count"] == 1


def test_empty_cube_scope_completes(tmp_path: Path) -> None:
    manifest = module.collect_cube_dimensions(
        TM1({}),
        cubes=[],
        object_catalog_path=None,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    assert manifest["status"] == "COMPLETE"
    assert manifest["relationship_count"] == 0


def test_main_returns_zero(monkeypatch: Any) -> None:
    class Connection:
        def __enter__(self) -> object:
            return object()
        def __exit__(self, *args: Any) -> None:
            return None
    monkeypatch.setattr(module, "get_tm1_connection", lambda: Connection())
    monkeypatch.setattr(module, "collect_cube_dimensions", lambda *args, **kwargs: {
        "status": "COMPLETE",
        "snapshot_id": "x",
        "scope": "all",
        "cube_count": 0,
        "completed_cube_count": 0,
        "relationship_count": 0,
        "validation_count": 0,
        "error_count": 0,
        "published_current": True,
    })
    assert module.main(["--quiet"]) == 0
