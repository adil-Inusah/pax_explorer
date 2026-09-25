from __future__ import annotations

"""Build a governed, lossless unified graph from PAX Explorer catalogs.

Source catalogs remain authoritative. The builder creates a derived graph with
one validation and one provenance record per graph relationship, plus one
projection outcome for every source relationship considered.
"""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
CURRENT_ROOT = ROOT_DIR / "data" / "current"
SNAPSHOT_ROOT = ROOT_DIR / "data" / "snapshots"
REVIEW_ROOT = ROOT_DIR / "data" / "review"

NODE_FILES = (
    "objects.json",
    "hierarchies.json",
    "attributes.json",
    "subsets.json",
    "views.json",
    "chore_tasks.json",
    "external_data_sources.json",
    "operational_dependencies.json",
)

DIRECT_RELATIONSHIP_DOMAINS = {
    "Hierarchy": ("hierarchy_relationships.json", "hierarchy_relationship_validations.json"),
    "Subset": ("subset_relationships.json", "subset_relationship_validations.json"),
    "View": ("view_relationships.json", "view_relationship_validations.json"),
    "CubeDimension": (
        "cube_dimension_relationships.json",
        "cube_dimension_relationship_validations.json",
    ),
    "Chore": ("chore_relationships.json", "chore_relationship_validations.json"),
    "Operational": (
        "operational_relationships.json",
        "operational_relationship_validations.json",
    ),
}

SEMANTIC_DOMAINS = {
    "TI": (
        "ti_relationships.json",
        "ti_relationship_validations.json",
        "semantic_ti_relationship_validations.json",
    ),
    "RULE": (
        "rule_relationships.json",
        "rule_relationship_validations.json",
        "semantic_rule_relationship_validations.json",
    ),
}

MANIFEST_FILES = {
    "core_metadata": "manifest.json",
    "ti_lineage": "ti_lineage_manifest.json",
    "rule_lineage": "rule_lineage_manifest.json",
    "attributes": "attribute_manifest.json",
    "hierarchies": "hierarchy_manifest.json",
    "subsets": "subset_manifest.json",
    "views": "view_manifest.json",
    "cube_dimensions": "cube_dimension_manifest.json",
    "chore_tasks": "chore_lineage_manifest.json",
    "process_data_sources": "data_source_manifest.json",
    "operational_dependencies": "operational_dependency_manifest.json",
    "catalog_profile": "catalog_match_manifest.json",
    "semantic_plan": "catalog_validation_resolution_manifest.json",
    "semantic_validations": "semantic_validation_manifest.json",
}

OUTPUT_FILES = (
    "graph_nodes.json",
    "graph_relationships.json",
    "graph_validations.json",
    "graph_provenance.json",
    "graph_unresolved_references.json",
    "graph_projection_outcomes.json",
    "graph_outbound_index.json",
    "graph_inbound_index.json",
    "graph_type_summary.json",
    "graph_manifest.json",
    "graph_errors.json",
    "catalog_build_lineage.json",
    "catalog_scope.json",
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def snapshot_id(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def clean(value: Any) -> str:
    return str(value or "").strip()


def token(value: Any) -> str:
    return clean(value).upper().replace(" ", "_")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def records(path: Path, *, required: bool = True) -> list[dict[str, Any]]:
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"Required graph input not found: {path}")
        return []
    payload = read_json(path)
    if not isinstance(payload, list):
        raise TypeError(f"{path.name} must contain a JSON array")
    if any(not isinstance(item, Mapping) for item in payload):
        raise TypeError(f"{path.name} contains non-object records")
    return [dict(item) for item in payload]


def object_node_id(object_type: Any, object_name: Any) -> str:
    kind = clean(object_type).casefold()
    name = clean(object_name)
    if not kind or not name:
        return ""
    return f"{kind}::{name}"


def attribute_node_id(record: Mapping[str, Any]) -> str:
    dimension = clean(record.get("dimension_name"))
    hierarchy = clean(record.get("hierarchy_name"))
    attribute = clean(record.get("attribute_name"))
    if not dimension or not hierarchy or not attribute:
        return ""
    return f"attribute::{dimension}::{hierarchy}::{attribute}"


def supplied_node_id(record: Mapping[str, Any], source_file: str) -> str:
    direct = clean(record.get("node_id"))
    if direct:
        return direct
    if source_file == "objects.json":
        return object_node_id(record.get("object_type"), record.get("object_name"))
    if source_file == "attributes.json":
        return attribute_node_id(record)
    return ""


def endpoint_state(node: Mapping[str, Any], validation_status: Any = "") -> str:
    status = token(validation_status)
    node_type = token(node.get("node_type"))
    if node_type == "REFERENCE_EXPRESSION":
        return "DYNAMIC"
    if node_type == "PLANNED_TARGET":
        return "PLANNED"
    if node_type in {"FILE", "COMMAND", "SCRIPT", "EXECUTABLE", "EXTERNAL_DATA_SOURCE"}:
        return "EXTERNAL_UNVERIFIED"
    if "DYNAMIC" in status:
        return "DYNAMIC"
    return "CANONICAL"


def source_identity(domain: str, record: Mapping[str, Any]) -> str:
    if domain == "TI":
        return f"process::{clean(record.get('process_name') or record.get('source_name'))}"
    return f"cube::{clean(record.get('source_cube') or record.get('source_name'))}"


def semantic_key(domain: str, record: Mapping[str, Any], ordinal: int = 1) -> str:
    source = source_identity(domain, record)
    relationship = token(record.get("relationship_type"))
    target_type = token(record.get("target_type") or record.get("target_object_type"))
    target = clean(record.get("target_name") or record.get("target_expression"))
    raw = "\x1f".join((domain, source, relationship, target_type, target, str(ordinal)))
    return f"semantic-source::{domain.casefold()}::{sha256_text(raw.casefold())}"


def normalized_match_value(value: Any) -> str:
    text = clean(value)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    return " ".join(text.split()).casefold()


def semantic_source_name(domain: str, record: Mapping[str, Any]) -> str:
    if domain == "TI":
        return clean(record.get("process_name") or record.get("source_name"))
    return clean(
        record.get("source_cube")
        or record.get("source_name")
        or record.get("cube_name")
    )


def semantic_source_from_relationship_id(
    domain: str,
    semantic: Mapping[str, Any],
) -> str:
    relationship_id = clean(semantic.get("relationship_id"))
    relationship_type = clean(semantic.get("relationship_type"))
    prefix = f"semantic::{domain.casefold()}::"
    marker = f"::{relationship_type}::"
    if not relationship_id.casefold().startswith(prefix.casefold()):
        return ""
    remainder = relationship_id[len(prefix):]
    marker_position = remainder.casefold().find(marker.casefold())
    if marker_position < 0:
        return ""
    return remainder[:marker_position]


def semantic_target_type(record: Mapping[str, Any]) -> str:
    return token(
        record.get("target_type")
        or record.get("target_object_type")
        or record.get("domain")
    )


def semantic_target_name(record: Mapping[str, Any]) -> str:
    return clean(
        record.get("target_name")
        or record.get("catalog_match_name")
        or record.get("target_expression")
    )


def pair_semantic_relationship(
    domain: str,
    semantic: Mapping[str, Any],
    source_validation: Mapping[str, Any],
    source_relationships: list[
        dict[str, Any]
    ],
    available_indexes: set[int],
) -> tuple[
    dict[str, Any],
    int | None,
    str,
]:
    embedded = semantic.get(
        "source_validation"
    )

    evidence = (
        dict(embedded)
        if isinstance(embedded, Mapping)
        else dict(source_validation)
    )

    wanted_source = (
        semantic_source_from_relationship_id(
            domain,
            semantic,
        )
        or semantic_source_name(
            domain,
            evidence,
        )
        or semantic_source_name(
            domain,
            semantic,
        )
    )

    wanted_relationship = token(
        semantic.get("relationship_type")
        or evidence.get("relationship_type")
    )

    wanted_target_type = (
        semantic_target_type(semantic)
        or semantic_target_type(evidence)
    )

    wanted_target_name = (
        normalized_match_value(
            semantic_target_name(semantic)
            or semantic_target_name(
                evidence
            )
        )
    )

    wanted_expression = (
        normalized_match_value(
            evidence.get(
                "target_expression"
            )
            or semantic.get(
                "target_expression"
            )
        )
    )

    scored: list[
        tuple[int, int]
    ] = []

    for index in sorted(
        available_indexes
    ):
        candidate = (
            source_relationships[index]
        )

        candidate_source = (
            semantic_source_name(
                domain,
                candidate,
            )
        )

        candidate_relationship = token(
            candidate.get(
                "relationship_type"
            )
        )

        if (
            wanted_source
            and normalized_match_value(
                candidate_source
            )
            != normalized_match_value(
                wanted_source
            )
        ):
            continue

        if (
            wanted_relationship
            and candidate_relationship
            != wanted_relationship
        ):
            continue

        score = 100

        candidate_type = (
            semantic_target_type(
                candidate
            )
        )

        candidate_name = (
            normalized_match_value(
                semantic_target_name(
                    candidate
                )
            )
        )

        candidate_expression = (
            normalized_match_value(
                candidate.get(
                    "target_expression"
                )
            )
        )

        if (
            wanted_target_type
            and candidate_type
            == wanted_target_type
        ):
            score += 20

        if (
            wanted_target_name
            and candidate_name
            == wanted_target_name
        ):
            score += 40

        if (
            wanted_expression
            and candidate_expression
            == wanted_expression
        ):
            score += 10

        scored.append(
            (score, index)
        )

    if not scored:
        return {}, None, "UNMATCHED"

    scored.sort(
        key=lambda item: (
            -item[0],
            item[1],
        )
    )

    best_score = scored[0][0]

    best_indexes = [
        index
        for score, index in scored
        if score == best_score
    ]

    selected_index = (
        best_indexes[0]
    )

    available_indexes.remove(
        selected_index
    )

    method = (
        "EXACT_SEMANTIC_SIGNATURE"
        if best_score >= 160
        else
        "RELAXED_SEMANTIC_SIGNATURE"
    )

    return (
        source_relationships[
            selected_index
        ],
        selected_index,
        method,
    )

def synthetic_endpoint(
    *,
    kind: str,
    domain: str,
    target_type: str,
    target_name: str,
    expression: str,
    candidates: list[str] | None = None,
) -> dict[str, Any]:
    basis = "\x1f".join((domain, target_type, target_name, expression))
    digest = sha256_text(basis.casefold())
    node_type = "REFERENCE_EXPRESSION" if kind == "DYNAMIC" else "PLANNED_TARGET"
    return {
        "node_id": f"{node_type.casefold().replace('_', '-')}::{domain.casefold()}::{digest}",
        "node_type": node_type,
        "object_type": node_type.casefold(),
        "display_value": target_name or expression,
        "target_domain": target_type,
        "target_expression": expression or None,
        "candidate_node_ids": candidates or [],
        "endpoint_state": kind,
        "node_origin": "SEMANTIC_PROJECTION",
    }


def canonical_name_node_id(target_type: str, target_name: str) -> str:
    kind = token(target_type)
    name = clean(target_name)
    if not kind or not name:
        return ""
    if kind in {"CUBE", "DIMENSION", "PROCESS", "CHORE"}:
        return f"{kind.casefold()}::{name}"
    return ""


def build_catalog_lineage(current_root: Path, pipeline_run_id: str, graph_id: str) -> dict[str, Any]:
    source_snapshots: dict[str, Any] = {}
    for domain, filename in MANIFEST_FILES.items():
        path = current_root / filename
        if not path.is_file():
            source_snapshots[domain] = None
            continue
        payload = read_json(path)
        source_snapshots[domain] = payload.get("snapshot_id")
    return {
        "pipeline_run_id": pipeline_run_id or None,
        "graph_snapshot_id": graph_id,
        "source_snapshots": source_snapshots,
    }


def build_unified_graph(
    *,
    current_root: Path = CURRENT_ROOT,
    snapshot_root: Path = SNAPSHOT_ROOT,
    review_root: Path = REVIEW_ROOT,
    timestamp: datetime | None = None,
    publish_current: bool = True,
    pipeline_run_id: str = "",
) -> dict[str, Any]:
    started = timestamp or now_utc()
    run_id = snapshot_id(started)
    snapshot_dir = snapshot_root / run_id

    nodes: dict[str, dict[str, Any]] = {}
    graph_relationships: dict[str, dict[str, Any]] = {}
    graph_validations: dict[str, dict[str, Any]] = {}
    graph_provenance: dict[str, dict[str, Any]] = {}
    unresolved: dict[str, dict[str, Any]] = {}
    outcomes: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    expected_source_counts: dict[str, int] = {}

    def add_node(record: Mapping[str, Any], source_file: str) -> str:
        node_id = supplied_node_id(record, source_file)
        if not node_id:
            errors.append({
                "stage": "PROJECT_NODE",
                "source_artifact": source_file,
                "error": "Unable to derive node_id",
            })
            return ""
        payload = dict(record)
        payload["node_id"] = node_id
        payload["node_type"] = token(
            payload.get("node_type") or payload.get("object_type")
        )
        payload.setdefault("node_origin", "CATALOG")
        existing = nodes.get(node_id)
        if existing is not None and existing != payload:
            # Prefer the richer explicit catalog node and record no error when
            # the collision is the same canonical identity from another domain.
            if len(payload) > len(existing):
                nodes[node_id] = payload
        else:
            nodes[node_id] = payload
        return node_id

    for filename in NODE_FILES:
        for record in records(current_root / filename):
            add_node(record, filename)

    def ensure_endpoint(node_id: str, node_type: str, source_artifact: str) -> None:
        if not node_id or node_id in nodes:
            return
        nodes[node_id] = {
            "node_id": node_id,
            "node_type": token(node_type) or "UNKNOWN",
            "object_type": (token(node_type) or "unknown").casefold(),
            "node_origin": "RELATIONSHIP_ENDPOINT",
            "source_artifact": source_artifact,
        }

    def project_direct(
        domain: str,
        relationship_file: str,
        validation_file: str,
    ) -> None:
        source_relationships = records(current_root / relationship_file)
        expected_source_counts[relationship_file] = len(source_relationships)
        validation_index = {
            clean(item.get("relationship_id")): item
            for item in records(current_root / validation_file)
        }
        for relationship in source_relationships:
            source_record_id = clean(relationship.get("relationship_id"))
            if not source_record_id:
                errors.append({
                    "stage": "PROJECT_RELATIONSHIP",
                    "source_artifact": relationship_file,
                    "error": "Source relationship has no relationship_id",
                })
                continue
            source_id = clean(relationship.get("source_id"))
            source_type = token(relationship.get("source_type"))
            target_id = clean(relationship.get("target_id"))
            target_type = token(relationship.get("target_type"))
            rel_type = token(relationship.get("relationship_type"))
            missing_fields = [
                name
                for name, value in (
                    ("source_id", source_id),
                    ("source_type", source_type),
                    ("target_id", target_id),
                    ("target_type", target_type),
                    ("relationship_type", rel_type),
                )
                if not value
            ]
            if missing_fields:
                errors.append({
                    "stage": "PROJECT_RELATIONSHIP",
                    "source_artifact": relationship_file,
                    "source_record_id": source_record_id,
                    "missing_fields": missing_fields,
                    "error": "Source relationship is missing required fields",
                })
                outcomes.append({
                    "source_artifact": relationship_file,
                    "source_record_id": source_record_id,
                    "graph_relationship_ids": [],
                    "projection_outcome": "ERROR",
                })
                continue
            ensure_endpoint(source_id, source_type, relationship_file)
            ensure_endpoint(target_id, target_type, relationship_file)
            graph_id = f"graph::{domain.casefold()}::{source_record_id}"
            validation = validation_index.get(source_record_id, {})
            payload = dict(relationship)
            payload["graph_relationship_id"] = graph_id
            payload["source_relationship_id"] = source_record_id
            payload["endpoint_state"] = endpoint_state(
                nodes.get(target_id, {}), validation.get("validation_status")
            )
            graph_relationships[graph_id] = payload
            graph_validations[graph_id] = {
                "graph_validation_id": f"graph-validation::{graph_id}",
                "graph_relationship_id": graph_id,
                "validation_status": clean(validation.get("validation_status")) or "PROJECTED",
                "endpoint_state": payload["endpoint_state"],
                "source_validation_id": validation.get("validation_id"),
            }
            graph_provenance[graph_id] = {
                "graph_relationship_id": graph_id,
                "pipeline_run_id": pipeline_run_id or None,
                "source_artifacts": [relationship_file],
                "source_record_ids": [source_record_id],
                "source_snapshot_ids": [clean(relationship.get("snapshot_id"))],
                "source_validation_ids": [clean(validation.get("validation_id"))],
                "semantic_validation_ids": [],
                "resolution_plan_ids": [],
                "derivation_method": "DIRECT_PROJECTION",
            }
            outcomes.append({
                "source_artifact": relationship_file,
                "source_record_id": source_record_id,
                "graph_relationship_ids": [graph_id],
                "projection_outcome": "PROJECTED",
            })

    for domain, (relationship_file, validation_file) in DIRECT_RELATIONSHIP_DOMAINS.items():
        project_direct(domain, relationship_file, validation_file)

    # Data-source relationships overlap operational relationships by design.
    operational_signatures = {
        (
            clean(item.get("source_id")),
            token(item.get("relationship_type")),
            clean(item.get("target_id")),
        ): graph_id
        for graph_id, item in graph_relationships.items()
        if item.get("source_relationship_id") and graph_id.startswith("graph::operational::")
    }
    ds_relationships = records(current_root / "process_data_source_relationships.json")
    expected_source_counts["process_data_source_relationships.json"] = len(
        ds_relationships
    )
    ds_validation_index = {
        clean(item.get("relationship_id")): item
        for item in records(current_root / "process_data_source_validations.json")
    }
    for relationship in ds_relationships:
        source_record_id = clean(relationship.get("relationship_id"))
        signature = (
            clean(relationship.get("source_id")),
            token(relationship.get("relationship_type")),
            clean(relationship.get("target_id")),
        )
        existing_graph_id = operational_signatures.get(signature)
        if existing_graph_id:
            provenance = graph_provenance[existing_graph_id]
            provenance["source_artifacts"].append("process_data_source_relationships.json")
            provenance["source_record_ids"].append(source_record_id)
            provenance["source_snapshot_ids"].append(clean(relationship.get("snapshot_id")))
            validation = ds_validation_index.get(source_record_id, {})
            provenance["source_validation_ids"].append(clean(validation.get("validation_id")))
            outcomes.append({
                "source_artifact": "process_data_source_relationships.json",
                "source_record_id": source_record_id,
                "graph_relationship_ids": [existing_graph_id],
                "projection_outcome": "EXCLUDED_REDUNDANT_WITH_PROVENANCE",
            })
            continue
        # Non-overlapping process source edges are projected directly.
        source_id = clean(relationship.get("source_id"))
        target_id = clean(relationship.get("target_id"))
        ensure_endpoint(source_id, clean(relationship.get("source_type")), "process_data_source_relationships.json")
        ensure_endpoint(target_id, clean(relationship.get("target_type")), "process_data_source_relationships.json")
        graph_id = f"graph::datasource::{source_record_id}"
        validation = ds_validation_index.get(source_record_id, {})
        payload = dict(relationship)
        payload["graph_relationship_id"] = graph_id
        payload["source_relationship_id"] = source_record_id
        payload["endpoint_state"] = endpoint_state(nodes.get(target_id, {}), validation.get("validation_status"))
        graph_relationships[graph_id] = payload
        graph_validations[graph_id] = {
            "graph_validation_id": f"graph-validation::{graph_id}",
            "graph_relationship_id": graph_id,
            "validation_status": clean(validation.get("validation_status")) or "PROJECTED",
            "endpoint_state": payload["endpoint_state"],
            "source_validation_id": validation.get("validation_id"),
        }
        graph_provenance[graph_id] = {
            "graph_relationship_id": graph_id,
            "pipeline_run_id": pipeline_run_id or None,
            "source_artifacts": ["process_data_source_relationships.json"],
            "source_record_ids": [source_record_id],
            "source_snapshot_ids": [clean(relationship.get("snapshot_id"))],
            "source_validation_ids": [clean(validation.get("validation_id"))],
            "semantic_validation_ids": [],
            "resolution_plan_ids": [],
            "derivation_method": "DIRECT_PROJECTION",
        }
        outcomes.append({
            "source_artifact": "process_data_source_relationships.json",
            "source_record_id": source_record_id,
            "graph_relationship_ids": [graph_id],
            "projection_outcome": "PROJECTED",
        })

  # Semantic TI and rule projection. Validation indexes apply only to the
# validation artifact; source relationships are paired by semantic content.
    for domain, (relationship_file, validation_file, semantic_file) in SEMANTIC_DOMAINS.items():
        source_relationships = records(current_root / relationship_file)
        source_validations = records(current_root / validation_file)
        semantic_records = records(current_root / semantic_file)
        expected_source_counts[relationship_file] = len(semantic_records)
        available_relationship_indexes = set(range(len(source_relationships)))
        for index, semantic in enumerate(semantic_records):
            source_position = semantic.get("source_validation_index")
            source_index = (
                source_position - 1
                if isinstance(source_position, int) and source_position >= 1
                else None
            )
            if isinstance(source_index, int) and 0 <= source_index < len(source_validations):
                source_validation = source_validations[source_index]
            elif isinstance(semantic.get("source_validation"), Mapping):
                source_validation = dict(semantic["source_validation"])
         
            else:
                source_validation = {}

            source_relationship, matched_relationship_index, pairing_method = (
                pair_semantic_relationship(
                    domain,
                    semantic,
                    source_validation,
                    source_relationships,
                    available_relationship_indexes,
                )
            )
            semantic_relationship_type = token(semantic.get("relationship_type"))
            source_relationship_type = token(
                source_relationship.get("relationship_type")
            )
            if (
                not source_relationship
                or not source_relationship_type
                or semantic_relationship_type != source_relationship_type
            ):
                errors.append({
                    "stage": "PAIR_SEMANTIC_SOURCE",
                    "source_artifact": semantic_file,
                    "semantic_validation_id": semantic.get("semantic_validation_id"),
                    "source_validation_index": source_position,
                    "semantic_relationship_type": semantic_relationship_type,
                    "source_relationship_type": source_relationship_type,
                    "source_pairing_method": pairing_method,
                    "error": "Unable to pair semantic validation to a compatible source relationship",
                })
                outcomes.append({
                    "source_artifact": relationship_file,
                    "source_record_id": clean(semantic.get("semantic_validation_id")),
                    "graph_relationship_ids": [],
                    "projection_outcome": "ERROR",
                })
                continue
            source_id = source_identity(domain, source_relationship)
            ensure_endpoint(source_id, "PROCESS" if domain == "TI" else "CUBE", relationship_file)
            resolved_id = clean(semantic.get("resolved_target_node_id"))
            candidates = [clean(item) for item in semantic.get("candidate_node_ids", []) if clean(item)]
            decision = token(semantic.get("decision_type"))
            status = token(semantic.get("semantic_validation_status"))
            target_type = token(
                semantic.get("domain")
                or source_relationship.get("target_type")
                or source_relationship.get("target_object_type")
            )
            target_name = clean(semantic.get("target_name") or source_relationship.get("target_name"))
            expression = clean(source_relationship.get("target_expression"))
            projection_outcome = "PROJECTED"
            if resolved_id:
                target_id = resolved_id
                ensure_endpoint(target_id, target_type, semantic_file)
            elif decision == "PARSER_PRESERVED" and "VALID" in status:
                target_id = canonical_name_node_id(target_type, target_name)
                if target_id and target_id in nodes:
                    pass
                else:
                    synthetic = synthetic_endpoint(
                        kind="PLANNED",
                        domain=domain,
                        target_type=target_type,
                        target_name=target_name,
                        expression=expression,
                        candidates=candidates,
                    )
                    target_id = synthetic["node_id"]
                    nodes[target_id] = synthetic
                    unresolved[target_id] = synthetic
                    projection_outcome = "PROJECTED_AS_PLANNED_TARGET"
            elif "DYNAMIC" in status or semantic.get("profiler_classification") == "DYNAMIC_REFERENCE":
                synthetic = synthetic_endpoint(
                    kind="DYNAMIC",
                    domain=domain,
                    target_type=target_type,
                    target_name=target_name,
                    expression=expression,
                    candidates=candidates,
                )
                target_id = synthetic["node_id"]
                nodes[target_id] = synthetic
                unresolved[target_id] = synthetic
                projection_outcome = "PROJECTED_AS_DYNAMIC_REFERENCE"
            else:
                synthetic = synthetic_endpoint(
                    kind="PLANNED",
                    domain=domain,
                    target_type=target_type,
                    target_name=target_name,
                    expression=expression,
                    candidates=candidates,
                )
                target_id = synthetic["node_id"]
                nodes[target_id] = synthetic
                unresolved[target_id] = synthetic
                projection_outcome = "PROJECTED_AS_PLANNED_TARGET"

            source_record_id = semantic_key(domain, source_relationship, int(semantic.get("semantic_group_position") or 1))
            semantic_validation_id = clean(semantic.get("semantic_validation_id"))
            graph_identity = "|".join(
                (source_record_id, target_id, semantic_validation_id)
            )
            graph_id = (
                f"graph::semantic::{domain.casefold()}::"
                f"{sha256_text(graph_identity.casefold())}"
            )
            if graph_id in graph_relationships:
                errors.append({
                    "stage": "PROJECT_SEMANTIC_RELATIONSHIP",
                    "graph_relationship_id": graph_id,
                    "semantic_validation_id": semantic_validation_id,
                    "error": "Graph relationship ID collision detected",
                })
                outcomes.append({
                    "source_artifact": relationship_file,
                    "source_record_id": source_record_id,
                    "graph_relationship_ids": [],
                    "projection_outcome": "ERROR",
                })
                continue
            relationship = {
                "graph_relationship_id": graph_id,
                "source_id": source_id,
                "source_type": "PROCESS" if domain == "TI" else "CUBE",
                "target_id": target_id,
                "target_type": token(nodes[target_id].get("node_type")),
                "relationship_type": token(semantic.get("relationship_type") or source_relationship.get("relationship_type")),
                "relationship_origin": domain,
                "resolution_method": clean(semantic.get("resolution_method")),
                "endpoint_state": endpoint_state(nodes[target_id], status),
                "source_relationship_id": source_record_id,
                "semantic_validation_id": semantic_validation_id,
                "decision_type": clean(semantic.get("decision_type")),
                "review_required": bool(semantic.get("review_required")),
            }
            graph_relationships[graph_id] = relationship
            graph_validations[graph_id] = {
                "graph_validation_id": f"graph-validation::{graph_id}",
                "graph_relationship_id": graph_id,
                "validation_status": clean(semantic.get("semantic_validation_status")),
                "endpoint_state": relationship["endpoint_state"],
                "semantic_validation_id": semantic_validation_id,
                "review_required": bool(semantic.get("review_required")),
            }
            graph_provenance[graph_id] = {
                "graph_relationship_id": graph_id,
                "pipeline_run_id": pipeline_run_id or None,
                "source_artifacts": [relationship_file, validation_file, semantic_file],
                "source_record_ids": [source_record_id],
                "source_snapshot_ids": [clean(semantic.get("source_snapshot_id"))],
                "source_validation_ids": [],
                "semantic_validation_ids": [semantic_validation_id],
                "resolution_plan_ids": [clean(semantic.get("plan_id"))],
                "derivation_method": "SEMANTIC_PROJECTION",
                "source_pairing_method": pairing_method,
                "source_validation_position": source_position,
                "source_relationship_index": matched_relationship_index,
                "evidence": {
                    "procedures": source_relationship.get("procedures", []),
                    "functions": source_relationship.get("functions", []),
                    "evidence_lines": source_relationship.get("evidence_lines", []),
                    "first_line": source_relationship.get("first_line"),
                    "confidence": source_relationship.get("confidence"),
                    "target_expression": expression or None,
                    "candidate_node_ids": candidates,
                    "review_reason": semantic.get("review_reason"),
                },
            }
            outcomes.append({
                "source_artifact": relationship_file,
                "source_record_id": source_record_id,
                "graph_relationship_ids": [graph_id],
                "projection_outcome": projection_outcome,
            })

    relationship_payload = sorted(graph_relationships.values(), key=lambda item: item["graph_relationship_id"])
    validation_payload = sorted(graph_validations.values(), key=lambda item: item["graph_relationship_id"])
    provenance_payload = sorted(graph_provenance.values(), key=lambda item: item["graph_relationship_id"])
    node_payload = sorted(nodes.values(), key=lambda item: item["node_id"])
    outcome_payload = sorted(outcomes, key=lambda item: (item["source_artifact"], item["source_record_id"]))
    unresolved_payload = sorted(unresolved.values(), key=lambda item: item["node_id"])
    synthetic_endpoint_ids = {
        clean(item.get("node_id"))
        for item in node_payload
        if token(item.get("node_type"))
        in {"REFERENCE_EXPRESSION", "PLANNED_TARGET"}
    }
    unresolved_endpoint_ids = {
        clean(item.get("node_id")) for item in unresolved_payload
    }
    missing_unresolved_ids = sorted(
        synthetic_endpoint_ids - unresolved_endpoint_ids
    )
    orphan_unresolved_ids = sorted(
        unresolved_endpoint_ids - synthetic_endpoint_ids
    )

    node_ids = {item["node_id"] for item in node_payload}
    relationship_ids = {item["graph_relationship_id"] for item in relationship_payload}
    validation_ids = {item["graph_relationship_id"] for item in validation_payload}
    provenance_ids = {item["graph_relationship_id"] for item in provenance_payload}
    malformed_graph_relationships = [
        clean(item.get("graph_relationship_id"))
        for item in relationship_payload
        if not clean(item.get("source_id"))
        or not token(item.get("source_type"))
        or not clean(item.get("target_id"))
        or not token(item.get("target_type"))
        or not token(item.get("relationship_type"))
    ]
    missing_endpoints = [
        clean(item.get("graph_relationship_id"))
        for item in relationship_payload
        if clean(item.get("source_id")) not in node_ids
        or clean(item.get("target_id")) not in node_ids
    ]
    if len(relationship_payload) != len(relationship_ids):
        errors.append({"stage": "VALIDATE_GRAPH", "error": "Duplicate graph relationship IDs"})
    if relationship_ids != validation_ids:
        errors.append({"stage": "VALIDATE_GRAPH", "error": "Graph relationship-validation identities do not reconcile"})
    if relationship_ids != provenance_ids:
        errors.append({"stage": "VALIDATE_GRAPH", "error": "Graph relationship-provenance identities do not reconcile"})
    if malformed_graph_relationships:
        errors.append({
            "stage": "VALIDATE_GRAPH",
            "error": "Graph relationships contain missing required identity or type fields",
            "relationship_ids": malformed_graph_relationships[:25],
            "relationship_count": len(malformed_graph_relationships),
        })
    if missing_endpoints:
        errors.append({
            "stage": "VALIDATE_GRAPH",
            "error": "Graph relationships contain missing endpoints",
            "relationship_ids": missing_endpoints[:25],
        })
    if missing_unresolved_ids or orphan_unresolved_ids:
        errors.append({
            "stage": "VALIDATE_GRAPH",
            "error": (
                "Synthetic endpoints and unresolved reference records "
                "do not reconcile"
            ),
            "synthetic_endpoint_count": len(synthetic_endpoint_ids),
            "unresolved_reference_count": len(unresolved_endpoint_ids),
            "missing_unresolved_ids": missing_unresolved_ids[:25],
            "orphan_unresolved_ids": orphan_unresolved_ids[:25],
        })
    projected_outcome_count = sum(
        token(item.get("projection_outcome"))
        in {
            "PROJECTED",
            "PROJECTED_AS_DYNAMIC_REFERENCE",
            "PROJECTED_AS_PLANNED_TARGET",
        }
        for item in outcome_payload
    )
    if projected_outcome_count != len(relationship_payload):
        errors.append({
            "stage": "VALIDATE_GRAPH",
            "error": "Projected outcomes do not reconcile to graph relationships",
            "projected_outcome_count": projected_outcome_count,
            "graph_relationship_count": len(relationship_payload),
        })
    if any(not clean(item.get("projection_outcome")) for item in outcome_payload):
        errors.append({"stage": "VALIDATE_GRAPH", "error": "Projection outcome is missing"})

    outbound: dict[str, list[str]] = defaultdict(list)
    inbound: dict[str, list[str]] = defaultdict(list)
    for item in relationship_payload:
        source_id = clean(item.get("source_id"))
        target_id = clean(item.get("target_id"))
        graph_relationship_id = clean(item.get("graph_relationship_id"))
        if not source_id or not target_id or not graph_relationship_id:
            continue
        outbound[source_id].append(graph_relationship_id)
        inbound[target_id].append(graph_relationship_id)
    outbound_payload = {
        key: sorted(value) for key, value in sorted(outbound.items())
    }
    inbound_payload = {
        key: sorted(value) for key, value in sorted(inbound.items())
    }
    actual_outcome_counts = Counter(
        clean(item.get("source_artifact")) for item in outcome_payload
    )
    source_projection_counts = {
        artifact: {
            "source_records": expected_count,
            "outcomes": int(actual_outcome_counts.get(artifact, 0)),
            "reconciles": expected_count == int(actual_outcome_counts.get(artifact, 0)),
        }
        for artifact, expected_count in sorted(expected_source_counts.items())
    }
    source_coverage_failures = [
        artifact
        for artifact, details in source_projection_counts.items()
        if not details["reconciles"]
    ]
    if source_coverage_failures:
        errors.append({
            "stage": "VALIDATE_GRAPH",
            "error": "Source relationship counts do not reconcile to projection outcomes",
            "source_artifacts": source_coverage_failures,
        })

    type_summary = {
        "node_type_counts": dict(sorted(Counter(
            token(item.get("node_type")) or "UNKNOWN" for item in node_payload
        ).items())),
        "relationship_type_counts": dict(sorted(Counter(
            token(item.get("relationship_type")) or "UNKNOWN"
            for item in relationship_payload
        ).items())),
        "endpoint_state_counts": dict(sorted(Counter(
            token(item.get("endpoint_state")) or "UNKNOWN"
            for item in relationship_payload
        ).items())),
        "projection_outcome_counts": dict(sorted(Counter(
            token(item.get("projection_outcome")) or "UNKNOWN"
            for item in outcome_payload
        ).items())),
    }
    status = "PARTIAL" if errors else "COMPLETE"
    manifest = {
        "snapshot_id": run_id,
        "pipeline_run_id": pipeline_run_id or None,
        "status": status,
        "started_at": started.isoformat(),
        "completed_at": now_utc().isoformat(),
        "node_count": len(node_payload),
        "relationship_count": len(relationship_payload),
        "validation_count": len(validation_payload),
        "provenance_count": len(provenance_payload),
        "projection_outcome_count": len(outcome_payload),
        "unresolved_reference_count": len(unresolved_payload),
        "synthetic_endpoint_count": len(synthetic_endpoint_ids),
        "missing_unresolved_count": len(missing_unresolved_ids),
        "orphan_unresolved_count": len(orphan_unresolved_ids),
        "projected_outcome_count": projected_outcome_count,
        "source_projection_counts": source_projection_counts,
        "source_coverage_failure_count": len(source_coverage_failures),
        "error_count": len(errors),
        "duplicate_node_ids": len(node_payload) - len(node_ids),
        "duplicate_relationship_ids": len(relationship_payload) - len(relationship_ids),
        "missing_endpoint_count": len(missing_endpoints),
        "malformed_relationship_count": len(malformed_graph_relationships),
        "source_artifacts_modified": False,
        "publish_current_requested": publish_current,
        "published_current": bool(status == "COMPLETE" and publish_current),
    }
    lineage = build_catalog_lineage(current_root, pipeline_run_id, run_id)
    scope = {
        "included": [
            "core objects", "attributes", "hierarchies", "public subsets", "public views",
            "chore tasks", "configured process data sources", "operational dependencies",
            "TI semantic relationships", "rule semantic relationships",
        ],
        "partial": [
            "dynamic runtime targets", "external physical-system verification",
            "procedure lifecycle interpretation",
        ],
        "excluded": [
            "private subsets", "private views", "element-level structures",
            "security assignments", "runtime sessions and transaction logs",
        ],
    }
    outputs = {
        "graph_nodes.json": node_payload,
        "graph_relationships.json": relationship_payload,
        "graph_validations.json": validation_payload,
        "graph_provenance.json": provenance_payload,
        "graph_unresolved_references.json": unresolved_payload,
        "graph_projection_outcomes.json": outcome_payload,
        "graph_outbound_index.json": outbound_payload,
        "graph_inbound_index.json": inbound_payload,
        "graph_type_summary.json": type_summary,
        "graph_manifest.json": manifest,
        "graph_errors.json": errors,
        "catalog_build_lineage.json": lineage,
        "catalog_scope.json": scope,
    }
    for name, payload in outputs.items():
        write_json(snapshot_dir / name, payload)
    if status == "COMPLETE" and publish_current:
        for name, payload in outputs.items():
            write_json(current_root / name, payload)
    write_json(review_root / "latest_graph_manifest.json", manifest)
    return manifest


def render_manifest(manifest: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "=" * 70,
            "PAX EXPLORER UNIFIED GRAPH",
            "=" * 70,
            f"Snapshot      : {manifest.get('snapshot_id', 'UNKNOWN')}",
            f"Status        : {manifest.get('status', 'UNKNOWN')}",
            f"Nodes         : {int(manifest.get('node_count', 0) or 0):,}",
            f"Relationships : {int(manifest.get('relationship_count', 0) or 0):,}",
            f"Validations   : {int(manifest.get('validation_count', 0) or 0):,}",
            f"Provenance    : {int(manifest.get('provenance_count', 0) or 0):,}",
            f"Outcomes      : {int(manifest.get('projection_outcome_count', 0) or 0):,}",
            f"Unresolved    : {int(manifest.get('unresolved_reference_count', 0) or 0):,}",
            f"Errors        : {int(manifest.get('error_count', 0) or 0):,}",
            f"Published     : {bool(manifest.get('published_current', False))}",
        ]
    ) + "\n"


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the governed PAX Explorer unified graph.")
    parser.add_argument("--current-root", type=Path, default=CURRENT_ROOT)
    parser.add_argument("--snapshot-root", type=Path, default=SNAPSHOT_ROOT)
    parser.add_argument("--review-root", type=Path, default=REVIEW_ROOT)
    parser.add_argument("--pipeline-run-id", default="")
    parser.add_argument("--no-publish", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        manifest = build_unified_graph(
            current_root=args.current_root,
            snapshot_root=args.snapshot_root,
            review_root=args.review_root,
            pipeline_run_id=args.pipeline_run_id,
            publish_current=not args.no_publish,
        )
        print(render_manifest(manifest), end="")
        return 0 if manifest.get("status") == "COMPLETE" else 1
    except Exception as error:
        print(f"Unified graph build failed: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
