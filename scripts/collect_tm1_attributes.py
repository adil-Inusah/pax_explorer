from __future__ import annotations

"""Collect TM1 element-attribute definitions through targeted REST endpoints.

This collector retrieves hierarchy names and element-attribute definitions only.
It deliberately avoids ``tm1.hierarchies.get()``, because that method expands
hierarchy elements, edges, subsets, attributes, and the default member.

Publication rules:
* Every run writes a timestamped diagnostic snapshot.
* Only COMPLETE runs replace the governed files under ``data/current``.
* Attribute values are never collected. Only definitions are inventoried.
"""

import argparse
import json
import statistics
import sys
from collections import Counter
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
    """Write JSON atomically so governed output is never partially replaced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write("\n")
    temporary.replace(path)


def sorted_unique_names(values: Iterable[Any]) -> list[str]:
    by_key: dict[str, str] = {}
    for value in values:
        name = normalize_name(getattr(value, "name", value))
        if name:
            by_key.setdefault(normalized_key(name), name)
    return sorted(by_key.values(), key=str.casefold)


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


def normalize_name_collection(value: Any, *, description: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return sorted_unique_names(value.keys())
    return sorted_unique_names(iterable_items(value, description=description))


def object_catalog_dimension_names(
    object_catalog: Any,
    *,
    scope: str,
) -> list[str]:
    if not isinstance(object_catalog, list):
        raise TypeError("objects.json must contain a JSON array.")

    names: list[str] = []
    for record in object_catalog:
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


def get_dimension_names_from_tm1(tm1: Any) -> list[str]:
    service = getattr(tm1, "dimensions", None)
    method = getattr(service, "get_all_names", None)
    if not callable(method):
        raise AttributeError(
            "The TM1 connection does not expose dimensions.get_all_names()."
        )
    return normalize_name_collection(
        method(),
        description="TM1 dimension-name collection",
    )


def get_hierarchy_names(tm1: Any, dimension_name: str) -> list[str]:
    service = getattr(tm1, "hierarchies", None)
    method = getattr(service, "get_all_names", None)
    if callable(method):
        return normalize_name_collection(
            method(dimension_name),
            description=f"Hierarchy names for {dimension_name}",
        )

    dimension_service = getattr(tm1, "dimensions", None)
    nested_service = getattr(dimension_service, "hierarchies", None)
    nested_method = getattr(nested_service, "get_all_names", None)
    if callable(nested_method):
        return normalize_name_collection(
            nested_method(dimension_name),
            description=f"Hierarchy names for {dimension_name}",
        )

    raise AttributeError(
        "The TM1 connection does not expose a targeted hierarchy-name service."
    )


def get_element_service(tm1: Any) -> Any:
    direct_service = getattr(tm1, "elements", None)
    direct_method = getattr(direct_service, "get_element_attributes", None)
    if callable(direct_method):
        return direct_service

    hierarchy_service = getattr(tm1, "hierarchies", None)
    nested_service = getattr(hierarchy_service, "elements", None)
    nested_method = getattr(nested_service, "get_element_attributes", None)
    if callable(nested_method):
        return nested_service

    raise AttributeError(
        "The TM1 connection does not expose elements.get_element_attributes()."
    )


def get_element_attributes(
    element_service: Any,
    dimension_name: str,
    hierarchy_name: str,
) -> list[Any]:
    """Retrieve attribute definitions only, never the complete hierarchy."""
    method = getattr(element_service, "get_element_attributes", None)
    if not callable(method):
        raise AttributeError(
            "The element service does not support get_element_attributes()."
        )
    return iterable_items(
        method(dimension_name, hierarchy_name),
        description=(
            "Element-attribute definitions for "
            f"{dimension_name}::{hierarchy_name}"
        ),
    )


def attribute_name(attribute: Any) -> str:
    if isinstance(attribute, str):
        return normalize_name(attribute)
    if isinstance(attribute, Mapping):
        return normalize_name(attribute.get("Name") or attribute.get("name"))
    return normalize_name(
        getattr(attribute, "name", None) or getattr(attribute, "Name", None)
    )


def raw_attribute_type(attribute: Any) -> Any:
    if isinstance(attribute, Mapping):
        return attribute.get(
            "Type",
            attribute.get("type", attribute.get("attribute_type")),
        )
    return (
        getattr(attribute, "attribute_type", None)
        or getattr(attribute, "type", None)
        or getattr(attribute, "Type", None)
    )


def normalize_attribute_type(value: Any) -> tuple[str, str, bool]:
    enum_name = normalize_name(getattr(value, "name", ""))
    enum_value = getattr(value, "value", value)
    raw_value = normalize_name(enum_value)
    token = (enum_name or raw_value).upper()

    if token in {"1", "N", "NUMERIC", "NUMBER"}:
        return raw_value or "N", "NUMERIC", False
    if token in {"3", "A", "ALIAS"}:
        return raw_value or "A", "ALIAS", True
    if token in {"2", "S", "STRING", "TEXT", ""}:
        return raw_value or "S", "STRING", False
    return raw_value or token, "UNKNOWN", False


def build_attribute_record(
    *,
    snapshot_id: str,
    collected_at: str,
    dimension_name: str,
    hierarchy_name: str,
    attribute: Any,
) -> dict[str, Any] | None:
    name = attribute_name(attribute)
    if not name:
        return None
    tm1_type, data_type, is_alias = normalize_attribute_type(
        raw_attribute_type(attribute)
    )
    qualified_name = f"{dimension_name}::{hierarchy_name}::{name}"
    return {
        "snapshot_id": snapshot_id,
        "collected_at": collected_at,
        "object_type": "attribute",
        "object_name": qualified_name,
        "qualified_name": qualified_name,
        "dimension_name": dimension_name,
        "hierarchy_name": hierarchy_name,
        "attribute_name": name,
        "tm1_attribute_type": tm1_type,
        "attribute_data_type": data_type,
        "is_alias": is_alias,
        "is_default_hierarchy": (
            normalized_key(dimension_name) == normalized_key(hierarchy_name)
        ),
        "is_control": (
            is_control_name(dimension_name)
            or is_control_name(hierarchy_name)
            or is_control_name(name)
        ),
    }


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def resolve_collection_scope(
    tm1: Any,
    *,
    scope: str,
    dimensions: list[str] | None,
    object_catalog_path: Path,
) -> tuple[list[str], str]:
    if dimensions:
        return sorted_unique_names(dimensions), "EXPLICIT_DIMENSIONS"

    if object_catalog_path.is_file():
        names = object_catalog_dimension_names(
            read_json(object_catalog_path),
            scope=scope,
        )
        return names, "OBJECT_CATALOG"

    names = get_dimension_names_from_tm1(tm1)
    if scope == "regular":
        names = [name for name in names if not is_control_name(name)]
    elif scope == "control":
        names = [name for name in names if is_control_name(name)]
    return names, "TM1_DIMENSION_SERVICE"


def collect_attributes(
    tm1: Any,
    *,
    scope: str = "regular",
    dimensions: list[str] | None = None,
    snapshot_root: Path = SNAPSHOT_ROOT,
    current_root: Path = CURRENT_ROOT,
    object_catalog_path: Path = OBJECTS_FILE,
    timestamp: datetime | None = None,
    publish_current: bool = True,
    progress: bool = True,
) -> dict[str, Any]:
    if scope not in VALID_SCOPES:
        raise ValueError(f"Unsupported scope: {scope}")

    started = timestamp or utc_now()
    started_clock = perf_counter()
    run_snapshot_id = build_snapshot_id(started)
    collected_at = started.isoformat()
    snapshot_dir = snapshot_root / run_snapshot_id / "attributes"

    records_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    hierarchy_metrics: list[dict[str, Any]] = []
    element_service = get_element_service(tm1)

    dimension_names, dimension_source = resolve_collection_scope(
        tm1,
        scope=scope,
        dimensions=dimensions,
        object_catalog_path=object_catalog_path,
    )

    attempted_hierarchies = 0
    completed_hierarchies = 0
    for index, dimension_name in enumerate(dimension_names, start=1):
        dimension_started = perf_counter()
        hierarchy_names: list[str] = []
        try:
            list_started = perf_counter()
            hierarchy_names = get_hierarchy_names(tm1, dimension_name)
            hierarchy_list_seconds = perf_counter() - list_started
        except Exception as error:
            errors.append(
                {
                    "dimension_name": dimension_name,
                    "hierarchy_name": None,
                    "stage": "LIST_HIERARCHIES",
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            continue

        for hierarchy_name in hierarchy_names:
            attempted_hierarchies += 1
            request_started = perf_counter()
            try:
                attributes = get_element_attributes(
                    element_service,
                    dimension_name,
                    hierarchy_name,
                )
                request_seconds = perf_counter() - request_started
                completed_hierarchies += 1

                record_count_before = len(records_by_key)
                for attribute in attributes:
                    record = build_attribute_record(
                        snapshot_id=run_snapshot_id,
                        collected_at=collected_at,
                        dimension_name=dimension_name,
                        hierarchy_name=hierarchy_name,
                        attribute=attribute,
                    )
                    if record is None:
                        continue
                    key = (
                        normalized_key(dimension_name),
                        normalized_key(hierarchy_name),
                        normalized_key(record["attribute_name"]),
                    )
                    records_by_key.setdefault(key, record)

                hierarchy_metrics.append(
                    {
                        "dimension_name": dimension_name,
                        "hierarchy_name": hierarchy_name,
                        "hierarchy_list_seconds": round(hierarchy_list_seconds, 6),
                        "attribute_request_seconds": round(request_seconds, 6),
                        "attribute_count": len(records_by_key) - record_count_before,
                        "slow_request": (
                            request_seconds > SLOW_REQUEST_WARNING_SECONDS
                        ),
                    }
                )
            except Exception as error:
                errors.append(
                    {
                        "dimension_name": dimension_name,
                        "hierarchy_name": hierarchy_name,
                        "stage": "GET_ELEMENT_ATTRIBUTES",
                        "error": f"{type(error).__name__}: {error}",
                    }
                )

        if progress:
            elapsed = perf_counter() - started_clock
            print(
                f"dimension={index}/{len(dimension_names)} "
                f"name={dimension_name} "
                f"hierarchies={len(hierarchy_names)} "
                f"attributes={len(records_by_key)} "
                f"errors={len(errors)} "
                f"elapsed={elapsed:.1f}s"
            )

    records = sorted(
        records_by_key.values(),
        key=lambda item: (
            item["dimension_name"].casefold(),
            item["hierarchy_name"].casefold(),
            item["attribute_name"].casefold(),
        ),
    )
    completed = utc_now()
    total_seconds = perf_counter() - started_clock
    request_durations = [
        float(item["attribute_request_seconds"])
        for item in hierarchy_metrics
    ]
    type_counts = Counter(record["attribute_data_type"] for record in records)

    status = "PARTIAL" if errors else "COMPLETE"
    metrics = {
        "dimension_source": dimension_source,
        "dimension_count": len(dimension_names),
        "hierarchy_count": attempted_hierarchies,
        "completed_hierarchy_count": completed_hierarchies,
        "attribute_count": len(records),
        "attribute_type_counts": dict(sorted(type_counts.items())),
        "total_seconds": round(total_seconds, 6),
        "mean_seconds_per_dimension": round(
            total_seconds / len(dimension_names), 6
        ) if dimension_names else 0.0,
        "mean_attribute_request_seconds": round(
            statistics.mean(request_durations), 6
        ) if request_durations else 0.0,
        "median_attribute_request_seconds": round(
            statistics.median(request_durations), 6
        ) if request_durations else 0.0,
        "p95_attribute_request_seconds": round(
            percentile(request_durations, 0.95), 6
        ),
        "maximum_attribute_request_seconds": round(
            max(request_durations), 6
        ) if request_durations else 0.0,
        "slow_request_count": sum(
            bool(item["slow_request"]) for item in hierarchy_metrics
        ),
        "hierarchies": hierarchy_metrics,
    }
    manifest = {
        "snapshot_id": run_snapshot_id,
        "status": status,
        "scope": scope,
        "started_at": collected_at,
        "completed_at": completed.isoformat(),
        "dimension_count": len(dimension_names),
        "hierarchy_count": attempted_hierarchies,
        "attribute_count": len(records),
        "error_count": len(errors),
        "publish_current_requested": publish_current,
        "published_current": bool(status == "COMPLETE" and publish_current),
    }

    write_json(snapshot_dir / "attributes.json", records)
    write_json(snapshot_dir / "attribute_manifest.json", manifest)
    write_json(snapshot_dir / "attribute_collection_metrics.json", metrics)
    write_json(snapshot_dir / "attribute_collection_errors.json", errors)

    if status == "COMPLETE" and publish_current:
        write_json(current_root / "attributes.json", records)
        write_json(current_root / "attribute_manifest.json", manifest)
        write_json(current_root / "attribute_collection_metrics.json", metrics)
        write_json(current_root / "attribute_collection_errors.json", errors)

    return manifest


def print_manifest(manifest: dict[str, Any]) -> None:
    print("=" * 70)
    print("TM1 ATTRIBUTE DEFINITION INVENTORY")
    print("=" * 70)
    print(f"Snapshot    : {manifest.get('snapshot_id', 'UNKNOWN')}")
    print(f"Status      : {manifest.get('status', 'UNKNOWN')}")
    print(f"Scope       : {manifest.get('scope', 'UNKNOWN')}")
    print(f"Dimensions  : {int(manifest.get('dimension_count', 0) or 0):,}")
    print(f"Hierarchies : {int(manifest.get('hierarchy_count', 0) or 0):,}")
    print(f"Attributes  : {int(manifest.get('attribute_count', 0) or 0):,}")
    print(f"Errors      : {int(manifest.get('error_count', 0) or 0):,}")
    print(f"Published   : {bool(manifest.get('published_current', False))}")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect TM1 element-attribute definitions efficiently."
    )
    parser.add_argument(
        "--scope",
        choices=VALID_SCOPES,
        default="regular",
        help="Dimension scope when --dimension is omitted (default: regular).",
    )
    parser.add_argument(
        "--dimension",
        action="append",
        dest="dimensions",
        help="Collect one dimension. Repeat to collect multiple dimensions.",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="Write a snapshot but do not replace data/current attribute files.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-dimension progress output.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        with get_tm1_connection() as tm1:
            manifest = collect_attributes(
                tm1,
                scope=args.scope,
                dimensions=args.dimensions,
                publish_current=not args.no_publish,
                progress=not args.quiet,
            )
        print_manifest(manifest)
        return 0 if manifest.get("status") == "COMPLETE" else 1
    except Exception as error:
        print(
            "Attribute collection failed: "
            f"{type(error).__name__}: {error}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
