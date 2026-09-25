from __future__ import annotations

import json
from pathlib import Path

import pytest

from utilities.unified_graph_query import GraphContractError, UnifiedGraphQuery


def write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def graph_root(tmp_path: Path, *, published: bool = True) -> Path:
    root = tmp_path / "current"
    nodes = [
        {"node_id": "process::Load", "node_type": "PROCESS", "object_name": "Load"},
        {"node_id": "cube::Input", "node_type": "CUBE", "object_name": "Input"},
        {"node_id": "cube::Output", "node_type": "CUBE", "object_name": "Output"},
        {"node_id": "planned-target::ti::1", "node_type": "PLANNED_TARGET", "display_value": "vCube", "endpoint_state": "PLANNED"},
    ]
    relationships = [
        {"graph_relationship_id": "r1", "source_id": "process::Load", "target_id": "cube::Input", "relationship_type": "READS_FROM_CUBE", "endpoint_state": "CANONICAL"},
        {"graph_relationship_id": "r2", "source_id": "process::Load", "target_id": "cube::Output", "relationship_type": "WRITES_TO_CUBE", "endpoint_state": "CANONICAL"},
        {"graph_relationship_id": "r3", "source_id": "process::Load", "target_id": "planned-target::ti::1", "relationship_type": "WRITES_TO_CUBE", "endpoint_state": "PLANNED", "review_required": True},
    ]
    validations = [{"graph_relationship_id": item["graph_relationship_id"], "validation_status": "VALID"} for item in relationships]
    provenance = [{"graph_relationship_id": item["graph_relationship_id"], "source_artifacts": ["ti_relationships.json"]} for item in relationships]
    write(root / "graph_manifest.json", {"status": "COMPLETE", "published_current": published, "error_count": 0, "snapshot_id": "S1"})
    write(root / "graph_nodes.json", nodes)
    write(root / "graph_relationships.json", relationships)
    write(root / "graph_validations.json", validations)
    write(root / "graph_provenance.json", provenance)
    write(root / "graph_outbound_index.json", {"process::Load": ["r1", "r2", "r3"]})
    write(root / "graph_inbound_index.json", {"cube::Input": ["r1"], "cube::Output": ["r2"], "planned-target::ti::1": ["r3"]})
    return root


def engine(tmp_path: Path) -> UnifiedGraphQuery:
    semantics = tmp_path / "semantics.json"
    write(semantics, {"READS_FROM_CUBE": {"label": "Reads from", "category": "DATA_FLOW"}, "WRITES_TO_CUBE": {"label": "Writes to", "category": "DATA_FLOW"}})
    return UnifiedGraphQuery(graph_root(tmp_path), semantics_path=semantics)


def test_rejects_unpublished_graph(tmp_path: Path) -> None:
    with pytest.raises(GraphContractError, match="published"):
        UnifiedGraphQuery(graph_root(tmp_path, published=False))


def test_search_is_case_insensitive_and_visual_ready(tmp_path: Path) -> None:
    result = engine(tmp_path).search("input")
    assert result["nodes"][0]["id"] == "cube::Input"
    assert result["nodes"][0]["visual"]["shape"] == "database"


def test_neighborhood_returns_edges_paths_and_facets(tmp_path: Path) -> None:
    result = engine(tmp_path).neighborhood("process::Load", direction="outbound", depth=1)
    assert result["summary"]["edge_count"] == 3
    assert result["facets"]["relationship_types"] == {"READS_FROM_CUBE": 1, "WRITES_TO_CUBE": 2}
    planned = next(item for item in result["edges"] if item["endpoint_state"] == "PLANNED")
    assert planned["visual"]["line_style"] == "dotted"


def test_impact_is_cycle_safe_and_depth_limited(tmp_path: Path) -> None:
    result = engine(tmp_path).impact("cube::Input", direction="both", depth=3)
    assert result["query"]["operation"] == "impact"
    assert len(result["nodes"]) == 4


def test_process_story_groups_relationship_categories(tmp_path: Path) -> None:
    result = engine(tmp_path).process_story("Load")
    assert result["summary"]["relationship_groups"] == {"DATA_FLOW": 3}


def test_explain_combines_relationship_validation_and_provenance(tmp_path: Path) -> None:
    result = engine(tmp_path).explain("r1")
    assert result["relationship"]["label"] == "Reads from"
    assert result["validation"]["validation_status"] == "VALID"
    assert result["provenance"]["source_artifacts"] == ["ti_relationships.json"]
