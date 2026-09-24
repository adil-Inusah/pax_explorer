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
    "regular_objects": "regular_objects.json",
    "control_objects": "control_objects.json",
    "rule_relationships": "rule_relationships.json",
    "rule_validations": "rule_relationship_validations.json",
    "ti_relationships": "ti_relationships.json",
    "ti_validations": "ti_relationship_validations.json",
    "attributes": "attributes.json",
    "attribute_manifest": "attribute_manifest.json",
    "attribute_metrics": "attribute_collection_metrics.json",
    "attribute_errors": "attribute_collection_errors.json",
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

RAW_ISSUE_STATUSES = {
    "BROKEN_REFERENCE",
    "RESOLVED_TARGET_NOT_IN_CATALOG",
}

WARNING_STATUSES = {
    "UNRESOLVED_DYNAMIC_REFERENCE",
    "PENDING_DYNAMIC_RESOLUTION",
    "PENDING_CATALOG_LOOKUP",
}

INFORMATIONAL_STATUSES = {
    "ATTRIBUTE_CATALOG_DEFERRED",
    "HIERARCHY_CATALOG_DEFERRED",
    "ELEMENT_CATALOG_DEFERRED",
    "RUNTIME_VIEW_REFERENCE",
    "RUNTIME_SUBSET_REFERENCE",
    "DYNAMIC_EXTERNAL_FILE",
    "DYNAMIC_EXTERNAL_COMMAND",
    "NOT_CROSS_CHECKED",
}

EXCEPTION_FILE_CANDIDATES = (
    Path("config") / "Catalog_quality_exceptions.json",
    Path("Catalog_quality_exceptions.json"),
)


class SmokeTestError(RuntimeError):
    pass


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the multi-file holistic TM1 catalog under data/current, "
            "including the governed attribute-definition inventory."
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
    parser.add_argument(
        "--exceptions",
        type=Path,
        default=None,
        help=(
            "Path to Catalog_quality_exceptions.json. By default, the script "
            "looks under config/ relative to the project root."
        ),
    )
    return parser.parse_args()


def resolve_catalog_directory(path: Path) -> Path:
    if path.name.casefold() == "manifest.json":
        return path.parent
    return path


def resolve_project_root(current_dir: Path) -> Path:
    if current_dir.name.casefold() == "current" and current_dir.parent.name.casefold() == "data":
        return current_dir.parent.parent
    return Path.cwd()


def resolve_exception_path(
    current_dir: Path,
    explicit_path: Path | None,
) -> Path | None:
    if explicit_path is not None:
        return explicit_path
    project_root = resolve_project_root(current_dir)
    for relative_path in EXCEPTION_FILE_CANDIDATES:
        candidate = project_root / relative_path
        if candidate.is_file():
            return candidate
    return None


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
        index
        for index, record in enumerate(records)
        if not isinstance(record, dict)
    ]
    if invalid_indexes:
        raise SmokeTestError(
            f"{source} contains non-object records at indexes "
            f"{invalid_indexes[:5]}"
        )
    return records


def first_value(record: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def normalize_key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def normalize_token(value: Any) -> str:
    return str(value or "UNKNOWN").strip().upper().replace(" ", "_")


def object_key(record: dict[str, Any]) -> tuple[str, str]:
    return (
        normalize_key(first_value(record, OBJECT_TYPE_KEYS)),
        normalize_key(first_value(record, ("object_name", "name", "objectName"))),
    )


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
        if normalized_status in {"FAILED", "FAILURE", "ERROR", "PARTIAL"}:
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
    error_count = manifest.get("error_count")
    if isinstance(error_count, int) and error_count > 0:
        warnings.append(f"manifest error_count is {error_count}")
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
            key
            for key in candidate_keys
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
            f"{source_name} has {missing_count} records without {selected_key}"
        )
    if duplicates:
        failures.append(
            f"{source_name} has {len(duplicates)} duplicate {selected_key} values"
        )


def validate_object_keys(
    records: list[dict[str, Any]],
    source_name: str,
    failures: list[str],
) -> set[tuple[str, str]]:
    keys = [object_key(record) for record in records]
    missing = sum(not object_type or not object_name for object_type, object_name in keys)
    if missing:
        failures.append(
            f"{source_name} has {missing} records without object type or name"
        )
    usable = [key for key in keys if key[0] and key[1]]
    duplicate_count = len(usable) - len(set(usable))
    if duplicate_count:
        failures.append(
            f"{source_name} has {duplicate_count} duplicate object type/name keys"
        )
    return set(usable)


def validate_object_scope(
    regular_objects: list[dict[str, Any]],
    control_objects: list[dict[str, Any]],
    failures: list[str],
) -> None:
    invalid_regular = [
        record
        for record in regular_objects
        if str(record.get("object_name") or "").strip().startswith("}")
        or record.get("is_control") is not False
        or normalize_token(record.get("catalog_scope")) != "REGULAR"
    ]
    if invalid_regular:
        failures.append(
            f"regular_objects.json has {len(invalid_regular)} invalid scope records"
        )

    invalid_control = [
        record
        for record in control_objects
        if not str(record.get("object_name") or "").strip().startswith("}")
        or record.get("is_control") is not True
        or normalize_token(record.get("catalog_scope")) != "CONTROL"
    ]
    if invalid_control:
        failures.append(
            f"control_objects.json has {len(invalid_control)} invalid scope records"
        )


def normalized_count_mapping(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(count, int) or count < 0:
            return None
        result[normalize_token(key)] = count
    return result


def reconcile_manifest_counts(
    manifest: dict[str, Any],
    *,
    objects: list[dict[str, Any]],
    regular_objects: list[dict[str, Any]],
    control_objects: list[dict[str, Any]],
    object_counts: Counter[str],
    regular_counts: Counter[str],
    control_counts: Counter[str],
    failures: list[str],
) -> None:
    scalar_checks = {
        "object_count": len(objects),
        "regular_object_count": len(regular_objects),
        "control_object_count": len(control_objects),
    }
    for field_name, actual in scalar_checks.items():
        declared = manifest.get(field_name)
        if declared != actual:
            failures.append(
                f"manifest {field_name}={declared!r}, expected {actual}"
            )

    if len(objects) != len(regular_objects) + len(control_objects):
        failures.append(
            "regular and control object counts do not reconcile to objects.json"
        )

    mapping_checks = {
        "object_counts": dict(object_counts),
        "regular_object_counts": dict(regular_counts),
        "control_object_counts": dict(control_counts),
    }
    for field_name, actual in mapping_checks.items():
        declared = normalized_count_mapping(manifest.get(field_name))
        if declared != actual:
            failures.append(
                f"manifest {field_name} does not match file-level counts: "
                f"declared={declared}, actual={actual}"
            )

    all_types = set(object_counts) | set(regular_counts) | set(control_counts)
    for object_type in sorted(all_types):
        if object_counts.get(object_type, 0) != (
            regular_counts.get(object_type, 0)
            + control_counts.get(object_type, 0)
        ):
            failures.append(
                f"object type {object_type} does not reconcile between "
                "full, regular, and control catalogs"
            )


def load_exception_register(
    exception_path: Path | None,
    failures: list[str],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    if exception_path is None:
        failures.append("Catalog_quality_exceptions.json was not found")
        return {}, {}, {}

    try:
        payload = read_json(exception_path)
    except SmokeTestError as error:
        failures.append(str(error))
        return {}, {}, {}

    if not isinstance(payload, dict):
        failures.append("quality exception register root must be a JSON object")
        return {}, {}, {}

    exception_records = payload.get("exceptions")
    if not isinstance(exception_records, list):
        failures.append("quality exception register must contain an exceptions list")
        return {}, {}, payload

    exceptions = [record for record in exception_records if isinstance(record, dict)]
    if len(exceptions) != len(exception_records):
        failures.append("quality exception register contains non-object entries")

    declared_count = payload.get("exception_count")
    if declared_count != len(exceptions):
        failures.append(
            f"exception_count={declared_count!r}, expected {len(exceptions)}"
        )

    relationship_total = sum(
        record.get("relationship_count", 0)
        for record in exceptions
        if isinstance(record.get("relationship_count"), int)
    )
    if payload.get("relationship_count") != relationship_total:
        failures.append(
            "quality exception relationship_count does not reconcile to entries"
        )

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for record in exceptions:
        exception_id = str(record.get("exception_id") or "").strip()
        key = (
            normalize_key(record.get("target_type")),
            normalize_key(record.get("target_name")),
        )
        if not exception_id:
            failures.append("quality exception entry is missing exception_id")
        elif exception_id in by_id:
            failures.append(f"duplicate quality exception ID: {exception_id}")
        else:
            by_id[exception_id] = record

        if not all(key):
            failures.append(
                f"quality exception {exception_id or '<unknown>'} is missing target key"
            )
        elif key in by_key:
            failures.append(
                f"duplicate quality exception target key: {key[0]}::{key[1]}"
            )
        else:
            by_key[key] = record

        if normalize_token(record.get("exception_status")) not in {
            "REGISTERED",
            "ACTIVE",
        }:
            failures.append(
                f"quality exception {exception_id or '<unknown>'} is not registered or active"
            )
        if record.get("relationship_count", 0) <= 0:
            failures.append(
                f"quality exception {exception_id or '<unknown>'} has invalid relationship_count"
            )
        if record.get("exclude_from_quality_score") is not True:
            failures.append(
                f"quality exception {exception_id or '<unknown>'} is not excluded from score"
            )
        if record.get("required_for_holistic_success") is not False:
            failures.append(
                f"quality exception {exception_id or '<unknown>'} is required for success"
            )

    return by_key, by_id, payload


def validate_exception_application(
    validation_records: list[dict[str, Any]],
    exception_by_key: dict[tuple[str, str], dict[str, Any]],
    exception_by_id: dict[str, dict[str, Any]],
    failures: list[str],
) -> dict[str, int]:
    applied_records = [
        record
        for record in validation_records
        if str(record.get("exception_id") or "").strip()
    ]
    unknown_ids: set[str] = set()
    invalid_applications = 0
    for record in applied_records:
        exception_id = str(record.get("exception_id") or "").strip()
        registered = exception_by_id.get(exception_id)
        if registered is None:
            unknown_ids.add(exception_id)
            continue
        record_key = (
            normalize_key(
                first_value(record, ("target_type", "target_object_type"))
            ),
            normalize_key(record.get("target_name")),
        )
        registered_key = (
            normalize_key(registered.get("target_type")),
            normalize_key(registered.get("target_name")),
        )
        if record_key != registered_key:
            invalid_applications += 1

    if unknown_ids:
        failures.append(
            f"validation records reference unknown exception IDs: {sorted(unknown_ids)}"
        )
    if invalid_applications:
        failures.append(
            f"{invalid_applications} validation records apply an exception to the wrong target"
        )

    raw_issue_records = [
        record
        for record in validation_records
        if normalize_token(first_value(record, STATUS_KEYS)) in RAW_ISSUE_STATUSES
    ]
    unregistered_issue_records = [
        record
        for record in raw_issue_records
        if not str(record.get("exception_id") or "").strip()
    ]
    excluded_records = [
        record
        for record in applied_records
        if record.get("exclude_from_quality_score") is True
    ]
    required_records = [
        record
        for record in applied_records
        if record.get("required_for_holistic_success") is True
    ]

    # expected_registered_edges = sum(
    #     record.get("relationship_count", 0)
    #     for record in exception_by_key.values()
    #     if isinstance(record.get("relationship_count"), int)
    # )

    published_exceptions = [
        record
        for record in exception_by_key.values()
        if record.get(
            "applies_to_validation_records",
            True,
        )
        is True
    ]

    provenance_exceptions = [
        record
        for record in exception_by_key.values()
        if record.get(
            "applies_to_validation_records",
            True,
        )
        is not True
    ]

    expected_registered_edges = sum(
        record.get(
            "relationship_count",
            0,
        )
        for record in published_exceptions
        if isinstance(
            record.get(
                "relationship_count"
            ),
            int,
        )
    )

    provenance_relationship_count = sum(
        record.get(
            "relationship_count",
            0,
        )
        for record in provenance_exceptions
        if isinstance(
            record.get(
                "relationship_count"
            ),
            int,
        )
    )
    if applied_records and len(applied_records) != expected_registered_edges:
        failures.append(
            "applied exception record count does not match registered relationship coverage: "
            f"applied={len(applied_records)}, expected={expected_registered_edges}"
        )
    if required_records:
        failures.append(
            f"{len(required_records)} applied exceptions are marked required for holistic success"
        )

    return {
        "registered_exception_targets": (
            len(exception_by_key)
        ),
        "published_exception_targets": (
            len(published_exceptions)
        ),
        "registered_exception_records": (
            len(applied_records)
        ),
        "provenance_candidate_targets": (
            len(provenance_exceptions)
        ),
        "provenance_candidate_records": (
            provenance_relationship_count
        ),
        "excluded_from_quality_score": (
            len(excluded_records)
        ),
        "raw_missing_target_records": (
            len(raw_issue_records)
        ),
        "unregistered_missing_target_records": (
            len(unregistered_issue_records)
        ),
    }


def attribute_identity(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        normalize_key(record.get("dimension_name")),
        normalize_key(record.get("hierarchy_name")),
        normalize_key(record.get("attribute_name")),
    )


def validate_attribute_inventory(
    *,
    attributes: list[dict[str, Any]],
    manifest: Any,
    metrics: Any,
    errors: list[dict[str, Any]],
    object_dimensions: set[str],
    failures: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        failures.append("attribute_manifest.json root must be a JSON object")
        manifest = {}
    if not isinstance(metrics, dict):
        failures.append(
            "attribute_collection_metrics.json root must be a JSON object"
        )
        metrics = {}

    status = normalize_token(manifest.get("status"))
    if status not in {"COMPLETE", "COMPLETED", "SUCCESS"}:
        failures.append(
            f"attribute_manifest.json status is {manifest.get('status')!r}"
        )

    scope = normalize_key(manifest.get("scope"))
    if scope != "all":
        failures.append(
            "attribute_manifest.json scope must be 'all' for governed current output"
        )

    if manifest.get("published_current") is not True:
        failures.append(
            "attribute_manifest.json published_current must be true"
        )
    if manifest.get("publish_current_requested") is not True:
        failures.append(
            "attribute_manifest.json publish_current_requested must be true"
        )

    scalar_checks = {
        "attribute_count": len(attributes),
        "error_count": len(errors),
    }
    for field, actual in scalar_checks.items():
        if manifest.get(field) != actual:
            failures.append(
                f"attribute manifest {field}={manifest.get(field)!r}, "
                f"expected {actual}"
            )

    metric_checks = {
        "attribute_count": len(attributes),
        "dimension_count": manifest.get("dimension_count"),
        "hierarchy_count": manifest.get("hierarchy_count"),
    }
    for field, expected in metric_checks.items():
        if metrics.get(field) != expected:
            failures.append(
                f"attribute metrics {field}={metrics.get(field)!r}, "
                f"expected {expected!r}"
            )

    hierarchy_count = metrics.get("hierarchy_count")
    completed_hierarchy_count = metrics.get("completed_hierarchy_count")
    if hierarchy_count != completed_hierarchy_count:
        failures.append(
            "attribute hierarchy counts do not reconcile: "
            f"attempted={hierarchy_count!r}, "
            f"completed={completed_hierarchy_count!r}"
        )

    if errors:
        failures.append(
            f"attribute_collection_errors.json contains {len(errors)} records"
        )

    identities: list[tuple[str, str, str]] = []
    missing_required = 0
    invalid_qualified_names = 0
    invalid_object_types = 0
    invalid_types = 0
    invalid_alias_flags = 0
    invalid_control_flags = 0
    unknown_parent_dimensions: set[str] = set()

    allowed_types = {"STRING", "NUMERIC", "ALIAS"}
    for record in attributes:
        identity = attribute_identity(record)
        identities.append(identity)
        if not all(identity):
            missing_required += 1

        expected_qualified_name = "::".join(
            (
                str(record.get("dimension_name") or ""),
                str(record.get("hierarchy_name") or ""),
                str(record.get("attribute_name") or ""),
            )
        )
        if str(record.get("qualified_name") or "") != expected_qualified_name:
            invalid_qualified_names += 1
        if str(record.get("object_name") or "") != expected_qualified_name:
            invalid_qualified_names += 1
        if normalize_token(record.get("object_type")) != "ATTRIBUTE":
            invalid_object_types += 1

        data_type = normalize_token(record.get("attribute_data_type"))
        if data_type not in allowed_types:
            invalid_types += 1
        if (data_type == "ALIAS") != (record.get("is_alias") is True):
            invalid_alias_flags += 1

        dimension_name = str(record.get("dimension_name") or "").strip()
        expected_control = (
            dimension_name.startswith("}")
            or str(record.get("hierarchy_name") or "").strip().startswith("}")
            or str(record.get("attribute_name") or "").strip().startswith("}")
        )
        if record.get("is_control") is not expected_control:
            invalid_control_flags += 1
        dimension_key = normalize_key(dimension_name)
        if dimension_key and dimension_key not in object_dimensions:
            unknown_parent_dimensions.add(dimension_name)

    duplicate_count = len(identities) - len(set(identities))
    checks = (
        (missing_required, "attribute records have missing identity fields"),
        (duplicate_count, "duplicate normalized attribute identities"),
        (invalid_qualified_names, "invalid attribute qualified/object names"),
        (invalid_object_types, "attribute records have invalid object_type"),
        (invalid_types, "attribute records have unknown data types"),
        (invalid_alias_flags, "attribute records have inconsistent alias flags"),
        (invalid_control_flags, "attribute records have inconsistent control flags"),
    )
    for count, message in checks:
        if count:
            failures.append(f"{count} {message}")
    if unknown_parent_dimensions:
        failures.append(
            "attribute records reference dimensions absent from objects.json: "
            f"{sorted(unknown_parent_dimensions)[:20]}"
        )

    regular_attributes = sum(
        record.get("is_control") is False for record in attributes
    )
    control_attributes = sum(
        record.get("is_control") is True for record in attributes
    )
    if regular_attributes + control_attributes != len(attributes):
        failures.append(
            "regular and control attribute counts do not reconcile to attributes.json"
        )

    type_counts = Counter(
        normalize_token(record.get("attribute_data_type"))
        for record in attributes
    )
    declared_type_counts = normalized_count_mapping(
        metrics.get("attribute_type_counts")
    )
    if declared_type_counts != dict(type_counts):
        failures.append(
            "attribute metrics attribute_type_counts do not match attributes.json"
        )

    if not attributes:
        failures.append("attributes.json contains no attribute definitions")

    return {
        "attribute_collection": "COMPLETE" if not errors else "PARTIAL",
        "attributes": len(attributes),
        "regular_attributes": regular_attributes,
        "control_attributes": control_attributes,
        "attribute_dimensions": manifest.get("dimension_count", 0),
        "attribute_hierarchies": manifest.get("hierarchy_count", 0),
        "completed_attribute_hierarchies": metrics.get(
            "completed_hierarchy_count", 0
        ),
        "attribute_collection_errors": len(errors),
        "attribute_identity_duplicates": duplicate_count,
        "attribute_unknown_types": invalid_types,
        "attribute_alias_flag_errors": invalid_alias_flags,
        "attribute_inventory_reconciles": (
            not errors
            and duplicate_count == 0
            and missing_required == 0
            and invalid_qualified_names == 0
            and invalid_object_types == 0
            and invalid_types == 0
            and invalid_alias_flags == 0
            and invalid_control_flags == 0
            and not unknown_parent_dimensions
            and manifest.get("attribute_count") == len(attributes)
            and metrics.get("attribute_count") == len(attributes)
            and hierarchy_count == completed_hierarchy_count
        ),
        "attribute_type_counts": dict(sorted(type_counts.items())),
    }


def validate_lineage_manifest(
    path: Path,
    *,
    expected_relationships: int,
    expected_validations: int,
    failures: list[str],
) -> None:
    if not path.is_file():
        return
    try:
        payload = read_json(path)
    except SmokeTestError as error:
        failures.append(str(error))
        return
    if not isinstance(payload, dict):
        failures.append(f"{path.name} root must be a JSON object")
        return
    status = normalize_token(payload.get("status"))
    if status not in {"COMPLETE", "COMPLETED", "SUCCESS"}:
        failures.append(f"{path.name} status is {payload.get('status')!r}")
    if payload.get("relationship_count") != expected_relationships:
        failures.append(
            f"{path.name} relationship_count does not match its relationship file"
        )
    if payload.get("validation_count") != expected_validations:
        failures.append(
            f"{path.name} validation_count does not match its validation file"
        )


def run_smoke_test(
    catalog_path: Path,
    exception_path: Path | None = None,
) -> int:
    failures: list[str] = []
    warnings: list[str] = []
    current_dir = resolve_catalog_directory(catalog_path)

    print("=" * 72)
    print("HOLISTIC TM1 CATALOG SMOKE TEST")
    print("=" * 72)
    print(f"catalog_directory={current_dir.resolve()}")

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
        "regular_objects",
        "control_objects",
        "rule_relationships",
        "rule_validations",
        "ti_relationships",
        "ti_validations",
        "attributes",
        "attribute_errors",
    ):
        source = current_dir / CATALOG_FILES[logical_name]
        try:
            extracted = extract_records(payloads[logical_name], source=source)
            record_sets[logical_name] = extracted
            print(f"records={source.name} count={len(extracted)}")
            validate_identifiers(extracted, source.name, failures)
        except SmokeTestError as error:
            failures.append(str(error))
            record_sets[logical_name] = []

    objects = record_sets["objects"]
    regular_objects = record_sets["regular_objects"]
    control_objects = record_sets["control_objects"]
    rule_relationships = record_sets["rule_relationships"]
    rule_validations = record_sets["rule_validations"]
    ti_relationships = record_sets["ti_relationships"]
    ti_validations = record_sets["ti_validations"]
    attributes = record_sets["attributes"]
    attribute_errors = record_sets["attribute_errors"]
    attribute_metrics = payloads["attribute_metrics"]
    attribute_manifest = payloads["attribute_manifest"]

    if not objects:
        failures.append("objects.json contains no object records")
    if not regular_objects:
        failures.append("regular_objects.json contains no object records")
    if not control_objects:
        failures.append("control_objects.json contains no object records")

    all_keys = validate_object_keys(objects, "objects.json", failures)
    regular_keys = validate_object_keys(
        regular_objects, "regular_objects.json", failures
    )
    control_keys = validate_object_keys(
        control_objects, "control_objects.json", failures
    )
    validate_object_scope(regular_objects, control_objects, failures)

    overlap = regular_keys & control_keys
    if overlap:
        failures.append(
            f"regular and control object catalogs overlap on {len(overlap)} keys"
        )
    split_union = regular_keys | control_keys
    if split_union != all_keys:
        missing_from_split = all_keys - split_union
        extra_in_split = split_union - all_keys
        failures.append(
            "regular/control object keys do not reconcile to objects.json: "
            f"missing={len(missing_from_split)}, extra={len(extra_in_split)}"
        )

    object_counts = count_by_key(objects, OBJECT_TYPE_KEYS)
    regular_object_counts = count_by_key(regular_objects, OBJECT_TYPE_KEYS)
    control_object_counts = count_by_key(control_objects, OBJECT_TYPE_KEYS)
    reconcile_manifest_counts(
        manifest,
        objects=objects,
        regular_objects=regular_objects,
        control_objects=control_objects,
        object_counts=object_counts,
        regular_counts=regular_object_counts,
        control_counts=control_object_counts,
        failures=failures,
    )

    object_dimensions = {
        object_name
        for object_type, object_name in all_keys
        if object_type == "dimension"
    }
    attribute_metrics = validate_attribute_inventory(
        attributes=attributes,
        manifest=attribute_manifest,
        metrics=attribute_metrics,
        errors=attribute_errors,
        object_dimensions=object_dimensions,
        failures=failures,
        warnings=warnings,
    )

    expected_object_types = {"CHORE", "CUBE", "DIMENSION", "PROCESS"}
    missing_types = sorted(expected_object_types - set(object_counts))
    if missing_types:
        failures.append(f"objects.json is missing object types: {missing_types}")

    relationship_records = rule_relationships + ti_relationships
    validation_records = rule_validations + ti_validations
    if not relationship_records:
        failures.append("relationship files contain no relationship records")
    if not validation_records:
        failures.append("validation files contain no validation records")

    relationship_total = len(relationship_records)
    validation_total = len(validation_records)
    if relationship_total != validation_total:
        failures.append(
            "relationship and validation totals do not reconcile: "
            f"{relationship_total} relationships versus {validation_total} validations"
        )

    relationship_counts = count_by_key(
        relationship_records,
        ("relationship_type", "type", "relationshipType"),
    )
    validation_counts = count_by_key(validation_records, STATUS_KEYS)

    resolved_exception_path = resolve_exception_path(current_dir, exception_path)
    exception_by_key, exception_by_id, exception_payload = load_exception_register(
        resolved_exception_path,
        failures,
    )
    if resolved_exception_path is not None:
        print(f"quality_exception_register={resolved_exception_path.resolve()}")

    exception_metrics = validate_exception_application(
        validation_records,
        exception_by_key,
        exception_by_id,
        failures,
    )

    for category in sorted(WARNING_STATUSES):
        count = validation_counts.get(category, 0)
        if count:
            warnings.append(f"{category}: {count}")

    unregistered_issue_count = exception_metrics[
        "unregistered_missing_target_records"
    ]
    if unregistered_issue_count:
        warnings.append(
            "UNREGISTERED_MISSING_TARGET_RECORDS: "
            f"{unregistered_issue_count}"
        )

    validate_lineage_manifest(
        current_dir / "ti_lineage_manifest.json",
        expected_relationships=len(ti_relationships),
        expected_validations=len(ti_validations),
        failures=failures,
    )
    validate_lineage_manifest(
        current_dir / "rule_lineage_manifest.json",
        expected_relationships=len(rule_relationships),
        expected_validations=len(rule_validations),
        failures=failures,
    )

    optional_present = [
        file_name
        for file_name in OPTIONAL_FILES
        if (current_dir / file_name).is_file()
    ]
    optional_missing = [
        file_name
        for file_name in OPTIONAL_FILES
        if not (current_dir / file_name).is_file()
    ]
    print(f"optional_files_present={len(optional_present)}")
    if optional_missing:
        print("optional_files_missing=" + ",".join(optional_missing))

    informational_counts = {
        status: validation_counts.get(status, 0)
        for status in sorted(INFORMATIONAL_STATUSES)
        if validation_counts.get(status, 0)
    }

    summary = {
        "manifest_keys": len(manifest),
        "objects": len(objects),
        "regular_objects": len(regular_objects),
        "control_objects": len(control_objects),
        "object_split_reconciles": (
            len(objects) == len(regular_objects) + len(control_objects)
            and not overlap
            and split_union == all_keys
        ),
        "object_counts": dict(sorted(object_counts.items())),
        "regular_object_counts": dict(sorted(regular_object_counts.items())),
        "control_object_counts": dict(sorted(control_object_counts.items())),
        "rule_relationships": len(rule_relationships),
        "ti_relationships": len(ti_relationships),
        "relationships": relationship_total,
        "relationship_counts": dict(sorted(relationship_counts.items())),
        "rule_validations": len(rule_validations),
        "ti_validations": len(ti_validations),
        "validations": validation_total,
        "validation_counts": dict(sorted(validation_counts.items())),
        "informational_validation_counts": informational_counts,
        **exception_metrics,
        "quality_exception_register_keys": len(exception_by_id),
        "quality_exception_register_loaded": bool(exception_payload),
        **attribute_metrics,
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

    scalar_keys = (
        "manifest_keys",
        "objects",
        "regular_objects",
        "control_objects",
        "object_split_reconciles",
        "rule_relationships",
        "ti_relationships",
        "relationships",
        "rule_validations",
        "ti_validations",
        "validations",
        "registered_exception_targets",
        "registered_exception_records",
        "excluded_from_quality_score",
        "raw_missing_target_records",
        "unregistered_missing_target_records",
        "quality_exception_register_loaded",
        "attribute_collection",
        "attributes",
        "regular_attributes",
        "control_attributes",
        "attribute_dimensions",
        "attribute_hierarchies",
        "completed_attribute_hierarchies",
        "attribute_collection_errors",
        "attribute_identity_duplicates",
        "attribute_unknown_types",
        "attribute_alias_flag_errors",
        "attribute_inventory_reconciles",
    )
    for key in scalar_keys:
        if key in summary:
            print(f"{key}={summary[key]}")

    mapping_keys = (
        "object_counts",
        "regular_object_counts",
        "control_object_counts",
        "relationship_counts",
        "validation_counts",
        "informational_validation_counts",
        "attribute_type_counts",
    )
    for key in mapping_keys:
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
    return run_smoke_test(
        arguments.catalog_path,
        arguments.exceptions,
    )


if __name__ == "__main__":
    raise SystemExit(main())
