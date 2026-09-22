from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Mapping

import pytest

from scripts import collect_tm1_ti_lineage as collector


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class FakeProcessService:
    def __init__(
        self,
        processes: Mapping[str, object],
        failures: set[str] | None = None,
    ) -> None:
        self.processes = processes
        self.failures = failures or set()

    def get_all_names(self) -> list[str]:
        # Preserve insertion order so collector sorting is tested.
        return list(self.processes)

    def get(self, process_name: str) -> object:
        if process_name in self.failures:
            raise RuntimeError(f"Unable to retrieve {process_name}")
        return self.processes[process_name]


class FakeTM1:
    def __init__(
        self,
        processes: Mapping[str, object],
        failures: set[str] | None = None,
    ) -> None:
        self.processes = FakeProcessService(processes, failures)


@contextmanager
def fake_connection(
    processes: Mapping[str, object],
    failures: set[str] | None = None,
) -> Iterator[FakeTM1]:
    yield FakeTM1(processes, failures)


@pytest.fixture
def isolated_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    current_root = tmp_path / "current"
    snapshot_root = tmp_path / "snapshots"
    current_root.mkdir()
    snapshot_root.mkdir()

    write_json(
        current_root / "objects.json",
        [
            {"object_type": "process", "object_name": "Child Process"},
            {"object_type": "cube", "object_name": "Source Cube"},
            {"object_type": "cube", "object_name": "Target Cube"},
            {"object_type": "dimension", "object_name": "Cost Centre"},
        ],
    )

    monkeypatch.setattr(collector, "CURRENT_ROOT", current_root)
    monkeypatch.setattr(collector, "SNAPSHOT_ROOT", snapshot_root)
    return current_root, snapshot_root


def test_get_process_procedures_supports_snake_case_properties() -> None:
    process = SimpleNamespace(
        prolog_procedure="Prolog text",
        metadata_procedure="Metadata text",
        data_procedure="Data text",
        epilog_procedure="Epilog text",
    )

    assert collector.get_process_procedures(process) == {
        "Prolog": "Prolog text",
        "Metadata": "Metadata text",
        "Data": "Data text",
        "Epilog": "Epilog text",
    }


def test_get_process_procedures_supports_pascal_case_properties() -> None:
    process = SimpleNamespace(
        PrologProcedure="Prolog text",
        MetadataProcedure="Metadata text",
        DataProcedure="Data text",
        EpilogProcedure="Epilog text",
    )

    assert collector.get_process_procedures(process) == {
        "Prolog": "Prolog text",
        "Metadata": "Metadata text",
        "Data": "Data text",
        "Epilog": "Epilog text",
    }


def test_get_process_procedures_supports_mixed_property_styles() -> None:
    process = SimpleNamespace(
        prolog_procedure="Prolog text",
        MetadataProcedure="Metadata text",
        data_procedure="Data text",
        EpilogProcedure="Epilog text",
    )

    assert collector.get_process_procedures(process) == {
        "Prolog": "Prolog text",
        "Metadata": "Metadata text",
        "Data": "Data text",
        "Epilog": "Epilog text",
    }


def test_get_process_procedures_returns_empty_strings_for_missing_properties() -> None:
    assert collector.get_process_procedures(SimpleNamespace()) == {
        "Prolog": "",
        "Metadata": "",
        "Data": "",
        "Epilog": "",
    }


def test_get_process_procedures_normalizes_none_to_empty_string() -> None:
    process = SimpleNamespace(
        prolog_procedure=None,
        metadata_procedure=None,
        data_procedure=None,
        epilog_procedure=None,
    )

    assert collector.get_process_procedures(process) == {
        "Prolog": "",
        "Metadata": "",
        "Data": "",
        "Epilog": "",
    }


def test_get_process_procedures_prefers_snake_case_when_both_exist() -> None:
    process = SimpleNamespace(
        prolog_procedure="snake case",
        PrologProcedure="pascal case",
    )

    procedures = collector.get_process_procedures(process)
    assert procedures["Prolog"] == "snake case"


def test_collect_ti_lineage_creates_complete_snapshot_and_current_outputs(
    isolated_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_root, snapshot_root = isolated_roots
    processes = {
        "Z Empty": SimpleNamespace(
            prolog_procedure="",
            metadata_procedure="",
            data_procedure="",
            epilog_procedure="",
        ),
        "A Load": SimpleNamespace(
            prolog_procedure=(
                "sCube = 'Target Cube';\n"
                "CubeClearData(sCube);\n"
                "ExecuteProcess('Child Process');"
            ),
            metadata_procedure="",
            data_procedure="CellPutN(1, 'Target Cube', 'Actual');",
            epilog_procedure="",
        ),
    }
    monkeypatch.setattr(
        collector,
        "get_tm1_connection",
        lambda: fake_connection(processes),
    )

    manifest = collector.collect_ti_lineage()

    assert manifest["status"] == "COMPLETE"
    assert manifest["process_count"] == 2
    assert manifest["evidence_count"] >= 3
    assert manifest["relationship_count"] >= 3
    assert manifest["error_count"] == 0

    snapshot_directory = snapshot_root / manifest["snapshot_id"] / "ti_lineage"
    expected_snapshot_files = {
        "process_definitions.json",
        "relationship_evidence.json",
        "relationships.json",
        "relationship_validations.json",
        "errors.json",
        "manifest.json",
    }
    assert {path.name for path in snapshot_directory.iterdir()} == expected_snapshot_files

    expected_current_files = {
        "objects.json",
        "ti_process_definitions.json",
        "ti_relationship_evidence.json",
        "ti_relationships.json",
        "ti_relationship_validations.json",
        "ti_lineage_manifest.json",
    }
    assert {path.name for path in current_root.iterdir()} == expected_current_files

    definitions = read_json(current_root / "ti_process_definitions.json")
    assert [record["process_name"] for record in definitions] == ["A Load", "Z Empty"]
    assert definitions[0]["procedures"]["Prolog"].startswith("sCube")
    assert definitions[1]["procedures"] == {
        "Prolog": "",
        "Metadata": "",
        "Data": "",
        "Epilog": "",
    }

    evidence = read_json(current_root / "ti_relationship_evidence.json")
    source_processes = {item["process_name"] for item in evidence}
    assert source_processes == {"A Load"}


def test_collect_ti_lineage_handles_empty_process_list(
    isolated_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _ = isolated_roots
    monkeypatch.setattr(
        collector,
        "get_tm1_connection",
        lambda: fake_connection({}),
    )

    manifest = collector.collect_ti_lineage()

    assert manifest["status"] == "COMPLETE"
    assert manifest["process_count"] == 0
    assert manifest["evidence_count"] == 0
    assert manifest["relationship_count"] == 0
    assert manifest["error_count"] == 0


def test_partial_collection_writes_snapshot_but_preserves_current_outputs(
    isolated_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_root, snapshot_root = isolated_roots
    marker_relationships = [{"marker": "known-good-ti-data"}]
    marker_manifest = {"status": "COMPLETE", "marker": True}
    write_json(current_root / "ti_relationships.json", marker_relationships)
    write_json(current_root / "ti_lineage_manifest.json", marker_manifest)

    processes = {
        "Good Process": SimpleNamespace(
            prolog_procedure="ExecuteProcess('Child Process');",
        ),
        "Broken Process": SimpleNamespace(
            prolog_procedure="CellPutN(1, 'Target Cube', 'Actual');",
        ),
    }
    monkeypatch.setattr(
        collector,
        "get_tm1_connection",
        lambda: fake_connection(processes, failures={"Broken Process"}),
    )

    manifest = collector.collect_ti_lineage()

    assert manifest["status"] == "PARTIAL"
    assert manifest["process_count"] == 1
    assert manifest["evidence_count"] >= 1
    assert manifest["error_count"] == 1
    assert read_json(current_root / "ti_relationships.json") == marker_relationships
    assert read_json(current_root / "ti_lineage_manifest.json") == marker_manifest

    snapshot_directory = snapshot_root / manifest["snapshot_id"] / "ti_lineage"
    errors = read_json(snapshot_directory / "errors.json")
    assert errors == [
        {
            "process_name": "Broken Process",
            "error": "RuntimeError: Unable to retrieve Broken Process",
        }
    ]
    definitions = read_json(snapshot_directory / "process_definitions.json")
    assert [record["process_name"] for record in definitions] == ["Good Process"]
    assert (snapshot_directory / "relationships.json").exists()
    assert (snapshot_directory / "manifest.json").exists()


def test_collect_ti_lineage_requires_current_object_catalog(
    isolated_roots: tuple[Path, Path],
) -> None:
    current_root, _ = isolated_roots
    (current_root / "objects.json").unlink()

    with pytest.raises(FileNotFoundError, match="collect_tm1_metadata.py"):
        collector.collect_ti_lineage()


def test_ti_collector_does_not_overwrite_rule_outputs(
    isolated_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_root, _ = isolated_roots
    rule_payload = [{"source": "rule marker"}]
    write_json(current_root / "rule_relationships.json", rule_payload)

    monkeypatch.setattr(
        collector,
        "get_tm1_connection",
        lambda: fake_connection(
            {
                "Load": SimpleNamespace(
                    prolog_procedure="ExecuteProcess('Child Process');"
                )
            }
        ),
    )

    manifest = collector.collect_ti_lineage()

    assert manifest["status"] == "COMPLETE"
    assert read_json(current_root / "rule_relationships.json") == rule_payload
    assert (current_root / "ti_relationships.json").exists()


def test_main_returns_zero_for_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        collector,
        "collect_ti_lineage",
        lambda: {
            "snapshot_id": "20260922T000000Z",
            "status": "COMPLETE",
            "process_count": 1,
            "evidence_count": 1,
            "relationship_count": 1,
            "error_count": 0,
        },
    )
    assert collector.main() == 0


def test_main_returns_one_for_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        collector,
        "collect_ti_lineage",
        lambda: {
            "snapshot_id": "20260922T000000Z",
            "status": "PARTIAL",
            "process_count": 1,
            "evidence_count": 1,
            "relationship_count": 1,
            "error_count": 1,
        },
    )
    assert collector.main() == 1


def test_main_returns_one_when_collection_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_error() -> dict[str, object]:
        raise RuntimeError("connection failed")

    monkeypatch.setattr(collector, "collect_ti_lineage", raise_error)
    assert collector.main() == 1
