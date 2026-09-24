from __future__ import annotations

"""Collect first-class TM1 hierarchy definitions and ownership relationships.

The collector enumerates hierarchy names only. It does not retrieve hierarchy
objects, elements, edges, subsets, members, levels, or attribute values.
"""

import argparse
import json
import statistics
import sys
from collections.abc import Iterable as IterableABC, Mapping
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utilities.tm1_connection import get_tm1_connection

SNAPSHOT_ROOT = ROOT_DIR / "data" / "snapshots"
CURRENT_ROOT = ROOT_DIR / "data" / "current"
OBJECTS_FILE = CURRENT_ROOT / "objects.json"
VALID_SCOPES = ("regular", "control", "all")
SLOW_REQUEST_WARNING_SECONDS = 1.0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_snapshot_id(timestamp: datetime) -> str:
    return timestamp.strftime("%Y%m%dT%H%M%SZ")


def normalize_name(value: Any) -> str:
    return str(value or "").strip()


def normalized_key(value: Any) -> str:
    return " ".join(normalize_name(value).casefold().split())


def is_control_name(value: Any) -> bool:
    return normalize_name(value).startswith("}")


def read_json(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8-sig") as file:
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


def sorted_unique_names(values: Iterable[Any]) -> list[str]:
    by_key: dict[str, str] = {}
    for value in values:
        name = normalize_name(getattr(value, "name", value))
        if name:
            by_key.setdefault(normalized_key(name), name)
    return sorted(by_key.values(), key=str.casefold)


def normalize_name_collection(value: Any, *, description: str) -> list[str]:
    if isinstance(value, Mapping):
        return sorted_unique_names(value.keys())
    return sorted_unique_names(iterable_items(value, description=description))


def get_dimension_names_from_tm1(tm1: Any) -> list[str]:
    service = getattr(tm1, "dimensions", None)
    method = getattr(service, "get_all_names", None)
    if not callable(method):
        raise AttributeError(
            "The TM1 connection does not expose dimensions.get_all_names()."
        )
    return normalize_name_collection(
        method(), description="TM1 dimension-name collection"
    )


def object_catalog_dimension_names(payload: Any, *, scope: str) -> list[str]:
    if not isinstance(payload, list):
        raise TypeError("objects.json must contain a JSON array.")
    names: list[str] = []
    for record in payload:
        if not isinstance(record, Mapping):
            continue
        if normalized_key(record.get("object_type")) != "dimension":
            continue
        name = normalize_name(record.get("object_name"))
        if not name:
            continue
        control = bool(record.get("is_control", is_control_name(name)))
        if scope == "regular" and control:
            continue
        if scope == "control" and not control:
            continue
        names.append(name)
    return sorted_unique_names(names)


def resolve_dimension_scope(
    tm1: Any,
    *,
    scope: str,
    dimensions: list[str] | None,
    object_catalog_path: Path | None,
) -> tuple[list[str], str]:
    if dimensions:
        return sorted_unique_names(dimensions), "EXPLICIT_DIMENSIONS"
    if object_catalog_path is not None and object_catalog_path.is_file():
        return (
            object_catalog_dimension_names(
                read_json(object_catalog_path), scope=scope
            ),
            "OBJECT_CATALOG",
        )
    return get_dimension_names_from_tm1(tm1), "TM1_DIMENSION_SERVICE"


def get_hierarchy_names(tm1: Any, dimension_name: str) -> list[str]:
    direct_service = getattr(tm1, "hierarchies", None)
    direct_method = getattr(direct_service, "get_all_names", None)
    if callable(direct_method):
        return normalize_name_collection(
            direct_method(dimension_name),
            description=f"Hierarchy names for {dimension_name}",
        )
    dimensions = getattr(tm1, "dimensions", None)
    nested_service = getattr(dimensions, "hierarchies", None)
    nested_method = getattr(nested_service, "get_all_names", None)
    if callable(nested_method):
        return normalize_name_collection(
            nested_method(dimension_name),
            description=f"Hierarchy names for {dimension_name}",
        )
    raise AttributeError(
        "The TM1 connection does not expose a hierarchy-name service."
    )


def hierarchy_node_id(dimension_name: str, hierarchy_name: str) -> str:
    return f"hierarchy::{dimension_name}::{hierarchy_name}"


def dimension_node_id(dimension_name: str) -> str:
    return f"dimension::{dimension_name}"


def relationship_id(dimension_name: str, hierarchy_name: str) -> str:
    return f"hierarchy-ownership::{dimension_name}::{hierarchy_name}"


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round((len(ordered) - 1) * fraction)),
    )
    return ordered[index]


def collect_hierarchies(
    tm1: Any,
    *,
    scope: str = "regular",
    dimensions: list[str] | None = None,
    snapshot_root: Path = SNAPSHOT_ROOT,
    current_root: Path = CURRENT_ROOT,
    object_catalog_path: Path | None = None,
    timestamp: datetime | None = None,
    publish_current: bool = True,
    progress: bool = False,
) -> dict[str, Any]:
    if scope not in VALID_SCOPES:
        raise ValueError(f"Unsupported scope: {scope}")

    started = timestamp or utc_now()
    started_clock = perf_counter()
    snapshot_id = build_snapshot_id(started)
    collected_at = started.isoformat()
    snapshot_dir = snapshot_root / snapshot_id

    dimension_names, dimension_source = resolve_dimension_scope(
        tm1,
        scope=scope,
        dimensions=dimensions,
        object_catalog_path=object_catalog_path,
    )

    records_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    relationships: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    request_metrics: list[dict[str, Any]] = []

    for index, dimension_name in enumerate(dimension_names, start=1):
        request_started = perf_counter()
        try:
            hierarchy_names = get_hierarchy_names(tm1, dimension_name)
            request_seconds = perf_counter() - request_started
            request_metrics.append(
                {
                    "dimension_name": dimension_name,
                    "hierarchy_count": len(hierarchy_names),
                    "request_seconds": round(request_seconds, 6),
                    "slow_request": (
                        request_seconds > SLOW_REQUEST_WARNING_SECONDS
                    ),
                }
            )
            for hierarchy_name in hierarchy_names:
                key = (
                    normalized_key(dimension_name),
                    normalized_key(hierarchy_name),
                )
                node_id = hierarchy_node_id(dimension_name, hierarchy_name)
                records_by_key.setdefault(
                    key,
                    {
                        "snapshot_id": snapshot_id,
                        "collected_at": collected_at,
                        "node_id": node_id,
                        "node_type": "HIERARCHY",
                        "object_type": "hierarchy",
                        "object_name": f"{dimension_name}::{hierarchy_name}",
                        "qualified_name": f"{dimension_name}::{hierarchy_name}",
                        "dimension_name": dimension_name,
                        "hierarchy_name": hierarchy_name,
                        "is_default_hierarchy": (
                            normalized_key(dimension_name)
                            == normalized_key(hierarchy_name)
                        ),
                        "is_control": (
                            is_control_name(dimension_name)
                            or is_control_name(hierarchy_name)
                        ),
                    },
                )
                edge_id = relationship_id(dimension_name, hierarchy_name)
                relationships.append(
                    {
                        "snapshot_id": snapshot_id,
                        "relationship_id": edge_id,
                        "source_id": node_id,
                        "source_type": "HIERARCHY",
                        "target_id": dimension_node_id(dimension_name),
                        "target_type": "DIMENSION",
                        "relationship_type": "BELONGS_TO_DIMENSION",
                        "relationship_origin": "METADATA",
                        "resolution_method": "CATALOG_MATCH",
                    }
                )
                validations.append(
                    {
                        "snapshot_id": snapshot_id,
                        "validation_id": f"validation::{edge_id}",
                        "relationship_id": edge_id,
                        "validation_status": "VALID",
                        "source_id": node_id,
                        "target_id": dimension_node_id(dimension_name),
                    }
                )
        except Exception as error:
            errors.append(
                {
                    "dimension_name": dimension_name,
                    "stage": "LIST_HIERARCHIES",
                    "error": f"{type(error).__name__}: {error}",
                }
            )

        if progress:
            print(
                f"dimension={index}/{len(dimension_names)} "
                f"name={dimension_name} "
                f"hierarchies={len(records_by_key)} "
                f"errors={len(errors)} "
                f"elapsed={perf_counter() - started_clock:.1f}s"
            )

    records = sorted(
        records_by_key.values(),
        key=lambda item: (
            item["dimension_name"].casefold(),
            item["hierarchy_name"].casefold(),
        ),
    )
    relationships.sort(key=lambda item: item["relationship_id"].casefold())
    validations.sort(key=lambda item: item["relationship_id"].casefold())

    elapsed = perf_counter() - started_clock
    durations = [float(item["request_seconds"]) for item in request_metrics]
    status = "PARTIAL" if errors else "COMPLETE"
    manifest = {
        "snapshot_id": snapshot_id,
        "status": status,
        "scope": scope,
        "started_at": collected_at,
        "completed_at": utc_now().isoformat(),
        "dimension_count": len(dimension_names),
        "hierarchy_count": len(records),
        "relationship_count": len(relationships),
        "validation_count": len(validations),
        "error_count": len(errors),
        "publish_current_requested": publish_current,
        "published_current": bool(status == "COMPLETE" and publish_current),
    }
    metrics = {
        "dimension_source": dimension_source,
        "dimension_count": len(dimension_names),
        "hierarchy_count": len(records),
        "relationship_count": len(relationships),
        "validation_count": len(validations),
        "total_seconds": round(elapsed, 6),
        "mean_request_seconds": (
            round(statistics.mean(durations), 6) if durations else 0.0
        ),
        "median_request_seconds": (
            round(statistics.median(durations), 6) if durations else 0.0
        ),
        "p95_request_seconds": round(percentile(durations, 0.95), 6),
        "maximum_request_seconds": (
            round(max(durations), 6) if durations else 0.0
        ),
        "slow_request_count": sum(
            bool(item["slow_request"]) for item in request_metrics
        ),
        "dimensions": request_metrics,
    }

    outputs = {
        "hierarchies.json": records,
        "hierarchy_relationships.json": relationships,
        "hierarchy_relationship_validations.json": validations,
        "hierarchy_manifest.json": manifest,
        "hierarchy_collection_metrics.json": metrics,
        "hierarchy_collection_errors.json": errors,
    }
    for file_name, payload in outputs.items():
        write_json(snapshot_dir / file_name, payload)
    if status == "COMPLETE" and publish_current:
        for file_name, payload in outputs.items():
            write_json(current_root / file_name, payload)
    return manifest


def print_manifest(manifest: dict[str, Any]) -> None:
    print("=" * 70)
    print("TM1 HIERARCHY DEFINITION INVENTORY")
    print("=" * 70)
    print(f"Snapshot      : {manifest.get('snapshot_id', 'UNKNOWN')}")
    print(f"Status        : {manifest.get('status', 'UNKNOWN')}")
    print(f"Scope         : {manifest.get('scope', 'UNKNOWN')}")
    print(f"Dimensions    : {int(manifest.get('dimension_count', 0) or 0):,}")
    print(f"Hierarchies   : {int(manifest.get('hierarchy_count', 0) or 0):,}")
    print(f"Relationships : {int(manifest.get('relationship_count', 0) or 0):,}")
    print(f"Validations   : {int(manifest.get('validation_count', 0) or 0):,}")
    print(f"Errors        : {int(manifest.get('error_count', 0) or 0):,}")
    print(f"Published     : {bool(manifest.get('published_current', False))}")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect first-class TM1 hierarchy definitions."
    )
    parser.add_argument(
        "--scope", choices=VALID_SCOPES, default="all"
    )
    parser.add_argument(
        "--dimension",
        action="append",
        dest="dimensions",
        help="Collect one dimension. Repeat for multiple dimensions.",
    )
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        with get_tm1_connection() as tm1:
            default_options = (
                args.scope == "all"
                and not args.dimensions
                and not args.no_publish
                and not args.quiet
            )
            if default_options:
                manifest = collect_hierarchies(
                    tm1,
                    scope="all",
                    object_catalog_path=OBJECTS_FILE,
                    progress=True,
                )
            else:
                manifest = collect_hierarchies(
                    tm1,
                    scope=args.scope,
                    dimensions=args.dimensions,
                    object_catalog_path=OBJECTS_FILE,
                    publish_current=not args.no_publish,
                    progress=not args.quiet,
                )
        print_manifest(manifest)
        return 0 if manifest.get("status") == "COMPLETE" else 1
    except Exception as error:
        print(
            "Hierarchy collection failed: "
            f"{type(error).__name__}: {error}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
