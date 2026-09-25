from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import build_semantic_relationship_validations as module


def when() -> datetime:
    return datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)


def validation(status: str, name: str) -> dict[str, object]:
    return {
        "validation_status": status,
        "relationship_type": "READS_ATTRIBUTE",
        "target_type": "ATTRIBUTE",
        "target_name": name,
    }


def plan_record(
    *,
    plan_id: str,
    origin: str,
    relationship_id: str,
    status: str,
    decision: str,
) -> dict[str, object]:
    return {
        "plan_id": plan_id,
        "origin": origin,
        "relationship_id": relationship_id,
        "relationship_type": "READS_ATTRIBUTE",
        "domain": "ATTRIBUTE",
        "original_validation_status": "ATTRIBUTE_CATALOG_DEFERRED",
        "profiler_classification": "EXACT_MATCH",
        "proposed_validation_status": status,
        "decision_type": decision,
        "resolution_method": "EXACT_CATALOG_MATCH",
        "resolved_target_node_id": "attribute::Dim::Dim::Caption",
        "candidate_node_ids": ["attribute::Dim::Dim::Caption"],
        "source_snapshot_id": "TI1" if origin == "TI" else "RULE1",
    }


def write_inputs(
    tmp_path: Path,
    *,
    plans: list[dict[str, object]],
    ti: list[dict[str, object]],
    rule: list[dict[str, object]],
    published: bool = True,
) -> dict[str, Path]:
    paths = {
        "plan": tmp_path / "plan.json",
        "plan_manifest": tmp_path / "plan_manifest.json",
        "ti": tmp_path / "ti.json",
        "rule": tmp_path / "rule.json",
        "ti_manifest": tmp_path / "ti_manifest.json",
        "rule_manifest": tmp_path / "rule_manifest.json",
    }
    module.write_json(paths["plan"], plans)
    module.write_json(
        paths["plan_manifest"],
        {
            "status": "COMPLETE",
            "snapshot_id": "PLAN1",
            "published_current": published,
            "source_artifacts_modified": False,
        },
    )
    module.write_json(paths["ti"], ti)
    module.write_json(paths["rule"], rule)
    module.write_json(paths["ti_manifest"], {"snapshot_id": "TI1"})
    module.write_json(paths["rule_manifest"], {"snapshot_id": "RULE1"})
    return paths


def execute(tmp_path: Path, paths: dict[str, Path]):
    return module.build_semantic_relationship_validations(
        plan_path=paths["plan"],
        plan_manifest_path=paths["plan_manifest"],
        ti_validations_path=paths["ti"],
        rule_validations_path=paths["rule"],
        ti_manifest_path=paths["ti_manifest"],
        rule_manifest_path=paths["rule_manifest"],
        snapshot_root=tmp_path / "snapshots",
        current_root=tmp_path / "current",
        timestamp=when(),
    )


def test_plan_record_becomes_semantic_validation() -> None:
    record = module.semantic_validation_from_plan(
        plan_record(
            plan_id="p1",
            origin="TI",
            relationship_id="r1",
            status="VALID",
            decision="AUTOMATIC",
        ),
        run_id="RUN1",
        plan_snapshot_id="PLAN1",
    )
    assert record["semantic_validation_status"] == "VALID"
    assert record["decision_type"] == "AUTOMATIC"
    assert record["review_required"] is False


def test_review_plan_is_marked_review_required() -> None:
    source = plan_record(
        plan_id="p1",
        origin="RULE",
        relationship_id="r1",
        status="AMBIGUOUS_REFERENCE",
        decision="REVIEW",
    )
    source["resolved_target_node_id"] = None
    record = module.semantic_validation_from_plan(
        source,
        run_id="RUN1",
        plan_snapshot_id="PLAN1",
    )
    assert record["review_required"] is True


def test_non_deferred_validation_is_preserved() -> None:
    source = validation("VALID", "Cube")
    record = module.copy_non_deferred_validation(
        source,
        origin="TI",
        index=1,
        run_id="RUN1",
        source_snapshot_id="TI1",
    )
    assert record["semantic_validation_status"] == "VALID"
    assert record["decision_type"] == "PARSER_PRESERVED"
    assert record["source_validation"] == source


def test_unpublished_plan_is_rejected(tmp_path: Path) -> None:
    paths = write_inputs(tmp_path, plans=[], ti=[], rule=[], published=False)
    with pytest.raises(ValueError, match="must be published"):
        execute(tmp_path, paths)


def test_source_files_are_not_modified_on_partial(tmp_path: Path) -> None:
    paths = write_inputs(
        tmp_path,
        plans=[],
        ti=[validation("VALID", "x")],
        rule=[],
    )
    original = paths["ti"].read_text(encoding="utf-8")
    manifest = execute(tmp_path, paths)
    assert manifest["status"] == "PARTIAL"
    assert paths["ti"].read_text(encoding="utf-8") == original
    assert manifest["published_current"] is False


def test_stable_id_is_deterministic() -> None:
    assert module.stable_id("x", "A", 1) == module.stable_id("x", "A", 1)
    assert module.stable_id("x", "A", 1) != module.stable_id("x", "A", 2)


def test_validation_status_uses_supported_aliases() -> None:
    assert module.validation_status({"status": "valid"}) == "VALID"
    assert module.validation_status({"result": "broken reference"}) == "BROKEN_REFERENCE"
