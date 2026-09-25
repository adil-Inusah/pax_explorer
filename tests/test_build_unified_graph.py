from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import scripts.build_unified_graph as module


def when() -> datetime:
    return datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)


def write_base_inputs(root: Path) -> None:
    for name in module.NODE_FILES:
        module.write_json(root / name, [])
    for relationship_file, validation_file in module.DIRECT_RELATIONSHIP_DOMAINS.values():
        module.write_json(root / relationship_file, [])
        module.write_json(root / validation_file, [])
    module.write_json(root / "process_data_source_relationships.json", [])
    module.write_json(root / "process_data_source_validations.json", [])
    for relationship_file, validation_file, semantic_file in module.SEMANTIC_DOMAINS.values():
        module.write_json(root / relationship_file, [])
        module.write_json(root / validation_file, [])
        module.write_json(root / semantic_file, [])


def build(tmp_path: Path):
    return module.build_unified_graph(
        current_root=tmp_path / "current",
        snapshot_root=tmp_path / "snapshots",
        review_root=tmp_path / "review",
        timestamp=when(),
        pipeline_run_id="pipeline-1",
    )


def test_projects_core_and_structural_relationship(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()
    write_base_inputs(current)
    module.write_json(
        current / "objects.json",
        [{"snapshot_id": "s1", "object_type": "dimension", "object_name": "D"}],
    )
    module.write_json(
        current / "hierarchies.json",
        [{
            "snapshot_id": "s2",
            "node_id": "hierarchy::D::D",
            "node_type": "HIERARCHY",
            "object_type": "hierarchy",
        }],
    )
    relationship = {
        "snapshot_id": "s2",
        "relationship_id": "r1",
        "source_id": "hierarchy::D::D",
        "source_type": "HIERARCHY",
        "target_id": "dimension::D",
        "target_type": "DIMENSION",
        "relationship_type": "BELONGS_TO_DIMENSION",
        "relationship_origin": "METADATA",
        "resolution_method": "CATALOG_MATCH",
    }
    module.write_json(current / "hierarchy_relationships.json", [relationship])
    module.write_json(
        current / "hierarchy_relationship_validations.json",
        [{
            "validation_id": "validation::r1",
            "relationship_id": "r1",
            "validation_status": "VALID",
        }],
    )

    manifest = build(tmp_path)

    assert manifest["status"] == "COMPLETE"
    assert manifest["relationship_count"] == 1
    assert manifest["relationship_count"] == manifest["validation_count"]
    assert manifest["relationship_count"] == manifest["provenance_count"]
    nodes = module.read_json(current / "graph_nodes.json")
    assert {item["node_id"] for item in nodes} == {
        "dimension::D",
        "hierarchy::D::D",
    }


def test_deduplicates_data_source_edge_into_operational_provenance(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    write_base_inputs(current)
    module.write_json(
        current / "external_data_sources.json",
        [{"node_id": "data-source::ASCII_FILE::1", "node_type": "EXTERNAL_DATA_SOURCE"}],
    )
    module.write_json(
        current / "operational_dependencies.json",
        [{"node_id": "file::1", "node_type": "FILE", "object_type": "file"}],
    )
    operational = {
        "snapshot_id": "op",
        "relationship_id": "op-r1",
        "source_id": "data-source::ASCII_FILE::1",
        "source_type": "EXTERNAL_DATA_SOURCE",
        "target_id": "file::1",
        "target_type": "FILE",
        "relationship_type": "RESOLVES_TO_FILE",
    }
    source = dict(operational)
    source["snapshot_id"] = "ds"
    source["relationship_id"] = "ds-r1"
    module.write_json(current / "operational_relationships.json", [operational])
    module.write_json(
        current / "operational_relationship_validations.json",
        [{
            "validation_id": "validation::op-r1",
            "relationship_id": "op-r1",
            "validation_status": "EXTERNAL_DEPENDENCY_NOT_CROSS_CHECKED",
        }],
    )
    module.write_json(current / "process_data_source_relationships.json", [source])
    module.write_json(
        current / "process_data_source_validations.json",
        [{
            "validation_id": "validation::ds-r1",
            "relationship_id": "ds-r1",
            "validation_status": "EXTERNAL_DEPENDENCY_NOT_CROSS_CHECKED",
        }],
    )

    manifest = build(tmp_path)

    assert manifest["status"] == "COMPLETE"
    assert manifest["relationship_count"] == 1
    provenance = module.read_json(current / "graph_provenance.json")
    assert provenance[0]["source_record_ids"] == ["op-r1", "ds-r1"]
    outcomes = module.read_json(current / "graph_projection_outcomes.json")
    assert {item["projection_outcome"] for item in outcomes} == {
        "PROJECTED",
        "EXCLUDED_REDUNDANT_WITH_PROVENANCE",
    }


def test_projects_dynamic_semantic_target_as_reference_expression(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    write_base_inputs(current)
    module.write_json(
        current / "objects.json",
        [{"object_type": "process", "object_name": "P", "snapshot_id": "core"}],
    )
    relationship = {
        "process_name": "P",
        "relationship_type": "READS_FROM_CUBE",
        "target_type": "cube",
        "target_name": "vCube",
        "target_expression": "vCube",
        "confidence": "UNRESOLVED",
    }
    module.write_json(current / "ti_relationships.json", [relationship])
    module.write_json(
        current / "ti_relationship_validations.json",
        [dict(relationship, validation_status="UNRESOLVED_DYNAMIC_REFERENCE")],
    )
    module.write_json(
        current / "semantic_ti_relationship_validations.json",
        [{
            "semantic_validation_id": "sv1",
            "origin": "TI",
            "relationship_type": "READS_FROM_CUBE",
            "domain": "CUBE",
            "semantic_validation_status": "UNRESOLVED_DYNAMIC_REFERENCE",
            "decision_type": "REVIEW",
            "profiler_classification": "DYNAMIC_REFERENCE",
            "target_name": "vCube",
            "source_snapshot_id": "ti",
            "candidate_node_ids": [],
            "review_required": True,
        }],
    )

    manifest = build(tmp_path)

    assert manifest["status"] == "COMPLETE"
    assert manifest["unresolved_reference_count"] == 1
    nodes = module.read_json(current / "graph_nodes.json")
    reference = next(
        item for item in nodes if item["node_type"] == "REFERENCE_EXPRESSION"
    )
    assert reference["endpoint_state"] == "DYNAMIC"
    relationships = module.read_json(current / "graph_relationships.json")
    assert relationships[0]["target_id"] == reference["node_id"]
    assert relationships[0]["endpoint_state"] == "DYNAMIC"


def test_partial_build_preserves_current_and_writes_diagnostics(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    write_base_inputs(current)
    module.write_json(current / "graph_nodes.json", [{"sentinel": True}])
    module.write_json(
        current / "hierarchy_relationships.json",
        [{"relationship_id": "broken", "source_id": "", "target_id": ""}],
    )
    module.write_json(
        current / "hierarchy_relationship_validations.json",
        [{"relationship_id": "broken", "validation_status": "VALID"}],
    )

    manifest = build(tmp_path)

    assert manifest["status"] == "PARTIAL"
    assert manifest["error_count"] >= 1
    assert manifest["published_current"] is False
    assert module.read_json(current / "graph_nodes.json") == [{"sentinel": True}]

    snapshot = tmp_path / "snapshots" / "20260925T180000Z"
    errors = module.read_json(snapshot / "graph_errors.json")
    assert any(
        item.get("stage") == "PROJECT_RELATIONSHIP"
        and "relationship_type" in item.get("missing_fields", [])
        for item in errors
    )
    outcomes = module.read_json(snapshot / "graph_projection_outcomes.json")
    assert outcomes == [{
        "source_artifact": "hierarchy_relationships.json",
        "source_record_id": "broken",
        "graph_relationship_ids": [],
        "projection_outcome": "ERROR",
    }]


def test_type_summary_is_defensive_for_unknown_values(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()
    write_base_inputs(current)

    manifest = build(tmp_path)

    assert manifest["status"] == "COMPLETE"
    summary = module.read_json(current / "graph_type_summary.json")
    assert summary == {
        "node_type_counts": {},
        "relationship_type_counts": {},
        "endpoint_state_counts": {},
        "projection_outcome_counts": {},
    }



def test_parser_preserved_noncanonical_target_is_unresolved(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    write_base_inputs(current)
    module.write_json(
        current / "objects.json",
        [{"object_type": "cube", "object_name": "SYS Legacy Elements", "snapshot_id": "core"}],
    )
    relationship = {
        "source_cube": "SYS Legacy Elements",
        "relationship_type": "READS_FROM",
        "target_object_type": "CUBE",
        "target_name": "!}Dimensions",
        "target_expression": "!}Dimensions",
        "confidence": "HIGH",
    }
    module.write_json(current / "rule_relationships.json", [relationship])
    module.write_json(
        current / "rule_relationship_validations.json",
        [dict(relationship, validation_status="VALID")],
    )
    module.write_json(
        current / "semantic_rule_relationship_validations.json",
        [{
            "semantic_validation_id": "sv1",
            "origin": "RULE",
            "relationship_type": "READS_FROM",
            "domain": "CUBE",
            "semantic_validation_status": "VALID",
            "decision_type": "PARSER_PRESERVED",
            "resolved_target_node_id": None,
            "candidate_node_ids": [],
            "target_name": "!}Dimensions",
            "source_snapshot_id": "rule",
            "review_required": False,
        }],
    )

    manifest = build(tmp_path)

    assert manifest["status"] == "COMPLETE"
    assert manifest["synthetic_endpoint_count"] == 1
    assert manifest["unresolved_reference_count"] == 1
    assert manifest["missing_unresolved_count"] == 0
    assert manifest["orphan_unresolved_count"] == 0
    unresolved = module.read_json(current / "graph_unresolved_references.json")
    assert len(unresolved) == 1
    assert unresolved[0]["node_type"] == "PLANNED_TARGET"
    assert unresolved[0]["display_value"] == "!}Dimensions"


def test_distinct_semantic_validations_do_not_overwrite_each_other(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    write_base_inputs(current)
    module.write_json(
        current / "objects.json",
        [
            {"object_type": "process", "object_name": "P", "snapshot_id": "core"},
            {"object_type": "cube", "object_name": "C", "snapshot_id": "core"},
        ],
    )
    relationship = {
        "process_name": "P",
        "relationship_type": "READS_FROM_CUBE",
        "target_type": "cube",
        "target_name": "C",
        "target_expression": "'C'",
        "confidence": "CONFIRMED",
    }
    module.write_json(current / "ti_relationships.json", [relationship, relationship])
    module.write_json(
        current / "ti_relationship_validations.json",
        [dict(relationship, validation_status="VALID") for _ in range(2)],
    )
    module.write_json(
        current / "semantic_ti_relationship_validations.json",
        [
            {
                "semantic_validation_id": semantic_id,
                "origin": "TI",
                "relationship_type": "READS_FROM_CUBE",
                "domain": "CUBE",
                "semantic_validation_status": "VALID",
                "decision_type": "PARSER_PRESERVED",
                "target_name": "C",
                "source_snapshot_id": "ti",
                "candidate_node_ids": [],
                "review_required": False,
            }
            for semantic_id in ("sv1", "sv2")
        ],
    )

    manifest = build(tmp_path)

    assert manifest["status"] == "COMPLETE"
    assert manifest["relationship_count"] == 2
    assert manifest["projected_outcome_count"] == 2
    relationships = module.read_json(current / "graph_relationships.json")
    assert len({item["graph_relationship_id"] for item in relationships}) == 2



def test_source_validation_index_is_one_based(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()
    write_base_inputs(current)
    module.write_json(
        current / "objects.json",
        [
            {"object_type": "process", "object_name": "P", "snapshot_id": "core"},
            {"object_type": "cube", "object_name": "First", "snapshot_id": "core"},
            {"object_type": "cube", "object_name": "Second", "snapshot_id": "core"},
        ],
    )
    relationships = [
        {
            "process_name": "P",
            "relationship_type": "READS_FROM_CUBE",
            "target_type": "cube",
            "target_name": name,
            "target_expression": repr(name),
        }
        for name in ("First", "Second")
    ]
    module.write_json(current / "ti_relationships.json", relationships)
    module.write_json(
        current / "ti_relationship_validations.json",
        [dict(item, validation_status="VALID") for item in relationships],
    )
    module.write_json(
        current / "semantic_ti_relationship_validations.json",
        [{
            "semantic_validation_id": "sv-first",
            "source_validation_index": 1,
            "origin": "TI",
            "relationship_type": "READS_FROM_CUBE",
            "domain": "CUBE",
            "semantic_validation_status": "VALID",
            "decision_type": "PARSER_PRESERVED",
            "target_name": "First",
            "candidate_node_ids": [],
            "review_required": False,
        }],
    )

    manifest = build(tmp_path)

    assert manifest["status"] == "COMPLETE"
    graph_relationship = module.read_json(current / "graph_relationships.json")[0]
    assert graph_relationship["target_id"] == "cube::First"


def test_source_projection_counts_reconcile(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()
    write_base_inputs(current)
    module.write_json(
        current / "objects.json",
        [{"object_type": "dimension", "object_name": "D", "snapshot_id": "core"}],
    )
    module.write_json(
        current / "hierarchies.json",
        [{"node_id": "hierarchy::D::D", "node_type": "HIERARCHY"}],
    )
    module.write_json(
        current / "hierarchy_relationships.json",
        [{
            "relationship_id": "r1",
            "source_id": "hierarchy::D::D",
            "source_type": "HIERARCHY",
            "target_id": "dimension::D",
            "target_type": "DIMENSION",
            "relationship_type": "BELONGS_TO_DIMENSION",
        }],
    )
    module.write_json(
        current / "hierarchy_relationship_validations.json",
        [{"relationship_id": "r1", "validation_status": "VALID"}],
    )

    manifest = build(tmp_path)

    assert manifest["status"] == "COMPLETE"
    assert manifest["source_coverage_failure_count"] == 0
    assert manifest["source_projection_counts"]["hierarchy_relationships.json"] == {
        "source_records": 1,
        "outcomes": 1,
        "reconciles": True,
    }



def test_semantic_pairing_ignores_relationship_list_order(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()
    write_base_inputs(current)
    module.write_json(
        current / "objects.json",
        [
            {"object_type": "process", "object_name": "P", "snapshot_id": "core"},
            {"object_type": "cube", "object_name": "A", "snapshot_id": "core"},
            {"object_type": "cube", "object_name": "B", "snapshot_id": "core"},
        ],
    )
    read_a = {
        "process_name": "P",
        "relationship_type": "READS_FROM_CUBE",
        "target_type": "cube",
        "target_name": "A",
        "target_expression": "'A'",
    }
    write_b = {
        "process_name": "P",
        "relationship_type": "WRITES_TO_CUBE",
        "target_type": "cube",
        "target_name": "B",
        "target_expression": "'B'",
    }
    module.write_json(current / "ti_relationships.json", [write_b, read_a])
    module.write_json(
        current / "ti_relationship_validations.json",
        [dict(read_a, validation_status="VALID"), dict(write_b, validation_status="VALID")],
    )
    module.write_json(
        current / "semantic_ti_relationship_validations.json",
        [
            {
                "semantic_validation_id": "sv-read",
                "source_validation_index": 1,
                "origin": "TI",
                "relationship_type": "READS_FROM_CUBE",
                "domain": "CUBE",
                "semantic_validation_status": "VALID",
                "decision_type": "PARSER_PRESERVED",
                "target_name": "A",
                "candidate_node_ids": [],
                "review_required": False,
            },
            {
                "semantic_validation_id": "sv-write",
                "source_validation_index": 2,
                "origin": "TI",
                "relationship_type": "WRITES_TO_CUBE",
                "domain": "CUBE",
                "semantic_validation_status": "VALID",
                "decision_type": "PARSER_PRESERVED",
                "target_name": "B",
                "candidate_node_ids": [],
                "review_required": False,
            },
        ],
    )

    manifest = build(tmp_path)

    assert manifest["status"] == "COMPLETE"
    relationships = module.read_json(current / "graph_relationships.json")
    assert {(item["relationship_type"], item["target_id"]) for item in relationships} == {
        ("READS_FROM_CUBE", "cube::A"),
        ("WRITES_TO_CUBE", "cube::B"),
    }
    provenance = module.read_json(current / "graph_provenance.json")
    assert {item["source_pairing_method"] for item in provenance} <= {
        "EXACT_SEMANTIC_SIGNATURE",
        "RELAXED_SEMANTIC_SIGNATURE",
    }

def test_plan_based_semantic_record_uses_relationship_id_source(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    write_base_inputs(current)

    module.write_json(
        current / "objects.json",
        [
            {
                "object_type": "process",
                "object_name": "Process A",
                "snapshot_id": "core",
            }
        ],
    )

    create_attribute = {
        "process_name": "Process A",
        "relationship_type": "CREATES_ATTRIBUTE",
        "target_type": "attribute",
        "target_name": "Dimension A.Attribute A",
        "target_expression": "'Dimension A'",
    }

    unrelated = {
        "process_name": "Process B",
        "relationship_type": "READS_FROM_CUBE",
        "target_type": "cube",
        "target_name": "Cube B",
        "target_expression": "'Cube B'",
    }

    module.write_json(
        current / "ti_relationships.json",
        [
            unrelated,
            create_attribute,
        ],
    )

    module.write_json(
        current / "ti_relationship_validations.json",
        [
            {
                **unrelated,
                "validation_status": "VALID",
            },
            {
                **create_attribute,
                "validation_status": (
                    "ATTRIBUTE_CATALOG_DEFERRED"
                ),
            },
        ],
    )

    module.write_json(
        current
        / "semantic_ti_relationship_validations.json",
        [
            {
                "semantic_validation_id": "sv-create",
                "relationship_id": (
                    "semantic::ti::Process A::"
                    "CREATES_ATTRIBUTE::"
                    "attribute::"
                    "dimension a.attribute a"
                ),
                "origin": "TI",
                "relationship_type": "CREATES_ATTRIBUTE",
                "domain": "ATTRIBUTE",
                "semantic_validation_status": (
                    "REVIEW_EXISTING_CREATE_TARGET"
                ),
                "decision_type": "REVIEW",
                "target_name": "Dimension A.Attribute A",
                "candidate_node_ids": [],
                "review_required": True,
            }
        ],
    )

    manifest = build(tmp_path)

    assert manifest["status"] == "COMPLETE"
    assert manifest["error_count"] == 0

    relationships = module.read_json(
        current / "graph_relationships.json"
    )

    assert len(relationships) == 1
    assert (
        relationships[0]["relationship_type"]
        == "CREATES_ATTRIBUTE"
    )
    assert (
        relationships[0]["source_id"]
        == "process::Process A"
    )

    provenance = module.read_json(
        current / "graph_provenance.json"
    )

    assert provenance[0]["source_pairing_method"] in {
        "EXACT_SEMANTIC_SIGNATURE",
        "RELAXED_SEMANTIC_SIGNATURE",
    }