from __future__ import annotations

from pathlib import Path

from scripts import run_holistic_acceptance as module


def write_json(path: Path, payload: object) -> None:
    module.write_json(path, payload)


def semantic_record(index: int, decision: str, *, plan_based: bool) -> dict[str, object]:
    return {
        "semantic_validation_id": f"semantic-{index}",
        "decision_type": decision,
        "plan_id": f"plan-{index}" if plan_based else None,
    }


def test_semantic_resolution_gate_passes_complete_artifacts(tmp_path: Path) -> None:
    plan = [
        {
            "plan_id": f"plan-{index}",
            "decision_type": "AUTOMATIC" if index < 1720 else "REVIEW",
        }
        for index in range(2933)
    ]
    semantic = [
        semantic_record(
            index,
            "AUTOMATIC" if index < 1720 else "REVIEW",
            plan_based=True,
        )
        for index in range(2933)
    ]
    semantic.extend(
        semantic_record(2933 + index, "PARSER_PRESERVED", plan_based=False)
        for index in range(1353)
    )

    write_json(
        tmp_path / "catalog_validation_resolution_manifest.json",
        {
            "status": "COMPLETE",
            "snapshot_id": "PLAN1",
            "profile_record_count": 2933,
            "plan_record_count": 2933,
            "automatic_count": 1720,
            "review_count": 1213,
            "error_count": 0,
            "published_current": True,
            "source_artifacts_modified": False,
        },
    )
    write_json(tmp_path / "catalog_validation_resolution_summary.json", {})
    write_json(tmp_path / "catalog_validation_resolution_plan.json", plan)
    (tmp_path / "catalog_validation_resolution_review.csv").write_text(
        "plan_id\n", encoding="utf-8"
    )
    write_json(tmp_path / "catalog_validation_resolution_errors.json", [])

    write_json(
        tmp_path / "semantic_validation_manifest.json",
        {
            "status": "COMPLETE",
            "snapshot_id": "SEM1",
            "ti_source_validation_count": 4019,
            "rule_source_validation_count": 267,
            "ti_semantic_validation_count": 4019,
            "rule_semantic_validation_count": 267,
            "semantic_validation_count": 4286,
            "plan_record_count": 2933,
            "automatic_plan_count": 1720,
            "review_plan_count": 1213,
            "error_count": 0,
            "published_current": True,
            "source_artifacts_modified": False,
        },
    )
    write_json(tmp_path / "semantic_validation_summary.json", {})
    write_json(
        tmp_path / "semantic_ti_relationship_validations.json",
        semantic[:4019],
    )
    write_json(
        tmp_path / "semantic_rule_relationship_validations.json",
        semantic[4019:],
    )
    write_json(tmp_path / "semantic_validation_errors.json", [])

    result = module.check_semantic_resolution_layers(tmp_path)
    assert result["pass"] is True
    assert result["details"]["plan_records"] == 2933
    assert result["details"]["total_semantic_validations"] == 4286


def test_semantic_resolution_gate_fails_when_artifact_missing(tmp_path: Path) -> None:
    result = module.check_semantic_resolution_layers(tmp_path)
    assert result["pass"] is False
    assert "semantic_validation_manifest.json" in result["details"]["missing_files"]
