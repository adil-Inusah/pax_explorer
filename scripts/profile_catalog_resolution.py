from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG_ROOT = ROOT_DIR / "data" / "catalog"
OUTPUT_FILE = "catalog_resolution_profile.json"

RESOLVED_STATUSES = {"VALID"}
BACKLOG_STATUSES = {
    "NOT_YET_CATALOGED",
    "UNRESOLVED_DYNAMIC_REFERENCE",
    "PARAMETERIZED_REFERENCE",
    "BROKEN_REFERENCE",
    "NOT_CROSS_CHECKED",
}

DOMAIN_BY_TARGET_TYPE = {
    "hierarchy": "HIERARCHY_INVENTORY",
    "attribute": "ATTRIBUTE_INVENTORY_AND_RESOLUTION",
    "view": "VIEW_INVENTORY_AND_RESOLUTION",
    "subset": "SUBSET_INVENTORY_AND_RESOLUTION",
    "file": "EXTERNAL_FILE_MODELING",
    "command": "EXTERNAL_COMMAND_MODELING",
    "cube": "TI_DYNAMIC_CUBE_RESOLUTION",
    "dimension": "TI_DYNAMIC_DIMENSION_RESOLUTION",
    "process": "TI_DYNAMIC_PROCESS_RESOLUTION",
    "element": "ELEMENT_INVENTORY_AND_RESOLUTION",
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


def normalized(value: Any) -> str:
    return str(value or "").strip().upper()


def percentage(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 2) if denominator else 100.0


def sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def relationship_reference_count(record: Mapping[str, Any]) -> int:
    try:
        value = int(record.get("reference_count") or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, value)


def build_resolution_profile(
    *,
    relationships: list[dict[str, Any]],
    validations: list[dict[str, Any]],
    quality_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    relationships_by_id = {
        str(item.get("relationship_id")): item
        for item in relationships
        if item.get("relationship_id")
    }

    accepted_ids: set[str] = set()
    if quality_report:
        exception_analysis = quality_report.get("quality_exceptions", {})
        if isinstance(exception_analysis, dict):
            for item in exception_analysis.get("accepted_errors", []):
                if not isinstance(item, dict):
                    continue
                validation = item.get("validation", {})
                if isinstance(validation, dict) and validation.get("relationship_id"):
                    accepted_ids.add(str(validation["relationship_id"]))

    status_by_relationship: dict[str, str] = {}
    for validation in validations:
        relationship_id = str(validation.get("relationship_id") or "")
        if relationship_id:
            status_by_relationship[relationship_id] = normalized(
                validation.get("validation_status")
            )

    total = len(relationships_by_id)
    resolved = 0
    backlog: list[dict[str, Any]] = []

    by_status: Counter[str] = Counter()
    by_target_type: Counter[str] = Counter()
    by_relationship_type: Counter[str] = Counter()
    by_discovery_method: Counter[str] = Counter()
    by_status_target: Counter[str] = Counter()
    by_status_relationship: Counter[str] = Counter()
    by_status_method: Counter[str] = Counter()
    by_domain: Counter[str] = Counter()

    weighted_by_domain: Counter[str] = Counter()
    target_frequency: Counter[str] = Counter()
    expression_frequency: Counter[str] = Counter()
    variable_frequency: Counter[str] = Counter()
    distinct_targets_by_domain: defaultdict[str, set[str]] = defaultdict(set)

    for relationship_id, relationship in relationships_by_id.items():
        status = status_by_relationship.get(relationship_id, "NOT_CROSS_CHECKED")
        if status in RESOLVED_STATUSES:
            resolved += 1
            continue

        target_type = str(relationship.get("target_type") or "unknown").strip().lower()
        relationship_type = normalized(relationship.get("relationship_type")) or "<BLANK>"
        method = str(relationship.get("discovery_method") or "<BLANK>").strip()
        target = str(relationship.get("target_qualified_name") or "<BLANK>").strip()
        properties = relationship.get("properties") or {}
        if not isinstance(properties, dict):
            properties = {}
        expression = str(properties.get("target_expression") or "").strip()
        reference_count = relationship_reference_count(relationship)
        domain = DOMAIN_BY_TARGET_TYPE.get(
            target_type, f"OTHER_{target_type.upper()}"
        )
        accepted_exception = relationship_id in accepted_ids

        item = {
            "relationship_id": relationship_id,
            "validation_status": status,
            "accepted_exception": accepted_exception,
            "source": relationship.get("source_qualified_name"),
            "relationship_type": relationship.get("relationship_type"),
            "target_type": target_type,
            "target": target,
            "target_expression": expression or None,
            "discovery_method": method,
            "function_name": relationship.get("function_name"),
            "confidence": relationship.get("confidence"),
            "reference_count": reference_count,
            "resolution_domain": domain,
        }
        backlog.append(item)

        by_status[status] += 1
        by_target_type[target_type] += 1
        by_relationship_type[relationship_type] += 1
        by_discovery_method[method] += 1
        by_status_target[f"{status}, {target_type}"] += 1
        by_status_relationship[f"{status}, {relationship_type}"] += 1
        by_status_method[f"{status}, {method}"] += 1
        by_domain[domain] += 1
        weighted_by_domain[domain] += reference_count
        target_frequency[target] += 1
        distinct_targets_by_domain[domain].add(target.casefold())
        if expression:
            expression_frequency[expression] += 1
            if expression.replace("_", "").isalnum() and not (
                len(expression) >= 2 and expression[0] in {"'", '"'}
                and expression[-1] == expression[0]
            ):
                variable_frequency[expression] += 1

    backlog_count = len(backlog)
    literal_uncataloged = by_status.get("NOT_YET_CATALOGED", 0)
    dynamic = by_status.get("UNRESOLVED_DYNAMIC_REFERENCE", 0)
    metadata_only_potential = resolved + literal_uncataloged

    domain_opportunities = []
    for domain, count in sorted(by_domain.items(), key=lambda item: (-item[1], item[0])):
        domain_opportunities.append(
            {
                "domain": domain,
                "relationship_count": count,
                "distinct_target_count": len(distinct_targets_by_domain[domain]),
                "reference_weighted_count": weighted_by_domain[domain],
                "share_of_backlog_percent": percentage(count, backlog_count),
                "maximum_resolution_uplift_points": round(100.0 * count / total, 2)
                if total else 0.0,
            }
        )

    top_targets = [
        {"target": name, "relationship_count": count}
        for name, count in target_frequency.most_common(50)
    ]
    top_expressions = [
        {"target_expression": name, "relationship_count": count}
        for name, count in expression_frequency.most_common(50)
    ]
    top_variables = [
        {"variable": name, "relationship_count": count}
        for name, count in variable_frequency.most_common(50)
    ]

    return {
        "resolution_status": "PROFILED",
        "metrics": {
            "total_relationships": total,
            "resolved_relationships": resolved,
            "resolution_backlog_relationships": backlog_count,
            "current_resolution_rate_percent": percentage(resolved, total),
            "metadata_only_potential_resolved_relationships": metadata_only_potential,
            "metadata_only_potential_resolution_rate_percent": percentage(
                metadata_only_potential, total
            ),
            "dynamic_relationships": dynamic,
            "literal_not_yet_cataloged_relationships": literal_uncataloged,
            "accepted_exception_relationships": len(accepted_ids),
        },
        "distributions": {
            "backlog_by_validation_status": sorted_counter(by_status),
            "backlog_by_target_type": sorted_counter(by_target_type),
            "backlog_by_relationship_type": sorted_counter(by_relationship_type),
            "backlog_by_discovery_method": sorted_counter(by_discovery_method),
            "backlog_by_status_and_target_type": sorted_counter(by_status_target),
            "backlog_by_status_and_relationship_type": sorted_counter(
                by_status_relationship
            ),
            "backlog_by_status_and_discovery_method": sorted_counter(by_status_method),
        },
        "resolution_opportunities": domain_opportunities,
        "top_unresolved_targets": top_targets,
        "top_target_expressions": top_expressions,
        "top_variable_expressions": top_variables,
        "backlog": sorted(
            backlog,
            key=lambda item: (
                item["validation_status"],
                item["target_type"],
                str(item["target"]).casefold(),
                str(item["source"]).casefold(),
            ),
        ),
        "recommended_sequence": [
            "HIERARCHY_INVENTORY",
            "ATTRIBUTE_INVENTORY_AND_RESOLUTION",
            "TI_ATTRIBUTE_EXPRESSION_RESOLUTION",
            "SUBSET_INVENTORY_AND_RESOLUTION",
            "VIEW_INVENTORY_AND_RESOLUTION",
            "EXTERNAL_FILE_AND_COMMAND_MODELING",
            "TI_CUBE_DIMENSION_PROCESS_RESOLUTION",
        ],
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile TM1 catalog relationship resolution opportunities."
    )
    parser.add_argument("--catalog-root", type=Path, default=DEFAULT_CATALOG_ROOT)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    root = arguments.catalog_root
    try:
        relationships = record_list(
            read_json(root / "catalog_relationships.json"), "relationships"
        )
        validations = record_list(
            read_json(root / "catalog_validations.json"), "validations"
        )
        quality_path = root / "catalog_quality.json"
        quality_report = read_json(quality_path) if quality_path.exists() else None
        if quality_report is not None and not isinstance(quality_report, dict):
            raise ValueError("catalog_quality.json must contain a JSON object.")

        profile = build_resolution_profile(
            relationships=relationships,
            validations=validations,
            quality_report=quality_report,
        )
        output = arguments.output or root / OUTPUT_FILE
        write_json(output, profile)

        metrics = profile["metrics"]
        print("=" * 70)
        print("TM1 CATALOG RESOLUTION PROFILE")
        print("=" * 70)
        print(f"Relationships                  : {metrics['total_relationships']:,}")
        print(f"Resolved relationships         : {metrics['resolved_relationships']:,}")
        print(f"Resolution backlog             : {metrics['resolution_backlog_relationships']:,}")
        print(f"Current resolution rate        : {metrics['current_resolution_rate_percent']:.2f}%")
        print(f"Metadata-only potential rate   : {metrics['metadata_only_potential_resolution_rate_percent']:.2f}%")
        print(f"Dynamic relationships          : {metrics['dynamic_relationships']:,}")
        print(f"Literal uncataloged targets    : {metrics['literal_not_yet_cataloged_relationships']:,}")
        print(f"Output                         : {output}")
        return 0
    except Exception as error:
        print(f"Catalog resolution profiling failed: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
