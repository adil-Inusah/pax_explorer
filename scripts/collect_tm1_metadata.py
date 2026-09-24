from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utilities.tm1_connection import get_tm1_connection

CURRENT_ROOT = ROOT_DIR / "data" / "current"
LATEST_ROOT = CURRENT_ROOT
SNAPSHOT_ROOT = ROOT_DIR / "data" / "snapshots"

OBJECT_SERVICES: tuple[tuple[str, str], ...] = (
    ("cube", "cubes"),
    ("dimension", "dimensions"),
    ("process", "processes"),
    ("chore", "chores"),
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_snapshot_id(timestamp: datetime) -> str:
    return timestamp.strftime("%Y%m%dT%H%M%SZ")


def normalize_name(value: Any) -> str:
    return str(value or "").strip()


def normalized_key(value: Any) -> str:
    return " ".join(normalize_name(value).casefold().split())


def is_control_object(object_name: Any) -> bool:
    return normalize_name(object_name).startswith("}")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write("\n")
    temporary_path.replace(path)


def sorted_unique_names(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, dict):
        iterable = values.keys()
    elif isinstance(values, (str, bytes, bytearray)):
        raise TypeError("Object-name collection must not be a string.")
    else:
        try:
            iterable = iter(values)
        except TypeError as error:
            raise TypeError(
                "Object-name collection is not iterable. "
                f"Received: {type(values).__name__}"
            ) from error

    by_key: dict[str, str] = {}
    for value in iterable:
        name = normalize_name(value)
        if name:
            by_key.setdefault(normalized_key(name), name)
    return sorted(by_key.values(), key=str.casefold)


def build_object_record(
    *,
    snapshot_id: str,
    collected_at: str,
    object_type: str,
    object_name: str,
) -> dict[str, Any]:
    control = is_control_object(object_name)
    return {
        "snapshot_id": snapshot_id,
        "object_type": object_type,
        "object_name": object_name,
        "is_control": control,
        "catalog_scope": "CONTROL" if control else "REGULAR",
        "collected_at": collected_at,
    }


def collect_names(
    tm1: Any,
    object_type: str,
    retrieval_function: Callable[[Any], Any],
    snapshot_id: str,
    collected_at: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collect one object family while preserving the legacy result contract."""
    started_at = perf_counter()
    try:
        names = sorted_unique_names(retrieval_function(tm1))
        object_records = [
            build_object_record(
                snapshot_id=snapshot_id,
                collected_at=collected_at,
                object_type=object_type,
                object_name=name,
            )
            for name in names
        ]
        count = len(object_records)
        return object_records, {
            "object_type": object_type,
            "status": "PASS",
            "record_count": count,
            "count": count,
            "duration_seconds": round(perf_counter() - started_at, 6),
            "error": None,
        }
    except Exception as error:
        return [], {
            "object_type": object_type,
            "status": "FAIL",
            "record_count": 0,
            "count": 0,
            "duration_seconds": round(perf_counter() - started_at, 6),
            "error": f"{type(error).__name__}: {error}",
        }


def _scalar_version(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def get_tm1_version(tm1: Any) -> str:
    """Return a real scalar server version and ignore unconfigured mocks."""
    for property_name in ("version", "tm1_version", "product_version"):
        version = _scalar_version(getattr(tm1, property_name, None))
        if version:
            return version

    server = getattr(tm1, "server", None)
    rest = getattr(tm1, "rest", None)
    for owner in (server, rest):
        if owner is None:
            continue
        for property_name in ("version", "tm1_version", "product_version"):
            version = _scalar_version(getattr(owner, property_name, None))
            if version:
                return version

    for owner, method_names in (
        (server, ("get_product_version", "get_version")),
        (rest, ("get_server_version", "get_version")),
    ):
        if owner is None:
            continue
        for method_name in method_names:
            method = getattr(owner, method_name, None)
            if not callable(method):
                continue
            try:
                version = _scalar_version(method())
            except Exception:
                continue
            if version:
                return version
    return "UNKNOWN"


def object_sort_key(record: dict[str, Any]) -> tuple[str, str]:
    return (
        normalize_name(record.get("object_type")).casefold(),
        normalize_name(record.get("object_name")).casefold(),
    )


def count_by_type(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(
        normalize_name(record.get("object_type"))
        for record in records
        if normalize_name(record.get("object_type"))
    )
    return dict(sorted(counts.items()))


def validate_object_split(
    all_objects: list[dict[str, Any]],
    regular_objects: list[dict[str, Any]],
    control_objects: list[dict[str, Any]],
) -> None:
    if len(all_objects) != len(regular_objects) + len(control_objects):
        raise ValueError(
            "Object split does not reconcile: "
            f"all={len(all_objects)}, regular={len(regular_objects)}, "
            f"control={len(control_objects)}"
        )

    invalid_regular = [
        record
        for record in regular_objects
        if is_control_object(record.get("object_name"))
        or record.get("is_control") is not False
        or record.get("catalog_scope") != "REGULAR"
    ]
    if invalid_regular:
        raise ValueError(
            "Regular object catalog contains records classified as control objects."
        )

    invalid_control = [
        record
        for record in control_objects
        if not is_control_object(record.get("object_name"))
        or record.get("is_control") is not True
        or record.get("catalog_scope") != "CONTROL"
    ]
    if invalid_control:
        raise ValueError(
            "Control object catalog contains records without the } prefix."
        )

    def record_key(record: dict[str, Any]) -> tuple[str, str]:
        return (
            normalized_key(record.get("object_type")),
            normalized_key(record.get("object_name")),
        )

    all_keys = {record_key(record) for record in all_objects}
    regular_keys = {record_key(record) for record in regular_objects}
    control_keys = {record_key(record) for record in control_objects}
    if regular_keys & control_keys:
        raise ValueError("Regular and control object catalogs overlap.")
    if all_keys != regular_keys | control_keys:
        raise ValueError(
            "Regular and control object keys do not reconcile to objects.json."
        )
    if len(all_keys) != len(all_objects):
        raise ValueError("Duplicate object type/name keys exist in the catalog.")


def write_catalog_files(
    root: Path,
    *,
    objects: list[dict[str, Any]],
    regular_objects: list[dict[str, Any]],
    control_objects: list[dict[str, Any]],
    manifest: dict[str, Any],
    errors: list[dict[str, Any]],
) -> None:
    write_json(root / "objects.json", objects)
    write_json(root / "regular_objects.json", regular_objects)
    write_json(root / "control_objects.json", control_objects)
    write_json(root / "manifest.json", manifest)
    write_json(root / "metadata_errors.json", errors)


def collect_tm1_metadata(
    tm1: Any | None = None,
    *,
    current_root: Path | None = None,
    snapshot_root: Path | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    publication_root = current_root if current_root is not None else LATEST_ROOT
    historical_root = snapshot_root if snapshot_root is not None else SNAPSHOT_ROOT
    started_at = timestamp or utc_now()
    run_snapshot_id = build_snapshot_id(started_at)
    collected_at = started_at.isoformat()
    snapshot_directory = (
        historical_root
        / run_snapshot_id
    )

    objects: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    collection_results: dict[str, dict[str, Any]] = {}

    def collect(connection: Any) -> str:
        tm1_version = get_tm1_version(connection)
        for object_type, service_name in OBJECT_SERVICES:
            object_records, result = collect_names(
                tm1=connection,
                object_type=object_type,
                retrieval_function=(
                    lambda current_tm1, current_service=service_name: getattr(
                        current_tm1, current_service
                    ).get_all_names()
                ),
                snapshot_id=run_snapshot_id,
                collected_at=collected_at,
            )
            objects.extend(object_records)
            collection_results[object_type] = dict(result)
            if result["status"] == "FAIL":
                errors.append(
                    {
                        "object_type": object_type,
                        "service_name": service_name,
                        "error": result["error"],
                    }
                )
        return tm1_version

    if tm1 is None:
        with get_tm1_connection() as connection:
            tm1_version = collect(connection)
    else:
        tm1_version = collect(tm1)

    objects = sorted(objects, key=object_sort_key)
    regular_objects = [record for record in objects if not record["is_control"]]
    control_objects = [record for record in objects if record["is_control"]]
    validate_object_split(objects, regular_objects, control_objects)

    object_counts = count_by_type(objects)
    regular_object_counts = count_by_type(regular_objects)
    control_object_counts = count_by_type(control_objects)
    collections = [
        dict(
            collection_results.get(
                object_type,
                {
                    "object_type": object_type,
                    "status": "MISSING",
                    "record_count": 0,
                    "count": 0,
                    "duration_seconds": 0.0,
                    "error": None,
                },
            )
        )
        for object_type, _ in OBJECT_SERVICES
    ]

    completed_at = utc_now()
    status = "PARTIAL" if errors else "COMPLETE"
    manifest: dict[str, Any] = {
        "snapshot_id": run_snapshot_id,
        "status": status,
        "started_at": collected_at,
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round(
            (completed_at - started_at).total_seconds(), 6
        ),
        "tm1_version": tm1_version,
        "object_count": len(objects),
        "regular_object_count": len(regular_objects),
        "control_object_count": len(control_objects),
        "object_counts": object_counts,
        "regular_object_counts": regular_object_counts,
        "control_object_counts": control_object_counts,
        "collections": collections,
        "collection_results": collection_results,
        "error_count": len(errors),
        "errors": errors,
    }

    write_catalog_files(
        snapshot_directory,
        objects=objects,
        regular_objects=regular_objects,
        control_objects=control_objects,
        manifest=manifest,
        errors=errors,
    )
    if status == "COMPLETE":
        write_catalog_files(
            publication_root,
            objects=objects,
            regular_objects=regular_objects,
            control_objects=control_objects,
            manifest=manifest,
            errors=errors,
        )
    return manifest


def print_manifest(manifest: dict[str, Any]) -> None:
    object_count = int(manifest.get("object_count", 0) or 0)
    regular_object_count = int(
        manifest.get("regular_object_count", object_count) or 0
    )
    control_object_count = int(
        manifest.get(
            "control_object_count",
            max(object_count - regular_object_count, 0),
        )
        or 0
    )
    collection_results = manifest.get("collection_results") or {}
    legacy_collections = manifest.get("collections") or []
    if not collection_results and isinstance(legacy_collections, list):
        collection_results = {
            str(item.get("object_type", "")): item
            for item in legacy_collections
            if isinstance(item, dict)
        }

    print()
    print("=" * 70)
    print("TM1 METADATA COLLECTION")
    print("=" * 70)
    print(f"Snapshot ID     : {manifest.get('snapshot_id', 'UNKNOWN')}")
    print(f"TM1 Version     : {manifest.get('tm1_version', 'UNKNOWN')}")
    print(f"Status          : {manifest.get('status', 'UNKNOWN')}")
    print(f"Objects         : {object_count:,}")
    print(f"Regular objects : {regular_object_count:,}")
    print(f"Control objects : {control_object_count:,}")
    print()
    print("Collections:")
    for object_type, _ in OBJECT_SERVICES:
        result = collection_results.get(
            object_type,
            {
                "status": "MISSING",
                "record_count": 0,
                "count": 0,
                "duration_seconds": 0.0,
            },
        )
        count = result.get("record_count", result.get("count", 0))
        print(
            f"  {object_type:<12} "
            f"{str(result.get('status', 'MISSING')):<8} "
            f"{int(count or 0):>8,} "
            f"{float(result.get('duration_seconds', 0.0) or 0.0):>8.3f}s"
        )

    regular_counts = manifest.get("regular_object_counts") or {}
    control_counts = manifest.get("control_object_counts") or {}
    if regular_counts:
        print()
        print("Regular objects by type:")
        for object_type, count in sorted(regular_counts.items()):
            print(f"  {object_type:<12} {int(count):>8,}")
    if control_counts:
        print()
        print("Control objects by type:")
        for object_type, count in sorted(control_counts.items()):
            print(f"  {object_type:<12} {int(count):>8,}")
    print(f"Errors          : {int(manifest.get('error_count', 0) or 0):,}")


def main() -> int:
    try:
        manifest = collect_tm1_metadata()
        print_manifest(manifest)
        return 0 if manifest.get("status") == "COMPLETE" else 1
    except Exception as error:
        print(
            "TM1 metadata collection failed: "
            f"{type(error).__name__}: {error}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
