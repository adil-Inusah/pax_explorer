from __future__ import annotations

"""Profile deferred TM1 references against governed first-class catalogs.

This module is diagnostic only. It does not overwrite TI or rule validations.
It classifies candidate matches for hierarchy, view, subset, and attribute
references so matching policy can be reviewed before validation statuses change.
"""

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

CURRENT_ROOT = ROOT_DIR / "data" / "current"
SNAPSHOT_ROOT = ROOT_DIR / "data" / "snapshots"

TI_RELATIONSHIPS = CURRENT_ROOT / "ti_relationships.json"
TI_VALIDATIONS = CURRENT_ROOT / "ti_relationship_validations.json"
RULE_RELATIONSHIPS = CURRENT_ROOT / "rule_relationships.json"
RULE_VALIDATIONS = CURRENT_ROOT / "rule_relationship_validations.json"
HIERARCHIES = CURRENT_ROOT / "hierarchies.json"
VIEWS = CURRENT_ROOT / "views.json"
SUBSETS = CURRENT_ROOT / "subsets.json"
ATTRIBUTES = CURRENT_ROOT / "attributes.json"

DEFERRED_STATUS_DOMAIN = {
    "HIERARCHY_CATALOG_DEFERRED": "HIERARCHY",
    "RUNTIME_VIEW_REFERENCE": "VIEW",
    "RUNTIME_SUBSET_REFERENCE": "SUBSET",
    "ATTRIBUTE_CATALOG_DEFERRED": "ATTRIBUTE",
}

CREATE_RELATIONSHIPS = {
    "CREATES_ATTRIBUTE",
    "CREATES_SUBSET",
    "CREATES_VIEW",
    "UPDATES_HIERARCHY",
}
DELETE_RELATIONSHIPS = {
    "DELETES_ATTRIBUTE",
    "DELETES_SUBSET",
    "DELETES_VIEW",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def snapshot_id(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def clean(value: Any) -> str:
    return str(value or "").strip()


def key(value: Any) -> str:
    return " ".join(clean(value).casefold().split())


def token(value: Any) -> str:
    return clean(value).upper().replace(" ", "_")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write("\n")
    temporary.replace(path)


def records(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    if not isinstance(payload, list):
        raise TypeError(f"{path.name} must contain a JSON array.")
    if any(not isinstance(item, Mapping) for item in payload):
        raise TypeError(f"{path.name} contains a non-object record.")
    return [dict(item) for item in payload]


def first(record: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = record.get(name)
        if value is not None and clean(value):
            return value
    return None


def relationship_id(record: Mapping[str, Any]) -> str:
    return clean(first(record, "relationship_id", "id"))



def semantic_relationship_key(
    record: Mapping[str, Any],
) -> tuple[str, str, str, str]:
    source_name = first(
        record,
        "process_name",
        "cube_name",
        "source_name",
        "source_cube",
        "source_object_name",
        "source",
    )

    relationship_name = first(
        record,
        "relationship_type",
        "type",
        "relationshipType",
    )

    target_type = first(
        record,
        "target_type",
        "target_object_type",
        "targetType",
    )

    target_name = first(
        record,
        "target_name",
        "resolved_target_name",
        "target_expression",
        "target",
    )

    return (
        key(source_name),
        token(relationship_name),
        key(target_type),
        key(target_name),
    )


def relationship_tiebreaker(
    record: Mapping[str, Any],
) -> tuple[str, ...]:
    """Provide deterministic ordering within duplicate semantic groups."""

    return (
        key(
            first(
                record,
                "procedure",
                "procedure_name",
                "rule_section",
                "section",
            )
        ),
        key(
            first(
                record,
                "source_expression",
                "expression",
                "raw_expression",
                "statement",
            )
        ),
        key(
            first(
                record,
                "dimension_name",
                "target_dimension",
                "dimension",
            )
        ),
        key(
            first(
                record,
                "hierarchy_name",
                "target_hierarchy",
                "hierarchy",
            )
        ),
        key(
            first(
                record,
                "attribute_name",
                "attribute",
            )
        ),
        key(
            first(
                record,
                "subset_name",
                "subset",
            )
        ),
        key(
            first(
                record,
                "view_name",
                "view",
            )
        ),
        clean(
            first(
                record,
                "line_number",
                "source_line",
                "line",
            )
        ),
        clean(
            first(
                record,
                "column_number",
                "column",
            )
        ),
        clean(
            first(
                record,
                "evidence_index",
                "sequence",
                "position",
            )
        ),
    )

def display_relationship_id(
    record: Mapping[str, Any],
    origin: str,
) -> str:
    explicit = relationship_id(record)
    if explicit:
        return explicit
    semantic = semantic_relationship_key(record)
    return "semantic::" + origin.casefold() + "::" + "::".join(semantic)


def relationship_type(record: Mapping[str, Any]) -> str:
    return token(first(record, "relationship_type", "type", "relationshipType"))


def validation_status(record: Mapping[str, Any]) -> str:
    return token(
        first(
            record,
            "validation_status",
            "status",
            "resolution_status",
            "result",
        )
    )


def field(record: Mapping[str, Any], *names: str) -> str:
    return clean(first(record, *names))


def dynamic_value(value: str) -> bool:
    if not value:
        return False
    markers = ("|", "${", "@{", "%", "&", "[", "]", "??")
    return any(marker in value for marker in markers)


def split_qualified(value: str) -> list[str]:
    return [part.strip() for part in value.split("::") if part.strip()]


def extract_context(record: Mapping[str, Any], domain: str) -> dict[str, str]:
    target_name = field(
        record,
        "target_name",
        "resolved_target_name",
        "target_expression",
        "object_name",
    )
    parts = split_qualified(target_name)
    context = {
        "cube_name": field(record, "cube_name", "target_cube", "source_cube"),
        "dimension_name": field(
            record,
            "dimension_name",
            "target_dimension",
            "dimension",
        ),
        "hierarchy_name": field(
            record,
            "hierarchy_name",
            "target_hierarchy",
            "hierarchy",
        ),
        "attribute_name": field(record, "attribute_name", "attribute"),
        "subset_name": field(record, "subset_name", "subset"),
        "view_name": field(record, "view_name", "view"),
        "target_name": target_name,
    }

    if domain == "VIEW":
        if not context["view_name"]:
            context["view_name"] = parts[-1] if parts else target_name
        if not context["cube_name"] and len(parts) >= 2:
            context["cube_name"] = parts[-2]
    elif domain == "HIERARCHY":
        if not context["hierarchy_name"]:
            context["hierarchy_name"] = parts[-1] if parts else target_name
        if not context["dimension_name"] and len(parts) >= 2:
            context["dimension_name"] = parts[-2]
    elif domain == "SUBSET":
        if not context["subset_name"]:
            context["subset_name"] = parts[-1] if parts else target_name
        if not context["hierarchy_name"] and len(parts) >= 2:
            context["hierarchy_name"] = parts[-2]
        if not context["dimension_name"] and len(parts) >= 3:
            context["dimension_name"] = parts[-3]
    elif domain == "ATTRIBUTE":
        if not context["attribute_name"]:
            context["attribute_name"] = parts[-1] if parts else target_name
        if not context["hierarchy_name"] and len(parts) >= 2:
            context["hierarchy_name"] = parts[-2]
        if not context["dimension_name"] and len(parts) >= 3:
            context["dimension_name"] = parts[-3]
    return context


class CatalogIndexes:
    def __init__(
        self,
        hierarchy_records: list[dict[str, Any]],
        view_records: list[dict[str, Any]],
        subset_records: list[dict[str, Any]],
        attribute_records: list[dict[str, Any]],
    ) -> None:
        self.hierarchy_exact: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        self.hierarchy_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in hierarchy_records:
            dimension = key(record.get("dimension_name"))
            hierarchy = key(record.get("hierarchy_name"))
            self.hierarchy_exact[(dimension, hierarchy)].append(record)
            self.hierarchy_by_name[hierarchy].append(record)

        self.view_exact: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        self.view_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in view_records:
            cube = key(record.get("cube_name"))
            view = key(record.get("view_name"))
            self.view_exact[(cube, view)].append(record)
            self.view_by_name[view].append(record)

        self.subset_exact: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        self.subset_by_dimension_name: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        self.subset_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in subset_records:
            dimension = key(record.get("dimension_name"))
            hierarchy = key(record.get("hierarchy_name"))
            subset = key(record.get("subset_name"))
            self.subset_exact[(dimension, hierarchy, subset)].append(record)
            self.subset_by_dimension_name[(dimension, subset)].append(record)
            self.subset_by_name[subset].append(record)

        self.attribute_exact: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        self.attribute_by_dimension_name: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        self.attribute_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in attribute_records:
            dimension = key(record.get("dimension_name"))
            hierarchy = key(record.get("hierarchy_name"))
            attribute = key(record.get("attribute_name"))
            self.attribute_exact[(dimension, hierarchy, attribute)].append(record)
            self.attribute_by_dimension_name[(dimension, attribute)].append(record)
            self.attribute_by_name[attribute].append(record)


def node_id(record: Mapping[str, Any], domain: str) -> str:
    explicit = clean(record.get("node_id"))
    if explicit:
        return explicit
    if domain == "ATTRIBUTE":
        return (
            f"attribute::{record.get('dimension_name')}::"
            f"{record.get('hierarchy_name')}::{record.get('attribute_name')}"
        )
    return clean(record.get("qualified_name") or record.get("object_name"))


def classify(
    domain: str,
    rel_type: str,
    context: Mapping[str, str],
    indexes: CatalogIndexes,
) -> tuple[str, list[dict[str, Any]], str]:
    relevant_values = [
        context.get("cube_name", ""),
        context.get("dimension_name", ""),
        context.get("hierarchy_name", ""),
        context.get("attribute_name", ""),
        context.get("subset_name", ""),
        context.get("view_name", ""),
        context.get("target_name", ""),
    ]
    if any(dynamic_value(value) for value in relevant_values if value):
        return "DYNAMIC_REFERENCE", [], "Expression contains dynamic tokens"

    candidates: list[dict[str, Any]] = []
    match_class = "INSUFFICIENT_CONTEXT"
    reason = "Required parent or target name is missing"

    if domain == "HIERARCHY":
        dimension = key(context.get("dimension_name"))
        hierarchy = key(context.get("hierarchy_name"))
        if dimension and hierarchy:
            candidates = indexes.hierarchy_exact.get((dimension, hierarchy), [])
            match_class = "EXACT_MATCH" if len(candidates) == 1 else "TARGET_NOT_IN_CATALOG"
            reason = "Dimension and hierarchy were supplied"
        elif hierarchy:
            candidates = indexes.hierarchy_by_name.get(hierarchy, [])
            match_class = "UNIQUE_GLOBAL_MATCH" if len(candidates) == 1 else (
                "AMBIGUOUS_MATCH" if len(candidates) > 1 else "TARGET_NOT_IN_CATALOG"
            )
            reason = "Hierarchy name was supplied without dimension"

    elif domain == "VIEW":
        cube = key(context.get("cube_name"))
        view = key(context.get("view_name"))
        if cube and view:
            candidates = indexes.view_exact.get((cube, view), [])
            match_class = "EXACT_MATCH" if len(candidates) == 1 else "TARGET_NOT_IN_CATALOG"
            reason = "Cube and view were supplied"
        elif view:
            candidates = indexes.view_by_name.get(view, [])
            match_class = "UNIQUE_GLOBAL_MATCH" if len(candidates) == 1 else (
                "AMBIGUOUS_MATCH" if len(candidates) > 1 else "TARGET_NOT_IN_CATALOG"
            )
            reason = "View name was supplied without cube"

    elif domain in {"SUBSET", "ATTRIBUTE"}:
        dimension = key(context.get("dimension_name"))
        hierarchy = key(context.get("hierarchy_name"))
        name = key(
            context.get("subset_name")
            if domain == "SUBSET"
            else context.get("attribute_name")
        )
        exact = indexes.subset_exact if domain == "SUBSET" else indexes.attribute_exact
        by_dimension = (
            indexes.subset_by_dimension_name
            if domain == "SUBSET"
            else indexes.attribute_by_dimension_name
        )
        by_name = indexes.subset_by_name if domain == "SUBSET" else indexes.attribute_by_name

        if dimension and hierarchy and name:
            candidates = exact.get((dimension, hierarchy, name), [])
            match_class = "EXACT_MATCH" if len(candidates) == 1 else "TARGET_NOT_IN_CATALOG"
            reason = "Dimension, hierarchy, and target name were supplied"
        elif dimension and name:
            candidates = by_dimension.get((dimension, name), [])
            default_matches = [
                item
                for item in candidates
                if key(item.get("hierarchy_name")) == dimension
                or item.get("is_default_hierarchy") is True
            ]
            if len(default_matches) == 1:
                candidates = default_matches
                match_class = "DEFAULT_HIERARCHY_MATCH"
            elif len(candidates) == 1:
                match_class = "UNIQUE_HIERARCHY_MATCH"
            elif len(candidates) > 1:
                match_class = "AMBIGUOUS_MATCH"
            else:
                match_class = "TARGET_NOT_IN_CATALOG"
            reason = "Dimension and target name were supplied without hierarchy"
        elif name:
            candidates = by_name.get(name, [])
            match_class = "UNIQUE_GLOBAL_MATCH" if len(candidates) == 1 else (
                "AMBIGUOUS_MATCH" if len(candidates) > 1 else "TARGET_NOT_IN_CATALOG"
            )
            reason = "Only target name was supplied"

    if match_class == "TARGET_NOT_IN_CATALOG":
        if rel_type in CREATE_RELATIONSHIPS:
            match_class = "VALID_CREATE_TARGET"
            reason += "; absent target is valid for a create operation"
        elif rel_type in DELETE_RELATIONSHIPS:
            match_class = "MISSING_DELETE_TARGET"
            reason += "; delete target is absent"
    elif match_class in {
        "EXACT_MATCH",
        "DEFAULT_HIERARCHY_MATCH",
        "UNIQUE_HIERARCHY_MATCH",
        "UNIQUE_GLOBAL_MATCH",
    } and rel_type in CREATE_RELATIONSHIPS:
        match_class = "EXISTING_CREATE_TARGET"
        reason += "; create target already exists"

    return match_class, candidates, reason


def profile_catalog_matches(
    *,
    ti_relationships_path: Path = TI_RELATIONSHIPS,
    ti_validations_path: Path = TI_VALIDATIONS,
    rule_relationships_path: Path = RULE_RELATIONSHIPS,
    rule_validations_path: Path = RULE_VALIDATIONS,
    hierarchies_path: Path = HIERARCHIES,
    views_path: Path = VIEWS,
    subsets_path: Path = SUBSETS,
    attributes_path: Path = ATTRIBUTES,
    snapshot_root: Path = SNAPSHOT_ROOT,
    current_root: Path = CURRENT_ROOT,
    timestamp: datetime | None = None,
    publish_current: bool = True,
) -> dict[str, Any]:
    started = timestamp or utc_now()
    run_id = snapshot_id(started)
    snapshot_dir = snapshot_root / run_id

    relationship_sets = {
        "TI": records(ti_relationships_path),
        "RULE": records(rule_relationships_path),
    }
    validation_sets = {
        "TI": records(ti_validations_path),
        "RULE": records(rule_validations_path),
    }
    indexes = CatalogIndexes(
        records(hierarchies_path),
        records(views_path),
        records(subsets_path),
        records(attributes_path),
    )

    detail: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    input_status_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()

    for origin in ("TI", "RULE"):
        by_id: dict[str, dict[str, Any]] = {}
        relationship_groups: dict[
            tuple[str, str, str, str],
            list[dict[str, Any]],
        ] = defaultdict(list)
        validation_groups: dict[
            tuple[str, str, str, str],
            list[dict[str, Any]],
        ] = defaultdict(list)

        for relationship_record in relationship_sets[origin]:
            explicit_id = relationship_id(relationship_record)
            if explicit_id:
                by_id[explicit_id] = relationship_record
            relationship_groups[
                semantic_relationship_key(relationship_record)
            ].append(relationship_record)

        # Explicit IDs are authoritative. Records without an explicit ID are
        # grouped by the same semantic identity used by the lineage collectors.
        explicit_pairs: list[
            tuple[dict[str, Any], dict[str, Any]]
        ] = []
        for validation in validation_sets[origin]:
            status = validation_status(validation)
            if status not in DEFERRED_STATUS_DOMAIN:
                continue
            explicit_id = relationship_id(validation)
            if explicit_id and explicit_id in by_id:
                explicit_pairs.append((validation, by_id[explicit_id]))
            else:
                validation_groups[
                    semantic_relationship_key(validation)
                ].append(validation)

        paired_records: list[
            tuple[dict[str, Any], dict[str, Any], str, int, int]
        ] = []
        for validation, relationship in explicit_pairs:
            paired_records.append(
                (validation, relationship, "RELATIONSHIP_ID", 1, 1)
            )

        for semantic_key, validation_group in validation_groups.items():
            relationship_group = list(
                relationship_groups.get(semantic_key, [])
            )
            validation_group = sorted(
                validation_group,
                key=relationship_tiebreaker,
            )
            relationship_group = sorted(
                relationship_group,
                key=relationship_tiebreaker,
            )
            validation_count = len(validation_group)
            relationship_count = len(relationship_group)

            if relationship_count == 0:
                for validation in validation_group:
                    status = validation_status(validation)
                    input_status_counts[status] += 1
                    errors.append(
                        {
                            "origin": origin,
                            "relationship_id": display_relationship_id(
                                validation, origin
                            ),
                            "stage": "JOIN_VALIDATION_TO_RELATIONSHIP",
                            "error": (
                                "Deferred validation has no matching relationship; "
                                "semantic_candidate_count=0"
                            ),
                        }
                    )
                continue

            if relationship_count != validation_count:
                for validation in validation_group:
                    status = validation_status(validation)
                    input_status_counts[status] += 1
                    errors.append(
                        {
                            "origin": origin,
                            "relationship_id": display_relationship_id(
                                validation, origin
                            ),
                            "stage": "JOIN_VALIDATION_TO_RELATIONSHIP",
                            "error": (
                                "Deferred validation group does not reconcile to "
                                "the relationship group; "
                                f"validation_count={validation_count}; "
                                f"relationship_count={relationship_count}"
                            ),
                        }
                    )
                continue

            join_method = (
                "SEMANTIC_KEY"
                if relationship_count == 1
                else "SEMANTIC_GROUP_ORDINAL"
            )
            for position, (validation, relationship) in enumerate(
                zip(validation_group, relationship_group, strict=True),
                start=1,
            ):
                paired_records.append(
                    (
                        validation,
                        relationship,
                        join_method,
                        position,
                        relationship_count,
                    )
                )

        for (
            validation,
            relationship,
            join_method,
            group_position,
            group_size,
        ) in paired_records:
            status = validation_status(validation)
            domain = DEFERRED_STATUS_DOMAIN[status]
            input_status_counts[status] += 1
            explicit_id = relationship_id(validation)
            base_id = (
                explicit_id
                or display_relationship_id(relationship, origin)
            )
            rel_id = (
                f"{base_id}::occurrence::{group_position:04d}"
                if group_size > 1 and not explicit_id
                else base_id
            )
            rel_type = relationship_type(relationship)
            context = extract_context(relationship, domain)
            match_class, candidates, reason = classify(
                domain, rel_type, context, indexes
            )
            candidate_ids = [node_id(item, domain) for item in candidates]
            domain_counts[domain] += 1
            classification_counts[match_class] += 1
            detail.append(
                {
                    "snapshot_id": run_id,
                    "origin": origin,
                    "relationship_id": rel_id,
                    "join_method": join_method,
                    "semantic_group_position": group_position,
                    "semantic_group_size": group_size,
                    "relationship_type": rel_type,
                    "input_validation_status": status,
                    "domain": domain,
                    "match_classification": match_class,
                    "match_reason": reason,
                    "candidate_count": len(candidates),
                    "candidate_node_ids": candidate_ids,
                    **context,
                }
            )

    detail.sort(
        key=lambda item: (
            item["domain"],
            item["match_classification"],
            item["origin"],
            item["relationship_id"],
        )
    )
    review_classes = {
        "AMBIGUOUS_MATCH",
        "TARGET_NOT_IN_CATALOG",
        "DYNAMIC_REFERENCE",
        "MISSING_DELETE_TARGET",
        "INSUFFICIENT_CONTEXT",
        "EXISTING_CREATE_TARGET",
    }
    review_queue = [
        item for item in detail if item["match_classification"] in review_classes
    ]

    profile = {
        "snapshot_id": run_id,
        "deferred_reference_count": len(detail),
        "input_status_counts": dict(sorted(input_status_counts.items())),
        "domain_counts": dict(sorted(domain_counts.items())),
        "classification_counts": dict(sorted(classification_counts.items())),
        "review_queue_count": len(review_queue),
        "error_count": len(errors),
    }
    status = "PARTIAL" if errors else "COMPLETE"
    manifest = {
        "snapshot_id": run_id,
        "status": status,
        "started_at": started.isoformat(),
        "completed_at": utc_now().isoformat(),
        **profile,
        "publish_current_requested": publish_current,
        "published_current": bool(status == "COMPLETE" and publish_current),
    }

    outputs = {
        "catalog_match_profile.json": profile,
        "catalog_match_profile_detail.json": detail,
        "catalog_match_manifest.json": manifest,
        "catalog_match_errors.json": errors,
    }
    for name, payload in outputs.items():
        write_json(snapshot_dir / name, payload)
    write_review_csv(snapshot_dir / "catalog_match_review_queue.csv", review_queue)

    if status == "COMPLETE" and publish_current:
        for name, payload in outputs.items():
            write_json(current_root / name, payload)
        write_review_csv(current_root / "catalog_match_review_queue.csv", review_queue)
    return manifest


def write_review_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "origin",
        "relationship_id",
        "join_method",
        "semantic_group_position",
        "semantic_group_size",
        "relationship_type",
        "input_validation_status",
        "domain",
        "match_classification",
        "match_reason",
        "candidate_count",
        "cube_name",
        "dimension_name",
        "hierarchy_name",
        "attribute_name",
        "subset_name",
        "view_name",
        "target_name",
        "candidate_node_ids",
    ]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            output = {field: row.get(field) for field in fields}
            output["candidate_node_ids"] = " | ".join(
                row.get("candidate_node_ids", [])
            )
            writer.writerow(output)
    temporary.replace(path)


def print_manifest(manifest: Mapping[str, Any]) -> None:
    print("=" * 70)
    print("TM1 CATALOG MATCH PROFILER")
    print("=" * 70)
    print(f"Snapshot            : {manifest.get('snapshot_id', 'UNKNOWN')}")
    print(f"Status              : {manifest.get('status', 'UNKNOWN')}")
    print(
        "Deferred references : "
        f"{int(manifest.get('deferred_reference_count', 0) or 0):,}"
    )
    print(
        "Review queue        : "
        f"{int(manifest.get('review_queue_count', 0) or 0):,}"
    )
    print(f"Errors              : {int(manifest.get('error_count', 0) or 0):,}")
    print(f"Published           : {bool(manifest.get('published_current', False))}")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile deferred TM1 relationships against governed catalogs."
    )
    parser.add_argument("--no-publish", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        manifest = profile_catalog_matches(
            publish_current=not args.no_publish
        )
        print_manifest(manifest)
        return 0 if manifest.get("status") == "COMPLETE" else 1
    except Exception as error:
        print(
            "Catalog match profiling failed: "
            f"{type(error).__name__}: {error}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
