from __future__ import annotations

import argparse
import json
import sys

from collections.abc import (
    Iterable as IterableABC,
    Mapping,
)
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

"""Deferred full attribute collector.

The full scan is not part of the production pipeline because hierarchy
retrieval averages approximately 4.8 seconds per sampled dimension.
Use tools/explore_tm1_dimension.py for on-demand inspection.
"""

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utilities.tm1_connection import get_tm1_connection

SNAPSHOT_ROOT = ROOT_DIR / "data" / "snapshots"
CURRENT_ROOT = ROOT_DIR / "data" / "current"


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

def read_json(
    path: Path,
) -> Any:
    with Path(path).open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)



def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
    temporary.replace(path)


def sorted_unique_names(values: Iterable[Any]) -> list[str]:
    by_key: dict[str, str] = {}
    for value in values:
        name = normalize_name(value)
        if name:
            by_key.setdefault(normalized_key(name), name)
    return sorted(by_key.values(), key=str.casefold)

def iterable_items(
    value: Any,
    *,
    description: str,
) -> list[Any]:
    """Convert an unknown collection into a concrete list safely."""
    if value is None:
        return []

    if isinstance(value, Mapping):
        return list(value.values())

    if isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise TypeError(
            f"{description} must be a collection, "
            "not a string or byte sequence."
        )

    if not isinstance(
        value,
        IterableABC,
    ):
        raise TypeError(
            f"{description} is not iterable. "
            f"Received: {type(value).__name__}"
        )

    return list(value)


def get_dimension_names(
    tm1: Any,
) -> list[str]:
    dimension_service = getattr(
        tm1,
        "dimensions",
        None,
    )

    if dimension_service is None:
        raise AttributeError(
            "The TM1 connection does not expose "
            "a dimensions service."
        )

    get_all_names = getattr(
        dimension_service,
        "get_all_names",
        None,
    )

    if not callable(get_all_names):
        raise AttributeError(
            "The TM1 dimensions service does not "
            "support get_all_names()."
        )

    names = get_all_names()

    return sorted_unique_names(
        iterable_items(
            names,
            description=(
                "TM1 dimension-name collection"
            ),
        )
    )


def get_hierarchy_names(
    tm1: Any,
    dimension_name: str,
) -> list[str]:
    direct_service = getattr(
        tm1,
        "hierarchies",
        None,
    )

    direct_get_all_names = getattr(
        direct_service,
        "get_all_names",
        None,
    )

    if callable(direct_get_all_names):
        names = direct_get_all_names(
            dimension_name
        )

        return sorted_unique_names(
            iterable_items(
                names,
                description=(
                    "TM1 hierarchy-name collection "
                    f"for dimension {dimension_name}"
                ),
            )
        )

    dimension_service = getattr(
        tm1,
        "dimensions",
        None,
    )

    if dimension_service is None:
        raise AttributeError(
            "The TM1 connection does not expose "
            "a dimensions service."
        )

    nested_hierarchy_service = getattr(
        dimension_service,
        "hierarchies",
        None,
    )

    nested_get_all_names = getattr(
        nested_hierarchy_service,
        "get_all_names",
        None,
    )

    if callable(nested_get_all_names):
        names = nested_get_all_names(
            dimension_name
        )

        return sorted_unique_names(
            iterable_items(
                names,
                description=(
                    "TM1 hierarchy-name collection "
                    f"for dimension {dimension_name}"
                ),
            )
        )

    get_dimension = getattr(
        dimension_service,
        "get",
        None,
    )

    if not callable(get_dimension):
        raise AttributeError(
            "The TM1 dimensions service does not "
            "support dimension retrieval."
        )

    dimension = get_dimension(
        dimension_name
    )

    hierarchy_collection = getattr(
        dimension,
        "hierarchies",
        None,
    )

    if isinstance(
        hierarchy_collection,
        Mapping,
    ):
        return sorted_unique_names(
            hierarchy_collection.keys()
        )

    collection_to_iterate = (
        hierarchy_collection
        if hierarchy_collection is not None
        else dimension
    )

    hierarchy_candidates = iterable_items(
        collection_to_iterate,
        description=(
            "Hierarchy collection for dimension "
            f"{dimension_name}"
        ),
    )

    return sorted_unique_names(
        getattr(
            hierarchy,
            "name",
            hierarchy,
        )
        for hierarchy in hierarchy_candidates
    )    


def get_hierarchy(
    tm1: Any,
    dimension_name: str,
    hierarchy_name: str,
) -> Any:
    direct_service = getattr(
        tm1,
        "hierarchies",
        None,
    )

    direct_get = getattr(
        direct_service,
        "get",
        None,
    )

    if callable(direct_get):
        return direct_get(
            dimension_name,
            hierarchy_name,
        )

    dimension_service = getattr(
        tm1,
        "dimensions",
        None,
    )

    if dimension_service is None:
        raise AttributeError(
            "The TM1 connection does not expose "
            "a dimensions service."
        )

    nested_hierarchy_service = getattr(
        dimension_service,
        "hierarchies",
        None,
    )

    nested_get = getattr(
        nested_hierarchy_service,
        "get",
        None,
    )

    if callable(nested_get):
        return nested_get(
            dimension_name,
            hierarchy_name,
        )

    get_dimension = getattr(
        dimension_service,
        "get",
        None,
    )

    if not callable(get_dimension):
        raise AttributeError(
            "The TM1 dimensions service does not "
            "support dimension retrieval."
        )

    dimension = get_dimension(
        dimension_name
    )

    hierarchy_collection = getattr(
        dimension,
        "hierarchies",
        None,
    )

    if isinstance(
        hierarchy_collection,
        Mapping,
    ):
        direct_match = (
            hierarchy_collection.get(
                hierarchy_name
            )
        )

        if direct_match is not None:
            return direct_match

        for (
            candidate_name,
            candidate,
        ) in hierarchy_collection.items():
            if normalized_key(
                candidate_name
            ) == normalized_key(
                hierarchy_name
            ):
                return candidate

        raise KeyError(
            "Hierarchy not found: "
            f"{dimension_name}::"
            f"{hierarchy_name}"
        )

    collection_to_iterate = (
        hierarchy_collection
        if hierarchy_collection is not None
        else dimension
    )

    hierarchy_candidates = iterable_items(
        collection_to_iterate,
        description=(
            "Hierarchy collection for dimension "
            f"{dimension_name}"
        ),
    )

    for hierarchy in hierarchy_candidates:
        candidate_name = normalize_name(
            getattr(
                hierarchy,
                "name",
                hierarchy,
            )
        )

        if normalized_key(
            candidate_name
        ) == normalized_key(
            hierarchy_name
        ):
            return hierarchy

    raise KeyError(
        "Hierarchy not found: "
        f"{dimension_name}::"
        f"{hierarchy_name}"
    )


def get_element_attributes(
    tm1: Any,
    dimension_name: str,
    hierarchy_name: str,
) -> list[Any]:
    hierarchy = get_hierarchy(
        tm1,
        dimension_name,
        hierarchy_name,
    )

    attributes = getattr(
        hierarchy,
        "element_attributes",
        None,
    )

    if attributes is None:
        attributes = getattr(
            hierarchy,
            "attributes",
            None,
        )

    if attributes is None:
        return []

    if isinstance(attributes, Mapping):
        return list(attributes.values())

    return iterable_items(
        attributes,
        description=(
            "Element-attribute collection for "
            f"{dimension_name}::"
            f"{hierarchy_name}"
        ),
    )

def attribute_name(
    attribute: Any,
) -> str:
    if isinstance(attribute, str):
        return normalize_name(attribute)

    if isinstance(attribute, Mapping):
        return normalize_name(
            attribute.get("Name")
            or attribute.get("name")
        )

    return normalize_name(
        getattr(attribute, "name", None)
        or getattr(attribute, "Name", None)
    )


def raw_attribute_type(
    attribute: Any,
) -> Any:
    if isinstance(attribute, Mapping):
        return attribute.get(
            "Type",
            attribute.get("type"),
        )

    return (
        getattr(
            attribute,
            "attribute_type",
            None,
        )
        or getattr(
            attribute,
            "type",
            None,
        )
        or getattr(
            attribute,
            "Type",
            None,
        )
    )


def normalize_attribute_type(
    value: Any,
) -> tuple[str, str, bool]:
    enum_name = normalize_name(
        getattr(value, "name", "")
    )

    enum_value = getattr(
        value,
        "value",
        value,
    )

    raw_value = normalize_name(
        enum_value
    )

    token = (
        enum_name
        or raw_value
    ).upper()

    if token in {
        "1",
        "N",
        "NUMERIC",
        "NUMBER",
    }:
        return (
            raw_value or "N",
            "NUMERIC",
            False,
        )

    if token in {
        "3",
        "A",
        "ALIAS",
    }:
        return (
            raw_value or "A",
            "ALIAS",
            True,
        )

    if token in {
        "2",
        "S",
        "STRING",
        "TEXT",
        "",
    }:
        return (
            raw_value or "S",
            "STRING",
            False,
        )

    return (
        raw_value or token,
        "UNKNOWN",
        False,
    )


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
    object_name = f"{dimension_name}::{hierarchy_name}::{name}"
    return {
        "snapshot_id": snapshot_id,
        "collected_at": collected_at,
        "object_type": "attribute",
        "object_name": object_name,
        "dimension_name": dimension_name,
        "hierarchy_name": hierarchy_name,
        "attribute_name": name,
        "tm1_attribute_type": tm1_type,
        "attribute_data_type": data_type,
        "is_alias": is_alias,
        "is_default_hierarchy": normalized_key(dimension_name)
        == normalized_key(hierarchy_name),
        "is_control": is_control_name(dimension_name)
        or is_control_name(hierarchy_name)
        or is_control_name(name),
    }


def collect_attributes(
    tm1: Any,
    *,
    snapshot_root: Path = SNAPSHOT_ROOT,
    current_root: Path = CURRENT_ROOT,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    started = timestamp or utc_now()
    snapshot_id = build_snapshot_id(started)
    collected_at = started.isoformat()
    snapshot_dir = snapshot_root / snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    current_root.mkdir(parents=True, exist_ok=True)

    records_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    dimension_count = 0
    hierarchy_count = 0

    try:
        dimensions = get_dimension_names(tm1)
        dimension_count = len(dimensions)
        for dimension_name in dimensions:
            try:
                hierarchies = get_hierarchy_names(tm1, dimension_name)
            except Exception as error:
                errors.append({
                    "dimension_name": dimension_name,
                    "hierarchy_name": None,
                    "error": f"{type(error).__name__}: {error}",
                })
                continue

            for hierarchy_name in hierarchies:
                hierarchy_count += 1
                try:
                    attributes = get_element_attributes(
                        tm1, dimension_name, hierarchy_name
                    )
                    for attribute in attributes:
                        record = build_attribute_record(
                            snapshot_id=snapshot_id,
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
                except Exception as error:
                    errors.append({
                        "dimension_name": dimension_name,
                        "hierarchy_name": hierarchy_name,
                        "error": f"{type(error).__name__}: {error}",
                    })

        records = sorted(
            records_by_key.values(),
            key=lambda item: (
                item["dimension_name"].casefold(),
                item["hierarchy_name"].casefold(),
                item["attribute_name"].casefold(),
            ),
        )
        status = "PARTIAL" if errors else "COMPLETE"
        manifest = {
            "snapshot_id": snapshot_id,
            "status": status,
            "started_at": collected_at,
            "completed_at": utc_now().isoformat(),
            "dimension_count": dimension_count,
            "hierarchy_count": hierarchy_count,
            "attribute_count": len(records),
            "error_count": len(errors),
            "errors": errors,
        }

        write_json(snapshot_dir / "attributes.json", records)
        write_json(snapshot_dir / "attribute_manifest.json", manifest)

        if status == "COMPLETE":
            write_json(current_root / "attributes.json", records)
            write_json(current_root / "attribute_manifest.json", manifest)

        return manifest
    except Exception as error:
        manifest = {
            "snapshot_id": snapshot_id,
            "status": "FAILED",
            "started_at": collected_at,
            "completed_at": utc_now().isoformat(),
            "dimension_count": dimension_count,
            "hierarchy_count": hierarchy_count,
            "attribute_count": len(records_by_key),
            "error_count": len(errors) + 1,
            "errors": errors + [{
                "dimension_name": None,
                "hierarchy_name": None,
                "error": f"{type(error).__name__}: {error}",
            }],
        }
        write_json(snapshot_dir / "attribute_manifest.json", manifest)
        raise


def print_manifest(manifest: dict[str, Any]) -> None:
    print("=" * 70)
    print("TM1 ATTRIBUTE DEFINITION INVENTORY")
    print("=" * 70)
    print(f"Snapshot    : {manifest['snapshot_id']}")
    print(f"Status      : {manifest['status']}")
    print(f"Dimensions  : {manifest['dimension_count']:,}")
    print(f"Hierarchies : {manifest['hierarchy_count']:,}")
    print(f"Attributes  : {manifest['attribute_count']:,}")
    print(f"Errors      : {manifest['error_count']:,}")

def parse_arguments(
    argv: list[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect TM1 element "
            "attribute definitions."
        )
    )

    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
) -> int:
    parse_arguments(argv)

    try:
        with get_tm1_connection() as tm1:
            manifest = collect_attributes(
                tm1
            )

        print_manifest(manifest)

        return (
            0
            if manifest["status"]
            == "COMPLETE"
            else 1
        )

    except Exception as error:
        print(
            "Attribute collection failed: "
            f"{type(error).__name__}: "
            f"{error}"
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())