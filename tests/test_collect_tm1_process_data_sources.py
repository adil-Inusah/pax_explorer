from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from scripts import collect_tm1_process_data_sources as module


def when() -> datetime:
    return datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)


class FakeProcessService:
    def __init__(self, processes: list[object]) -> None:
        self._processes = processes

    def get_all_names(self) -> list[str]:
        return [str(getattr(item, "name")) for item in self._processes]

    def get(self, name: str) -> object:
        return next(item for item in self._processes if getattr(item, "name") == name)


class FakeTM1:
    def __init__(self, processes: list[object]) -> None:
        self.processes = FakeProcessService(processes)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def ascii_process(name: str, path: str) -> object:
    return SimpleNamespace(
        name=name,
        datasource_type="ASCII",
        datasource_data_source_name_for_server=path,
        datasource_data_source_name_for_client=path,
        datasource_password="",
        datasource_user_name="",
    )


def collect(tmp_path: Path, processes: list[object]):
    return module.collect_process_data_sources(
        FakeTM1(processes),
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )


def test_ascii_source_publishes_enriched_file_contract(tmp_path: Path) -> None:
    manifest = collect(tmp_path, [ascii_process("Load File", "model_upload/input.csv")])
    assert manifest["status"] == "COMPLETE"
    relationships = read_json(
        tmp_path / "current" / "process_data_source_relationships.json"
    )
    read_edge = next(
        item for item in relationships if item["relationship_type"] == "READS_FROM_FILE"
    )
    assert read_edge["process_name"] == "Load File"
    assert read_edge["target_expression"] == "model_upload/input.csv"
    assert read_edge["configured_data_source_id"].startswith(
        "data-source::ASCII_FILE::"
    )
    assert read_edge["target_id"] == module.file_node_id("model_upload/input.csv")


def test_ascii_source_publishes_source_to_file_bridge(tmp_path: Path) -> None:
    collect(tmp_path, [ascii_process("Load File", "model_upload/input.csv")])
    relationships = read_json(
        tmp_path / "current" / "process_data_source_relationships.json"
    )
    read_edge = next(
        item for item in relationships if item["relationship_type"] == "READS_FROM_FILE"
    )
    bridge = next(
        item for item in relationships if item["relationship_type"] == "RESOLVES_TO_FILE"
    )
    assert bridge["source_id"] == read_edge["configured_data_source_id"]
    assert bridge["target_id"] == read_edge["target_id"]
    assert bridge["target_expression"] == read_edge["target_expression"]


def test_shared_ascii_source_deduplicates_bridge(tmp_path: Path) -> None:
    collect(
        tmp_path,
        [
            ascii_process("Load A", "model_upload/input.csv"),
            ascii_process("Load B", "model_upload/input.csv"),
        ],
    )
    relationships = read_json(
        tmp_path / "current" / "process_data_source_relationships.json"
    )
    assert sum(
        item["relationship_type"] == "READS_FROM_FILE" for item in relationships
    ) == 2
    assert sum(
        item["relationship_type"] == "RESOLVES_TO_FILE" for item in relationships
    ) == 1


def test_relationship_and_validation_identities_reconcile(tmp_path: Path) -> None:
    manifest = collect(tmp_path, [ascii_process("Load File", "model_upload/input.csv")])
    relationships = read_json(
        tmp_path / "current" / "process_data_source_relationships.json"
    )
    validations = read_json(
        tmp_path / "current" / "process_data_source_validations.json"
    )
    assert manifest["relationship_count"] == manifest["validation_count"]
    assert {item["relationship_id"] for item in relationships} == {
        item["relationship_id"] for item in validations
    }
