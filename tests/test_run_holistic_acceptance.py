from __future__ import annotations

from pathlib import Path

import scripts.run_holistic_acceptance as module


def test_gate_normalizes_boolean() -> None:
    assert module.gate("x", True)["pass"] is True
    assert module.gate("x", False)["pass"] is False


def test_render_text_reports_decision() -> None:
    report = {
        "generated_at": "2026-09-25T00:00:00+00:00",
        "catalog_directory": "data/current",
        "gates": [{"gate": "manifests", "pass": True, "details": {}}],
        "failure_count": 0,
        "warning_count": 1,
        "decision": "READY_FOR_SEMANTIC_RESOLUTION",
    }
    text = module.render_text(report)
    assert "[PASS] manifests" in text
    assert "Warnings: 1" in text
    assert "READY_FOR_SEMANTIC_RESOLUTION" in text


def test_write_json_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    module.write_json(path, {"pass": True})
    assert module.read_json(path) == {"pass": True}
    assert not path.with_suffix(".json.tmp").exists()


def test_sensitive_data_passes_clean_files(tmp_path: Path) -> None:
    for name in module.SENSITIVE_FILES:
        module.write_json(tmp_path / name, [])
    result = module.check_sensitive_data(tmp_path)
    assert result["pass"] is True


def test_sensitive_data_detects_unredacted_command(tmp_path: Path) -> None:
    for name in module.SENSITIVE_FILES:
        module.write_json(tmp_path / name, [])
    (tmp_path / "operational_dependencies.json").write_text(
        '[{"credential_present": true, "credential_redacted": false, '
        '"sanitized_expression": "tool password=secret"}]',
        encoding="utf-8",
    )
    result = module.check_sensitive_data(tmp_path)
    assert result["pass"] is False
    assert result["details"]["findings"]
    assert result["details"]["unredacted_operational_dependencies"] == 1

def write_data_source_gate_inputs(
    root: Path,
    *,
    relationship_count: int = 453,
    validation_count: int = 453,
) -> None:
    module.write_json(
        root / "data_source_manifest.json",
        {
            "status": "COMPLETE",
            "process_count": 386,
            "configured_process_count": 280,
            "data_source_count": 107,
            "relationship_count": (
                relationship_count
            ),
            "validation_count": (
                validation_count
            ),
            "error_count": 0,
            "published_current": True,
        },
    )

    module.write_json(
        root / "catalog_match_manifest.json",
        {
            "status": "COMPLETE",
            "deferred_reference_count": 2933,
            "review_queue_count": 1213,
            "error_count": 0,
            "published_current": True,
        },
    )

    detail: list[dict[str, str]] = []

    for classification, count in (
        module.EXPECTED_CLASSIFICATIONS.items()
    ):
        detail.extend(
            {
                "match_classification": (
                    classification
                )
            }
            for _ in range(count)
        )

    module.write_json(
        root / "catalog_match_profile_detail.json",
        detail,
    )

def test_data_source_profile_gate_accepts_refactored_contract(
    tmp_path: Path,
) -> None:
    write_data_source_gate_inputs(
        tmp_path
    )

    result = (
        module.check_data_source_and_profile(
            tmp_path
        )
    )

    assert result["pass"] is True

    assert (
        result["details"][
            "data_source_pass"
        ]
        is True
    )

    assert (
        result["details"][
            "profile_pass"
        ]
        is True
    )

def test_data_source_profile_gate_rejects_validation_mismatch(
    tmp_path: Path,
) -> None:
    write_data_source_gate_inputs(
        tmp_path,
        relationship_count=453,
        validation_count=452,
    )

    result = (
        module.check_data_source_and_profile(
            tmp_path
        )
    )

    assert result["pass"] is False

    assert (
        result["details"][
            "data_source_pass"
        ]
        is False
    )

    assert (
        result["details"][
            "profile_pass"
        ]
        is True
    )
