from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from scripts import collect_tm1_rule_lineage as collector


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class FakeCubeService:
    def __init__(self, cubes: Mapping[str, object], failures: set[str] | None = None):
        self.cubes = cubes
        self.failures = failures or set()

    def get_all_names(self) -> list[str]:
        # Deliberately return insertion order so the collector's sorting is tested.
        return list(self.cubes)

    def get(self, cube_name: str) -> object:
        if cube_name in self.failures:
            raise RuntimeError(f"Unable to retrieve {cube_name}")
        return self.cubes[cube_name]


class FakeTM1:
    def __init__(
        self,
        cubes: Mapping[str, object],
        failures: set[str] | None = None,
    ) -> None:
        self.cubes = FakeCubeService(
            cubes,
            failures,
        )


@contextmanager
def fake_connection(
    cubes: Mapping[str, object],
    failures: set[str] | None = None,
):
    yield FakeTM1(
        cubes,
        failures,
    )

@pytest.fixture
def isolated_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    current_root = tmp_path / "current"
    snapshot_root = tmp_path / "snapshots"
    current_root.mkdir()
    snapshot_root.mkdir()

    write_json(
        current_root / "objects.json",
        [
            {"object_type": "cube", "object_name": "Source Cube"},
            {"object_type": "cube", "object_name": "Target Cube"},
            {"object_type": "dimension", "object_name": "Cost Centre"},
        ],
    )

    monkeypatch.setattr(collector, "CURRENT_ROOT", current_root)
    monkeypatch.setattr(collector, "SNAPSHOT_ROOT", snapshot_root)
    return current_root, snapshot_root


@pytest.mark.parametrize(
    ("property_name", "property_value", "expected"),
    [
        ("rules", "DB('Source Cube', !Period);", "DB('Source Cube', !Period);"),
        ("rule", "ATTRS('Cost Centre', !CostCentre, 'Region');", "ATTRS('Cost Centre', !CostCentre, 'Region');"),
        ("Rules", "DIMSIZ('Cost Centre');", "DIMSIZ('Cost Centre');"),
        ("Rule", "DIMIX('Cost Centre', !CostCentre);", "DIMIX('Cost Centre', !CostCentre);"),
        ("rule_text", "ELLEV('Cost Centre', !CostCentre);", "ELLEV('Cost Centre', !CostCentre);"),
        ("RuleText", "ELPAR('Cost Centre', !CostCentre, 1);", "ELPAR('Cost Centre', !CostCentre, 1);"),
    ],
)
def test_get_cube_rule_text_supports_property_variants(
    property_name: str,
    property_value: str,
    expected: str,
) -> None:
    cube = SimpleNamespace(**{property_name: property_value})
    assert collector.get_cube_rule_text(cube) == expected


@pytest.mark.parametrize("nested_property", ["text", "Text", "rules", "Rules"])
def test_get_cube_rule_text_supports_wrapped_rule_objects(nested_property: str) -> None:
    wrapped = SimpleNamespace(**{nested_property: "DB('Source Cube', !Period);"})
    cube = SimpleNamespace(rules=wrapped)
    assert collector.get_cube_rule_text(cube) == "DB('Source Cube', !Period);"


@pytest.mark.parametrize("rule_value", [None, "", "   "])
def test_get_cube_rule_text_handles_empty_rules(rule_value: str | None) -> None:
    cube = SimpleNamespace(rules=rule_value)
    assert collector.get_cube_rule_text(cube) == (rule_value or "")


def test_get_cube_rule_text_returns_empty_when_property_is_missing() -> None:
    assert collector.get_cube_rule_text(SimpleNamespace()) == ""


def test_get_cube_rule_text_rejects_unsupported_rule_type() -> None:
    cube = SimpleNamespace(rules=object())
    with pytest.raises(TypeError, match="Unsupported rule value type"):
        collector.get_cube_rule_text(cube)


def test_collect_rule_lineage_creates_complete_snapshot_and_current_outputs(
    isolated_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_root, snapshot_root = isolated_roots
    cubes = {
        "Z Unruled": SimpleNamespace(rules=""),
        "A Reporting": SimpleNamespace(
            rules="""
['Amount'] = N: DB('Source Cube', !Period);
['Driver'] => DB('Target Cube', !Period);
['Region'] = S: ATTRS('Cost Centre', !CostCentre, 'Region');
"""
        ),
    }
    monkeypatch.setattr(
        collector,
        "get_tm1_connection",
        lambda: fake_connection(cubes),
    )

    manifest = collector.collect_rule_lineage()

    assert manifest["status"] == "COMPLETE"
    assert manifest["cube_count"] == 2
    assert manifest["cube_with_rules_count"] == 1
    assert manifest["evidence_count"] == 3
    assert manifest["relationship_count"] == 3
    assert manifest["validation_count"] == 3
    assert manifest["error_count"] == 0

    snapshot_directory = snapshot_root / manifest["snapshot_id"] / "rule_lineage"
    expected_snapshot_files = {
        "cube_rule_definitions.json",
        "relationship_evidence.json",
        "relationships.json",
        "relationship_validations.json",
        "errors.json",
        "manifest.json",
    }
    assert {path.name for path in snapshot_directory.iterdir()} == expected_snapshot_files

    expected_current_files = {
        "objects.json",
        "cube_rule_definitions.json",
        "rule_relationship_evidence.json",
        "rule_relationships.json",
        "rule_relationship_validations.json",
        "rule_lineage_manifest.json",
    }
    assert {path.name for path in current_root.iterdir()} == expected_current_files

    definitions = read_json(current_root / "cube_rule_definitions.json")
    assert [record["cube_name"] for record in definitions] == ["A Reporting", "Z Unruled"]
    assert definitions[0]["has_rules"] is True
    assert definitions[1]["has_rules"] is False

    evidence = read_json(current_root / "rule_relationship_evidence.json")
    assert {(item["relationship_type"], item["target_name"]) for item in evidence} == {
        ("READS_FROM", "Source Cube"),
        ("FEEDS", "Target Cube"),
        ("USES_ATTRIBUTE", "Cost Centre"),
    }


def test_collect_rule_lineage_handles_empty_cube_list(
    isolated_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _ = isolated_roots
    monkeypatch.setattr(
        collector,
        "get_tm1_connection",
        lambda: fake_connection({}),
    )

    manifest = collector.collect_rule_lineage()

    assert manifest["status"] == "COMPLETE"
    assert manifest["cube_count"] == 0
    assert manifest["cube_with_rules_count"] == 0
    assert manifest["evidence_count"] == 0
    assert manifest["relationship_count"] == 0
    assert manifest["validation_count"] == 0
    assert manifest["error_count"] == 0


def test_partial_collection_writes_snapshot_but_preserves_current_outputs(
    isolated_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_root, snapshot_root = isolated_roots
    marker_payload = [{"marker": "known-good-current-data"}]
    write_json(current_root / "rule_relationships.json", marker_payload)
    write_json(current_root / "rule_lineage_manifest.json", {"status": "COMPLETE", "marker": True})

    cubes = {
        "Good Cube": SimpleNamespace(rules="DB('Source Cube', !Period);"),
        "Broken Cube": SimpleNamespace(rules="DB('Target Cube', !Period);"),
    }
    monkeypatch.setattr(
        collector,
        "get_tm1_connection",
        lambda: fake_connection(cubes, failures={"Broken Cube"}),
    )

    manifest = collector.collect_rule_lineage()

    assert manifest["status"] == "PARTIAL"
    assert manifest["cube_count"] == 1
    assert manifest["cube_with_rules_count"] == 1
    assert manifest["error_count"] == 1
    assert read_json(current_root / "rule_relationships.json") == marker_payload
    assert read_json(current_root / "rule_lineage_manifest.json") == {
        "status": "COMPLETE",
        "marker": True,
    }

    snapshot_directory = snapshot_root / manifest["snapshot_id"] / "rule_lineage"
    errors = read_json(snapshot_directory / "errors.json")
    assert errors == [
        {
            "cube_name": "Broken Cube",
            "error": "RuntimeError: Unable to retrieve Broken Cube",
        }
    ]
    assert (snapshot_directory / "relationships.json").exists()
    assert (snapshot_directory / "manifest.json").exists()


def test_collect_rule_lineage_requires_current_object_catalog(
    isolated_roots: tuple[Path, Path],
) -> None:
    current_root, _ = isolated_roots
    (current_root / "objects.json").unlink()

    with pytest.raises(FileNotFoundError, match="collect_tm1_metadata.py"):
        collector.collect_rule_lineage()


def test_rule_collector_does_not_overwrite_ti_outputs(
    isolated_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_root, _ = isolated_roots
    ti_payload = [{"source": "TI marker"}]
    write_json(current_root / "ti_relationships.json", ti_payload)

    monkeypatch.setattr(
        collector,
        "get_tm1_connection",
        lambda: fake_connection(
            {"Reporting": SimpleNamespace(rules="DB('Source Cube', !Period);")}
        ),
    )

    manifest = collector.collect_rule_lineage()

    assert manifest["status"] == "COMPLETE"
    assert read_json(current_root / "ti_relationships.json") == ti_payload
    assert (current_root / "rule_relationships.json").exists()


def test_main_returns_zero_for_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        collector,
        "collect_rule_lineage",
        lambda: {
            "snapshot_id": "20260922T000000Z",
            "status": "COMPLETE",
            "cube_count": 1,
            "cube_with_rules_count": 1,
            "evidence_count": 1,
            "relationship_count": 1,
            "validation_count": 1,
            "error_count": 0,
        },
    )
    assert collector.main() == 0


def test_main_returns_one_for_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        collector,
        "collect_rule_lineage",
        lambda: {
            "snapshot_id": "20260922T000000Z",
            "status": "PARTIAL",
            "cube_count": 1,
            "cube_with_rules_count": 1,
            "evidence_count": 1,
            "relationship_count": 1,
            "validation_count": 1,
            "error_count": 1,
        },
    )
    assert collector.main() == 1


def test_main_returns_one_when_collection_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_error() -> dict[str, object]:
        raise RuntimeError("connection failed")

    monkeypatch.setattr(collector, "collect_rule_lineage", raise_error)
    assert collector.main() == 1
