from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import scripts.profile_tm1_catalog_matches as module


def when() -> datetime:
    return datetime(2026, 9, 24, 22, 0, tzinfo=timezone.utc)


def write_inputs(root: Path) -> dict[str, Path]:
    names = (
        "ti_relationships", "ti_validations", "rule_relationships",
        "rule_validations", "hierarchies", "views", "subsets", "attributes",
    )
    paths = {name: root / f"{name}.json" for name in names}
    module.write_json(paths["ti_relationships"], [])
    module.write_json(paths["ti_validations"], [])
    module.write_json(paths["rule_relationships"], [])
    module.write_json(paths["rule_validations"], [])
    module.write_json(paths["hierarchies"], [
        {"node_id": "hierarchy::Dim::Dim", "dimension_name": "Dim", "hierarchy_name": "Dim"},
        {"node_id": "hierarchy::Dim::Alt", "dimension_name": "Dim", "hierarchy_name": "Alt"},
    ])
    module.write_json(paths["views"], [
        {"node_id": "view::Cube::PUBLIC::<none>::V", "cube_name": "Cube", "view_name": "V"},
    ])
    module.write_json(paths["subsets"], [
        {"node_id": "subset::Dim::Dim::PUBLIC::<none>::S", "dimension_name": "Dim", "hierarchy_name": "Dim", "subset_name": "S"},
    ])
    module.write_json(paths["attributes"], [
        {"dimension_name": "Dim", "hierarchy_name": "Dim", "attribute_name": "Caption", "is_default_hierarchy": True},
        {"dimension_name": "Dim", "hierarchy_name": "Alt", "attribute_name": "Code", "is_default_hierarchy": False},
    ])
    return paths


def profile(tmp_path: Path, paths: dict[str, Path]):
    manifest = module.profile_catalog_matches(
        ti_relationships_path=paths["ti_relationships"],
        ti_validations_path=paths["ti_validations"],
        rule_relationships_path=paths["rule_relationships"],
        rule_validations_path=paths["rule_validations"],
        hierarchies_path=paths["hierarchies"],
        views_path=paths["views"],
        subsets_path=paths["subsets"],
        attributes_path=paths["attributes"],
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )
    return manifest


def detail(tmp_path: Path):
    return module.read_json(tmp_path / "current" / "catalog_match_profile_detail.json")


def test_profiles_exact_view_match(tmp_path: Path) -> None:
    paths = write_inputs(tmp_path)
    module.write_json(paths["ti_relationships"], [
        {"process_name": "P", "relationship_type": "USES_VIEW", "target_type": "view", "target_name": "V", "cube_name": "Cube", "view_name": "V"},
    ])
    module.write_json(paths["ti_validations"], [
        {"process_name": "P", "relationship_type": "USES_VIEW", "target_type": "view", "target_name": "V", "validation_status": "RUNTIME_VIEW_REFERENCE"},
    ])
    assert profile(tmp_path, paths)["status"] == "COMPLETE"
    assert detail(tmp_path)[0]["match_classification"] == "EXACT_MATCH"


def test_profiles_default_and_unique_hierarchy_attribute_matches(tmp_path: Path) -> None:
    paths = write_inputs(tmp_path)
    module.write_json(paths["ti_relationships"], [
        {"process_name": "P", "relationship_type": "READS_ATTRIBUTE", "target_type": "attribute", "target_name": "Caption", "dimension_name": "Dim", "attribute_name": "Caption"},
        {"process_name": "P", "relationship_type": "READS_ATTRIBUTE", "target_type": "attribute", "target_name": "Code", "dimension_name": "Dim", "attribute_name": "Code"},
    ])
    module.write_json(paths["ti_validations"], [
        {"process_name": "P", "relationship_type": "READS_ATTRIBUTE", "target_type": "attribute", "target_name": "Caption", "validation_status": "ATTRIBUTE_CATALOG_DEFERRED"},
        {"process_name": "P", "relationship_type": "READS_ATTRIBUTE", "target_type": "attribute", "target_name": "Code", "validation_status": "ATTRIBUTE_CATALOG_DEFERRED"},
    ])
    profile(tmp_path, paths)
    assert {item["match_classification"] for item in detail(tmp_path)} == {
        "DEFAULT_HIERARCHY_MATCH", "UNIQUE_HIERARCHY_MATCH"
    }


def test_absent_create_target_is_valid(tmp_path: Path) -> None:
    paths = write_inputs(tmp_path)
    module.write_json(paths["ti_relationships"], [
        {"process_name": "P", "relationship_type": "CREATES_SUBSET", "target_type": "subset", "target_name": "New", "dimension_name": "Dim", "subset_name": "New"},
    ])
    module.write_json(paths["ti_validations"], [
        {"process_name": "P", "relationship_type": "CREATES_SUBSET", "target_type": "subset", "target_name": "New", "validation_status": "RUNTIME_SUBSET_REFERENCE"},
    ])
    profile(tmp_path, paths)
    assert detail(tmp_path)[0]["match_classification"] == "VALID_CREATE_TARGET"


def test_joins_rule_validation_by_cube_semantic_key(tmp_path: Path) -> None:
    paths = write_inputs(tmp_path)
    module.write_json(paths["rule_relationships"], [
        {"cube_name": "Cube", "relationship_type": "REFERENCES_HIERARCHY", "target_type": "hierarchy", "target_name": "Dim", "dimension_name": "Dim", "hierarchy_name": "Dim"},
    ])
    module.write_json(paths["rule_validations"], [
        {"cube_name": "Cube", "relationship_type": "REFERENCES_HIERARCHY", "target_type": "hierarchy", "target_name": "Dim", "validation_status": "HIERARCHY_CATALOG_DEFERRED"},
    ])
    assert profile(tmp_path, paths)["status"] == "COMPLETE"
    row = detail(tmp_path)[0]
    assert row["join_method"] == "SEMANTIC_KEY"
    assert row["match_classification"] == "EXACT_MATCH"


def test_joins_duplicate_rule_groups_by_ordinal(tmp_path: Path) -> None:
    paths = write_inputs(tmp_path)
    relationships = []
    validations = []
    for line in (10, 20):
        relationships.append({
            "cube_name": "Cube", "relationship_type": "REFERENCES_HIERARCHY",
            "target_type": "hierarchy", "target_name": "Dim",
            "dimension_name": "Dim", "hierarchy_name": "Dim", "line_number": line,
        })
        validations.append({
            "cube_name": "Cube", "relationship_type": "REFERENCES_HIERARCHY",
            "target_type": "hierarchy", "target_name": "Dim",
            "validation_status": "HIERARCHY_CATALOG_DEFERRED", "line_number": line,
        })
    module.write_json(paths["rule_relationships"], relationships)
    module.write_json(paths["rule_validations"], validations)
    manifest = profile(tmp_path, paths)
    rows = detail(tmp_path)
    assert manifest["status"] == "COMPLETE"
    assert len(rows) == 2
    assert {row["join_method"] for row in rows} == {"SEMANTIC_GROUP_ORDINAL"}
    assert {row["semantic_group_position"] for row in rows} == {1, 2}
    assert len({row["relationship_id"] for row in rows}) == 2


def test_mismatched_duplicate_group_is_partial(tmp_path: Path) -> None:
    paths = write_inputs(tmp_path)
    module.write_json(paths["rule_relationships"], [
        {"cube_name": "Cube", "relationship_type": "REFERENCES_HIERARCHY", "target_type": "hierarchy", "target_name": "Dim"},
        {"cube_name": "Cube", "relationship_type": "REFERENCES_HIERARCHY", "target_type": "hierarchy", "target_name": "Dim"},
    ])
    module.write_json(paths["rule_validations"], [
        {"cube_name": "Cube", "relationship_type": "REFERENCES_HIERARCHY", "target_type": "hierarchy", "target_name": "Dim", "validation_status": "HIERARCHY_CATALOG_DEFERRED"},
    ])
    manifest = profile(tmp_path, paths)
    assert manifest["status"] == "PARTIAL"
    errors = module.read_json(tmp_path / "snapshots" / "20260924T220000Z" / "catalog_match_errors.json")
    assert "validation_count=1; relationship_count=2" in errors[0]["error"]


def test_missing_relationship_is_partial_and_preserves_current(tmp_path: Path) -> None:
    paths = write_inputs(tmp_path)
    module.write_json(paths["ti_validations"], [
        {"process_name": "P", "relationship_type": "USES_VIEW", "target_type": "view", "target_name": "Missing", "validation_status": "RUNTIME_VIEW_REFERENCE"},
    ])
    current = tmp_path / "current"
    current.mkdir(parents=True, exist_ok=True)
    module.write_json(current / "catalog_match_profile_detail.json", [{"sentinel": True}])
    manifest = profile(tmp_path, paths)
    assert manifest["status"] == "PARTIAL"
    assert module.read_json(current / "catalog_match_profile_detail.json") == [{"sentinel": True}]
    errors = module.read_json(tmp_path / "snapshots" / "20260924T220000Z" / "catalog_match_errors.json")
    assert "semantic_candidate_count=0" in errors[0]["error"]
