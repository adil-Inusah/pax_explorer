from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scripts.collect_tm1_views as module


@dataclass
class NativeView:
    name: str
    suppress_empty_rows: bool = False
    suppress_empty_columns: bool = False
    format_string: str = "0.00"


@dataclass
class MDXView:
    name: str
    mdx: str = "SELECT ..."


class Views:
    def __init__(self, data: dict[str, list[Any]], failures: set[str] | None = None) -> None:
        self.data = data
        self.failures = failures or set()

    def get_all_names(self, cube_name: str, private: bool = False) -> list[str]:
        assert private is False
        return [view.name for view in self.data.get(cube_name, [])]

    def get(self, cube_name: str, view_name: str, private: bool = False) -> Any:
        assert private is False
        if cube_name in self.failures:
            raise RuntimeError("view unavailable")
        return next(view for view in self.data[cube_name] if view.name == view_name)


class Cubes:
    def __init__(self, names: list[str]) -> None:
        self.names = names

    def get_all_names(self) -> list[str]:
        return list(self.names)


class TM1:
    def __init__(self, data: dict[str, list[Any]], failures: set[str] | None = None) -> None:
        self.cubes = Cubes(list(data))
        self.views = Views(data, failures)


def when() -> datetime:
    return datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc)


def test_collects_native_and_mdx_views(tmp_path: Path) -> None:
    tm1 = TM1({"Cube": [NativeView("Report"), MDXView("MDX Report")]})
    manifest = module.collect_views(
        tm1,
        cubes=["Cube"],
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    assert manifest["status"] == "COMPLETE"
    assert manifest["view_count"] == 2
    assert manifest["relationship_count"] == 2
    assert manifest["validation_count"] == 2
    records = module.read_json(tmp_path / "current" / "views.json")
    assert {record["view_kind"] for record in records} == {"NATIVE", "MDX"}
    assert all(record["visibility"] == "PUBLIC" for record in records)


def test_identity_is_cube_qualified(tmp_path: Path) -> None:
    tm1 = TM1({"Cube A": [NativeView("Default")], "Cube B": [NativeView("Default")]})
    module.collect_views(
        tm1,
        cubes=["Cube A", "Cube B"],
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    records = module.read_json(tmp_path / "current" / "views.json")
    assert len({record["node_id"] for record in records}) == 2


def test_partial_run_preserves_current(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir(parents=True)
    module.write_json(current / "views.json", [{"sentinel": True}])
    tm1 = TM1({"Good": [NativeView("A")], "Bad": [NativeView("B")]}, {"Bad"})
    manifest = module.collect_views(
        tm1,
        cubes=["Good", "Bad"],
        snapshot_root=tmp_path / "snapshots",
        current_root=current,
        timestamp=when(),
    )
    assert manifest["status"] == "PARTIAL"
    assert module.read_json(current / "views.json") == [{"sentinel": True}]


def test_empty_cube_scope_completes(tmp_path: Path) -> None:
    manifest = module.collect_views(
        TM1({}),
        cubes=[],
        object_catalog_path=None,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    assert manifest["status"] == "COMPLETE"
    assert manifest["view_count"] == 0


def test_main_returns_zero(monkeypatch: Any) -> None:
    class Connection:
        def __enter__(self) -> object:
            return object()
        def __exit__(self, *args: Any) -> None:
            return None
    monkeypatch.setattr(module, "get_tm1_connection", lambda: Connection())
    monkeypatch.setattr(module, "collect_views", lambda *args, **kwargs: {
        "status": "COMPLETE", "snapshot_id": "x", "visibility_scope": "PUBLIC",
        "cube_count": 0, "view_count": 0, "relationship_count": 0,
        "validation_count": 0, "error_count": 0, "published_current": True,
    })
    assert module.main(["--quiet"]) == 0
