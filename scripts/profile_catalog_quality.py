from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG_ROOT = ROOT_DIR / "data" / "catalog"

DEFAULT_EXCEPTIONS_PATH = (
    ROOT_DIR
    / "config"
    / "catalog_quality_exceptions.json"
)

REQUIRED_FILES = {
    "manifest": "catalog_manifest.json",
    "objects": "catalog_objects.json",
    "relationships": "catalog_relationships.json",
    "evidence": "catalog_evidence.json",
    "validations": "catalog_validations.json",
    "warnings": "catalog_adapter_warnings.json",
    "source_manifests": "catalog_source_manifests.json",
}

PASS_STATUSES = {"VALID"}
TRACKED_NONERROR_STATUSES = {
    "NOT_YET_CATALOGED",
    "PARAMETERIZED_REFERENCE",
    "UNRESOLVED_DYNAMIC_REFERENCE",
    "NOT_CROSS_CHECKED",
}
ERROR_STATUSES = {
    "BROKEN_REFERENCE",
    "INVALID_DEFINITION",
    "DUPLICATE_OBJECT",
    "TYPE_MISMATCH",
}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
    temporary.replace(path)


def record_list(payload: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError(f"{label} must contain a JSON list.")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"{label}[{index}] must be a JSON object.")
        records.append(item)
    return records


def percentage(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 2) if denominator else 100.0


def normalized(value: Any) -> str:
    return str(value or "").strip().upper()


def count_by(records: Iterable[Mapping[str, Any]], key: str) -> dict[str, int]:
    counts = Counter(normalized(record.get(key)) or "<BLANK>" for record in records)
    return dict(sorted(counts.items()))


def _source_manifest_complete(source_manifests: Mapping[str, Any], name: str) -> bool:
    manifest = source_manifests.get(name)
    return isinstance(manifest, dict) and normalized(manifest.get("status")) == "COMPLETE"


def exception_key_from_relationship(
    relationship: Mapping[str, Any],
) -> tuple[str, str, str]:
    return (
        normalized(
            relationship.get(
                "source_qualified_name"
            )
        ),
        normalized(
            relationship.get(
                "relationship_type"
            )
        ),
        normalized(
            relationship.get(
                "target_qualified_name"
            )
        ),
    )


def exception_key_from_config(
    exception: Mapping[str, Any],
) -> tuple[str, str, str]:
    return (
        normalized(
            exception.get("source_object")
        ),
        normalized(
            exception.get(
                "relationship_type"
            )
        ),
        normalized(
            exception.get("target_object")
        ),
    )


def classify_quality_exceptions(
    *,
    relationships: list[dict[str, Any]],
    validations: list[dict[str, Any]],
    exceptions_payload: (
        Mapping[str, Any] | None
    ),
    environment: str,
    database_name: str,
) -> dict[str, Any]:
    """Match accepted exceptions to error validations.

    Raw errors remain unchanged. This function only
    distinguishes accepted errors from unaccepted errors.
    """
    configured_exceptions: list[
        dict[str, Any]
    ] = []

    if exceptions_payload:
        raw_exceptions = (
            exceptions_payload.get(
                "exceptions",
                [],
            )
        )

        if not isinstance(
            raw_exceptions,
            list,
        ):
            raise ValueError(
                "catalog_quality_exceptions.json "
                "must contain an 'exceptions' list."
            )

        configured_exceptions = [
            item
            for item in raw_exceptions
            if isinstance(item, dict)
        ]

    relationships_by_id = {
        str(
            relationship.get(
                "relationship_id"
            )
        ): relationship
        for relationship in relationships
        if relationship.get(
            "relationship_id"
        )
    }

    active_exceptions: dict[
        tuple[str, str, str],
        dict[str, Any],
    ] = {}

    inactive_exceptions: list[
        dict[str, Any]
    ] = []

    for exception in configured_exceptions:
        applies_to_catalog = (
            normalized(
                exception.get("environment")
            )
            == normalized(environment)
            and normalized(
                exception.get("database")
            )
            == normalized(database_name)
        )

        is_accepted = (
            normalized(
                exception.get("disposition")
            )
            == "ACCEPTED_KNOWN_ISSUE"
        )

        if applies_to_catalog and is_accepted:
            key = exception_key_from_config(
                exception
            )
            active_exceptions[key] = exception
        else:
            inactive_exceptions.append(
                exception
            )

    error_statuses = {
        "BROKEN_REFERENCE",
        "INVALID_DEFINITION",
        "DUPLICATE_OBJECT",
        "TYPE_MISMATCH",
    }

    accepted_errors: list[
        dict[str, Any]
    ] = []

    unaccepted_errors: list[
        dict[str, Any]
    ] = []

    matched_exception_ids: set[str] = set()

    for validation in validations:
        validation_status = normalized(
            validation.get(
                "validation_status"
            )
        )

        if (
            validation_status
            not in error_statuses
        ):
            continue

        relationship = (
            relationships_by_id.get(
                str(
                    validation.get(
                        "relationship_id"
                    )
                )
            )
        )

        if relationship is None:
            unaccepted_errors.append(
                {
                    "validation": validation,
                    "relationship": None,
                    "exception": None,
                }
            )
            continue

        relationship_key = (
            exception_key_from_relationship(
                relationship
            )
        )

        exception = active_exceptions.get(
            relationship_key
        )

        result = {
            "validation": validation,
            "relationship": relationship,
            "exception": exception,
        }

        if exception is None:
            unaccepted_errors.append(result)
            continue

        accepted_errors.append(result)

        exception_id = str(
            exception.get(
                "exception_id"
            )
            or ""
        )

        if exception_id:
            matched_exception_ids.add(
                exception_id
            )

    unmatched_active_exceptions = [
        exception
        for exception
        in active_exceptions.values()
        if str(
            exception.get(
                "exception_id"
            )
            or ""
        )
        not in matched_exception_ids
    ]

    return {
        "configured_exception_count": (
            len(configured_exceptions)
        ),
        "active_exception_count": (
            len(active_exceptions)
        ),
        "accepted_error_count": (
            len(accepted_errors)
        ),
        "unaccepted_error_count": (
            len(unaccepted_errors)
        ),
        "unmatched_active_exception_count": (
            len(
                unmatched_active_exceptions
            )
        ),
        "accepted_errors": accepted_errors,
        "unaccepted_errors": (
            unaccepted_errors
        ),
        "unmatched_active_exceptions": (
            unmatched_active_exceptions
        ),
        "inactive_exceptions": (
            inactive_exceptions
        ),

        
    }



def build_quality_report(
    *,
    manifest: Mapping[str, Any],
    objects: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    validations: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    source_manifests: Mapping[str, Any],
    exceptions_payload: (
    Mapping[str, Any] | None
    ) = None,
    ) -> dict[str, Any]:
    object_ids = {str(item.get("object_id") or "") for item in objects if item.get("object_id")}
    relationship_ids = {
        str(item.get("relationship_id") or "")
        for item in relationships
        if item.get("relationship_id")
    }

    duplicate_object_ids = len(object_ids) != sum(bool(item.get("object_id")) for item in objects)
    duplicate_relationship_ids = len(relationship_ids) != sum(
        bool(item.get("relationship_id")) for item in relationships
    )

    unknown_objects = [item for item in objects if normalized(item.get("object_type")) == "UNKNOWN"]
    blank_object_names = [item for item in objects if not str(item.get("object_name") or "").strip()]

    orphan_source_relationships = [
        item for item in relationships
        if str(item.get("source_object_id") or "") not in object_ids
    ]
    invalid_target_links = [
        item for item in relationships
        if item.get("target_object_id")
        and str(item.get("target_object_id")) not in object_ids
    ]
    blank_relationship_sources = [
        item for item in relationships if not str(item.get("source_qualified_name") or "").strip()
    ]
    blank_relationship_targets = [
        item for item in relationships if not str(item.get("target_qualified_name") or "").strip()
    ]

    orphan_evidence_sources = [
        item for item in evidence
        if str(item.get("source_object_id") or "") not in object_ids
    ]
    blank_evidence_targets = [
        item for item in evidence if not str(item.get("target_expression") or "").strip()
    ]

    validation_relationship_ids = {
        str(item.get("relationship_id"))
        for item in validations
        if item.get("relationship_id")
    }
    orphan_validation_relationships = [
        item for item in validations
        if item.get("relationship_id")
        and str(item.get("relationship_id")) not in relationship_ids
    ]
    validated_relationship_count = len(relationship_ids & validation_relationship_ids)
    missing_relationship_validation_ids = sorted(relationship_ids - validation_relationship_ids)

    validation_status_counts = count_by(validations, "validation_status")
    error_validation_count = sum(
        count for status, count in validation_status_counts.items()
        if status in ERROR_STATUSES
    )
    tracked_nonerror_count = sum(
        count for status, count in validation_status_counts.items()
        if status in TRACKED_NONERROR_STATUSES
    )
    valid_validation_count = sum(
        count for status, count in validation_status_counts.items()
        if status in PASS_STATUSES
    )

    exception_analysis = (
    classify_quality_exceptions(
        relationships=relationships,
        validations=validations,
        exceptions_payload=(
            exceptions_payload
        ),
        environment=str(
            manifest.get(
                "environment"
            )
            or ""
        ),
        database_name=str(
            manifest.get(
                "database_name"
            )
            or ""
        ),
    )
)

    source_manifest_checks = {
        "metadata": _source_manifest_complete(source_manifests, "metadata"),
        "ti_lineage": _source_manifest_complete(source_manifests, "ti_lineage"),
        "rule_lineage": _source_manifest_complete(source_manifests, "rule_lineage"),
    }

    gates = {
        "catalog_status_complete": normalized(manifest.get("status")) == "COMPLETE",
        "source_manifests_complete": all(source_manifest_checks.values()),
        "adapter_warning_count_zero": len(warnings) == 0,
        "object_ids_unique": not duplicate_object_ids,
        "relationship_ids_unique": not duplicate_relationship_ids,
        "unknown_object_count_zero": len(unknown_objects) == 0,
        "blank_object_name_count_zero": len(blank_object_names) == 0,
        "orphan_relationship_source_count_zero": len(orphan_source_relationships) == 0,
        "invalid_relationship_target_link_count_zero": len(invalid_target_links) == 0,
        "blank_relationship_source_count_zero": len(blank_relationship_sources) == 0,
        "blank_relationship_target_count_zero": len(blank_relationship_targets) == 0,
        "orphan_evidence_source_count_zero": len(orphan_evidence_sources) == 0,
        "blank_evidence_target_count_zero": len(blank_evidence_targets) == 0,
        "orphan_validation_relationship_count_zero": len(orphan_validation_relationships) == 0,
        "relationship_validation_coverage_100": validated_relationship_count == len(relationship_ids),
        "unaccepted_error_validation_count_zero": (
                exception_analysis[
                    "unaccepted_error_count"
                ]
                == 0
            ),
            "accepted_exceptions_are_matched": (
                exception_analysis[
                    "unmatched_active_exception_count"
                ]
                == 0
            ),
        
        
    }

    passed_gates = sum(gates.values())
    quality_control_score = percentage(passed_gates, len(gates))
    relationship_validation_coverage = percentage(
        validated_relationship_count, len(relationship_ids)
    )
    validation_resolution_rate = percentage(
        valid_validation_count, len(validations)
    )

 

  

 

    return {
        "quality_status": "PASS" if all(gates.values()) else "FAIL",
        "quality_control_score_percent": quality_control_score,
        "relationship_validation_coverage_percent": relationship_validation_coverage,
        "validation_resolution_rate_percent": validation_resolution_rate,
                "counts": {
                "objects": len(objects),
                "relationships": len(
                    relationships
                ),
                "evidence": len(evidence),
                "validations": len(validations),
                "adapter_warnings": len(warnings),
                "unknown_objects": len(
                    unknown_objects
                ),
                "orphan_relationship_sources": len(
                    orphan_source_relationships
                ),
                "invalid_relationship_target_links": len(
                    invalid_target_links
                ),
                "orphan_evidence_sources": len(
                    orphan_evidence_sources
                ),
                "blank_evidence_targets": len(
                    blank_evidence_targets
                ),
                "validated_relationships": (
                    validated_relationship_count
                ),
                "relationships_without_linked_validation": len(
                    missing_relationship_validation_ids
                ),
                "orphan_validation_relationships": len(
                    orphan_validation_relationships
                ),
                "valid_validations": (
                    valid_validation_count
                ),
                "tracked_nonerror_validations": (
                    tracked_nonerror_count
                ),
                "raw_error_validations": (
                    error_validation_count
                ),
                "accepted_error_validations": (
                    exception_analysis[
                        "accepted_error_count"
                    ]
                ),
                "unaccepted_error_validations": (
                    exception_analysis[
                        "unaccepted_error_count"
                    ]
                ),
                "active_quality_exceptions": (
                    exception_analysis[
                        "active_exception_count"
                    ]
                ),
                "unmatched_active_exceptions": (
                    exception_analysis[
                        "unmatched_active_exception_count"
                    ]
                ),
        },
        "distributions": {
            "object_types": count_by(objects, "object_type"),
            "relationship_types": count_by(relationships, "relationship_type"),
            "discovery_methods": count_by(relationships, "discovery_method"),
            "relationship_validation_statuses": count_by(
                relationships, "validation_status"
            ),
            "validation_statuses": validation_status_counts,
        },
        "source_manifest_checks": source_manifest_checks,
        "quality_gates": gates,
        "issues": {
            "unknown_objects": unknown_objects[:25],
            "orphan_relationship_sources": orphan_source_relationships[:25],
            "invalid_relationship_target_links": invalid_target_links[:25],
            "orphan_evidence_sources": orphan_evidence_sources[:25],
            "blank_evidence_targets": blank_evidence_targets[:25],
            "missing_relationship_validation_ids": missing_relationship_validation_ids[:100],
            "orphan_validation_relationships": orphan_validation_relationships[:25],
        },
        "quality_exceptions": exception_analysis,
        "interpretation": {
            "quality_control_score": (
                "Percentage of structural and integrity gates that passed."
            ),
            "relationship_validation_coverage": (
                "Percentage of catalog relationships linked to at least one validation record."
            ),
            "validation_resolution_rate": (
                "Percentage of validation records with VALID status. Dynamic and not-yet-cataloged references remain visible and do not get relabeled as valid."
            ),
        },
    }


def load_catalog(
    catalog_root: Path,
    exceptions_path: Path | None = (
        DEFAULT_EXCEPTIONS_PATH
    ),
) -> dict[str, Any]:
    catalog_root = Path(catalog_root)

    missing = [
        file_name
        for file_name
        in REQUIRED_FILES.values()
        if not (
            catalog_root / file_name
        ).exists()
    ]

    if missing:
        raise FileNotFoundError(
            "Required catalog files were not found: "
            + ", ".join(
                sorted(missing)
            )
        )

    manifest = read_json(
        catalog_root
        / REQUIRED_FILES["manifest"]
    )

    source_manifests = read_json(
        catalog_root
        / REQUIRED_FILES[
            "source_manifests"
        ]
    )

    if not isinstance(manifest, dict):
        raise ValueError(
            "catalog_manifest.json must "
            "contain a JSON object."
        )

    if not isinstance(
        source_manifests,
        dict,
    ):
        raise ValueError(
            "catalog_source_manifests.json "
            "must contain a JSON object."
        )

    exceptions_payload: dict[
        str,
        Any,
    ] = {
        "exceptions": []
    }

    if exceptions_path is not None:
        exceptions_path = Path(
            exceptions_path
        )

        if exceptions_path.exists():
            loaded_exceptions = read_json(
                exceptions_path
            )

            if not isinstance(
                loaded_exceptions,
                dict,
            ):
                raise ValueError(
                    "The catalog quality exception "
                    "register must contain a JSON "
                    "object."
                )

            exceptions_payload = (
                loaded_exceptions
            )

    return {
        "manifest": manifest,
        "objects": record_list(
            read_json(
                catalog_root
                / REQUIRED_FILES["objects"]
            ),
            "objects",
        ),
        "relationships": record_list(
            read_json(
                catalog_root
                / REQUIRED_FILES[
                    "relationships"
                ]
            ),
            "relationships",
        ),
        "evidence": record_list(
            read_json(
                catalog_root
                / REQUIRED_FILES["evidence"]
            ),
            "evidence",
        ),
        "validations": record_list(
            read_json(
                catalog_root
                / REQUIRED_FILES[
                    "validations"
                ]
            ),
            "validations",
        ),
        "warnings": record_list(
            read_json(
                catalog_root
                / REQUIRED_FILES["warnings"]
            ),
            "warnings",
        ),
        "source_manifests": (
            source_manifests
        ),
        "exceptions_payload": (
            exceptions_payload
        ),
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile unified TM1 catalog quality.")
    parser.add_argument("--catalog-root", type=Path, default=DEFAULT_CATALOG_ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--fail-on-quality-gate",
        action="store_true",
        help="Return exit code 1 when any quality gate fails.",
    )
    parser.add_argument(
    "--exceptions",
    type=Path,
    default=DEFAULT_EXCEPTIONS_PATH,
    help=(
        "Path to the catalog quality "
        "exception register."
    ),
)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        catalog = load_catalog(arguments.catalog_root)
        report = build_quality_report(**catalog)
        output = arguments.output or arguments.catalog_root / "catalog_quality.json"
        write_json(output, report)
        counts = report["counts"]
        print("=" * 70)
        print("TM1 CATALOG QUALITY")
        print("=" * 70)
        print(f"Status                         : {report['quality_status']}")
        print(f"Quality control score          : {report['quality_control_score_percent']:.2f}%")
        print(f"Relationship validation cover : {report['relationship_validation_coverage_percent']:.2f}%")
        print(f"Validation resolution rate     : {report['validation_resolution_rate_percent']:.2f}%")
        print(f"Objects                        : {counts['objects']:,}")
        print(f"Relationships                  : {counts['relationships']:,}")
        print(f"Evidence                       : {counts['evidence']:,}")
        print(f"Validations                    : {counts['validations']:,}")
        print(
                f"Raw error validations          : "
                f"{counts['raw_error_validations']:,}"
            )

        print(
            f"Accepted known exceptions      : "
            f"{counts['accepted_error_validations']:,}"
        )

        print(
            f"Unaccepted error validations   : "
            f"{counts['unaccepted_error_validations']:,}"
        )
        print(f"Output                         : {output}")
        if arguments.fail_on_quality_gate and report["quality_status"] != "PASS":
            return 1
        return 0
    except Exception as error:
        print(f"Catalog quality profiling failed: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
