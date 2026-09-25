from datetime import datetime, timezone
from pathlib import Path

import scripts.build_object_catalog as module


def write(path: Path, payload):
    module.write_json(path, payload)


def base(tmp_path: Path):
    current = tmp_path / "current"
    current.mkdir()
    write(current / "graph_manifest.json", {"status": "COMPLETE", "published_current": True, "snapshot_id": "g1", "pipeline_run_id": "p1"})
    write(current / "graph_nodes.json", [])
    write(current / "graph_relationships.json", [])
    write(current / "graph_provenance.json", [])
    write(current / "external_data_sources.json", [])
    write(current / "process_data_sources.json", [])
    return current


def build(tmp_path: Path, publish=True):
    return module.build_object_catalog(current_root=tmp_path / "current", snapshot_root=tmp_path / "snapshots", review_root=tmp_path / "review", timestamp=datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc), publish_current=publish, pipeline_run_id="p1")


def test_shared_view_has_one_object_and_unique_process_relationships(tmp_path: Path):
    current = base(tmp_path)
    nodes = [
        {"node_id": "cube::Cube A", "node_type": "CUBE", "object_name": "Cube A"},
        {"node_id": "process::A", "node_type": "PROCESS", "object_name": "A"},
        {"node_id": "process::B", "node_type": "PROCESS", "object_name": "B"},
        {"node_id": "view::Cube A::PUBLIC::<none>::View 1", "node_type": "VIEW", "cube_name": "Cube A", "view_name": "View 1", "visibility": "PUBLIC"},
    ]
    write(current / "graph_nodes.json", nodes)
    relationships = [
        {"graph_relationship_id": "g-a", "source_id": "process::A", "target_id": "view::Cube A::PUBLIC::<none>::View 1", "source_type": "PROCESS", "target_type": "VIEW", "relationship_type": "USES_VIEW"},
        {"graph_relationship_id": "g-b", "source_id": "process::B", "target_id": "view::Cube A::PUBLIC::<none>::View 1", "source_type": "PROCESS", "target_type": "VIEW", "relationship_type": "USES_VIEW"},
    ]
    write(current / "graph_relationships.json", relationships)
    write(current / "graph_provenance.json", [{"graph_relationship_id": item["graph_relationship_id"], "source_artifacts": ["ti"]} for item in relationships])
    manifest = build(tmp_path)
    assert manifest["status"] == "COMPLETE"
    catalog = module.read_json(current / "object_catalog.json")
    assert sum(item["object_id"] == "view::Cube A::PUBLIC::<none>::View 1" for item in catalog) == 1
    object_relationships = module.read_json(current / "object_relationships.json")
    uses = [item for item in object_relationships if item["relationship_type"] == "USES_VIEW"]
    assert len(uses) == 2
    assert len({item["object_relationship_id"] for item in uses}) == 2


def test_missing_temp_view_creates_reference_without_resolution_edge(tmp_path: Path):
    current = base(tmp_path)
    source_id = "data-source::TM1_CUBE_VIEW::x"
    write(current / "graph_nodes.json", [
        {"node_id": "process::P", "node_type": "PROCESS", "object_name": "P"},
        {"node_id": source_id, "node_type": "EXTERNAL_DATA_SOURCE", "source_type": "TM1_CUBE_VIEW", "server_source_name": "Balance Sheet Detail", "view_name": "Temp"},
    ])
    write(current / "external_data_sources.json", [{"node_id": source_id, "source_type": "TM1_CUBE_VIEW", "server_source_name": "Balance Sheet Detail", "view_name": "Temp"}])
    write(current / "process_data_sources.json", [{"process_name": "P", "source_id": source_id, "source_type": "TM1_CUBE_VIEW"}])
    manifest = build(tmp_path)
    assert manifest["status"] == "COMPLETE"
    resolutions = module.read_json(current / "object_reference_resolutions.json")
    assert resolutions[0]["resolution_status"] == "TARGET_NOT_IN_PUBLIC_CATALOG"
    relationships = module.read_json(current / "object_relationships.json")
    assert not any(item["relationship_type"] == "RESOLVES_TO_VIEW" for item in relationships)


def test_repeated_graph_evidence_consolidates(tmp_path: Path):
    current = base(tmp_path)
    nodes = [
        {"node_id": "process::P", "node_type": "PROCESS", "object_name": "P"},
        {"node_id": "cube::C", "node_type": "CUBE", "object_name": "C"},
    ]
    write(current / "graph_nodes.json", nodes)
    relationships = [
        {"graph_relationship_id": "g1", "source_id": "process::P", "target_id": "cube::C", "relationship_type": "READS_FROM_CUBE"},
        {"graph_relationship_id": "g2", "source_id": "process::P", "target_id": "cube::C", "relationship_type": "READS_FROM_CUBE"},
    ]
    write(current / "graph_relationships.json", relationships)
    write(current / "graph_provenance.json", [{"graph_relationship_id": "g1"}, {"graph_relationship_id": "g2"}])
    manifest = build(tmp_path)
    assert manifest["status"] == "COMPLETE"
    projected = [item for item in module.read_json(current / "object_relationships.json") if item["relationship_type"] == "READS_FROM_CUBE"]
    assert len(projected) == 1
    assert projected[0]["evidence_count"] == 2
    provenance = next(item for item in module.read_json(current / "object_relationship_provenance.json") if item["object_relationship_id"] == projected[0]["object_relationship_id"])
    assert provenance["source_graph_relationship_ids"] == ["g1", "g2"]


def test_attribute_ownership_is_explicit(tmp_path: Path):
    current = base(tmp_path)
    write(current / "graph_nodes.json", [
        {"node_id": "dimension::D", "node_type": "DIMENSION", "object_name": "D"},
        {"node_id": "hierarchy::D::D", "node_type": "HIERARCHY", "dimension_name": "D", "hierarchy_name": "D"},
        {"node_id": "attribute::D::D::A", "node_type": "ATTRIBUTE", "dimension_name": "D", "hierarchy_name": "D", "attribute_name": "A"},
    ])
    manifest = build(tmp_path)
    assert manifest["status"] == "COMPLETE"
    types = {item["relationship_type"] for item in module.read_json(current / "object_relationships.json")}
    assert {"BELONGS_TO_HIERARCHY", "BELONGS_TO_DIMENSION"} <= types


def test_partial_build_preserves_current(tmp_path: Path):
    current = base(tmp_path)
    write(current / "object_catalog.json", [{"sentinel": True}])
    write(current / "graph_nodes.json", [{"node_id": "process::P", "node_type": "PROCESS", "object_name": "P"}])
    write(current / "graph_relationships.json", [{"graph_relationship_id": "broken", "source_id": "process::P", "target_id": "missing", "relationship_type": "CALLS_PROCESS"}])
    write(current / "graph_provenance.json", [{"graph_relationship_id": "broken"}])
    manifest = build(tmp_path)
    assert manifest["status"] == "PARTIAL"
    assert module.read_json(current / "object_catalog.json") == [{"sentinel": True}]
