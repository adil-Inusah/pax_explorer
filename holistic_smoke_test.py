from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


CATALOG_FILES = {
    "manifest": "manifest.json",
    "objects": "objects.json",
    "rule_relationships": "rule_relationships.json",
    "rule_validations": "rule_relationship_validations.json",
    "ti_relationships": "ti_relationships.json",
    "ti_validations": "ti_relationship_validations.json",
}

OPTIONAL_FILES = (
    "cube_rule_definitions.json",
    "rule_function_profile.json",
    "rule_function_review_queue.json",
    "rule_lineage_manifest.json",
    "rule_parser_coverage.json",
    "ti_lineage_manifest.json",
    "ti_process_definitions.json",
)

LIST_KEYS = (
    "items",
    "records",
    "objects",
    "relationships",
    "validations",
    "data",
    "results",
)

STATUS_KEYS = (
    "validation_status",
    "status",
    "resolution_status",
    "result",
    "classification",
)

OBJECT_TYPE_KEYS = (
    "object_type",
    "type",
    "objectType",
)


class SmokeTestError(RuntimeError):
    pass


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the multi-file holistic TM1 catalog under "
            "catalog/current. The deferred attribute collector is not run."
        )
    )
    parser.add_argument(
        "catalog_path",
        nargs="?",
        type=Path,
        default=Path("data/current"),
        help=(
            "Catalog current directory or manifest.json path "
            "(default: data/current)"
        ),
    )
    return parser.parse_args()


def resolve_catalog_directory(path: Path) -> Path:
    if path.name.casefold() == "manifest.json":
        return path.parent
    return path


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise SmokeTestError(f"required file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise SmokeTestError(
            f"invalid JSON in {path}: line {error.lineno}, "
            f"column {error.colno}: {error.msg}"
        ) from error
    except OSError as error:
        raise SmokeTestError(f"unable to read {path}: {error}") from error


def extract_records(payload: Any, *, source: Path) -> list[dict[str, Any]]:
    """Extract records from a top-level list or a common wrapper object."""
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = None
        for key in LIST_KEYS:
            candidate = payload.get(key)
            if isinstance(candidate, list):
                records = candidate
                break

        if records is None:
            list_values = [
                value for value in payload.values() if isinstance(value, list)
            ]
            if len(list_values) == 1:
                records = list_values[0]
            elif not payload:
                records = []
            else:
                raise SmokeTestError(
                    f"cannot identify the record list in {source}; "
                    f"top-level keys: {sorted(payload)}"
                )
    else:
        raise SmokeTestError(
            f"{source} must contain a JSON array or wrapper object"
        )

    invalid_indexes = [
        index for index, record in enumerate(records)
        if not isinstance(record, dict)
    ]
    if invalid_indexes:
        preview = invalid_indexes[:5]
        raise SmokeTestError(
            f"{source} contains non-object records at indexes {preview}"
        )
    return records


def first_value(record: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def normalize_token(value: Any) -> str:
    return str(value or "UNKNOWN").strip().upper().replace(" ", "_")


def count_by_key(
    records: list[dict[str, Any]],
    keys: Iterable[str],
) -> Counter[str]:
    return Counter(
        normalize_token(first_value(record, keys))
        for record in records
    )


def parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def validate_manifest(
    manifest: Any,
    failures: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        failures.append("manifest.json root must be a JSON object")
        return {}

    if not manifest:
        failures.append("manifest.json is empty")
        return manifest

    status = manifest.get("status")
    if status is not None:
        normalized_status = normalize_token(status)
        if normalized_status in {"FAILED", "FAILURE", "ERROR"}:
            failures.append(f"manifest status is {status!r}")
        elif normalized_status not in {"COMPLETE", "COMPLETED", "SUCCESS"}:
            warnings.append(f"manifest status is {status!r}")

    started = manifest.get("started_at") or manifest.get("collected_at")
    completed = manifest.get("completed_at")
    if started and completed:
        try:
            started_at = parse_timestamp(started)
            completed_at = parse_timestamp(completed)
            if started_at and completed_at and completed_at < started_at:
                failures.append("manifest completed_at precedes started_at")
        except ValueError as error:
            failures.append(f"manifest contains an invalid timestamp: {error}")

    errors = manifest.get("errors")
    if isinstance(errors, list) and errors:
        warnings.append(f"manifest contains {len(errors)} reported errors")

    return manifest


def validate_identifiers(
    records: list[dict[str, Any]],
    source_name: str,
    failures: list[str],
) -> None:
    candidate_keys = (
        "object_id",
        "relationship_id",
        "validation_id",
        "id",
    )
    selected_key = next(
        (
            key for key in candidate_keys
            if any(key in record for record in records)
        ),
        None,
    )
    if selected_key is None:
        return

    identifiers = [
        str(record.get(selected_key, "")).strip()
        for record in records
    ]
    missing_count = sum(not identifier for identifier in identifiers)
    duplicates = [
        identifier
        for identifier, count in Counter(
            identifier for identifier in identifiers if identifier
        ).items()
        if count > 1
    ]
    if missing_count:
        failures.append(
            f"{source_name} has {missing_count} records without "
            f"{selected_key}"
        )
    if duplicates:
        failures.append(
            f"{source_name} has {len(duplicates)} duplicate "
            f"{selected_key} values"
        )


def run_smoke_test(catalog_path: Path) -> int:
    failures: list[str] = []
    warnings: list[str] = []
    current_dir = resolve_catalog_directory(catalog_path)

    print("=" * 72)
    print("HOLISTIC TM1 CATALOG SMOKE TEST")
    print("=" * 72)
    print(f"catalog_directory={current_dir.resolve()}")
    print("attribute_collection=DEFERRED")

    if not current_dir.is_dir():
        print(f"FAILURE: catalog directory not found: {current_dir}")
        print("HOLISTIC_SMOKE_TEST=FAIL")
        return 1

    payloads: dict[str, Any] = {}
    for logical_name, file_name in CATALOG_FILES.items():
        path = current_dir / file_name
        try:
            payloads[logical_name] = read_json(path)
            print(f"file={file_name} parse=PASS")
        except SmokeTestError as error:
            failures.append(str(error))
            print(f"file={file_name} parse=FAIL")

    if failures:
        return print_summary({}, failures, warnings)

    manifest = validate_manifest(payloads["manifest"], failures, warnings)

    record_sets: dict[str, list[dict[str, Any]]] = {}
    for logical_name in (
        "objects",
        "rule_relationships",
        "rule_validations",
        "ti_relationships",
        "ti_validations",
    ):
        source = current_dir / CATALOG_FILES[logical_name]
        try:
            records = extract_records(payloads[logical_name], source=source)
            record_sets[logical_name] = records
            print(f"records={source.name} count={len(records)}")
            validate_identifiers(records, source.name, failures)
        except SmokeTestError as error:
            failures.append(str(error))
            record_sets[logical_name] = []

    objects = record_sets["objects"]
    rule_relationships = record_sets["rule_relationships"]
    ti_relationships = record_sets["ti_relationships"]
    rule_validations = record_sets["rule_validations"]
    ti_validations = record_sets["ti_validations"]

    if not objects:
        failures.append("objects.json contains no object records")

    object_counts = count_by_key(objects, OBJECT_TYPE_KEYS)
    relationship_records = rule_relationships + ti_relationships
    validation_records = rule_validations + ti_validations
    relationship_counts = count_by_key(
        relationship_records,
        ("relationship_type", "type", "relationshipType"),
    )
    validation_counts = count_by_key(validation_records, STATUS_KEYS)

    expected_object_types = {"CHORE", "CUBE", "DIMENSION", "PROCESS"}
    known_object_types = set(object_counts)
    missing_types = sorted(expected_object_types - known_object_types)
    if missing_types:
        warnings.append(
            "objects.json does not expose expected object types under a "
            f"recognized type field: {missing_types}"
        )

    if not relationship_records:
        failures.append("relationship files contain no relationship records")
    if not validation_records:
        failures.append("validation files contain no validation records")

    relationship_total = len(relationship_records)
    validation_total = len(validation_records)
    if relationship_total != validation_total:
        warnings.append(
            "relationship and validation totals do not reconcile: "
            f"{relationship_total} relationships versus "
            f"{validation_total} validations"
        )

    for category in (
        "BROKEN_REFERENCE",
        "NOT_YET_CATALOGED",
        "UNRESOLVED_DYNAMIC_REFERENCE",
    ):
        count = validation_counts.get(category, 0)
        if count:
            warnings.append(f"{category}: {count}")

    optional_present = [
        file_name for file_name in OPTIONAL_FILES
        if (current_dir / file_name).is_file()
    ]
    optional_missing = [
        file_name for file_name in OPTIONAL_FILES
        if not (current_dir / file_name).is_file()
    ]
    print(f"optional_files_present={len(optional_present)}")
    if optional_missing:
        warnings.append(
            "optional catalog files not present: "
            + ", ".join(optional_missing)
        )

    summary = {
        "manifest_keys": len(manifest),
        "objects": len(objects),
        "object_counts": dict(sorted(object_counts.items())),
        "rule_relationships": len(rule_relationships),
        "ti_relationships": len(ti_relationships),
        "relationships": relationship_total,
        "relationship_counts": dict(sorted(relationship_counts.items())),
        "rule_validations": len(rule_validations),
        "ti_validations": len(ti_validations),
        "validations": validation_total,
        "validation_counts": dict(sorted(validation_counts.items())),
    }
    return print_summary(summary, failures, warnings)


def print_summary(
    summary: dict[str, Any],
    failures: list[str],
    warnings: list[str],
) -> int:
    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    for key in (
        "manifest_keys",
        "objects",
        "rule_relationships",
        "ti_relationships",
        "relationships",
        "rule_validations",
        "ti_validations",
        "validations",
    ):
        if key in summary:
            print(f"{key}={summary[key]}")

    for key in ("object_counts", "relationship_counts", "validation_counts"):
        values = summary.get(key, {})
        if values:
            print(f"{key}={json.dumps(values, sort_keys=True)}")

    print(f"failures={len(failures)}")
    print(f"warnings={len(warnings)}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    for failure in failures:
        print(f"FAILURE: {failure}")

    if failures:
        print("HOLISTIC_SMOKE_TEST=FAIL")
        return 1
    if warnings:
        print("HOLISTIC_SMOKE_TEST=PASS_WITH_WARNINGS")
    else:
        print("HOLISTIC_SMOKE_TEST=PASS")
    return 0


def main() -> int:
    arguments = parse_arguments()
    return run_smoke_test(arguments.catalog_path)


if __name__ == "__main__":
    raise SystemExit(main())
