from __future__ import annotations

"""Collect public TM1 native and MDX view definitions as first-class entities.

The collector performs one selective REST request per cube and retrieves public
view names only. It does not expand private views, cell data, evaluated MDX
results, or subtype-specific view definitions. Definition enrichment is a
separate follow-up stage.
"""

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter
from collections.abc import Iterable as IterableABC, Mapping
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable
from urllib.parse import quote

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utilities.tm1_connection import get_tm1_connection

SNAPSHOT_ROOT = ROOT_DIR / "data" / "snapshots"
CURRENT_ROOT = ROOT_DIR / "data" / "current"
OBJECTS_FILE = CURRENT_ROOT / "objects.json"
SLOW_REQUEST_WARNING_SECONDS = 1.0
VIEW_SELECT_FIELDS = (
    "Name",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_snapshot_id(timestamp: datetime) -> str:
    return timestamp.strftime("%Y%m%dT%H%M%SZ")


def clean(value: Any) -> str:
    return str(value or "").strip()


def normalized_key(value: Any) -> str:
    return " ".join(clean(value).casefold().split())


def is_control_name(value: Any) -> bool:
    return clean(value).startswith("}")


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
        name = clean(getattr(value, "name", value))
        if name:
            by_key.setdefault(normalized_key(name), name)
    return sorted(by_key.values(), key=str.casefold)


def public_cube_names(payload: Any) -> list[str]:
    if not isinstance(payload, list):
        raise TypeError("objects.json must contain a JSON array.")
    names: list[str] = []
    for record in payload:
        if not isinstance(record, Mapping):
            continue
        if normalized_key(record.get("object_type")) != "cube":
            continue
        name = clean(record.get("object_name"))
        if name:
            names.append(name)
    return sorted_unique_names(names)


def get_cube_names_from_tm1(tm1: Any) -> list[str]:
    service = getattr(tm1, "cubes", None)
    method = getattr(service, "get_all_names", None)
    if not callable(method):
        raise AttributeError("TM1 connection does not expose cubes.get_all_names().")
    return sorted_unique_names(
        iterable_items(method(), description="TM1 cube-name collection")
    )


def resolve_cube_scope(
    tm1: Any,
    *,
    cubes: list[str] | None,
    object_catalog_path: Path | None,
) -> tuple[list[str], str]:
    if cubes:
        return sorted_unique_names(cubes), "EXPLICIT_CUBES"
    if object_catalog_path is not None and object_catalog_path.is_file():
        return public_cube_names(read_json(object_catalog_path)), "OBJECT_CATALOG"
    return get_cube_names_from_tm1(tm1), "TM1_CUBE_SERVICE"


def get_view_service(tm1: Any) -> Any:
    service = getattr(tm1, "views", None)
    if service is not None:
        return service
    raise AttributeError("TM1 connection does not expose a view service.")


def get_rest_service(view_service: Any) -> Any | None:
    for attribute_name in ("_rest", "rest"):
        candidate = getattr(view_service, attribute_name, None)
        if callable(getattr(candidate, "GET", None)):
            return candidate
    return None


def odata_key(value: str) -> str:
    return quote(value.replace("'", "''"), safe="}$-_.~")


def response_json(response: Any) -> Any:
    method = getattr(response, "json", None)
    if callable(method):
        return method()
    if isinstance(response, Mapping):
        return response
    raise TypeError("View response does not expose JSON content.")


def selective_view_url(cube_name: str) -> str:
    cube = odata_key(cube_name)
    selected = ",".join(VIEW_SELECT_FIELDS)
    return f"/Cubes('{cube}')/Views?$select={selected}"


def selective_public_view_definitions(
    view_service: Any,
    cube_name: str,
) -> list[dict[str, Any]] | None:
    rest = get_rest_service(view_service)
    if rest is None:
        return None
    response = rest.GET(selective_view_url(cube_name))
    payload = response_json(response)
    if not isinstance(payload, Mapping):
        raise TypeError("Selective view response root must be a JSON object.")
    values = payload.get("value")
    if not isinstance(values, list):
        raise TypeError("Selective view response must contain a value array.")
    result: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise TypeError(
                f"Selective view response has a non-object at index {index}."
            )
        result.append(dict(value))
    return result


def compatibility_public_view_definitions(
    view_service: Any,
    cube_name: str,
) -> list[Any]:
    names_method = getattr(view_service, "get_all_names", None)
    get_method = getattr(view_service, "get", None)
    if not callable(names_method) or not callable(get_method):
        raise AttributeError(
            "View service supports neither selective REST retrieval nor "
            "get_all_names()/get() compatibility retrieval."
        )
    try:
        names = names_method(cube_name, private=False)
    except TypeError:
        names = names_method(cube_name)
    return [
        get_method(cube_name, name, private=False)
        for name in sorted_unique_names(
            iterable_items(names, description=f"Public views for {cube_name}")
        )
    ]


def public_view_definitions(
    view_service: Any,
    cube_name: str,
) -> tuple[list[Any], str]:
    selective = selective_public_view_definitions(view_service, cube_name)
    if selective is not None:
        return selective, "SELECTIVE_REST"
    return compatibility_public_view_definitions(view_service, cube_name), "COMPATIBILITY_FULL_GET"


def property_value(value: Any, *names: str) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    return None


def view_name_of(view: Any) -> str:
    return clean(property_value(view, "Name", "name", "view_name"))


def mdx_of(view: Any) -> str | None:
    value = clean(property_value(view, "MDX", "mdx", "expression"))
    return value or None


def view_kind_of(view: Any) -> str:
    type_name = clean(
        property_value(view, "@odata.type", "odata_type")
        or type(view).__name__
    ).upper()
    if "MDXVIEW" in type_name:
        return "MDX"
    if "NATIVEVIEW" in type_name:
        return "NATIVE"
    if mdx_of(view):
        return "MDX"
    return "UNKNOWN"


def bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    token = clean(value).casefold()
    if token in {"true", "1", "yes"}:
        return True
    if token in {"false", "0", "no"}:
        return False
    return None


def hash_text(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def view_node_id(cube_name: str, view_name: str) -> str:
    return f"view::{cube_name}::PUBLIC::<none>::{view_name}"


def cube_node_id(cube_name: str) -> str:
    return f"cube::{cube_name}"


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def collect_views(
    tm1: Any,
    *,
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
    collected_at = started.isoformat()
    snapshot_dir = snapshot_root / run_id

    cube_names, cube_source = resolve_cube_scope(
        tm1, cubes=cubes, object_catalog_path=object_catalog_path
    )
    view_service = get_view_service(tm1)

    records_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    relationships: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    cube_metrics: list[dict[str, Any]] = []

    for index, cube_name in enumerate(cube_names, start=1):
        request_started = perf_counter()
        cube_view_count = 0
        try:
            definitions, retrieval_mode = public_view_definitions(
                view_service, cube_name
            )
            request_seconds = perf_counter() - request_started
            for view in definitions:
                view_name = view_name_of(view)
                if not view_name:
                    raise ValueError(f"A public view on {cube_name} has no Name.")
                kind = view_kind_of(view)
                mdx = mdx_of(view)
                node_id = view_node_id(cube_name, view_name)
                records_by_key.setdefault(
                    (normalized_key(cube_name), normalized_key(view_name)),
                    {
                        "snapshot_id": run_id,
                        "collected_at": collected_at,
                        "node_id": node_id,
                        "node_type": "VIEW",
                        "object_type": "view",
                        "object_name": f"{cube_name}::{view_name}",
                        "qualified_name": f"{cube_name}::{view_name}",
                        "cube_name": cube_name,
                        "view_name": view_name,
                        "visibility": "PUBLIC",
                        "owner": None,
                        "view_kind": kind,
                        "mdx_expression": mdx,
                        "mdx_hash": hash_text(mdx),
                        "suppress_empty_rows": bool_value(
                            property_value(view, "SuppressEmptyRows", "suppress_empty_rows")
                        ),
                        "suppress_empty_columns": bool_value(
                            property_value(view, "SuppressEmptyColumns", "suppress_empty_columns")
                        ),
                        "format_string": clean(
                            property_value(view, "FormatString", "format_string")
                        ) or None,
                        "definition_loaded": (
                            retrieval_mode == "COMPATIBILITY_FULL_GET"
                        ),
                        "retrieval_mode": (
                            "SELECTIVE_NAME_ONLY"
                            if retrieval_mode == "SELECTIVE_REST"
                            else retrieval_mode
                        ),
                        "is_control": is_control_name(cube_name) or is_control_name(view_name),
                    },
                )
                edge_id = f"view-ownership::{cube_name}::{view_name}"
                relationships.append(
                    {
                        "snapshot_id": run_id,
                        "relationship_id": edge_id,
                        "source_id": node_id,
                        "source_type": "VIEW",
                        "target_id": cube_node_id(cube_name),
                        "target_type": "CUBE",
                        "relationship_type": "BELONGS_TO_CUBE",
                        "relationship_origin": "METADATA",
                        "resolution_method": "CATALOG_MATCH",
                    }
                )
                validations.append(
                    {
                        "snapshot_id": run_id,
                        "validation_id": f"validation::{edge_id}",
                        "relationship_id": edge_id,
                        "validation_status": "VALID",
                        "source_id": node_id,
                        "target_id": cube_node_id(cube_name),
                    }
                )
                cube_view_count += 1
            cube_metrics.append(
                {
                    "cube_name": cube_name,
                    "public_view_count": cube_view_count,
                    "request_seconds": round(request_seconds, 6),
                    "retrieval_mode": (
                        "SELECTIVE_NAME_ONLY"
                        if retrieval_mode == "SELECTIVE_REST"
                        else retrieval_mode
                    ),
                    "slow_request": request_seconds > SLOW_REQUEST_WARNING_SECONDS,
                }
            )
        except Exception as error:
            errors.append(
                {
                    "cube_name": cube_name,
                    "stage": "GET_PUBLIC_VIEW_DEFINITIONS",
                    "error": f"{type(error).__name__}: {error}",
                }
            )

        if progress:
            print(
                f"cube={index}/{len(cube_names)} name={cube_name} "
                f"public_views={cube_view_count} catalog_views={len(records_by_key)} "
                f"errors={len(errors)} elapsed={perf_counter() - started_clock:.1f}s"
            )

    records = sorted(
        records_by_key.values(),
        key=lambda item: (item["cube_name"].casefold(), item["view_name"].casefold()),
    )
    relationships.sort(key=lambda item: item["relationship_id"].casefold())
    validations.sort(key=lambda item: item["relationship_id"].casefold())
    elapsed = perf_counter() - started_clock
    durations = [float(item["request_seconds"]) for item in cube_metrics]
    kind_counts = Counter(record["view_kind"] for record in records)
    mode_counts = Counter(record["retrieval_mode"] for record in records)
    status = "PARTIAL" if errors else "COMPLETE"

    manifest = {
        "snapshot_id": run_id,
        "status": status,
        "visibility_scope": "PUBLIC",
        "started_at": collected_at,
        "completed_at": utc_now().isoformat(),
        "cube_count": len(cube_names),
        "view_count": len(records),
        "relationship_count": len(relationships),
        "validation_count": len(validations),
        "error_count": len(errors),
        "publish_current_requested": publish_current,
        "published_current": bool(status == "COMPLETE" and publish_current),
    }
    metrics = {
        "cube_source": cube_source,
        "cube_count": len(cube_names),
        "completed_cube_count": len(cube_metrics),
        "view_count": len(records),
        "view_kind_counts": dict(sorted(kind_counts.items())),
        "retrieval_mode_counts": dict(sorted(mode_counts.items())),
        "relationship_count": len(relationships),
        "validation_count": len(validations),
        "definition_request_count": len(cube_names),
        "total_seconds": round(elapsed, 6),
        "mean_cube_request_seconds": round(statistics.mean(durations), 6) if durations else 0.0,
        "median_cube_request_seconds": round(statistics.median(durations), 6) if durations else 0.0,
        "p95_cube_request_seconds": round(percentile(durations, 0.95), 6),
        "maximum_cube_request_seconds": round(max(durations), 6) if durations else 0.0,
        "slow_request_count": sum(bool(item["slow_request"]) for item in cube_metrics),
        "cubes": cube_metrics,
    }
    outputs = {
        "views.json": records,
        "view_relationships.json": relationships,
        "view_relationship_validations.json": validations,
        "view_manifest.json": manifest,
        "view_collection_metrics.json": metrics,
        "view_collection_errors.json": errors,
    }
    for file_name, payload in outputs.items():
        write_json(snapshot_dir / file_name, payload)
    if status == "COMPLETE" and publish_current:
        for file_name, payload in outputs.items():
            write_json(current_root / file_name, payload)
    return manifest


def print_manifest(manifest: dict[str, Any]) -> None:
    print("=" * 70)
    print("TM1 PUBLIC VIEW INVENTORY")
    print("=" * 70)
    print(f"Snapshot      : {manifest.get('snapshot_id', 'UNKNOWN')}")
    print(f"Status        : {manifest.get('status', 'UNKNOWN')}")
    print(f"Visibility    : {manifest.get('visibility_scope', 'UNKNOWN')}")
    print(f"Cubes         : {int(manifest.get('cube_count', 0) or 0):,}")
    print(f"Views         : {int(manifest.get('view_count', 0) or 0):,}")
    print(f"Relationships : {int(manifest.get('relationship_count', 0) or 0):,}")
    print(f"Validations   : {int(manifest.get('validation_count', 0) or 0):,}")
    print(f"Errors        : {int(manifest.get('error_count', 0) or 0):,}")
    print(f"Published     : {bool(manifest.get('published_current', False))}")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect public TM1 view definitions selectively.")
    parser.add_argument("--cube", action="append", dest="cubes")
    parser.add_argument("--object-catalog", type=Path, default=OBJECTS_FILE)
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        with get_tm1_connection() as tm1:
            manifest = collect_views(
                tm1,
                cubes=args.cubes,
                object_catalog_path=args.object_catalog,
                publish_current=not args.no_publish,
                progress=not args.quiet,
            )
        print_manifest(manifest)
        return 0 if manifest.get("status") == "COMPLETE" else 1
    except Exception as error:
        print(f"View collection failed: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
