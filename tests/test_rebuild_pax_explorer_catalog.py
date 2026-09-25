from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts import rebuild_pax_explorer_catalog as module


def when() -> datetime:
    return datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)


def test_pipeline_dependency_order() -> None:
    names = [stage.name for stage in module.PIPELINE]
    assert names.index("core_metadata") < names.index("ti_lineage")
    assert names.index("process_data_sources") < names.index(
        "operational_dependencies"
    )
    assert names.index("catalog_match_profile") < names.index(
        "semantic_resolution_plan"
    )
    assert names.index("semantic_resolution_plan") < names.index(
        "semantic_validations"
    )
    assert names[-3:] == [
        "semantic_validations",
        "unified_graph",
        "holistic_acceptance",
    ]


def test_select_stages_supports_range() -> None:
    stages = module.select_stages(
        skip_connection_check=True,
        start_at="attributes",
        stop_after="public_views",
    )
    assert [stage.name for stage in stages] == [
        "attributes",
        "hierarchies",
        "public_subsets",
        "public_views",
    ]


def test_select_stages_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown"):
        module.select_stages(
            skip_connection_check=True,
            start_at="missing",
            stop_after=None,
        )


def test_dry_run_executes_no_subprocess(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(module, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(module, "CURRENT_ROOT", tmp_path / "data" / "current")
    monkeypatch.setattr(module, "REVIEW_ROOT", tmp_path / "data" / "review")
    monkeypatch.setattr(module, "BACKUP_ROOT", tmp_path / "data" / "pipeline_backups")
    report = module.rebuild_catalog(
        from_scratch=True,
        skip_connection_check=False,
        keep_failed_current=False,
        no_backup=False,
        dry_run=True,
        start_at=None,
        stop_after="core_metadata",
        timestamp=when(),
    )
    assert report["status"] == "COMPLETE"
    assert all(item["status"] == "DRY_RUN" for item in report["stages"])
    assert not module.CURRENT_ROOT.exists()


def test_failure_restores_previous_current(tmp_path: Path, monkeypatch: Any) -> None:
    current = tmp_path / "data" / "current"
    current.mkdir(parents=True)
    (current / "sentinel.json").write_text('{"old": true}', encoding="utf-8")
    monkeypatch.setattr(module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(module, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(module, "CURRENT_ROOT", current)
    monkeypatch.setattr(module, "REVIEW_ROOT", tmp_path / "data" / "review")
    monkeypatch.setattr(module, "BACKUP_ROOT", tmp_path / "data" / "pipeline_backups")

    def fake_run(
        stage: module.Stage,
        *,
        dry_run: bool,
        pipeline_run_id: str,
    ) -> dict[str, Any]:
        assert pipeline_run_id == "20260925T160000Z"
        if stage.category == "PREFLIGHT":
            return {
                "name": stage.name,
                "module": stage.module,
                "category": stage.category,
                "command": [],
                "status": "PASS",
                "return_code": 0,
                "seconds": 0.0,
                "stdout": "",
                "stderr": "",
            }
        (current / "partial.json").write_text("{}", encoding="utf-8")
        return {
            "name": stage.name,
            "module": stage.module,
            "category": stage.category,
            "command": [],
            "status": "FAIL",
            "return_code": 1,
            "seconds": 0.0,
            "stdout": "",
            "stderr": "failure",
        }

    monkeypatch.setattr(module, "run_stage", fake_run)
    report = module.rebuild_catalog(
        from_scratch=True,
        skip_connection_check=False,
        keep_failed_current=False,
        no_backup=False,
        dry_run=False,
        start_at=None,
        stop_after="core_metadata",
        timestamp=when(),
    )
    assert report["status"] == "FAILED"
    assert report["restored_previous_current"] is True
    assert (current / "sentinel.json").is_file()
    assert not (current / "partial.json").exists()


def test_backup_and_restore_round_trip(tmp_path: Path, monkeypatch: Any) -> None:
    current = tmp_path / "current"
    current.mkdir()
    (current / "a.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(module, "CURRENT_ROOT", current)
    monkeypatch.setattr(module, "BACKUP_ROOT", tmp_path / "backups")
    backup = module.backup_current("RUN1")
    module.reset_current()
    assert not (current / "a.json").exists()
    module.restore_current(backup)
    assert (current / "a.json").is_file()


def test_render_text_contains_failed_stage() -> None:
    report = {
        "run_id": "RUN1",
        "status": "FAILED",
        "from_scratch": True,
        "started_at": "start",
        "completed_at": "end",
        "stages": [
            {
                "status": "FAIL",
                "name": "ti_lineage",
                "seconds": 1.2,
                "return_code": 1,
            }
        ],
        "failed_stage": "ti_lineage",
        "backup_directory": "backup",
        "restored_previous_current": True,
        "decision": "CATALOG_REBUILD_FAILED",
    }
    text = module.render_text(report)
    assert "ti_lineage" in text
    assert "Restored:     True" in text


def test_attribute_stage_uses_all_scope() -> None:
    stage = next(
        item
        for item in module.PIPELINE
        if item.name == "attributes"
    )

    assert stage.module == (
        "scripts.collect_tm1_attributes"
    )

    assert stage.arguments == (
        "--scope",
        "all",
    )


def test_unified_graph_stage_uses_active_pipeline_run_id() -> None:
    stage = next(item for item in module.PIPELINE if item.name == "unified_graph")
    assert stage.module == "scripts.build_unified_graph"
    assert stage.category == "DERIVE"
    assert module.stage_arguments(stage, pipeline_run_id="RUN123") == (
        "--pipeline-run-id", "RUN123",
    )
    assert module.stage_command(stage, pipeline_run_id="RUN123")[-2:] == [
        "--pipeline-run-id", "RUN123",
    ]


def test_graph_run_id_is_not_forwarded_to_other_stages() -> None:
    for name in ("semantic_validations", "holistic_acceptance"):
        stage = next(item for item in module.PIPELINE if item.name == name)
        assert "--pipeline-run-id" not in module.stage_command(
            stage, pipeline_run_id="RUN123"
        )
