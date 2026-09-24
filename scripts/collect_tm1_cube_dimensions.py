from __future__ import annotations

"""Collect ordered TM1 cube-to-dimension structural relationships.

Each relationship preserves the presentation dimension position returned by the
TM1 cube definition. The collector does not read cube cells, rules, or views.
"""

import argparse
import json
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

CURRENT_ROOT = ROOT_DIR / "data" / "current"
SNAPSHOT_ROOT = ROOT_DIR / "data" / "snapshots"
OBJECTS_FILE = CURRENT_ROOT / "objects.json"
VALID_SCOPES = ("regular", "control", "all")


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


def sorted_unique_names(values: Iterable[Any]) -> list[str]:
    by_key: dict[str, str] = {}
    for value in values:
        name = clean(getattr(value, "name", value))
        if name:
            by_key.setdefault(normalized_key(name), name)
    return sorted(by_key.values(), key=str.casefold)


def object_catalog(
    payload: Any,
) -> tuple[list[dict[str, Any]], set[str]]:
    if not isinstance(payload, list):
        raise TypeError("objects.json must contain a JSON array.")
    cubes: list[dict[str, Any]] = []
    dimensions: set[str] = set()
    for record in payload:
        if not isinstance(record, Mapping):
            continue
        object_type = normalized_key(record.get("object_type"))
        object_name = clean(record.get("object_name"))
        if not object_name:
            continue
        if object_type == "cube":
            cubes.append(dict(record))
        elif object_type == "dimension":
            dimensions.add(normalized_key(object_name))
    cubes.sort(key=lambda item: clean(item.get("object_name")).casefold())
    return cubes, dimensions


def resolve_cube_scope(
    tm1: Any,
    *,
    scope: str,
    cubes: list[str] | None,
    object_catalog_path: Path | None,
) -> tuple[list[str], set[str], str]:
    if scope not in VALID_SCOPES:
        raise ValueError(f"Unsupported scope: {scope}")

    if object_catalog_path is not None and object_catalog_path.is_file():
        catalog_cubes, dimension_keys = object_catalog(read_json(object_catalog_path))
        if cubes:
            requested = {normalized_key(name) for name in cubes}
            names = [
                clean(record.get("object_name"))
                for record in catalog_cubes
                if normalized_key(record.get("object_name")) in requested
            ]
            missing = requested - {normalized_key(name) for name in names}
            if missing:
                raise ValueError(
                    "Requested cubes are absent from objects.json: "
                    + ", ".join(sorted(missing))
                )
            return names, dimension_keys, "EXPLICIT_CUBES_FROM_OBJECT_CATALOG"

        names = []
        for record in catalog_cubes:
            name = clean(record.get("object_name"))
            control = bool(record.get("is_control", is_control_name(name)))
            if scope == "regular" and control:
                continue
            if scope == "control" and not control:
                continue
            names.append(name)
        return names, dimension_keys, "OBJECT_CATALOG"

    service = getattr(tm1, "cubes", None)
    get_names = getattr(service, "get_all_names", None)
    if not callable(get_names):
        raise AttributeError("TM1 connection does not expose cubes.get_all_names().")
    names = sorted_unique_names(
        iterable_items(get_names(), description="TM1 cube-name collection")
    )
    if cubes:
        requested = {normalized_key(name) for name in cubes}
        names = [name for name in names if normalized_key(name) in requested]
    elif scope == "regular":
        names = [name for name in names if not is_control_name(name)]
    elif scope == "control":
        names = [name for name in names if is_control_name(name)]
    return names, set(), "TM1_CUBE_SERVICE"


def cube_dimensions(cube: Any) -> list[str]:
    raw = None
    if isinstance(cube, Mapping):
        raw = cube.get("Dimensions") or cube.get("dimensions")
    if raw is None:
        raw = getattr(cube, "dimensions", None)
    if raw is None:
        raw = getattr(cube, "Dimensions", None)
    values = iterable_items(raw, description="Cube dimension collection")
    result: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            name = clean(item.get("Name") or item.get("name"))
        else:
            name = clean(getattr(item, "name", item))
        if name:
            result.append(name)
    return result


def get_cube(tm1: Any, cube_name: str) -> Any:
    service = getattr(tm1, "cubes", None)
    get_one = getattr(service, "get", None)
    if not callable(get_one):
        raise AttributeError("TM1 connection does not expose cubes.get().")
    return get_one(cube_name)


def cube_node_id(name: str) -> str:
    return f"cube::{name}"


def dimension_node_id(name: str) -> str:
    return f"dimension::{name}"


def collect_cube_dimensions(
    tm1: Any,
    *,
    scope: str = "all",
    cubes: list[str] | None = None,
    object_catalog_path: Path | None = None,
    snapshot_root: Path = SNAPSHOT_ROOT,
    current_root: Path = CURRENT_ROOT,
    timestamp: datetime | None = None,
    publish_current: bool = True,
    progress: bool = False,
) -> dict[str, Any]:
    started = timestamp or utc_now()
    started_clock = perf_counter()
    run_id = build_snapshot_id(started)
    snapshot_dir = snapshot_root / run_id

    cube_names, dimension_keys, cube_source = resolve_cube_scope(
        tm1,
        scope=scope,
        cubes=cubes,
        object_catalog_path=object_catalog_path,
    )

    relationships: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    cube_metrics: list[dict[str, Any]] = []

    for cube_index, cube_name in enumerate(cube_names, start=1):
        request_started = perf_counter()
        try:
            dimensions = cube_dimensions(get_cube(tm1, cube_name))
            request_seconds = perf_counter() - request_started
            seen_positions: set[int] = set()
            seen_dimensions: set[str] = set()

            for position, dimension_name in enumerate(dimensions, start=1):
                dimension_key = normalized_key(dimension_name)
                if position in seen_positions:
                    raise ValueError(
                        f"Duplicate dimension position {position} in cube {cube_name}"
                    )
                if dimension_key in seen_dimensions:
                    raise ValueError(
                        f"Duplicate dimension {dimension_name!r} in cube {cube_name}"
                    )
                seen_positions.add(position)
                seen_dimensions.add(dimension_key)

                relationship_id = (
                    f"cube-dimension::{cube_name}::{position:04d}::{dimension_name}"
                )
                source_id = cube_node_id(cube_name)
                target_id = dimension_node_id(dimension_name)
                relationships.append(
                    {
                        "snapshot_id": run_id,
                        "relationship_id": relationship_id,
                        "source_id": source_id,
                        "source_type": "CUBE",
                        "source_name": cube_name,
                        "target_id": target_id,
                        "target_type": "DIMENSION",
                        "target_name": dimension_name,
                        "relationship_type": "USES_DIMENSION",
                        "relationship_origin": "METADATA",
                        "resolution_method": "CUBE_DEFINITION",
                        "dimension_position": position,
                        "is_control": (
                            is_control_name(cube_name)
                            or is_control_name(dimension_name)
                        ),
                    }
                )
                target_valid = (
                    not dimension_keys or dimension_key in dimension_keys
                )
                validations.append(
                    {
                        "snapshot_id": run_id,
                        "validation_id": f"validation::{relationship_id}",
                        "relationship_id": relationship_id,
                        "validation_status": (
                            "VALID"
                            if target_valid
                            else "DIMENSION_NOT_IN_CATALOG"
                        ),
                        "source_id": source_id,
                        "target_id": target_id,
                    }
                )

            cube_metrics.append(
                {
                    "cube_name": cube_name,
                    "dimension_count": len(dimensions),
                    "request_seconds": round(request_seconds, 6),
                }
            )
        except Exception as error:
            errors.append(
                {
                    "cube_name": cube_name,
                    "stage": "GET_CUBE_DIMENSIONS",
                    "error": f"{type(error).__name__}: {error}",
                }
            )

        if progress:
            print(
                f"cube={cube_index}/{len(cube_names)} name={cube_name} "
                f"relationships={len(relationships)} errors={len(errors)} "
                f"elapsed={perf_counter() - started_clock:.1f}s"
            )

    relationships.sort(key=lambda item: item["relationship_id"].casefold())
    validations.sort(key=lambda item: item["relationship_id"].casefold())
    status = "PARTIAL" if errors else "COMPLETE"
    manifest = {
        "snapshot_id": run_id,
        "status": status,
        "scope": scope,
        "started_at": started.isoformat(),
        "completed_at": utc_now().isoformat(),
        "cube_source": cube_source,
        "cube_count": len(cube_names),
        "completed_cube_count": len(cube_metrics),
        "relationship_count": len(relationships),
        "validation_count": len(validations),
        "error_count": len(errors),
        "total_seconds": round(perf_counter() - started_clock, 6),
        "publish_current_requested": publish_current,
        "published_current": bool(status == "COMPLETE" and publish_current),
    }
    metrics = {
        "cube_source": cube_source,
        "cube_count": len(cube_names),
        "completed_cube_count": len(cube_metrics),
        "relationship_count": len(relationships),
        "total_seconds": manifest["total_seconds"],
        "cubes": cube_metrics,
    }
    outputs = {
        "cube_dimension_relationships.json": relationships,
        "cube_dimension_relationship_validations.json": validations,
        "cube_dimension_manifest.json": manifest,
        "cube_dimension_collection_metrics.json": metrics,
        "cube_dimension_collection_errors.json": errors,
    }
    for file_name, payload in outputs.items():
        write_json(snapshot_dir / file_name, payload)
    if status == "COMPLETE" and publish_current:
        for file_name, payload in outputs.items():
            write_json(current_root / file_name, payload)
    return manifest


def print_manifest(manifest: Mapping[str, Any]) -> None:
    print("=" * 70)
    print("TM1 CUBE-DIMENSION RELATIONSHIPS")
    print("=" * 70)
    print(f"Snapshot      : {manifest.get('snapshot_id', 'UNKNOWN')}")
    print(f"Status        : {manifest.get('status', 'UNKNOWN')}")
    print(f"Scope         : {manifest.get('scope', 'UNKNOWN')}")
    print(f"Cubes         : {int(manifest.get('cube_count', 0) or 0):,}")
    print(f"Completed     : {int(manifest.get('completed_cube_count', 0) or 0):,}")
    print(f"Relationships : {int(manifest.get('relationship_count', 0) or 0):,}")
    print(f"Validations   : {int(manifest.get('validation_count', 0) or 0):,}")
    print(f"Errors        : {int(manifest.get('error_count', 0) or 0):,}")
    print(f"Published     : {bool(manifest.get('published_current', False))}")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect ordered TM1 cube-to-dimension relationships."
    )
    parser.add_argument("--scope", choices=VALID_SCOPES, default="all")
    parser.add_argument("--cube", action="append", dest="cubes")
    parser.add_argument("--object-catalog", type=Path, default=OBJECTS_FILE)
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        with get_tm1_connection() as tm1:
            manifest = collect_cube_dimensions(
                tm1,
                scope=args.scope,
                cubes=args.cubes,
                object_catalog_path=args.object_catalog,
                publish_current=not args.no_publish,
                progress=not args.quiet,
            )
        print_manifest(manifest)
        return 0 if manifest.get("status") == "COMPLETE" else 1
    except Exception as error:
        print(
            "Cube-dimension collection failed: "
            f"{type(error).__name__}: {error}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
