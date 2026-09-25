from __future__ import annotations

"""Rebuild the governed PAX Explorer catalog from source TM1 metadata.

The wrapper executes collectors and derived builders in dependency order. In
from-scratch mode it backs up data/current, rebuilds into a new current folder,
and restores the previous known-good catalog automatically if any stage fails.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_ROOT = ROOT_DIR / "data"
CURRENT_ROOT = DATA_ROOT / "current"
REVIEW_ROOT = DATA_ROOT / "review"
BACKUP_ROOT = DATA_ROOT / "pipeline_backups"


@dataclass(frozen=True)
class Stage:
    name: str
    module: str
    arguments: tuple[str, ...] = ()
    category: str = "COLLECT"


PIPELINE: tuple[Stage, ...] = (
    Stage("connection_check", "scripts.check_tm1_connection", category="PREFLIGHT"),
    Stage("core_metadata", "scripts.collect_tm1_metadata"),
    Stage("ti_lineage", "scripts.collect_tm1_ti_lineage"),
    Stage("rule_lineage", "scripts.collect_tm1_rule_lineage"),
    Stage("attributes", "scripts.collect_tm1_attributes",("--scope", "all")),
    Stage("hierarchies", "scripts.collect_tm1_hierarchies", ("--scope", "all")),
    Stage("public_subsets", "scripts.collect_tm1_subsets"),
    Stage("public_views", "scripts.collect_tm1_views"),
    Stage(
        "cube_dimensions",
        "scripts.collect_tm1_cube_dimensions",
        ("--scope", "all", "--quiet"),
    ),
    Stage("chore_tasks", "scripts.collect_tm1_chore_tasks"),
    Stage("process_data_sources", "scripts.collect_tm1_process_data_sources"),
    Stage(
        "operational_dependencies",
        "scripts.build_operational_dependencies",
        category="DERIVE",
    ),
    Stage(
        "catalog_match_profile",
        "scripts.profile_tm1_catalog_matches",
        category="DERIVE",
    ),
    Stage(
        "semantic_resolution_plan",
        "scripts.build_catalog_validation_resolution_plan",
        category="DERIVE",
    ),
    Stage(
        "semantic_validations",
        "scripts.build_semantic_relationship_validations",
        category="DERIVE",
    ),
    Stage(
        "unified_graph",
        "scripts.build_unified_graph",
        category="DERIVE",
    ),
    Stage(
        "holistic_acceptance",
        "scripts.run_holistic_acceptance",
        category="ACCEPTANCE",
    ),
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_id(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "=" * 72,
        "PAX EXPLORER FULL CATALOG REBUILD",
        "=" * 72,
        f"Run ID:       {report['run_id']}",
        f"Status:       {report['status']}",
        f"From scratch: {report['from_scratch']}",
        f"Started:      {report['started_at']}",
        f"Completed:    {report['completed_at']}",
        "",
    ]
    for stage in report["stages"]:
        lines.append(
            f"[{stage['status']}] {stage['name']} "
            f"({stage['seconds']:.2f}s, rc={stage['return_code']})"
        )
    lines.extend(
        [
            "",
            f"Failed stage: {report.get('failed_stage') or '<none>'}",
            f"Backup:       {report.get('backup_directory') or '<none>'}",
            f"Restored:     {report['restored_previous_current']}",
            f"Decision:     {report['decision']}",
        ]
    )
    return "\n".join(lines) + "\n"


def stage_arguments(
    stage: Stage,
    *,
    pipeline_run_id: str,
) -> tuple[str, ...]:
    arguments = tuple(stage.arguments)
    if stage.name == "unified_graph":
        arguments += ("--pipeline-run-id", pipeline_run_id)
    return arguments


def stage_command(
    stage: Stage,
    *,
    pipeline_run_id: str,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        stage.module,
        *stage_arguments(stage, pipeline_run_id=pipeline_run_id),
    ]


def run_stage(
    stage: Stage,
    *,
    dry_run: bool,
    pipeline_run_id: str,
) -> dict[str, Any]:
    command = stage_command(stage, pipeline_run_id=pipeline_run_id)
    if dry_run:
        return {
            "name": stage.name,
            "module": stage.module,
            "category": stage.category,
            "command": command,
            "status": "DRY_RUN",
            "return_code": 0,
            "seconds": 0.0,
            "stdout": "",
            "stderr": "",
        }
    started = perf_counter()
    completed = subprocess.run(
        command,
        cwd=ROOT_DIR,
        text=True,
        capture_output=True,
        check=False,
        env=os.environ.copy(),
    )
    seconds = perf_counter() - started
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return {
        "name": stage.name,
        "module": stage.module,
        "category": stage.category,
        "command": command,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "return_code": completed.returncode,
        "seconds": round(seconds, 6),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }

def backup_current(identifier: str) -> Path | None:
    if not CURRENT_ROOT.exists():
        return None
    backup_directory = BACKUP_ROOT / identifier / "current"
    backup_directory.parent.mkdir(parents=True, exist_ok=True)
    if backup_directory.exists():
        raise FileExistsError(f"Backup already exists: {backup_directory}")
    shutil.copytree(CURRENT_ROOT, backup_directory)
    return backup_directory


def reset_current() -> None:
    if CURRENT_ROOT.exists():
        shutil.rmtree(CURRENT_ROOT)
    CURRENT_ROOT.mkdir(parents=True, exist_ok=True)


def restore_current(backup_directory: Path | None) -> None:
    if CURRENT_ROOT.exists():
        shutil.rmtree(CURRENT_ROOT)
    if backup_directory is None:
        CURRENT_ROOT.mkdir(parents=True, exist_ok=True)
        return
    shutil.copytree(backup_directory, CURRENT_ROOT)


def select_stages(
    *,
    skip_connection_check: bool,
    start_at: str | None,
    stop_after: str | None,
) -> list[Stage]:
    selected = list(PIPELINE)
    if skip_connection_check:
        selected = [stage for stage in selected if stage.name != "connection_check"]
    names = [stage.name for stage in selected]
    if start_at:
        if start_at not in names:
            raise ValueError(f"Unknown --start-at stage: {start_at}")
        selected = selected[names.index(start_at) :]
        names = [stage.name for stage in selected]
    if stop_after:
        if stop_after not in names:
            raise ValueError(f"Unknown --stop-after stage: {stop_after}")
        selected = selected[: names.index(stop_after) + 1]
    return selected


def rebuild_catalog(
    *,
    from_scratch: bool,
    skip_connection_check: bool,
    keep_failed_current: bool,
    no_backup: bool,
    dry_run: bool,
    start_at: str | None,
    stop_after: str | None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    started_at = timestamp or utc_now()
    identifier = run_id(started_at)
    selected = select_stages(
        skip_connection_check=skip_connection_check,
        start_at=start_at,
        stop_after=stop_after,
    )

    stage_results: list[dict[str, Any]] = []
    backup_directory: Path | None = None
    failed_stage: str | None = None
    restored = False

    # Always check connectivity before changing current, unless explicitly skipped.
    preflight = next(
        (stage for stage in selected if stage.category == "PREFLIGHT"),
        None,
    )
    remaining = selected
    if preflight is not None:
        print(f"\n>>> {preflight.name}")
        result = run_stage(preflight, dry_run=dry_run, pipeline_run_id=identifier)
        stage_results.append(result)
        if result["return_code"] != 0:
            failed_stage = preflight.name
            remaining = []
        else:
            remaining = [stage for stage in selected if stage is not preflight]

    if failed_stage is None and from_scratch and not dry_run:
        if not no_backup:
            backup_directory = backup_current(identifier)
        reset_current()

    if failed_stage is None:
        for stage in remaining:
            print(f"\n>>> {stage.name}")
            result = run_stage(stage, dry_run=dry_run, pipeline_run_id=identifier)
            stage_results.append(result)
            if result["return_code"] != 0:
                failed_stage = stage.name
                break

    if (
        failed_stage is not None
        and from_scratch
        and not keep_failed_current
        and not dry_run
    ):
        restore_current(backup_directory)
        restored = True

    completed_at = utc_now()
    passed = failed_stage is None
    report = {
        "run_id": identifier,
        "status": "COMPLETE" if passed else "FAILED",
        "decision": "CATALOG_REBUILD_COMPLETE" if passed else "CATALOG_REBUILD_FAILED",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "from_scratch": from_scratch,
        "dry_run": dry_run,
        "selected_stages": [stage.name for stage in selected],
        "stages": stage_results,
        "failed_stage": failed_stage,
        "backup_directory": str(backup_directory) if backup_directory else None,
        "restored_previous_current": restored,
        "keep_failed_current": keep_failed_current,
    }
    REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    write_json(REVIEW_ROOT / "catalog_rebuild_report.json", report)
    (REVIEW_ROOT / "catalog_rebuild_report.txt").write_text(
        render_text(report),
        encoding="utf-8",
    )
    return report


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild all governed PAX Explorer source and derived artifacts."
    )
    parser.add_argument(
        "--from-scratch",
        action="store_true",
        help="Back up and clear data/current before collection.",
    )
    parser.add_argument("--skip-connection-check", action="store_true")
    parser.add_argument("--keep-failed-current", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--start-at",
        choices=[stage.name for stage in PIPELINE],
    )
    parser.add_argument(
        "--stop-after",
        choices=[stage.name for stage in PIPELINE],
    )
    parser.add_argument("--list-stages", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    if args.list_stages:
        for number, stage in enumerate(PIPELINE, start=1):
            print(f"{number:02d}. {stage.name}: {stage.module}")
        return 0
    if args.no_backup and not args.from_scratch:
        print("--no-backup only applies with --from-scratch", file=sys.stderr)
        return 2
    try:
        report = rebuild_catalog(
            from_scratch=args.from_scratch,
            skip_connection_check=args.skip_connection_check,
            keep_failed_current=args.keep_failed_current,
            no_backup=args.no_backup,
            dry_run=args.dry_run,
            start_at=args.start_at,
            stop_after=args.stop_after,
        )
        print("\n" + render_text(report), end="")
        return 0 if report["status"] == "COMPLETE" else 1
    except Exception as error:
        print(f"Catalog rebuild failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
