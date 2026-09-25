from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import build_catalog_validation_resolution_plan as module


def when() -> datetime:
    return datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)


def source_record(
    classification: str,
    *,
    relationship_id: str = "r1",
    domain: str = "ATTRIBUTE",
    candidates: list[str] | None = None,
) -> dict[str, object]:
    return {
        "origin": "TI",
        "relationship_id": relationship_id,
        "relationship_type": "READS_ATTRIBUTE",
        "input_validation_status": "ATTRIBUTE_CATALOG_DEFERRED",
        "domain": domain,
        "match_classification": classification,
        "candidate_node_ids": candidates or [],
        "semantic_group_position": 1,
        "semantic_group_size": 1,
        "join_method": "SEMANTIC_KEY",
        "target_name": "Dim.Caption",
        "dimension_name": "Dim",
        "attribute_name": "Caption",
    }


def write_inputs(tmp_path: Path, rows: list[dict[str, object]]) -> dict[str, Path]:
    paths = {
        "profile_detail": tmp_path / "catalog_match_profile_detail.json",
        "profile_manifest": tmp_path / "catalog_match_manifest.json",
        "ti_manifest": tmp_path / "ti_lineage_manifest.json",
        "rule_manifest": tmp_path / "rule_lineage_manifest.json",
    }
    module.write_json(paths["profile_detail"], rows)
    module.write_json(
        paths["profile_manifest"],
        {"status": "COMPLETE", "snapshot_id": "PROFILE1"},
    )
    module.write_json(paths["ti_manifest"], {"snapshot_id": "TI1"})
    module.write_json(paths["rule_manifest"], {"snapshot_id": "RULE1"})
    return paths


def execute(tmp_path: Path, rows: list[dict[str, object]], publish: bool = True):
    paths = write_inputs(tmp_path, rows)
    return module.build_catalog_validation_resolution_plan(
        profile_detail_path=paths["profile_detail"],
        profile_manifest_path=paths["profile_manifest"],
        ti_manifest_path=paths["ti_manifest"],
        rule_manifest_path=paths["rule_manifest"],
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
        publish_current=publish,
    )


def test_exact_match_is_automatic_and_resolved(tmp_path: Path) -> None:
    manifest = execute(
        tmp_path,
        [source_record("EXACT_MATCH", candidates=["attribute::Dim::Dim::Caption"])],
    )
    assert manifest["status"] == "COMPLETE"
    plan = module.read_json(
        tmp_path / "current" / "catalog_validation_resolution_plan.json"
    )
    assert plan[0]["decision_type"] == "AUTOMATIC"
    assert plan[0]["proposed_validation_status"] == "VALID"
    assert plan[0]["resolved_target_node_id"] == "attribute::Dim::Dim::Caption"


def test_default_hierarchy_match_is_automatic(tmp_path: Path) -> None:
    execute(
        tmp_path,
        [
            source_record(
                "DEFAULT_HIERARCHY_MATCH",
                candidates=["attribute::Dim::Dim::Caption"],
            )
        ],
    )
    plan = module.read_json(
        tmp_path / "current" / "catalog_validation_resolution_plan.json"
    )
    assert plan[0]["proposed_validation_status"] == "VALID_DEFAULT_HIERARCHY_MATCH"


def test_valid_create_target_is_automatic_without_resolved_node(tmp_path: Path) -> None:
    execute(tmp_path, [source_record("VALID_CREATE_TARGET", candidates=[])])
    plan = module.read_json(
        tmp_path / "current" / "catalog_validation_resolution_plan.json"
    )
    assert plan[0]["decision_type"] == "AUTOMATIC"
    assert plan[0]["resolved_target_node_id"] is None


def test_catalog_miss_is_domain_specific_review(tmp_path: Path) -> None:
    execute(tmp_path, [source_record("TARGET_NOT_IN_CATALOG", domain="VIEW")])
    plan = module.read_json(
        tmp_path / "current" / "catalog_validation_resolution_plan.json"
    )
    assert plan[0]["decision_type"] == "REVIEW"
    assert plan[0]["proposed_validation_status"] == "VIEW_NOT_IN_CATALOG"


def test_review_csv_contains_review_records_only(tmp_path: Path) -> None:
    execute(
        tmp_path,
        [
            source_record("EXACT_MATCH", relationship_id="a", candidates=["node::1"]),
            source_record("DYNAMIC_REFERENCE", relationship_id="b"),
        ],
    )
    csv_text = (
        tmp_path / "current" / "catalog_validation_resolution_review.csv"
    ).read_text(encoding="utf-8")
    assert "UNRESOLVED_DYNAMIC_REFERENCE" in csv_text
    assert "EXACT_CATALOG_MATCH" not in csv_text


def test_invalid_exact_match_makes_partial_and_preserves_current(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()
    module.write_json(
        current / "catalog_validation_resolution_plan.json",
        [{"sentinel": True}],
    )
    manifest = execute(
        tmp_path,
        [source_record("EXACT_MATCH", candidates=[])],
    )
    assert manifest["status"] == "PARTIAL"
    assert manifest["published_current"] is False
    assert module.read_json(
        current / "catalog_validation_resolution_plan.json"
    ) == [{"sentinel": True}]


def test_duplicate_relationship_identity_is_rejected(tmp_path: Path) -> None:
    rows = [
        source_record("DYNAMIC_REFERENCE", relationship_id="same"),
        source_record("DYNAMIC_REFERENCE", relationship_id="same"),
    ]
    manifest = execute(tmp_path, rows)
    assert manifest["status"] == "PARTIAL"
    assert manifest["duplicate_relationship_key_count"] == 1


def test_incomplete_profile_manifest_raises(tmp_path: Path) -> None:
    paths = write_inputs(tmp_path, [])
    module.write_json(
        paths["profile_manifest"],
        {"status": "PARTIAL", "snapshot_id": "PROFILE1"},
    )
    with pytest.raises(ValueError, match="must be COMPLETE"):
        module.build_catalog_validation_resolution_plan(
            profile_detail_path=paths["profile_detail"],
            profile_manifest_path=paths["profile_manifest"],
            ti_manifest_path=paths["ti_manifest"],
            rule_manifest_path=paths["rule_manifest"],
            snapshot_root=tmp_path / "snapshots",
            current_root=tmp_path / "current",
            timestamp=when(),
        )
