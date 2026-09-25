from __future__ import annotations

"""Run the complete PAX Explorer implementation acceptance gate.

The checker is intentionally read-only. It recalculates every gate directly
from governed artifacts and never depends on PowerShell session variables.
"""

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Mapping
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
CURRENT_ROOT = ROOT_DIR / "data" / "current"
REVIEW_ROOT = ROOT_DIR / "data" / "review"
EXCEPTIONS_FILE = ROOT_DIR / "config" / "catalog_quality_exceptions.json"

MANIFEST_FILES = (
    "manifest.json",
    "attribute_manifest.json",
    "hierarchy_manifest.json",
    "subset_manifest.json",
    "view_manifest.json",
    "cube_dimension_manifest.json",
    "chore_lineage_manifest.json",
    "data_source_manifest.json",
    "operational_dependency_manifest.json",
    "ti_lineage_manifest.json",
    "rule_lineage_manifest.json",
    "catalog_match_manifest.json",
    "catalog_validation_resolution_manifest.json",
    "semantic_validation_manifest.json",
)

ERROR_FILES = (
    "metadata_errors.json",
    "attribute_collection_errors.json",
    "hierarchy_collection_errors.json",
    "subset_collection_errors.json",
    "view_collection_errors.json",
    "cube_dimension_collection_errors.json",
    "chore_collection_errors.json",
    "data_source_collection_errors.json",
    "operational_dependency_errors.json",
    "catalog_match_errors.json",
    "catalog_validation_resolution_errors.json",
    "semantic_validation_errors.json",
)

RELATIONSHIP_DOMAINS = {
    "TI": ("ti_relationships.json", "ti_relationship_validations.json"),
    "Rule": ("rule_relationships.json", "rule_relationship_validations.json"),
    "Hierarchy": (
        "hierarchy_relationships.json",
        "hierarchy_relationship_validations.json",
    ),
    "Subset": ("subset_relationships.json", "subset_relationship_validations.json"),
    "View": ("view_relationships.json", "view_relationship_validations.json"),
    "CubeDimension": (
        "cube_dimension_relationships.json",
        "cube_dimension_relationship_validations.json",
    ),
    "Chore": ("chore_relationships.json", "chore_relationship_validations.json"),
    "DataSource": (
        "process_data_source_relationships.json",
        "process_data_source_validations.json",
    ),
    "Operational": (
        "operational_relationships.json",
        "operational_relationship_validations.json",
    ),
}

ENTITY_DOMAINS = {
    "Hierarchies": "hierarchies.json",
    "Subsets": "subsets.json",
    "Views": "views.json",
    "ChoreTasks": "chore_tasks.json",
    "DataSources": "external_data_sources.json",
    "Operational": "operational_dependencies.json",
}

EXPECTED_CLASSIFICATIONS = {
    "AMBIGUOUS_MATCH": 1,
    "DEFAULT_HIERARCHY_MATCH": 1625,
    "DYNAMIC_REFERENCE": 557,
    "EXACT_MATCH": 50,
    "EXISTING_CREATE_TARGET": 599,
    "INSUFFICIENT_CONTEXT": 1,
    "MISSING_DELETE_TARGET": 33,
    "TARGET_NOT_IN_CATALOG": 22,
    "VALID_CREATE_TARGET": 45,
}

SENSITIVE_FILES = (
    "external_data_sources.json",
    "process_data_sources.json",
    "process_data_source_relationships.json",
    "operational_dependencies.json",
    "operational_relationships.json",
    "chore_task_bindings.json",
)

SECRET_PATTERNS = (
    re.compile(r"password\s*=\s*(?!<redacted>)[^;,\s\"]+", re.I),
    re.compile(r"passwd\s*=\s*(?!<redacted>)[^;,\s\"]+", re.I),
    re.compile(r"pwd\s*=\s*(?!<redacted>)[^;,\s\"]+", re.I),
    re.compile(r"token\s*=\s*(?!<redacted>)[^;,\s\"]+", re.I),
    re.compile(r"api[_-]?key\s*=\s*(?!<redacted>)[^;,\s\"]+", re.I),
    re.compile(r"authorization\s*=\s*(?!<redacted>)[^;,\s\"]+", re.I),
    re.compile(r"bearer\s+[A-Za-z0-9._~+/-]+=*", re.I),
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def as_records(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    if not isinstance(payload, list):
        raise TypeError(f"{path.name} must contain a JSON array")
    if any(not isinstance(item, dict) for item in payload):
        raise TypeError(f"{path.name} contains non-object records")
    return payload


def gate(name: str, passed: bool, **details: Any) -> dict[str, Any]:
    return {"gate": name, "pass": bool(passed), "details": details}


def check_manifests(root: Path) -> dict[str, Any]:
    rows = []
    for name in MANIFEST_FILES:
        path = root / name
        if not path.is_file():
            rows.append({"file": name, "exists": False, "pass": False})
            continue
        payload = read_json(path)
        error_count = int(payload.get("error_count", 0) or 0)
        published = payload.get("published_current", "IMPLICIT")
        passed = (
            payload.get("status") == "COMPLETE"
            and error_count == 0
            and (published == "IMPLICIT" or published is True)
        )
        rows.append(
            {
                "file": name,
                "exists": True,
                "status": payload.get("status"),
                "error_count": error_count,
                "published_current": published,
                "snapshot_id": payload.get("snapshot_id"),
                "pass": passed,
            }
        )
    return gate("manifests", all(row["pass"] for row in rows), rows=rows)


def check_error_files(root: Path) -> dict[str, Any]:
    rows = []
    for name in ERROR_FILES:
        path = root / name
        if not path.is_file():
            rows.append({"file": name, "exists": False, "pass": False})
            continue
        count = len(as_records(path))
        rows.append({"file": name, "exists": True, "error_count": count, "pass": count == 0})
    return gate("collection_errors", all(row["pass"] for row in rows), rows=rows)


def check_relationships(root: Path) -> dict[str, Any]:
    rows = []
    for domain, (relationship_file, validation_file) in RELATIONSHIP_DOMAINS.items():
        rp, vp = root / relationship_file, root / validation_file
        if not rp.is_file() or not vp.is_file():
            rows.append({"domain": domain, "pass": False, "reason": "missing file"})
            continue
        relationships, validations = as_records(rp), as_records(vp)
        rids = [str(item.get("relationship_id") or "") for item in relationships]
        vids = [str(item.get("relationship_id") or "") for item in validations]
        id_coverage = all(rids) and all(vids)
        duplicate_rids = len(rids) - len(set(rids)) if id_coverage else 0
        duplicate_vids = len(vids) - len(set(vids)) if id_coverage else 0
        missing = len(set(rids) - set(vids)) if id_coverage else 0
        orphan = len(set(vids) - set(rids)) if id_coverage else 0
        passed = (
            len(relationships) == len(validations)
            and duplicate_rids == 0
            and duplicate_vids == 0
            and missing == 0
            and orphan == 0
        )
        rows.append(
            {
                "domain": domain,
                "relationships": len(relationships),
                "validations": len(validations),
                "id_coverage_checked": id_coverage,
                "duplicate_relationship_ids": duplicate_rids,
                "duplicate_validation_ids": duplicate_vids,
                "missing_validations": missing,
                "orphan_validations": orphan,
                "pass": passed,
            }
        )
    return gate("relationship_validation", all(row["pass"] for row in rows), rows=rows)


def check_entities(root: Path) -> dict[str, Any]:
    rows = []
    for domain, name in ENTITY_DOMAINS.items():
        path = root / name
        if not path.is_file():
            rows.append({"domain": domain, "pass": False, "reason": "missing file"})
            continue
        records = as_records(path)
        ids = [str(item.get("node_id") or "") for item in records]
        missing = sum(not value for value in ids)
        duplicates = len([name for name, count in Counter(ids).items() if name and count > 1])
        rows.append(
            {
                "domain": domain,
                "records": len(records),
                "missing_node_ids": missing,
                "duplicate_node_ids": duplicates,
                "pass": missing == 0 and duplicates == 0,
            }
        )
    return gate("entity_identity", all(row["pass"] for row in rows), rows=rows)


def count_relationship(records: list[dict[str, Any]], rel_type: str) -> int:
    return sum(item.get("relationship_type") == rel_type for item in records)


def check_structural(root: Path) -> dict[str, Any]:
    hierarchies = as_records(root / "hierarchies.json")
    hierarchy_relationships = as_records(root / "hierarchy_relationships.json")
    subsets = as_records(root / "subsets.json")
    subset_relationships = as_records(root / "subset_relationships.json")
    views = as_records(root / "views.json")
    view_relationships = as_records(root / "view_relationships.json")
    tasks = as_records(root / "chore_tasks.json")
    bindings = as_records(root / "chore_task_bindings.json")
    chore_relationships = as_records(root / "chore_relationships.json")
    cube_dimensions = as_records(root / "cube_dimension_relationships.json")
    details = {
        "hierarchies": len(hierarchies),
        "hierarchy_parents": count_relationship(hierarchy_relationships, "BELONGS_TO_DIMENSION"),
        "subsets": len(subsets),
        "subset_hierarchy_parents": count_relationship(subset_relationships, "BELONGS_TO_HIERARCHY"),
        "subset_dimension_parents": count_relationship(subset_relationships, "BELONGS_TO_DIMENSION"),
        "views": len(views),
        "view_cube_parents": count_relationship(view_relationships, "BELONGS_TO_CUBE"),
        "chore_tasks": len(tasks),
        "chore_task_parents": count_relationship(chore_relationships, "HAS_TASK"),
        "chore_process_calls": count_relationship(chore_relationships, "CALLS_PROCESS"),
        "chore_bindings": len(bindings),
        "chore_parameter_edges": count_relationship(chore_relationships, "PASSES_PARAMETER"),
        "cube_dimension_edges": len(cube_dimensions),
    }
    passed = (
        details["hierarchies"] == details["hierarchy_parents"] == 927
        and details["subsets"] == details["subset_hierarchy_parents"] == details["subset_dimension_parents"] == 10201
        and details["views"] == details["view_cube_parents"] == 2059
        and details["chore_tasks"] == details["chore_task_parents"] == details["chore_process_calls"] == 67
        and details["chore_bindings"] == details["chore_parameter_edges"] == 148
        and details["cube_dimension_edges"] == 1154
    )
    return gate("structural_invariants", passed, **details)


def check_cube_ordering(root: Path) -> dict[str, Any]:
    records = as_records(root / "cube_dimension_relationships.json")
    by_cube: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_cube[str(record.get("source_id"))].append(record)
    duplicate_positions = 0
    duplicate_dimensions = 0
    continuity_problems = 0
    for group in by_cube.values():
        positions = [int(item["dimension_position"]) for item in group]
        targets = [str(item["target_id"]) for item in group]
        duplicate_positions += len(positions) - len(set(positions))
        duplicate_dimensions += len(targets) - len(set(targets))
        if sorted(positions) != list(range(1, len(positions) + 1)):
            continuity_problems += 1
    passed = (
        len(records) == 1154
        and len(by_cube) == 379
        and duplicate_positions == 0
        and duplicate_dimensions == 0
        and continuity_problems == 0
    )
    return gate(
        "cube_dimension_ordering",
        passed,
        relationships=len(records),
        cubes_covered=len(by_cube),
        duplicate_positions=duplicate_positions,
        duplicate_dimensions=duplicate_dimensions,
        continuity_problems=continuity_problems,
    )


def check_data_source_and_profile(root: Path) -> dict[str, Any]:
    data_source = read_json(root / "data_source_manifest.json")
    profile_manifest = read_json(root / "catalog_match_manifest.json")
    detail = as_records(root / "catalog_match_profile_detail.json")
    actual = Counter(str(item.get("match_classification")) for item in detail)
    classification_pass = dict(actual) == EXPECTED_CLASSIFICATIONS
    data_source_pass = (
        data_source.get("status") == "COMPLETE"
        and int(data_source.get("process_count", 0)) == 386
        and int(data_source.get("configured_process_count", 0)) == 280
        and int(data_source.get("data_source_count", 0)) == 107
        and int(data_source.get("relationship_count", 0)) == 375
        and int(data_source.get("validation_count", 0)) == 375
        and int(data_source.get("error_count", 0)) == 0
        and data_source.get("published_current") is True
    )
    profile_pass = (
        profile_manifest.get("status") == "COMPLETE"
        and int(profile_manifest.get("deferred_reference_count", 0)) == 2933
        and int(profile_manifest.get("review_queue_count", 0)) == 1213
        and int(profile_manifest.get("error_count", 0)) == 0
        and profile_manifest.get("published_current") is True
        and len(detail) == 2933
        and classification_pass
    )
    return gate(
        "data_source_and_catalog_profile",
        data_source_pass and profile_pass,
        data_source_pass=data_source_pass,
        profile_pass=profile_pass,
        actual_classifications=dict(sorted(actual.items())),
        expected_classifications=EXPECTED_CLASSIFICATIONS,
    )


def check_semantic_resolution_layers(root: Path) -> dict[str, Any]:
    """Validate the governed resolution-plan and semantic-validation layers."""

    required_files = (
        "catalog_validation_resolution_manifest.json",
        "catalog_validation_resolution_summary.json",
        "catalog_validation_resolution_plan.json",
        "catalog_validation_resolution_review.csv",
        "catalog_validation_resolution_errors.json",
        "semantic_validation_manifest.json",
        "semantic_validation_summary.json",
        "semantic_ti_relationship_validations.json",
        "semantic_rule_relationship_validations.json",
        "semantic_validation_errors.json",
    )
    missing_files = [
        name for name in required_files if not (root / name).is_file()
    ]
    if missing_files:
        return gate(
            "semantic_resolution_layers",
            False,
            missing_files=missing_files,
        )

    plan_manifest = read_json(
        root / "catalog_validation_resolution_manifest.json"
    )
    semantic_manifest = read_json(root / "semantic_validation_manifest.json")
    plan = as_records(root / "catalog_validation_resolution_plan.json")
    plan_errors = as_records(
        root / "catalog_validation_resolution_errors.json"
    )
    semantic_ti = as_records(
        root / "semantic_ti_relationship_validations.json"
    )
    semantic_rule = as_records(
        root / "semantic_rule_relationship_validations.json"
    )
    semantic_errors = as_records(root / "semantic_validation_errors.json")

    if not isinstance(plan_manifest, Mapping):
        raise TypeError(
            "catalog_validation_resolution_manifest.json must be an object"
        )
    if not isinstance(semantic_manifest, Mapping):
        raise TypeError("semantic_validation_manifest.json must be an object")

    automatic_count = sum(
        item.get("decision_type") == "AUTOMATIC" for item in plan
    )
    review_count = sum(item.get("decision_type") == "REVIEW" for item in plan)
    duplicate_plan_ids = len(plan) - len(
        {str(item.get("plan_id") or "") for item in plan}
    )

    all_semantic = semantic_ti + semantic_rule
    duplicate_semantic_ids = len(all_semantic) - len(
        {
            str(item.get("semantic_validation_id") or "")
            for item in all_semantic
        }
    )
    plan_based_semantic = [
        item for item in all_semantic if str(item.get("plan_id") or "")
    ]
    unique_applied_plan_ids = {
        str(item.get("plan_id")) for item in plan_based_semantic
    }
    semantic_decision_counts = Counter(
        str(item.get("decision_type") or "") for item in all_semantic
    )

    plan_pass = (
        plan_manifest.get("status") == "COMPLETE"
        and int(plan_manifest.get("profile_record_count", 0)) == 2933
        and int(plan_manifest.get("plan_record_count", 0)) == 2933
        and int(plan_manifest.get("automatic_count", 0)) == 1720
        and int(plan_manifest.get("review_count", 0)) == 1213
        and int(plan_manifest.get("error_count", 0)) == 0
        and plan_manifest.get("published_current") is True
        and plan_manifest.get("source_artifacts_modified") is False
        and len(plan) == 2933
        and automatic_count == 1720
        and review_count == 1213
        and duplicate_plan_ids == 0
        and not plan_errors
    )

    semantic_pass = (
        semantic_manifest.get("status") == "COMPLETE"
        and int(semantic_manifest.get("ti_source_validation_count", 0)) == 4019
        and int(semantic_manifest.get("rule_source_validation_count", 0)) == 267
        and int(semantic_manifest.get("ti_semantic_validation_count", 0)) == 4019
        and int(semantic_manifest.get("rule_semantic_validation_count", 0)) == 267
        and int(semantic_manifest.get("semantic_validation_count", 0)) == 4286
        and int(semantic_manifest.get("plan_record_count", 0)) == 2933
        and int(semantic_manifest.get("automatic_plan_count", 0)) == 1720
        and int(semantic_manifest.get("review_plan_count", 0)) == 1213
        and int(semantic_manifest.get("error_count", 0)) == 0
        and semantic_manifest.get("published_current") is True
        and semantic_manifest.get("source_artifacts_modified") is False
        and len(semantic_ti) == 4019
        and len(semantic_rule) == 267
        and len(all_semantic) == 4286
        and len(plan_based_semantic) == 2933
        and len(unique_applied_plan_ids) == 2933
        and semantic_decision_counts.get("AUTOMATIC", 0) == 1720
        and semantic_decision_counts.get("REVIEW", 0) == 1213
        and semantic_decision_counts.get("PARSER_PRESERVED", 0) == 1353
        and duplicate_semantic_ids == 0
        and not semantic_errors
    )

    return gate(
        "semantic_resolution_layers",
        plan_pass and semantic_pass,
        plan_pass=plan_pass,
        semantic_pass=semantic_pass,
        resolution_plan_snapshot_id=plan_manifest.get("snapshot_id"),
        semantic_validation_snapshot_id=semantic_manifest.get("snapshot_id"),
        plan_records=len(plan),
        automatic_plan_records=automatic_count,
        review_plan_records=review_count,
        duplicate_plan_ids=duplicate_plan_ids,
        plan_error_count=len(plan_errors),
        ti_semantic_validations=len(semantic_ti),
        rule_semantic_validations=len(semantic_rule),
        total_semantic_validations=len(all_semantic),
        plan_based_semantic_validations=len(plan_based_semantic),
        unique_applied_plan_ids=len(unique_applied_plan_ids),
        semantic_decision_counts=dict(sorted(semantic_decision_counts.items())),
        duplicate_semantic_validation_ids=duplicate_semantic_ids,
        semantic_error_count=len(semantic_errors),
        source_artifacts_modified=(
            bool(plan_manifest.get("source_artifacts_modified"))
            or bool(semantic_manifest.get("source_artifacts_modified"))
        ),
    )


def check_sensitive_data(root: Path) -> dict[str, Any]:
    findings = []
    for name in SENSITIVE_FILES:
        path = root / name
        if not path.is_file():
            findings.append({"file": name, "finding": "FILE_NOT_FOUND"})
            continue
        text = path.read_text(encoding="utf-8-sig")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append({"file": name, "finding": "POTENTIAL_SECRET", "pattern": pattern.pattern})
    unredacted_sources = 0
    unredacted_operational = 0
    for item in as_records(root / "external_data_sources.json"):
        if item.get("credential_present") is True and item.get("credential_redacted") is not True:
            unredacted_sources += 1
    for item in as_records(root / "operational_dependencies.json"):
        if item.get("credential_present") is True and item.get("credential_redacted") is not True:
            unredacted_operational += 1
    passed = not findings and unredacted_sources == 0 and unredacted_operational == 0
    return gate(
        "sensitive_data",
        passed,
        findings=findings,
        unredacted_data_sources=unredacted_sources,
        unredacted_operational_dependencies=unredacted_operational,
    )


def run_command(command: list[str], cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "return_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "pass": completed.returncode == 0,
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "=" * 72,
        "PAX EXPLORER HOLISTIC ACCEPTANCE",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Catalog:   {report['catalog_directory']}",
        "",
    ]
    for item in report["gates"]:
        lines.append(f"[{ 'PASS' if item['pass'] else 'FAIL' }] {item['gate']}")
    lines.extend(
        [
            "",
            f"Failures: {report['failure_count']}",
            f"Warnings: {report['warning_count']}",
            f"Decision: {report['decision']}",
        ]
    )
    return "\n".join(lines) + "\n"


def run_acceptance(
    current_root: Path,
    review_root: Path,
    *,
    run_tests: bool,
    run_legacy_smoke: bool,
    exceptions_file: Path,
) -> dict[str, Any]:
    gates: list[dict[str, Any]] = []
    if run_tests:
        result = run_command([sys.executable, "-m", "pytest", "-q"], ROOT_DIR)
        gates.append(gate("regression_tests", result["pass"], **result))
    if run_legacy_smoke:
        result = run_command(
            [
                sys.executable,
                str(ROOT_DIR / "holistic_smoke_test.py"),
                str(current_root),
                "--exceptions",
                str(exceptions_file),
            ],
            ROOT_DIR,
        )
        warning_count = result["stdout"].count("WARNING:")
        gates.append(gate("legacy_holistic_smoke", result["pass"], warning_count=warning_count, **result))

    checks = (
        check_manifests,
        check_error_files,
        check_relationships,
        check_entities,
        check_structural,
        check_cube_ordering,
        check_data_source_and_profile,
        check_semantic_resolution_layers,
        check_sensitive_data,
    )
    for check in checks:
        try:
            gates.append(check(current_root))
        except Exception as error:
            gates.append(gate(check.__name__, False, error=f"{type(error).__name__}: {error}"))

    failures = [item for item in gates if not item["pass"]]
    warning_count = sum(int(item.get("details", {}).get("warning_count", 0) or 0) for item in gates)
    report = {
        "generated_at": now_utc().isoformat(),
        "catalog_directory": str(current_root.resolve()),
        "gates": gates,
        "failure_count": len(failures),
        "warning_count": warning_count,
        "overall_pass": not failures,
        "decision": "READY_FOR_SEMANTIC_RESOLUTION" if not failures else "BLOCKED_PENDING_REVIEW",
    }
    review_root.mkdir(parents=True, exist_ok=True)
    write_json(review_root / "holistic_acceptance_report.json", report)
    (review_root / "holistic_acceptance_report.txt").write_text(render_text(report), encoding="utf-8")
    return report


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run all PAX Explorer implementation acceptance gates.")
    parser.add_argument("--current-root", type=Path, default=CURRENT_ROOT)
    parser.add_argument("--review-root", type=Path, default=REVIEW_ROOT)
    parser.add_argument("--exceptions", type=Path, default=EXCEPTIONS_FILE)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-legacy-smoke", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    report = run_acceptance(
        args.current_root,
        args.review_root,
        run_tests=not args.skip_tests,
        run_legacy_smoke=not args.skip_legacy_smoke,
        exceptions_file=args.exceptions,
    )
    print(render_text(report), end="")
    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
