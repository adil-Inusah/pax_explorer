from __future__ import annotations

"""Build derived semantic TI and rule validation catalogs.

The builder is non-destructive. Original parser validation files remain
unchanged. Deferred parser validations are represented by the governed
semantic resolution plan, while all non-deferred parser validations are
carried into the derived semantic layer.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

CURRENT_ROOT = ROOT_DIR / "data" / "current"
SNAPSHOT_ROOT = ROOT_DIR / "data" / "snapshots"

PLAN_FILE = CURRENT_ROOT / "catalog_validation_resolution_plan.json"
PLAN_MANIFEST_FILE = CURRENT_ROOT / "catalog_validation_resolution_manifest.json"
TI_VALIDATIONS_FILE = CURRENT_ROOT / "ti_relationship_validations.json"
RULE_VALIDATIONS_FILE = CURRENT_ROOT / "rule_relationship_validations.json"
TI_MANIFEST_FILE = CURRENT_ROOT / "ti_lineage_manifest.json"
RULE_MANIFEST_FILE = CURRENT_ROOT / "rule_lineage_manifest.json"

DEFERRED_STATUSES = {
    "ATTRIBUTE_CATALOG_DEFERRED",
    "HIERARCHY_CATALOG_DEFERRED",
    "RUNTIME_SUBSET_REFERENCE",
    "RUNTIME_VIEW_REFERENCE",
}

EXPECTED_COUNTS = {"TI": 4019, "RULE": 267}
EXPECTED_PLAN_COUNT = 2933
EXPECTED_AUTOMATIC_COUNT = 1720
EXPECTED_REVIEW_COUNT = 1213


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def snapshot_id(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def clean(value: Any) -> str:
    return str(value or "").strip()


def token(value: Any) -> str:
    return clean(value).upper().replace(" ", "_")


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


def records(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    if not isinstance(payload, list):
        raise TypeError(f"{path.name} must contain a JSON array.")
    if any(not isinstance(item, Mapping) for item in payload):
        raise TypeError(f"{path.name} contains a non-object record.")
    return [dict(item) for item in payload]


def manifest_snapshot(path: Path) -> str | None:
    if not path.is_file():
        return None
    payload = read_json(path)
    if not isinstance(payload, Mapping):
        return None
    value = clean(payload.get("snapshot_id"))
    return value or None


def validation_status(record: Mapping[str, Any]) -> str:
    for name in (
        "validation_status",
        "status",
        "resolution_status",
        "result",
    ):
        value = record.get(name)
        if value is not None and clean(value):
            return token(value)
    return ""


def stable_id(prefix: str, *parts: Any) -> str:
    identity = "::".join(clean(part).casefold() for part in parts)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"{prefix}::{digest}"


def copy_non_deferred_validation(
    source: Mapping[str, Any],
    *,
    origin: str,
    index: int,
    run_id: str,
    source_snapshot_id: str | None,
) -> dict[str, Any]:
    status = validation_status(source)
    semantic_id = stable_id(
        "semantic-validation",
        origin,
        "parser",
        index,
        source.get("relationship_id"),
        source.get("source_name"),
        source.get("process_name"),
        source.get("cube_name"),
        source.get("relationship_type"),
        source.get("target_type"),
        source.get("target_name"),
        status,
    )
    return {
        "snapshot_id": run_id,
        "semantic_validation_id": semantic_id,
        "origin": origin,
        "relationship_id": source.get("relationship_id"),
        "relationship_type": source.get("relationship_type"),
        "domain": source.get("target_type"),
        "original_validation_status": status,
        "semantic_validation_status": status,
        "decision_type": "PARSER_PRESERVED",
        "resolution_method": "ORIGINAL_PARSER_VALIDATION",
        "resolved_target_node_id": source.get("resolved_target_node_id"),
        "candidate_node_ids": [],
        "review_required": False,
        "review_reason": None,
        "plan_id": None,
        "profiler_classification": None,
        "source_snapshot_id": source_snapshot_id,
        "resolution_plan_snapshot_id": None,
        "source_validation_index": index,
        "source_validation": dict(source),
    }


def semantic_validation_from_plan(
    plan: Mapping[str, Any],
    *,
    run_id: str,
    plan_snapshot_id: str,
) -> dict[str, Any]:
    origin = token(plan.get("origin"))
    relationship_id = clean(plan.get("relationship_id"))
    plan_id = clean(plan.get("plan_id"))
    proposed_status = token(plan.get("proposed_validation_status"))
    decision_type = token(plan.get("decision_type"))
    if not origin or not relationship_id or not plan_id or not proposed_status:
        raise ValueError(
            "Plan record must contain origin, relationship_id, plan_id, and "
            "proposed_validation_status."
        )
    if decision_type not in {"AUTOMATIC", "REVIEW"}:
        raise ValueError(f"Unsupported decision_type: {decision_type}")
    candidate_values = plan.get("candidate_node_ids", [])
    if not isinstance(candidate_values, list):
        raise TypeError("candidate_node_ids must be a JSON array.")
    candidates = [clean(value) for value in candidate_values if clean(value)]
    return {
        "snapshot_id": run_id,
        "semantic_validation_id": stable_id(
            "semantic-validation",
            origin,
            "plan",
            plan_id,
        ),
        "origin": origin,
        "relationship_id": relationship_id,
        "relationship_type": token(plan.get("relationship_type")),
        "domain": token(plan.get("domain")),
        "original_validation_status": token(
            plan.get("original_validation_status")
        ),
        "semantic_validation_status": proposed_status,
        "decision_type": decision_type,
        "resolution_method": token(plan.get("resolution_method")),
        "resolved_target_node_id": plan.get("resolved_target_node_id"),
        "candidate_node_ids": candidates,
        "review_required": decision_type == "REVIEW",
        "review_reason": plan.get("review_reason"),
        "plan_id": plan_id,
        "profiler_classification": token(
            plan.get("profiler_classification")
        ),
        "source_snapshot_id": plan.get("source_snapshot_id"),
        "resolution_plan_snapshot_id": plan_snapshot_id,
        "semantic_group_position": int(
            plan.get("semantic_group_position", 1) or 1
        ),
        "semantic_group_size": int(plan.get("semantic_group_size", 1) or 1),
        "target_name": plan.get("target_name"),
        "cube_name": plan.get("cube_name"),
        "dimension_name": plan.get("dimension_name"),
        "hierarchy_name": plan.get("hierarchy_name"),
        "attribute_name": plan.get("attribute_name"),
        "subset_name": plan.get("subset_name"),
        "view_name": plan.get("view_name"),
    }


def build_semantic_relationship_validations(
    *,
    plan_path: Path = PLAN_FILE,
    plan_manifest_path: Path = PLAN_MANIFEST_FILE,
    ti_validations_path: Path = TI_VALIDATIONS_FILE,
    rule_validations_path: Path = RULE_VALIDATIONS_FILE,
    ti_manifest_path: Path = TI_MANIFEST_FILE,
    rule_manifest_path: Path = RULE_MANIFEST_FILE,
    snapshot_root: Path = SNAPSHOT_ROOT,
    current_root: Path = CURRENT_ROOT,
    timestamp: datetime | None = None,
    publish_current: bool = True,
) -> dict[str, Any]:
    started = timestamp or utc_now()
    run_id = snapshot_id(started)
    snapshot_dir = snapshot_root / run_id

    plan_manifest = read_json(plan_manifest_path)
    if not isinstance(plan_manifest, Mapping):
        raise TypeError("Resolution-plan manifest must contain a JSON object.")
    if plan_manifest.get("status") != "COMPLETE":
        raise ValueError("Resolution plan must be COMPLETE before application.")
    if plan_manifest.get("published_current") is not True:
        raise ValueError("Resolution plan must be published before application.")
    if plan_manifest.get("source_artifacts_modified") is not False:
        raise ValueError("Resolution plan must be non-destructive.")

    plan_snapshot = clean(plan_manifest.get("snapshot_id"))
    plan_records = records(plan_path)
    ti_source = records(ti_validations_path)
    rule_source = records(rule_validations_path)
    source_by_origin = {"TI": ti_source, "RULE": rule_source}
    source_snapshots = {
        "TI": manifest_snapshot(ti_manifest_path),
        "RULE": manifest_snapshot(rule_manifest_path),
    }

    semantic_by_origin: dict[str, list[dict[str, Any]]] = {
        "TI": [],
        "RULE": [],
    }
    errors: list[dict[str, Any]] = []

    # Preserve parser-final validations and omit only the four deferred classes.
    non_deferred_counts: dict[str, int] = {}
    deferred_source_counts: dict[str, int] = {}
    for origin, source_records in source_by_origin.items():
        non_deferred = 0
        deferred = 0
        for index, source in enumerate(source_records, start=1):
            status = validation_status(source)
            if status in DEFERRED_STATUSES:
                deferred += 1
                continue
            non_deferred += 1
            semantic_by_origin[origin].append(
                copy_non_deferred_validation(
                    source,
                    origin=origin,
                    index=index,
                    run_id=run_id,
                    source_snapshot_id=source_snapshots[origin],
                )
            )
        non_deferred_counts[origin] = non_deferred
        deferred_source_counts[origin] = deferred

    plan_origin_counts: Counter[str] = Counter()
    automatic_count = 0
    review_count = 0
    for index, plan in enumerate(plan_records, start=1):
        try:
            semantic = semantic_validation_from_plan(
                plan,
                run_id=run_id,
                plan_snapshot_id=plan_snapshot,
            )
            origin = semantic["origin"]
            if origin not in semantic_by_origin:
                raise ValueError(f"Unsupported plan origin: {origin}")
            semantic_by_origin[origin].append(semantic)
            plan_origin_counts[origin] += 1
            if semantic["decision_type"] == "AUTOMATIC":
                automatic_count += 1
            else:
                review_count += 1
        except Exception as error:
            errors.append(
                {
                    "stage": "BUILD_FROM_PLAN",
                    "plan_record_index": index,
                    "plan_id": plan.get("plan_id"),
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    for origin in semantic_by_origin:
        semantic_by_origin[origin].sort(
            key=lambda item: item["semantic_validation_id"]
        )

    integrity_errors: list[str] = []
    if len(plan_records) != EXPECTED_PLAN_COUNT:
        integrity_errors.append(
            f"Expected {EXPECTED_PLAN_COUNT} plan records; found {len(plan_records)}."
        )
    if automatic_count != EXPECTED_AUTOMATIC_COUNT:
        integrity_errors.append(
            f"Expected {EXPECTED_AUTOMATIC_COUNT} automatic decisions; "
            f"found {automatic_count}."
        )
    if review_count != EXPECTED_REVIEW_COUNT:
        integrity_errors.append(
            f"Expected {EXPECTED_REVIEW_COUNT} review decisions; found {review_count}."
        )

    for origin, expected in EXPECTED_COUNTS.items():
        actual = len(semantic_by_origin[origin])
        if actual != expected:
            integrity_errors.append(
                f"{origin} semantic validation count expected {expected}; found {actual}."
            )
        if plan_origin_counts[origin] != deferred_source_counts[origin]:
            integrity_errors.append(
                f"{origin} plan count {plan_origin_counts[origin]} does not equal "
                f"deferred source count {deferred_source_counts[origin]}."
            )

    all_semantic = semantic_by_origin["TI"] + semantic_by_origin["RULE"]
    duplicate_ids = len(all_semantic) - len(
        {item["semantic_validation_id"] for item in all_semantic}
    )
    duplicate_plan_ids = len(
        [item for item in all_semantic if item.get("plan_id")]
    ) - len(
        {item["plan_id"] for item in all_semantic if item.get("plan_id")}
    )
    if duplicate_ids:
        integrity_errors.append("Duplicate semantic validation IDs detected.")
    if duplicate_plan_ids:
        integrity_errors.append("Duplicate plan IDs detected in semantic outputs.")

    for message in integrity_errors:
        errors.append({"stage": "SEMANTIC_INTEGRITY", "error": message})

    status_counts = Counter(
        item["semantic_validation_status"] for item in all_semantic
    )
    decision_counts = Counter(item["decision_type"] for item in all_semantic)
    review_required_count = sum(
        item["review_required"] is True for item in all_semantic
    )
    status = "PARTIAL" if errors else "COMPLETE"

    summary = {
        "snapshot_id": run_id,
        "ti_source_validation_count": len(ti_source),
        "rule_source_validation_count": len(rule_source),
        "ti_semantic_validation_count": len(semantic_by_origin["TI"]),
        "rule_semantic_validation_count": len(semantic_by_origin["RULE"]),
        "semantic_validation_count": len(all_semantic),
        "plan_record_count": len(plan_records),
        "automatic_plan_count": automatic_count,
        "review_plan_count": review_count,
        "review_required_count": review_required_count,
        "non_deferred_counts": non_deferred_counts,
        "deferred_source_counts": deferred_source_counts,
        "plan_origin_counts": dict(sorted(plan_origin_counts.items())),
        "semantic_status_counts": dict(sorted(status_counts.items())),
        "decision_counts": dict(sorted(decision_counts.items())),
        "duplicate_semantic_validation_id_count": duplicate_ids,
        "duplicate_plan_id_count": duplicate_plan_ids,
        "error_count": len(errors),
    }
    manifest = {
        "snapshot_id": run_id,
        "status": status,
        "started_at": started.isoformat(),
        "completed_at": utc_now().isoformat(),
        "resolution_plan_snapshot_id": plan_snapshot,
        "source_snapshots": source_snapshots,
        **summary,
        "publish_current_requested": publish_current,
        "published_current": bool(status == "COMPLETE" and publish_current),
        "source_artifacts_modified": False,
    }

    outputs = {
        "semantic_ti_relationship_validations.json": semantic_by_origin["TI"],
        "semantic_rule_relationship_validations.json": semantic_by_origin["RULE"],
        "semantic_validation_summary.json": summary,
        "semantic_validation_manifest.json": manifest,
        "semantic_validation_errors.json": errors,
    }
    for name, payload in outputs.items():
        write_json(snapshot_dir / name, payload)
    if status == "COMPLETE" and publish_current:
        for name, payload in outputs.items():
            write_json(current_root / name, payload)
    return manifest


def print_manifest(manifest: Mapping[str, Any]) -> None:
    print("=" * 70)
    print("PAX EXPLORER DERIVED SEMANTIC VALIDATIONS")
    print("=" * 70)
    print(f"Snapshot        : {manifest.get('snapshot_id', 'UNKNOWN')}")
    print(f"Status          : {manifest.get('status', 'UNKNOWN')}")
    print(f"TI validations  : {int(manifest.get('ti_semantic_validation_count', 0) or 0):,}")
    print(f"Rule validations: {int(manifest.get('rule_semantic_validation_count', 0) or 0):,}")
    print(f"Total semantic  : {int(manifest.get('semantic_validation_count', 0) or 0):,}")
    print(f"Automatic plan  : {int(manifest.get('automatic_plan_count', 0) or 0):,}")
    print(f"Review plan     : {int(manifest.get('review_plan_count', 0) or 0):,}")
    print(f"Errors          : {int(manifest.get('error_count', 0) or 0):,}")
    print(f"Published       : {bool(manifest.get('published_current', False))}")
    print("Source modified : False")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build non-destructive derived semantic relationship validations."
    )
    parser.add_argument("--plan", type=Path, default=PLAN_FILE)
    parser.add_argument("--plan-manifest", type=Path, default=PLAN_MANIFEST_FILE)
    parser.add_argument("--no-publish", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        manifest = build_semantic_relationship_validations(
            plan_path=args.plan,
            plan_manifest_path=args.plan_manifest,
            publish_current=not args.no_publish,
        )
        print_manifest(manifest)
        return 0 if manifest.get("status") == "COMPLETE" else 1
    except Exception as error:
        print(
            "Semantic validation build failed: "
            f"{type(error).__name__}: {error}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
