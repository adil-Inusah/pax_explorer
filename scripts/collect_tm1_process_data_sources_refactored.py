from __future__ import annotations

"""Collect first-class TM1 process data-source definitions safely.

The collector inventories process data-source metadata, creates one canonical
USES_DATA_SOURCE edge per configured process source, and emits additional
catalog edges for TM1 cube-view, hierarchy-subset, and file sources.
Passwords and credential-bearing values are never published.
"""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable as IterableABC, Mapping
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utilities.tm1_connection import get_tm1_connection

SNAPSHOT_ROOT = ROOT_DIR / "data" / "snapshots"
CURRENT_ROOT = ROOT_DIR / "data" / "current"
OBJECTS_FILE = CURRENT_ROOT / "objects.json"
VIEWS_FILE = CURRENT_ROOT / "views.json"
SUBSETS_FILE = CURRENT_ROOT / "subsets.json"

SOURCE_TYPES = {
    "NONE",
    "ASCII_FILE",
    "ODBC",
    "TM1_CUBE_VIEW",
    "TM1_DIMENSION_SUBSET",
    "JSON",
    "UNKNOWN",
}

SENSITIVE_FIELD_TOKENS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "credential",
    "privatekey",
    "private_key",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_snapshot_id(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def clean(value: Any) -> str:
    return str(value or "").strip()


def normalized_key(value: Any) -> str:
    return " ".join(clean(value).casefold().split())


def is_control_name(value: Any) -> bool:
    return clean(value).startswith("}")


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


def iterable_items(value: Any, *, description: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return list(value.values())
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{description} must be a collection, not a string.")
    if not isinstance(value, IterableABC):
        raise TypeError(
            f"{description} is not iterable. Received: {type(value).__name__}"
        )
    return list(value)


def property_value(value: Any, *names: str) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        result = getattr(value, name, None)
        if result is not None:
            return result
    return None


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_preview(value: str, limit: int = 160) -> str | None:
    text = clean(value)
    if not text:
        return None
    text = re.sub(
        r"(?i)(password|passwd|pwd|secret|token|api[_-]?key)\s*=\s*[^;\s]+",
        r"\1=<redacted>",
        text,
    )
    return text[:limit]


def is_sensitive_field(name: str) -> bool:
    token = normalized_key(name).replace(" ", "").replace("-", "_")
    return any(sensitive in token for sensitive in SENSITIVE_FIELD_TOKENS)


def process_name_of(process: Any) -> str:
    return clean(property_value(process, "name", "Name"))


def process_data_source_of(process: Any) -> Any:
    """Return a nested REST source or the flattened TM1py Process object.

    TM1py models data-source fields directly on Process as ``datasource_*``
    properties. Raw REST dictionaries may instead expose a nested DataSource.
    """
    nested = property_value(process, "data_source", "DataSource", "datasource")
    return nested if nested is not None else process


def source_field(source: Any, *names: str) -> str:
    return clean(property_value(source, *names))


def source_type_of(source: Any) -> str:
    if source is None:
        return "NONE"
    raw_type = clean(
        property_value(
            source,
            "datasource_type",
            "data_source_type",
            "dataSourceType",
            "type",
            "Type",
            "@odata.type",
        )
        or type(source).__name__
    ).upper()
    compact = re.sub(r"[^A-Z0-9]", "", raw_type)

    if compact in {"", "NONE", "PROCESS", "DICT", "MAPPING"}:
        # A flattened Process still needs its datasource_type inspected.
        flattened_type = clean(property_value(source, "datasource_type"))
        flattened_compact = re.sub(r"[^A-Z0-9]", "", flattened_type.upper())
        if flattened_compact in {"", "NONE"}:
            return "NONE"
        compact = flattened_compact

    if any(token in compact for token in ("ODBC", "SQL", "DATABASE")):
        return "ODBC"
    if any(token in compact for token in ("CUBEVIEW", "TM1VIEW")):
        return "TM1_CUBE_VIEW"
    if any(token in compact for token in ("DIMENSIONSUBSET", "SUBSET")):
        return "TM1_DIMENSION_SUBSET"
    if "JSON" in compact:
        return "JSON"
    if any(token in compact for token in ("ASCII", "CHARACTERDELIMITED", "FILE")):
        return "ASCII_FILE"

    if source_field(source, "datasource_query", "query", "Query"):
        return "ODBC"
    if source_field(source, "datasource_view", "view", "View"):
        return "TM1_CUBE_VIEW"
    if source_field(source, "datasource_subset", "subset", "Subset"):
        return "TM1_DIMENSION_SUBSET"
    if source_field(source, "datasource_json_root_pointer", "jsonRootPointer", "json_root_pointer"):
        return "JSON"
    if source_field(
        source,
        "datasource_data_source_name_for_server",
        "dataSourceNameForServer",
        "data_source_name_for_server",
        "datasource_data_source_name_for_client",
        "dataSourceNameForClient",
        "data_source_name_for_client",
    ):
        return "ASCII_FILE"
    return "UNKNOWN"


def catalog_key_set(path: Path | None, object_type: str, name_field: str = "object_name") -> set[str]:
    if path is None or not path.is_file():
        return set()
    payload = read_json(path)
    if not isinstance(payload, list):
        raise TypeError(f"{path.name} must contain a JSON array.")
    return {
        normalized_key(record.get(name_field))
        for record in payload
        if isinstance(record, Mapping)
        and (
            not object_type
            or normalized_key(record.get("object_type")) == normalized_key(object_type)
        )
        and clean(record.get(name_field))
    }


def get_process_service(tm1: Any) -> Any:
    service = getattr(tm1, "processes", None)
    if service is None:
        raise AttributeError("TM1 connection does not expose a process service.")
    return service


def get_processes(service: Any) -> list[Any]:
    """Retrieve full process definitions so flattened datasource fields exist."""
    get_all_names = getattr(service, "get_all_names", None)
    get_one = getattr(service, "get", None)
    if callable(get_all_names) and callable(get_one):
        names = iterable_items(
            get_all_names(),
            description="TM1 process-name collection",
        )
        return [get_one(clean(name)) for name in names if clean(name)]

    get_all = getattr(service, "get_all", None)
    if callable(get_all):
        return iterable_items(get_all(), description="TM1 process collection")

    raise AttributeError(
        "Process service supports neither get_all_names()/get() nor get_all()."
    )


def process_node_id(name: str) -> str:
    return f"process::{name}"


def data_source_node_id(source_type: str, identity: str) -> str:
    return f"data-source::{source_type}::{sha256_text(identity)}"


def file_node_id(path_value: str) -> str:
    return f"file::{sha256_text(path_value.casefold())}"


def cube_node_id(name: str) -> str:
    return f"cube::{name}"


def view_node_id(cube_name: str, view_name: str) -> str:
    return f"view::{cube_name}::PUBLIC::<none>::{view_name}"


def subset_node_id(dimension: str, hierarchy: str, subset: str) -> str:
    return f"subset::{dimension}::{hierarchy}::PUBLIC::<none>::{subset}"


def effective_cube_name(cube_name: Any, server_source_name: Any, client_source_name: Any) -> str:
    """Return the strongest available cube coordinate for a TM1 view source."""
    return clean(cube_name or server_source_name or client_source_name)


def effective_hierarchy_name(dimension_name: Any, hierarchy_name: Any) -> str:
    """Return the explicit hierarchy, or the TM1 default hierarchy."""
    return clean(hierarchy_name or dimension_name)


def configured_source_declaration_id(process_name: str, source_type: str, shared_source_id: str) -> str:
    identity = "\x1f".join((process_node_id(process_name), source_type, shared_source_id))
    return f"configured-source::{sha256_text(identity.casefold())}"


def build_source_record(
    *,
    run_id: str,
    collected_at: str,
    process_name: str,
    source: Any,
) -> tuple[dict[str, Any], str]:
    source_type = source_type_of(source)
    server_name = source_field(
        source, "datasource_data_source_name_for_server", "dataSourceNameForServer", "data_source_name_for_server"
    )
    client_name = source_field(
        source, "datasource_data_source_name_for_client", "dataSourceNameForClient", "data_source_name_for_client"
    )
    cube_name = source_field(source, "cube", "Cube", "cube_name")
    view_name = source_field(source, "datasource_view", "view", "View", "view_name")
    dimension_name = source_field(source, "dimension", "Dimension", "dimension_name")
    hierarchy_name = source_field(source, "hierarchy", "Hierarchy", "hierarchy_name")
    subset_name = source_field(source, "datasource_subset", "subset", "Subset", "subset_name")
    query = source_field(source, "datasource_query", "query", "Query")
    username = source_field(source, "datasource_user_name", "userName", "username", "user_name")
    password = source_field(source, "datasource_password", "password", "Password")
    json_root = source_field(source, "datasource_json_root_pointer", "jsonRootPointer", "json_root_pointer")
    effective_cube = effective_cube_name(cube_name, server_name, client_name)
    effective_hierarchy = effective_hierarchy_name(dimension_name, hierarchy_name)

    if source_type == "TM1_CUBE_VIEW":
        identity = (
            f"{source_type}::{normalized_key(effective_cube)}::"
            f"{normalized_key(view_name)}"
        )
    elif source_type == "TM1_DIMENSION_SUBSET":
        identity = (
            f"{source_type}::{normalized_key(dimension_name)}::"
            f"{normalized_key(effective_hierarchy)}::"
            f"{normalized_key(subset_name)}"
        )
    elif source_type in {"ASCII_FILE", "JSON"}:
        identity = f"{source_type}::{server_name or client_name}"
    elif source_type == "ODBC":
        identity = f"{source_type}::{server_name}::{query}"
    else:
        identity = f"{source_type}::{process_name}"

    node_id = data_source_node_id(source_type, identity)
    record = {
        "snapshot_id": run_id,
        "collected_at": collected_at,
        "node_id": node_id,
        "node_type": "EXTERNAL_DATA_SOURCE",
        "object_type": "external_data_source",
        "source_type": source_type,
        "process_name": process_name,
        "server_source_name": server_name or None,
        "client_source_name": client_name or None,
        "cube_name": cube_name or None,
        "effective_cube_name": effective_cube or None,
        "view_name": view_name or None,
        "dimension_name": dimension_name or None,
        "hierarchy_name": hierarchy_name or None,
        "effective_hierarchy_name": effective_hierarchy or None,
        "subset_name": subset_name or None,
        "query_hash": sha256_text(query) if query else None,
        "query_preview": safe_preview(query),
        "json_root_pointer": json_root or None,
        "username_present": bool(username),
        "username_hash": sha256_text(username) if username else None,
        "credential_present": bool(password),
        "credential_redacted": bool(password),
        "is_control": is_control_name(process_name),
    }
    return record, identity


def collect_process_data_sources(
    tm1: Any,
    *,
    object_catalog_path: Path | None = None,
    views_path: Path | None = None,
    subsets_path: Path | None = None,
    snapshot_root: Path = SNAPSHOT_ROOT,
    current_root: Path = CURRENT_ROOT,
    timestamp: datetime | None = None,
    publish_current: bool = True,
) -> dict[str, Any]:
    started = timestamp or utc_now()
    started_clock = perf_counter()
    run_id = build_snapshot_id(started)
    collected_at = started.isoformat()
    snapshot_dir = snapshot_root / run_id

    object_catalog_loaded = object_catalog_path is not None and object_catalog_path.is_file()
    process_keys = catalog_key_set(object_catalog_path, "process") if object_catalog_loaded else set()
    cube_keys = catalog_key_set(object_catalog_path, "cube") if object_catalog_loaded else set()
    view_catalog_loaded = views_path is not None and views_path.is_file()
    view_keys = catalog_key_set(views_path, "", "qualified_name") if view_catalog_loaded else set()
    subset_catalog_loaded = subsets_path is not None and subsets_path.is_file()
    subset_keys = catalog_key_set(subsets_path, "", "qualified_name") if subset_catalog_loaded else set()

    processes = get_processes(get_process_service(tm1))
    processes.sort(key=lambda item: process_name_of(item).casefold())

    source_records_by_id: dict[str, dict[str, Any]] = {}
    process_source_records: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    bridge_relationship_ids: set[str] = set()
    errors: list[dict[str, Any]] = []
    source_type_counts: Counter[str] = Counter()

    for process in processes:
        process_name = process_name_of(process)
        if not process_name:
            errors.append({"process_name": None, "stage": "PARSE_PROCESS", "error": "Process has no name"})
            continue
        source = process_data_source_of(process)
        source_type = source_type_of(source)
        source_type_counts[source_type] += 1
        if source_type == "NONE":
            continue

        record, identity = build_source_record(
            run_id=run_id,
            collected_at=collected_at,
            process_name=process_name,
            source=source,
        )
        node_id = record["node_id"]
        source_records_by_id.setdefault(node_id, record)
        declaration_id = configured_source_declaration_id(process_name, source_type, node_id)
        process_source_records.append({
            "snapshot_id": run_id,
            "process_name": process_name,
            "process_id": process_node_id(process_name),
            "configured_data_source_id": declaration_id,
            "source_id": node_id,
            "shared_source_id": node_id,
            "source_type": source_type,
            "server_source_name": record.get("server_source_name"),
            "client_source_name": record.get("client_source_name"),
            "cube_name": record.get("cube_name"),
            "effective_cube_name": record.get("effective_cube_name"),
            "view_name": record.get("view_name"),
            "dimension_name": record.get("dimension_name"),
            "hierarchy_name": record.get("hierarchy_name"),
            "effective_hierarchy_name": record.get("effective_hierarchy_name"),
            "subset_name": record.get("subset_name"),
            "query_hash": record.get("query_hash"),
            "json_root_pointer": record.get("json_root_pointer"),
        })

        edge_id = f"process-data-source::{process_name}::{node_id}"
        relationships.append({
            "snapshot_id": run_id,
            "relationship_id": edge_id,
            "source_id": process_node_id(process_name),
            "source_type": "PROCESS",
            "target_id": node_id,
            "target_type": "EXTERNAL_DATA_SOURCE",
            "relationship_type": "USES_DATA_SOURCE",
            "relationship_origin": "PROCESS_DEFINITION",
            "resolution_method": "CATALOG_EXTRACTION",
        })
        process_valid = not object_catalog_loaded or normalized_key(process_name) in process_keys
        validations.append({
            "snapshot_id": run_id,
            "validation_id": f"validation::{edge_id}",
            "relationship_id": edge_id,
            "validation_status": "VALID" if process_valid else "PROCESS_NOT_IN_CATALOG",
            "source_id": process_node_id(process_name),
            "target_id": node_id,
        })

        specialized: tuple[str, str, str, str] | None = None
        status = "EXTERNAL_DEPENDENCY_NOT_CROSS_CHECKED"
        if source_type in {"ASCII_FILE", "JSON"}:
            path_value = clean(record["server_source_name"] or record["client_source_name"])
            if path_value:
                specialized = ("READS_FROM_FILE", file_node_id(path_value), "FILE", path_value)
        elif source_type == "ODBC":
            specialized = ("READS_FROM_ODBC", node_id, "EXTERNAL_DATA_SOURCE", identity)
        elif source_type == "TM1_CUBE_VIEW":
            cube_name = clean(record["effective_cube_name"])
            view_name = clean(record["view_name"])
            if cube_name and view_name:
                specialized = ("READS_FROM_VIEW", view_node_id(cube_name, view_name), "VIEW", f"{cube_name}::{view_name}")
                if view_catalog_loaded:
                    status = "VALID" if normalized_key(f"{cube_name}::{view_name}") in view_keys else "VIEW_NOT_IN_CATALOG"
                elif object_catalog_loaded and normalized_key(cube_name) not in cube_keys:
                    status = "CUBE_NOT_IN_CATALOG"
        elif source_type == "TM1_DIMENSION_SUBSET":
            dimension = clean(record["dimension_name"])
            hierarchy = clean(record["effective_hierarchy_name"] or dimension)
            subset = clean(record["subset_name"])
            if dimension and hierarchy and subset:
                specialized = ("READS_FROM_SUBSET", subset_node_id(dimension, hierarchy, subset), "SUBSET", f"{dimension}::{hierarchy}::{subset}")
                if subset_catalog_loaded:
                    status = "VALID" if normalized_key(f"{dimension}::{hierarchy}::{subset}") in subset_keys else "SUBSET_NOT_IN_CATALOG"

        if specialized is not None:
            relationship_type, target_id, target_type, target_name = specialized
            specialized_id = f"process-source-target::{process_name}::{relationship_type}::{sha256_text(target_name)}"
            specialized_relationship = {
                "snapshot_id": run_id,
                "relationship_id": specialized_id,
                "source_id": process_node_id(process_name),
                "source_type": "PROCESS",
                "target_id": target_id,
                "target_type": target_type,
                "relationship_type": relationship_type,
                "relationship_origin": "PROCESS_DEFINITION",
                "resolution_method": "DATA_SOURCE_DEFINITION",
                "process_name": process_name,
                "target_expression": target_name,
                "configured_data_source_id": node_id,
            }
            relationships.append(specialized_relationship)
            validations.append({
                "snapshot_id": run_id,
                "validation_id": f"validation::{specialized_id}",
                "relationship_id": specialized_id,
                "validation_status": status,
                "source_id": process_node_id(process_name),
                "target_id": target_id,
                "process_name": process_name,
                "target_expression": target_name,
                "configured_data_source_id": node_id,
            })

            # Preserve the explicit configured-source-to-file identity bridge.
            if relationship_type == "READS_FROM_FILE":
                bridge_id = f"data-source-file::{node_id}::{target_id}"
                if bridge_id not in bridge_relationship_ids:
                    bridge_relationship_ids.add(bridge_id)
                    relationships.append({
                        "snapshot_id": run_id,
                        "relationship_id": bridge_id,
                        "source_id": node_id,
                        "source_type": "EXTERNAL_DATA_SOURCE",
                        "target_id": target_id,
                        "target_type": "FILE",
                        "relationship_type": "RESOLVES_TO_FILE",
                        "relationship_origin": "PROCESS_DEFINITION",
                        "resolution_method": (
                            "SERVER_SOURCE_NAME"
                            if record.get("server_source_name")
                            else "CLIENT_SOURCE_NAME"
                        ),
                        "target_expression": target_name,
                        "configured_data_source_id": node_id,
                    })
                    validations.append({
                        "snapshot_id": run_id,
                        "validation_id": f"validation::{bridge_id}",
                        "relationship_id": bridge_id,
                        "validation_status": "EXTERNAL_DEPENDENCY_NOT_CROSS_CHECKED",
                        "source_id": node_id,
                        "target_id": target_id,
                        "target_expression": target_name,
                        "configured_data_source_id": node_id,
                    })

    source_records = sorted(source_records_by_id.values(), key=lambda item: (item["source_type"], item["node_id"]))
    process_source_records.sort(key=lambda item: item["process_name"].casefold())
    relationships.sort(key=lambda item: item["relationship_id"].casefold())
    validations.sort(key=lambda item: item["relationship_id"].casefold())

    # Cross-artifact integrity checks. A COMPLETE publication must be usable
    # from an empty current directory without relying on stale enrichment.
    relationship_ids = [clean(item.get("relationship_id")) for item in relationships]
    validation_relationship_ids = [
        clean(item.get("relationship_id")) for item in validations
    ]
    source_node_ids = {clean(item.get("node_id")) for item in source_records}
    if len(relationships) != len(validations):
        errors.append({
            "stage": "VALIDATE_OUTPUT",
            "error": "Relationship and validation counts do not reconcile",
        })
    if len(relationship_ids) != len(set(relationship_ids)):
        errors.append({
            "stage": "VALIDATE_OUTPUT",
            "error": "Duplicate relationship IDs detected",
        })
    if set(relationship_ids) != set(validation_relationship_ids):
        errors.append({
            "stage": "VALIDATE_OUTPUT",
            "error": "Relationship and validation identities do not reconcile",
        })
    bridge_keys = {
        (clean(item.get("source_id")), clean(item.get("target_id")))
        for item in relationships
        if item.get("relationship_type") == "RESOLVES_TO_FILE"
    }
    for relationship in relationships:
        if relationship.get("relationship_type") != "READS_FROM_FILE":
            continue
        expression = clean(relationship.get("target_expression"))
        configured_id = clean(relationship.get("configured_data_source_id"))
        if not expression:
            errors.append({
                "stage": "VALIDATE_OUTPUT",
                "relationship_id": relationship.get("relationship_id"),
                "error": "READS_FROM_FILE has no target_expression",
            })
        if not configured_id or configured_id not in source_node_ids:
            errors.append({
                "stage": "VALIDATE_OUTPUT",
                "relationship_id": relationship.get("relationship_id"),
                "error": "READS_FROM_FILE has no valid configured_data_source_id",
            })
        if expression and clean(relationship.get("target_id")) != file_node_id(expression):
            errors.append({
                "stage": "VALIDATE_OUTPUT",
                "relationship_id": relationship.get("relationship_id"),
                "error": "READS_FROM_FILE target_id does not match target_expression",
            })
        if (configured_id, clean(relationship.get("target_id"))) not in bridge_keys:
            errors.append({
                "stage": "VALIDATE_OUTPUT",
                "relationship_id": relationship.get("relationship_id"),
                "error": "READS_FROM_FILE has no RESOLVES_TO_FILE bridge",
            })

    status = "PARTIAL" if errors else "COMPLETE"

    manifest = {
        "snapshot_id": run_id,
        "status": status,
        "started_at": collected_at,
        "completed_at": utc_now().isoformat(),
        "process_count": len(processes),
        "configured_process_count": len(process_source_records),
        "data_source_count": len(source_records),
        "relationship_count": len(relationships),
        "validation_count": len(validations),
        "error_count": len(errors),
        "total_seconds": round(perf_counter() - started_clock, 6),
        "source_type_counts": dict(sorted(source_type_counts.items())),
        "publish_current_requested": publish_current,
        "published_current": bool(status == "COMPLETE" and publish_current),
    }
    outputs = {
        "external_data_sources.json": source_records,
        "process_data_sources.json": process_source_records,
        "process_data_source_relationships.json": relationships,
        "process_data_source_validations.json": validations,
        "data_source_manifest.json": manifest,
        "data_source_collection_errors.json": errors,
    }
    for file_name, payload in outputs.items():
        write_json(snapshot_dir / file_name, payload)
    if status == "COMPLETE" and publish_current:
        for file_name, payload in outputs.items():
            write_json(current_root / file_name, payload)
    return manifest


def print_manifest(manifest: dict[str, Any]) -> None:
    print("=" * 70)
    print("TM1 PROCESS DATA SOURCE INVENTORY")
    print("=" * 70)
    print(f"Snapshot      : {manifest.get('snapshot_id', 'UNKNOWN')}")
    print(f"Status        : {manifest.get('status', 'UNKNOWN')}")
    print(f"Processes     : {int(manifest.get('process_count', 0) or 0):,}")
    print(f"Configured    : {int(manifest.get('configured_process_count', 0) or 0):,}")
    print(f"Data sources  : {int(manifest.get('data_source_count', 0) or 0):,}")
    print(f"Relationships : {int(manifest.get('relationship_count', 0) or 0):,}")
    print(f"Validations   : {int(manifest.get('validation_count', 0) or 0):,}")
    print(f"Errors        : {int(manifest.get('error_count', 0) or 0):,}")
    print(f"Published     : {bool(manifest.get('published_current', False))}")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect TM1 process data-source definitions safely.")
    parser.add_argument("--object-catalog", type=Path, default=OBJECTS_FILE)
    parser.add_argument("--views", type=Path, default=VIEWS_FILE)
    parser.add_argument("--subsets", type=Path, default=SUBSETS_FILE)
    parser.add_argument("--no-publish", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        with get_tm1_connection() as tm1:
            manifest = collect_process_data_sources(
                tm1,
                object_catalog_path=args.object_catalog,
                views_path=args.views,
                subsets_path=args.subsets,
                publish_current=not args.no_publish,
            )
        print_manifest(manifest)
        return 0 if manifest.get("status") == "COMPLETE" else 1
    except Exception as error:
        print(f"Data-source collection failed: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
