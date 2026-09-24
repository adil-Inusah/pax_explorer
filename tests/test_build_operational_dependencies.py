from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import scripts.build_operational_dependencies as module


def when() -> datetime:
    return datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)


def test_builds_files_commands_executables_and_scripts(tmp_path: Path) -> None:
    ti_relationships = tmp_path / "ti_relationships.json"
    ti_evidence = tmp_path / "ti_evidence.json"
    sources = tmp_path / "source_relationships.json"
    process_sources = tmp_path / "process_sources.json"
    module.write_json(
        ti_relationships,
        [
            {
                "process_name": "P",
                "relationship_type": "WRITES_FILE",
                "target_name": r"C:\\Data\\out.csv",
            },
            {
                "process_name": "P",
                "relationship_type": "EXECUTES_COMMAND",
                "target_name": r"powershell.exe -File C:\\Jobs\\load.ps1",
            },
        ],
    )
    module.write_json(ti_evidence, [])
    module.write_json(sources, [])
    module.write_json(process_sources, [])
    manifest = module.build_operational_dependencies(
        ti_relationships_path=ti_relationships,
        ti_evidence_path=ti_evidence,
        process_source_relationships_path=sources,
        process_sources_path=process_sources,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    assert manifest["status"] == "COMPLETE"
    nodes = module.read_json(tmp_path / "current" / "operational_dependencies.json")
    types = {node["node_type"] for node in nodes}
    assert {"FILE", "COMMAND", "EXECUTABLE", "SCRIPT"} <= types
    relationships = module.read_json(tmp_path / "current" / "operational_relationships.json")
    validations = module.read_json(
        tmp_path / "current" / "operational_relationship_validations.json"
    )
    assert len(relationships) == len(validations)


def test_redacts_credentials(tmp_path: Path) -> None:
    relationship = tmp_path / "relationships.json"
    module.write_json(
        relationship,
        [
            {
                "process_name": "P",
                "relationship_type": "EXECUTES_COMMAND",
                "target_name": "tool.exe password=secret-value",
            }
        ],
    )
    empty = tmp_path / "empty.json"
    module.write_json(empty, [])
    module.build_operational_dependencies(
        ti_relationships_path=relationship,
        ti_evidence_path=empty,
        process_source_relationships_path=empty,
        process_sources_path=empty,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    raw = (tmp_path / "current" / "operational_dependencies.json").read_text()
    assert "secret-value" not in raw
    assert "<redacted>" in raw


def test_dynamic_file_is_not_cross_checked(tmp_path: Path) -> None:
    relationship = tmp_path / "relationships.json"
    module.write_json(
        relationship,
        [
            {
                "process_name": "P",
                "relationship_type": "WRITES_FILE",
                "target_name": "C:\\Data\\" + "vFile" + ".csv",
            }
        ],
    )
    empty = tmp_path / "empty.json"
    module.write_json(empty, [])
    module.build_operational_dependencies(
        ti_relationships_path=relationship,
        ti_evidence_path=empty,
        process_source_relationships_path=empty,
        process_sources_path=empty,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    validations = module.read_json(
        tmp_path / "current" / "operational_relationship_validations.json"
    )
    assert validations[0]["validation_status"] == "DYNAMIC_EXTERNAL_FILE"


def test_partial_build_preserves_current(tmp_path: Path) -> None:
    relationship = tmp_path / "relationships.json"
    module.write_json(
        relationship,
        [{"relationship_type": "WRITES_FILE", "target_name": "x.csv"}],
    )
    empty = tmp_path / "empty.json"
    module.write_json(empty, [])
    current = tmp_path / "current"
    current.mkdir()
    module.write_json(current / "operational_dependencies.json", [{"sentinel": True}])
    manifest = module.build_operational_dependencies(
        ti_relationships_path=relationship,
        ti_evidence_path=empty,
        process_source_relationships_path=empty,
        process_sources_path=empty,
        snapshot_root=tmp_path / "snapshots",
        current_root=current,
        timestamp=when(),
    )
    assert manifest["status"] == "PARTIAL"
    assert module.read_json(current / "operational_dependencies.json") == [
        {"sentinel": True}
    ]


def test_empty_inventory_completes(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    module.write_json(empty, [])
    manifest = module.build_operational_dependencies(
        ti_relationships_path=empty,
        ti_evidence_path=empty,
        process_source_relationships_path=empty,
        process_sources_path=empty,
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    assert manifest["status"] == "COMPLETE"
    assert manifest["dependency_count"] == 0
