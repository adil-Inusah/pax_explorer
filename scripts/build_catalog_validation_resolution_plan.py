from __future__ import annotations

"""Build a non-destructive semantic validation resolution plan.

The planner consumes the governed catalog-match profile and produces one
proposed semantic decision for every profiled relationship. It never modifies
TI, rule, or catalog-match source artifacts.
"""

import argparse
import csv
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
PROFILE_DETAIL_FILE = CURRENT_ROOT / "catalog_match_profile_detail.json"
PROFILE_MANIFEST_FILE = CURRENT_ROOT / "catalog_match_manifest.json"
TI_MANIFEST_FILE = CURRENT_ROOT / "ti_lineage_manifest.json"
RULE_MANIFEST_FILE = CURRENT_ROOT / "rule_lineage_manifest.json"

AUTOMATIC_MAPPINGS: dict[str, tuple[str, str]] = {
    "EXACT_MATCH": ("VALID", "EXACT_CATALOG_MATCH"),
    "DEFAULT_HIERARCHY_MATCH": (
        "VALID_DEFAULT_HIERARCHY_MATCH",
        "DEFAULT_HIERARCHY_MATCH",
    ),
    "UNIQUE_HIERARCHY_MATCH": (
        "VALID_UNIQUE_HIERARCHY_MATCH",
        "UNIQUE_HIERARCHY_MATCH",
    ),
    "VALID_CREATE_TARGET": (
        "VALID_CREATE_TARGET",
        "ABSENT_TARGET_VALID_FOR_CREATE",
    ),
}

REVIEW_MAPPINGS: dict[str, tuple[str, str, str]] = {
    "EXISTING_CREATE_TARGET": (
        "REVIEW_EXISTING_CREATE_TARGET",
        "EXISTING_CREATE_TARGET",
        "Create operation targets an object that already exists.",
    ),
    "DYNAMIC_REFERENCE": (
        "UNRESOLVED_DYNAMIC_REFERENCE",
        "DYNAMIC_EXPRESSION",
        "Runtime-dependent expression does not resolve to one catalog node.",
    ),
    "MISSING_DELETE_TARGET": (
        "REVIEW_MISSING_DELETE_TARGET",
        "ABSENT_DELETE_TARGET",
        "Delete operation targets an object absent from the current catalog.",
    ),
    "AMBIGUOUS_MATCH": (
        "AMBIGUOUS_REFERENCE",
        "MULTIPLE_CATALOG_CANDIDATES",
        "More than one canonical catalog node is a plausible target.",
    ),
    "INSUFFICIENT_CONTEXT": (
        "INSUFFICIENT_REFERENCE_CONTEXT",
        "INSUFFICIENT_CONTEXT",
        "The source relationship does not contain enough identity context.",
    ),
    "UNIQUE_GLOBAL_MATCH": (
        "REVIEW_UNQUALIFIED_GLOBAL_MATCH",
        "UNQUALIFIED_UNIQUE_GLOBAL_MATCH",
        "Target is globally unique but expected parent context is absent.",
    ),
}

MISSING_STATUS_BY_DOMAIN = {
    "ATTRIBUTE": "ATTRIBUTE_NOT_IN_CATALOG",
    "SUBSET": "SUBSET_NOT_IN_CATALOG",
    "VIEW": "VIEW_NOT_IN_CATALOG",
    "HIERARCHY": "HIERARCHY_NOT_IN_CATALOG",
}

REVIEW_COLUMNS = (
    "plan_id",
    "origin",
    "relationship_id",
    "relationship_type",
    "domain",
    "original_validation_status",
    "profiler_classification",
    "proposed_validation_status",
    "decision_type",
    "resolution_method",
    "resolved_target_node_id",
    "candidate_node_ids",
    "semantic_group_position",
    "semantic_group_size",
    "target_name",
    "cube_name",
    "dimension_name",
    "hierarchy_name",
    "attribute_name",
    "subset_name",
    "view_name",
    "review_reason",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def snapshot_id(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def clean(value: Any) -> str:
    return str(value or "").strip()


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


def optional_manifest_snapshot(path: Path) -> str | None:
    if not path.is_file():
        return None
    payload = read_json(path)
    if not isinstance(payload, Mapping):
        return None
    value = clean(payload.get("snapshot_id"))
    return value or None


def candidate_ids(record: Mapping[str, Any]) -> list[str]:
    raw = record.get("candidate_node_ids")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError("candidate_node_ids must be a JSON array.")
    return [clean(item) for item in raw if clean(item)]


def stable_plan_id(origin: str, relationship_id: str) -> str:
    identity = f"{origin.casefold()}::{relationship_id}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"semantic-resolution-plan::{digest}"


def propose_decision(record: Mapping[str, Any]) -> dict[str, Any]:
    classification = clean(record.get("match_classification")).upper()
    domain = clean(record.get("domain")).upper()
    candidates = candidate_ids(record)

    if classification in AUTOMATIC_MAPPINGS:
        proposed_status, method = AUTOMATIC_MAPPINGS[classification]
        resolved_target = candidates[0] if len(candidates) == 1 else None
        if classification in {
            "EXACT_MATCH",
            "DEFAULT_HIERARCHY_MATCH",
            "UNIQUE_HIERARCHY_MATCH",
        } and resolved_target is None:
            raise ValueError(
                f"{classification} must have exactly one canonical candidate."
            )
        return {
            "proposed_validation_status": proposed_status,
            "decision_type": "AUTOMATIC",
            "resolution_method": method,
            "resolved_target_node_id": resolved_target,
            "review_reason": None,
        }

    if classification == "TARGET_NOT_IN_CATALOG":
        proposed_status = MISSING_STATUS_BY_DOMAIN.get(
            domain,
            "TARGET_NOT_IN_CATALOG",
        )
        return {
            "proposed_validation_status": proposed_status,
            "decision_type": "REVIEW",
            "resolution_method": "DETERMINISTIC_CATALOG_MISS",
            "resolved_target_node_id": None,
            "review_reason": (
                f"Deterministic {domain.lower() or 'target'} identity is absent "
                "from the current governed catalog."
            ),
        }

    mapping = REVIEW_MAPPINGS.get(classification)
    if mapping is None:
        raise ValueError(f"Unsupported profiler classification: {classification}")
    proposed_status, method, reason = mapping
    return {
        "proposed_validation_status": proposed_status,
        "decision_type": "REVIEW",
        "resolution_method": method,
        "resolved_target_node_id": None,
        "review_reason": reason,
    }


def build_plan_record(
    source: Mapping[str, Any],
    *,
    run_id: str,
    profiler_snapshot_id: str,
    source_snapshot_id: str | None,
) -> dict[str, Any]:
    origin = clean(source.get("origin")).upper()
    relationship = clean(source.get("relationship_id"))
    if not origin or not relationship:
        raise ValueError("Profile record must contain origin and relationship_id.")
    decision = propose_decision(source)
    return {
        "snapshot_id": run_id,
        "plan_id": stable_plan_id(origin, relationship),
        "origin": origin,
        "relationship_id": relationship,
        "relationship_type": clean(source.get("relationship_type")).upper(),
        "domain": clean(source.get("domain")).upper(),
        "original_validation_status": clean(
            source.get("input_validation_status")
        ).upper(),
        "profiler_classification": clean(
            source.get("match_classification")
        ).upper(),
        **decision,
        "candidate_node_ids": candidate_ids(source),
        "semantic_group_position": int(
            source.get("semantic_group_position", 1) or 1
        ),
        "semantic_group_size": int(source.get("semantic_group_size", 1) or 1),
        "join_method": clean(source.get("join_method")).upper(),
        "target_name": clean(source.get("target_name")) or None,
        "cube_name": clean(source.get("cube_name")) or None,
        "dimension_name": clean(source.get("dimension_name")) or None,
        "hierarchy_name": clean(source.get("hierarchy_name")) or None,
        "attribute_name": clean(source.get("attribute_name")) or None,
        "subset_name": clean(source.get("subset_name")) or None,
        "view_name": clean(source.get("view_name")) or None,
        "source_snapshot_id": source_snapshot_id,
        "profiler_snapshot_id": profiler_snapshot_id,
    }


def write_review_csv(
    path: Path,
    rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    fieldnames: list[str] = list(
        REVIEW_COLUMNS
    )

    with temporary.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as stream:
        writer: csv.DictWriter = (
            csv.DictWriter(
                stream,
                fieldnames=fieldnames,
            )
        )

        writer.writeheader()

        for row in rows:
            output: dict[str, Any] = {
                column: row.get(
                    column,
                    "",
                )
                for column in fieldnames
            }

            raw_candidate_ids = row.get(
                "candidate_node_ids",
                [],
            )

            if isinstance(
                raw_candidate_ids,
                list,
            ):
                output[
                    "candidate_node_ids"
                ] = " | ".join(
                    clean(value)
                    for value in raw_candidate_ids
                    if clean(value)
                )
            else:
                output[
                    "candidate_node_ids"
                ] = clean(
                    raw_candidate_ids
                )

            writer.writerow(output)

    temporary.replace(path)


def build_catalog_validation_resolution_plan(
    *,
    profile_detail_path: Path = PROFILE_DETAIL_FILE,
    profile_manifest_path: Path = PROFILE_MANIFEST_FILE,
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

    profile_manifest = read_json(profile_manifest_path)
    if not isinstance(profile_manifest, Mapping):
        raise TypeError("catalog_match_manifest.json must contain a JSON object.")
    if profile_manifest.get("status") != "COMPLETE":
        raise ValueError("Catalog-match profile must be COMPLETE before planning.")
    profiler_snapshot = clean(profile_manifest.get("snapshot_id"))
    if not profiler_snapshot:
        raise ValueError("Catalog-match manifest has no snapshot_id.")

    source_snapshots = {
        "TI": optional_manifest_snapshot(ti_manifest_path),
        "RULE": optional_manifest_snapshot(rule_manifest_path),
    }
    source_records = records(profile_detail_path)
    plan: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for index, source in enumerate(source_records, start=1):
        try:
            origin = clean(source.get("origin")).upper()
            plan.append(
                build_plan_record(
                    source,
                    run_id=run_id,
                    profiler_snapshot_id=profiler_snapshot,
                    source_snapshot_id=source_snapshots.get(origin),
                )
            )
        except Exception as error:
            errors.append(
                {
                    "profile_record_index": index,
                    "relationship_id": source.get("relationship_id"),
                    "origin": source.get("origin"),
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    plan.sort(key=lambda item: (item["origin"], item["relationship_id"]))
    automatic = [item for item in plan if item["decision_type"] == "AUTOMATIC"]
    review = [item for item in plan if item["decision_type"] == "REVIEW"]

    duplicate_plan_ids = len(plan) - len({item["plan_id"] for item in plan})
    duplicate_relationship_keys = len(plan) - len(
        {(item["origin"], item["relationship_id"]) for item in plan}
    )
    classification_counts = Counter(
        item["profiler_classification"] for item in plan
    )
    proposed_status_counts = Counter(
        item["proposed_validation_status"] for item in plan
    )
    decision_counts = Counter(item["decision_type"] for item in plan)
    domain_counts = Counter(item["domain"] for item in plan)
    origin_counts = Counter(item["origin"] for item in plan)

    integrity_errors = []
    if len(plan) != len(source_records):
        integrity_errors.append("Plan count does not equal source profile count.")
    if duplicate_plan_ids:
        integrity_errors.append("Resolution plan contains duplicate plan IDs.")
    if duplicate_relationship_keys:
        integrity_errors.append(
            "Resolution plan contains duplicate origin/relationship identities."
        )
    if len(automatic) + len(review) != len(plan):
        integrity_errors.append("Decision counts do not reconcile to plan count.")

    for message in integrity_errors:
        errors.append({"stage": "PLAN_INTEGRITY", "error": message})

    status = "PARTIAL" if errors else "COMPLETE"
    summary = {
        "snapshot_id": run_id,
        "profile_record_count": len(source_records),
        "plan_record_count": len(plan),
        "automatic_count": len(automatic),
        "review_count": len(review),
        "classification_counts": dict(sorted(classification_counts.items())),
        "proposed_status_counts": dict(sorted(proposed_status_counts.items())),
        "decision_counts": dict(sorted(decision_counts.items())),
        "domain_counts": dict(sorted(domain_counts.items())),
        "origin_counts": dict(sorted(origin_counts.items())),
        "duplicate_plan_id_count": duplicate_plan_ids,
        "duplicate_relationship_key_count": duplicate_relationship_keys,
        "error_count": len(errors),
    }
    manifest = {
        "snapshot_id": run_id,
        "status": status,
        "started_at": started.isoformat(),
        "completed_at": utc_now().isoformat(),
        "profiler_snapshot_id": profiler_snapshot,
        "source_snapshots": source_snapshots,
        **summary,
        "publish_current_requested": publish_current,
        "published_current": bool(status == "COMPLETE" and publish_current),
        "source_artifacts_modified": False,
    }

    outputs = {
        "catalog_validation_resolution_plan.json": plan,
        "catalog_validation_resolution_summary.json": summary,
        "catalog_validation_resolution_manifest.json": manifest,
        "catalog_validation_resolution_errors.json": errors,
    }
    for name, payload in outputs.items():
        write_json(snapshot_dir / name, payload)
    write_review_csv(
        snapshot_dir / "catalog_validation_resolution_review.csv",
        review,
    )

    if status == "COMPLETE" and publish_current:
        for name, payload in outputs.items():
            write_json(current_root / name, payload)
        write_review_csv(
            current_root / "catalog_validation_resolution_review.csv",
            review,
        )
    return manifest


def print_manifest(manifest: Mapping[str, Any]) -> None:
    print("=" * 70)
    print("PAX EXPLORER SEMANTIC VALIDATION RESOLUTION PLAN")
    print("=" * 70)
    print(f"Snapshot       : {manifest.get('snapshot_id', 'UNKNOWN')}")
    print(f"Status         : {manifest.get('status', 'UNKNOWN')}")
    print(f"Profile records: {int(manifest.get('profile_record_count', 0) or 0):,}")
    print(f"Plan records   : {int(manifest.get('plan_record_count', 0) or 0):,}")
    print(f"Automatic      : {int(manifest.get('automatic_count', 0) or 0):,}")
    print(f"Review         : {int(manifest.get('review_count', 0) or 0):,}")
    print(f"Errors         : {int(manifest.get('error_count', 0) or 0):,}")
    print(f"Published      : {bool(manifest.get('published_current', False))}")
    print("Source modified: False")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a non-destructive semantic validation resolution plan."
    )
    parser.add_argument("--profile-detail", type=Path, default=PROFILE_DETAIL_FILE)
    parser.add_argument("--profile-manifest", type=Path, default=PROFILE_MANIFEST_FILE)
    parser.add_argument("--no-publish", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        manifest = build_catalog_validation_resolution_plan(
            profile_detail_path=args.profile_detail,
            profile_manifest_path=args.profile_manifest,
            publish_current=not args.no_publish,
        )
        print_manifest(manifest)
        return 0 if manifest.get("status") == "COMPLETE" else 1
    except Exception as error:
        print(
            "Semantic resolution planning failed: "
            f"{type(error).__name__}: {error}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
