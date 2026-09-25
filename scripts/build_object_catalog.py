from __future__ import annotations

"""Build the governed conceptual object-to-object catalog.

The unified graph remains the lossless evidence layer. This builder creates
unique reusable objects, process-specific configuration declarations, shared
conceptual references, and consolidated object relationships.
"""

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utilities.object_identity import (
    canonical_id, canonical_key, clean, configured_source_id,
    file_reference_key, normalize, reference_id, relationship_id,
    relationship_key, subset_reference_key, token, view_reference_key,
)
from utilities.object_resolution import ObjectResolver

ROOT_DIR = Path(__file__).resolve().parent.parent
CURRENT_ROOT = ROOT_DIR / "data" / "current"
SNAPSHOT_ROOT = ROOT_DIR / "data" / "snapshots"
REVIEW_ROOT = ROOT_DIR / "data" / "review"
OUTPUT_FILES = (
    "object_catalog.json", "object_relationships.json",
    "object_relationship_validations.json", "object_relationship_provenance.json",
    "object_reference_resolutions.json", "object_projection_outcomes.json",
    "object_catalog_summary.json", "object_catalog_manifest.json",
    "object_catalog_errors.json",
)
RELATIONSHIP_CLASS = {
    "BELONGS_TO_DIMENSION": "STRUCTURAL", "BELONGS_TO_HIERARCHY": "STRUCTURAL",
    "BELONGS_TO_CUBE": "STRUCTURAL", "USES_DIMENSION": "STRUCTURAL",
    "CONTAINS_TASK": "STRUCTURAL", "RUNS_PROCESS": "EXECUTION",
    "CALLS_PROCESS": "EXECUTION", "PASSES_PARAMETER": "EXECUTION",
    "READS_FROM_CUBE": "DATA_FLOW", "WRITES_TO_CUBE": "DATA_FLOW",
    "INCREMENTS_CUBE": "DATA_FLOW", "CLEARS_CUBE": "DATA_FLOW",
    "READS_FROM_FILE": "OPERATIONAL", "WRITES_FILE": "OPERATIONAL",
    "READS_ATTRIBUTE": "SEMANTIC_USAGE", "WRITES_ATTRIBUTE": "SEMANTIC_USAGE",
    "USES_VIEW": "SEMANTIC_USAGE", "UPDATES_DIMENSION": "SEMANTIC_USAGE",
    "USES_DATA_SOURCE": "CONFIGURATION", "RESOLVES_TO_FILE": "RESOLUTION",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def snapshot_id(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def records(path: Path, required: bool = True) -> list[dict[str, Any]]:
    if not path.is_file():
        if required:
            raise FileNotFoundError(path)
        return []
    payload = read_json(path)
    if not isinstance(payload, list) or any(not isinstance(item, Mapping) for item in payload):
        raise TypeError(f"{path.name} must contain an object array")
    return [dict(item) for item in payload]


def build_object_catalog(*, current_root: Path = CURRENT_ROOT, snapshot_root: Path = SNAPSHOT_ROOT,
                         review_root: Path = REVIEW_ROOT, timestamp: datetime | None = None,
                         publish_current: bool = True, pipeline_run_id: str = "") -> dict[str, Any]:
    started = timestamp or now_utc()
    run_id = snapshot_id(started)
    snapshot_dir = snapshot_root / run_id / "object_catalog"
    errors: list[dict[str, Any]] = []
    objects: dict[str, dict[str, Any]] = {}
    relationships: dict[str, dict[str, Any]] = {}
    provenance: dict[str, dict[str, Any]] = {}
    outcomes: list[dict[str, Any]] = []
    resolutions: dict[str, dict[str, Any]] = {}

    graph_manifest = read_json(current_root / "graph_manifest.json")
    if graph_manifest.get("status") != "COMPLETE" or graph_manifest.get("published_current") is not True:
        raise RuntimeError("A COMPLETE published unified graph is required")
    graph_nodes = records(current_root / "graph_nodes.json")
    graph_relationships = records(current_root / "graph_relationships.json")
    graph_provenance = {clean(item.get("graph_relationship_id")): item for item in records(current_root / "graph_provenance.json")}

    def add_object(object_id: str, object_type: str, object_class: str, properties: Mapping[str, Any], identity_components: tuple[str, ...] | list[str]) -> str:
        if not object_id:
            errors.append({"stage": "PROJECT_OBJECT", "error": "Object has no identity"})
            return ""
        record = {
            "object_id": object_id, "object_type": token(object_type),
            "object_class": token(object_class),
            "display_name": clean(properties.get("display_value") or properties.get("object_name") or properties.get("view_name") or properties.get("subset_name") or properties.get("attribute_name") or object_id.rsplit("::", 1)[-1]),
            "identity_components": list(identity_components),
            "properties": dict(properties),
        }
        existing = objects.get(object_id)
        if existing and existing != record:
            # Same canonical identity can arrive through more than one evidence artifact.
            existing["properties"] = {**existing.get("properties", {}), **record["properties"]}
        else:
            objects[object_id] = record
        return object_id

    for node in graph_nodes:
        object_id = canonical_id(node)
        key = canonical_key(node)
        object_class = "CONCEPTUAL_REFERENCE" if token(node.get("node_type")) in {"REFERENCE_EXPRESSION", "PLANNED_TARGET"} else "EXTERNAL" if token(node.get("node_type")) in {"FILE", "COMMAND", "SCRIPT", "EXECUTABLE", "EXTERNAL_DATA_SOURCE"} else "CANONICAL"
        add_object(object_id, token(node.get("node_type") or node.get("object_type")), object_class, node, key)

    resolver = ObjectResolver(list(objects.values()))

    def add_relationship(source_id: str, rel_type: str, target_id: str, rel_class: str,
                         *, source_graph_id: str | None = None, evidence: Mapping[str, Any] | None = None,
                         validation_status: str = "VALID", generated: bool = False) -> str:
        key = relationship_key(source_id, rel_type, target_id, rel_class)
        rel_id = relationship_id(key)
        if not source_id or not target_id or source_id not in objects or target_id not in objects:
            errors.append({"stage": "PROJECT_RELATIONSHIP", "relationship_key": list(key), "error": "Relationship endpoint is missing"})
            return ""
        record = relationships.setdefault(rel_id, {
            "object_relationship_id": rel_id, "source_object_id": source_id,
            "source_object_type": objects[source_id]["object_type"],
            "relationship_type": token(rel_type), "relationship_class": token(rel_class),
            "target_object_id": target_id, "target_object_type": objects[target_id]["object_type"],
            "validation_status": validation_status, "evidence_count": 0,
            "generated": generated,
        })
        header = provenance.setdefault(rel_id, {"object_relationship_id": rel_id, "source_graph_relationship_ids": [], "source_artifacts": [], "source_record_ids": [], "source_snapshot_ids": [], "source_validation_ids": [], "semantic_validation_ids": []})
        if source_graph_id and source_graph_id not in header["source_graph_relationship_ids"]:
            header["source_graph_relationship_ids"].append(source_graph_id)
        if evidence:
            for field in ("source_artifacts", "source_record_ids", "source_snapshot_ids", "source_validation_ids", "semantic_validation_ids"):
                for value in evidence.get(field, []) or []:
                    if value and value not in header[field]:
                        header[field].append(value)
        record["evidence_count"] = len(header["source_graph_relationship_ids"]) or (1 if generated else 0)
        return rel_id

    # Project and consolidate every graph relationship.
    for edge in graph_relationships:
        graph_id = clean(edge.get("graph_relationship_id"))
        source_id, target_id = clean(edge.get("source_id")), clean(edge.get("target_id"))
        rel_type = token(edge.get("relationship_type"))
        rel_class = RELATIONSHIP_CLASS.get(rel_type, "OTHER")
        rel_id = add_relationship(source_id, rel_type, target_id, rel_class, source_graph_id=graph_id, evidence=graph_provenance.get(graph_id, {}), validation_status=clean(edge.get("endpoint_state") or "VALID"))
        outcomes.append({"source_graph_relationship_id": graph_id, "object_relationship_ids": [rel_id] if rel_id else [], "projection_outcome": "PROJECTED" if rel_id else "ERROR"})

    # Explicit structural ownership that is implicit in qualified identities.
    for item in list(objects.values()):
        p = item["properties"]
        kind = item["object_type"]
        oid = item["object_id"]
        if kind == "ATTRIBUTE":
            hierarchy_id = f"hierarchy::{clean(p.get('dimension_name'))}::{clean(p.get('hierarchy_name'))}"
            dimension_id = f"dimension::{clean(p.get('dimension_name'))}"
            add_relationship(oid, "BELONGS_TO_HIERARCHY", hierarchy_id, "STRUCTURAL", generated=True)
            add_relationship(oid, "BELONGS_TO_DIMENSION", dimension_id, "STRUCTURAL", generated=True)
        elif kind == "SUBSET":
            hierarchy_id = f"hierarchy::{clean(p.get('dimension_name'))}::{clean(p.get('hierarchy_name'))}"
            dimension_id = f"dimension::{clean(p.get('dimension_name'))}"
            add_relationship(oid, "BELONGS_TO_HIERARCHY", hierarchy_id, "STRUCTURAL", generated=True)
            add_relationship(oid, "BELONGS_TO_DIMENSION", dimension_id, "STRUCTURAL", generated=True)
        elif kind == "VIEW":
            add_relationship(oid, "BELONGS_TO_CUBE", f"cube::{clean(p.get('cube_name'))}", "STRUCTURAL", generated=True)

    # Process-specific source declarations and conceptual references.
    shared_sources = {clean(item.get("node_id")): item for item in records(current_root / "external_data_sources.json")}
    declarations = records(current_root / "process_data_sources.json")
    source_signatures: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for declaration in declarations:
        process_id = f"process::{clean(declaration.get('process_name'))}"
        shared_id = clean(declaration.get("source_id"))
        source_type = token(declaration.get("source_type"))
        source = shared_sources.get(shared_id, {})
        config_id = configured_source_id(process_id, source_type, shared_id)
        config_key = ("CONFIGURED_SOURCE", process_id, source_type, shared_id)
        add_object(config_id, "CONFIGURED_DATA_SOURCE", "CONFIGURATION", {**declaration, "owning_process_id": process_id, "shared_source_id": shared_id}, config_key)
        add_relationship(process_id, "HAS_CONFIGURED_SOURCE", config_id, "CONFIGURATION", generated=True)
        if shared_id in objects:
            add_relationship(config_id, "REFERENCES_SHARED_SOURCE", shared_id, "REFERENCE", generated=True)

        effective_cube = clean(source.get("cube_name") or source.get("server_source_name") or source.get("client_source_name"))
        signature = (source_type, normalize(effective_cube), normalize(source.get("view_name")), normalize(source.get("dimension_name")), normalize(source.get("hierarchy_name")), normalize(source.get("subset_name")), normalize(source.get("server_source_name")), normalize(source.get("client_source_name")))
        source_signatures[shared_id].add(signature)

        if source_type == "TM1_CUBE_VIEW":
            key = view_reference_key(effective_cube, clean(source.get("view_name")))
            ref_id = reference_id(key)
            resolution = resolver.resolve_view(effective_cube, clean(source.get("view_name")))
            add_object( ref_id, "TM1_VIEW_REFERENCE","CONCEPTUAL_REFERENCE", { "display_value": ( f"{effective_cube} / " f"{clean(source.get('view_name'))}"), "configured_cube_name": ( effective_cube ), "configured_view_name": clean( source.get("view_name") ), **resolution, },key,)
            add_relationship(config_id, "REFERENCES_TM1_VIEW", ref_id, "REFERENCE", generated=True, validation_status=resolution["resolution_status"])
            resolutions.setdefault(ref_id, {"reference_object_id": ref_id, **resolution})
            if resolution["resolved_target_object_id"]:
                add_relationship(ref_id, "RESOLVES_TO_VIEW", resolution["resolved_target_object_id"], "RESOLUTION", generated=True, validation_status=resolution["resolution_status"])
        elif source_type == "TM1_DIMENSION_SUBSET":
            key = subset_reference_key(clean(source.get("dimension_name")), clean(source.get("hierarchy_name") or source.get("dimension_name")), clean(source.get("subset_name")))
            ref_id = reference_id(key)
            resolution = resolver.resolve_subset(clean(source.get("dimension_name")), clean(source.get("hierarchy_name") or source.get("dimension_name")), clean(source.get("subset_name")))
            add_object(ref_id, "TM1_SUBSET_REFERENCE", "CONCEPTUAL_REFERENCE", {"dimension_name": clean(source.get("dimension_name")), "hierarchy_name": clean(source.get("hierarchy_name") or source.get("dimension_name")), "subset_name": clean(source.get("subset_name")), **resolution}, key)
            add_relationship(config_id, "REFERENCES_TM1_SUBSET", ref_id, "REFERENCE", generated=True, validation_status=resolution["resolution_status"])
            resolutions.setdefault(ref_id, {"reference_object_id": ref_id, **resolution})
            if resolution["resolved_target_object_id"]:
                add_relationship(ref_id, "RESOLVES_TO_SUBSET", resolution["resolved_target_object_id"], "RESOLUTION", generated=True, validation_status=resolution["resolution_status"])
        elif source_type in {"ASCII_FILE", "JSON"}:
            path_value = clean(source.get("server_source_name") or source.get("client_source_name"))
            key = file_reference_key(path_value)
            ref_id = reference_id(key)
            resolution = resolver.resolve_file(path_value)
            add_object(ref_id, "FILE_REFERENCE", "CONCEPTUAL_REFERENCE", {"path_expression": path_value, **resolution}, key)
            add_relationship(config_id, "REFERENCES_FILE", ref_id, "REFERENCE", generated=True, validation_status=resolution["resolution_status"])
            resolutions.setdefault(ref_id, {"reference_object_id": ref_id, **resolution})
            if resolution["resolved_target_object_id"]:
                add_relationship(ref_id, "RESOLVES_TO_FILE", resolution["resolved_target_object_id"], "RESOLUTION", generated=True, validation_status=resolution["resolution_status"])

    relationship_payload = sorted(relationships.values(), key=lambda x: x["object_relationship_id"])
    object_payload = sorted(objects.values(), key=lambda x: x["object_id"])
    provenance_payload = sorted(provenance.values(), key=lambda x: x["object_relationship_id"])
    validation_payload = [{"object_validation_id": f"object-validation::{item['object_relationship_id']}", "object_relationship_id": item["object_relationship_id"], "validation_status": item["validation_status"]} for item in relationship_payload]
    resolution_payload = sorted(resolutions.values(), key=lambda x: x["reference_object_id"])

    object_ids = {item["object_id"] for item in object_payload}
    relationship_ids = {item["object_relationship_id"] for item in relationship_payload}
    missing_endpoints = [item["object_relationship_id"] for item in relationship_payload if item["source_object_id"] not in object_ids or item["target_object_id"] not in object_ids]
    source_ids_with_multiple_signatures = sorted(key for key, values in source_signatures.items() if len(values) > 1)
    projected_graph_ids = {item["source_graph_relationship_id"] for item in outcomes if item["projection_outcome"] == "PROJECTED"}
    graph_ids = {clean(item.get("graph_relationship_id")) for item in graph_relationships}
    if projected_graph_ids != graph_ids:
        errors.append({"stage": "VALIDATE_OBJECT_CATALOG", "error": "Graph relationship projection coverage does not reconcile"})
    if missing_endpoints:
        errors.append({"stage": "VALIDATE_OBJECT_CATALOG", "error": "Object relationships contain missing endpoints", "relationship_ids": missing_endpoints[:25]})
    if len(relationship_ids) != len(relationship_payload):
        errors.append({"stage": "VALIDATE_OBJECT_CATALOG", "error": "Duplicate object relationship IDs"})
    if set(relationship_ids) != {item["object_relationship_id"] for item in provenance_payload}:
        errors.append({"stage": "VALIDATE_OBJECT_CATALOG", "error": "Relationship-provenance identities do not reconcile"})
    if set(relationship_ids) != {item["object_relationship_id"] for item in validation_payload}:
        errors.append({"stage": "VALIDATE_OBJECT_CATALOG", "error": "Relationship-validation identities do not reconcile"})

    summary = {
        "object_type_counts": dict(sorted(Counter(item["object_type"] for item in object_payload).items())),
        "object_class_counts": dict(sorted(Counter(item["object_class"] for item in object_payload).items())),
        "relationship_type_counts": dict(sorted(Counter(item["relationship_type"] for item in relationship_payload).items())),
        "relationship_class_counts": dict(sorted(Counter(item["relationship_class"] for item in relationship_payload).items())),
        "resolution_status_counts": dict(sorted(Counter(item["resolution_status"] for item in resolution_payload).items())),
    }
    status = "PARTIAL" if errors else "COMPLETE"
    manifest = {
        "snapshot_id": run_id, "pipeline_run_id": pipeline_run_id or graph_manifest.get("pipeline_run_id"),
        "source_graph_snapshot_id": graph_manifest.get("snapshot_id"), "status": status,
        "started_at": started.isoformat(), "completed_at": now_utc().isoformat(),
        "object_count": len(object_payload), "relationship_count": len(relationship_payload),
        "validation_count": len(validation_payload), "provenance_count": len(provenance_payload),
        "reference_resolution_count": len(resolution_payload), "projection_outcome_count": len(outcomes),
        "source_graph_relationship_count": len(graph_relationships),
        "configured_declaration_count": len(declarations), "shared_source_count": len(shared_sources),
        "source_ids_with_multiple_physical_signatures": len(source_ids_with_multiple_signatures),
        "source_ids_with_multiple_physical_signature_details": source_ids_with_multiple_signatures[:25],
        "missing_endpoint_count": len(missing_endpoints), "error_count": len(errors),
        "source_artifacts_modified": False, "publish_current_requested": publish_current,
        "published_current": bool(status == "COMPLETE" and publish_current),
    }
    outputs = {
        "object_catalog.json": object_payload, "object_relationships.json": relationship_payload,
        "object_relationship_validations.json": validation_payload,
        "object_relationship_provenance.json": provenance_payload,
        "object_reference_resolutions.json": resolution_payload,
        "object_projection_outcomes.json": sorted(outcomes, key=lambda x: x["source_graph_relationship_id"]),
        "object_catalog_summary.json": summary, "object_catalog_manifest.json": manifest,
        "object_catalog_errors.json": errors,
    }
    for name, payload in outputs.items():
        write_json(snapshot_dir / name, payload)
    if status == "COMPLETE" and publish_current:
        for name, payload in outputs.items():
            write_json(current_root / name, payload)
    write_json(review_root / "latest_object_catalog_manifest.json", manifest)
    return manifest


def render_manifest(manifest: Mapping[str, Any]) -> str:
    return "\n".join([
        "=" * 70, "PAX EXPLORER OBJECT-TO-OBJECT CATALOG", "=" * 70,
        f"Snapshot      : {manifest.get('snapshot_id', 'UNKNOWN')}",
        f"Status        : {manifest.get('status', 'UNKNOWN')}",
        f"Objects       : {int(manifest.get('object_count', 0) or 0):,}",
        f"Relationships : {int(manifest.get('relationship_count', 0) or 0):,}",
        f"Validations   : {int(manifest.get('validation_count', 0) or 0):,}",
        f"Provenance    : {int(manifest.get('provenance_count', 0) or 0):,}",
        f"Resolutions   : {int(manifest.get('reference_resolution_count', 0) or 0):,}",
        f"Errors        : {int(manifest.get('error_count', 0) or 0):,}",
        f"Published     : {bool(manifest.get('published_current', False))}",
    ]) + "\n"


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the governed object-to-object catalog")
    parser.add_argument("--current-root", type=Path, default=CURRENT_ROOT)
    parser.add_argument("--snapshot-root", type=Path, default=SNAPSHOT_ROOT)
    parser.add_argument("--review-root", type=Path, default=REVIEW_ROOT)
    parser.add_argument("--pipeline-run-id", default="")
    parser.add_argument("--no-publish", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        manifest = build_object_catalog(current_root=args.current_root, snapshot_root=args.snapshot_root,
                                        review_root=args.review_root, pipeline_run_id=args.pipeline_run_id,
                                        publish_current=not args.no_publish)
        print(render_manifest(manifest), end="")
        return 0 if manifest.get("status") == "COMPLETE" else 1
    except Exception as error:
        print(f"Object catalog build failed: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
