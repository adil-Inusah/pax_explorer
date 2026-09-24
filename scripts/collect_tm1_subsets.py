from __future__ import annotations

"""Collect public TM1 subset definitions through selective hierarchy queries.

Production mode performs one selective REST request per hierarchy and requests
only Name, Expression, and Alias. Static subset element membership is not
expanded. Compatibility mode supports older TM1py clients and unit-test doubles
through get_all_names() plus get().
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
HIERARCHIES_FILE = CURRENT_ROOT / "hierarchies.json"
SLOW_REQUEST_WARNING_SECONDS = 1.0
SELECTIVE_FIELDS = ("Name", "Expression", "Alias")


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


def hierarchy_pairs(payload: Any) -> list[tuple[str, str]]:
    if not isinstance(payload, list):
        raise TypeError("hierarchies.json must contain a JSON array.")
    pairs: dict[tuple[str, str], tuple[str, str]] = {}
    for record in payload:
        if not isinstance(record, Mapping):
            continue
        dimension_name = normalize_name(record.get("dimension_name"))
        hierarchy_name = normalize_name(record.get("hierarchy_name"))
        if dimension_name and hierarchy_name:
            pairs.setdefault(
                (normalized_key(dimension_name), normalized_key(hierarchy_name)),
                (dimension_name, hierarchy_name),
            )
    return sorted(
        pairs.values(),
        key=lambda pair: (pair[0].casefold(), pair[1].casefold()),
    )


def get_subset_service(tm1: Any) -> Any:
    direct = getattr(tm1, "subsets", None)
    if direct is not None:
        return direct
    hierarchy_service = getattr(tm1, "hierarchies", None)
    nested = getattr(hierarchy_service, "subsets", None)
    if nested is not None:
        return nested
    raise AttributeError("The TM1 connection does not expose a subset service.")


def get_rest_service(subset_service: Any) -> Any | None:
    for attribute_name in ("_rest", "rest"):
        candidate = getattr(subset_service, attribute_name, None)
        if callable(getattr(candidate, "GET", None)):
            return candidate
    return None


def odata_key(value: str) -> str:
    """Escape an OData string key and percent-encode unsafe URL characters."""
    escaped = value.replace("'", "''")
    return quote(escaped, safe="}$-_.~")


def response_json(response: Any) -> Any:
    method = getattr(response, "json", None)
    if callable(method):
        return method()
    if isinstance(response, Mapping):
        return response
    raise TypeError("Selective subset response does not expose JSON content.")


def selective_subset_url(
    dimension_name: str,
    hierarchy_name: str,
    fields: tuple[str, ...] = SELECTIVE_FIELDS,
) -> str:
    dimension = odata_key(dimension_name)
    hierarchy = odata_key(hierarchy_name)
    selected = ",".join(fields)
    return (
        f"/Dimensions('{dimension}')/Hierarchies('{hierarchy}')/Subsets"
        f"?$select={selected}"
    )


def selective_public_subset_definitions(
    subset_service: Any,
    dimension_name: str,
    hierarchy_name: str,
) -> list[dict[str, Any]] | None:
    """Return selective public subset definitions, or None if unavailable.

    None means the service does not expose an underlying REST client and the
    caller should use compatibility mode. REST errors are intentionally raised.
    """
    rest = get_rest_service(subset_service)
    if rest is None:
        return None
    url = selective_subset_url(dimension_name, hierarchy_name)
    response = rest.GET(url)
    payload = response_json(response)
    if not isinstance(payload, Mapping):
        raise TypeError("Selective subset response root must be a JSON object.")
    values = payload.get("value")
    if not isinstance(values, list):
        raise TypeError("Selective subset response must contain a value array.")
    records: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise TypeError(
                "Selective subset response contains a non-object record at "
                f"index {index}."
            )
        records.append(dict(value))
    return records


def compatibility_public_subset_definitions(
    subset_service: Any,
    dimension_name: str,
    hierarchy_name: str,
) -> list[Any]:
    """Compatibility path for old clients and isolated unit-test doubles."""
    get_all_names = getattr(subset_service, "get_all_names", None)
    get_subset = getattr(subset_service, "get", None)
    if not callable(get_all_names) or not callable(get_subset):
        raise AttributeError(
            "Subset service supports neither selective REST retrieval nor "
            "get_all_names()/get() compatibility retrieval."
        )
    names = sorted_unique_names(
        iterable_items(
            get_all_names(dimension_name, hierarchy_name, private=False),
            description=f"Public subsets for {dimension_name}::{hierarchy_name}",
        )
    )
    return [
        get_subset(name, dimension_name, hierarchy_name, private=False)
        for name in names
    ]


def public_subset_definitions(
    subset_service: Any,
    dimension_name: str,
    hierarchy_name: str,
) -> tuple[list[Any], str]:
    selective = selective_public_subset_definitions(
        subset_service,
        dimension_name,
        hierarchy_name,
    )
    if selective is not None:
        return selective, "SELECTIVE_REST"
    return (
        compatibility_public_subset_definitions(
            subset_service,
            dimension_name,
            hierarchy_name,
        ),
        "COMPATIBILITY_FULL_GET",
    )


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


def subset_name_of(subset: Any) -> str:
    return normalize_name(property_value(subset, "Name", "name"))


def expression_of(subset: Any) -> str | None:
    value = normalize_name(
        property_value(subset, "Expression", "expression", "MDX", "mdx")
    )
    return value or None


def alias_of(subset: Any) -> str | None:
    value = normalize_name(property_value(subset, "Alias", "alias"))
    return value or None


def element_count_of_compatibility_subset(subset: Any) -> int | None:
    raw = property_value(subset, "Elements", "elements")
    if raw is None:
        return None
    return len(
        iterable_items(raw, description="Compatibility subset element collection")
    )


def hash_text(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def subset_node_id(dimension: str, hierarchy: str, name: str) -> str:
    return f"subset::{dimension}::{hierarchy}::PUBLIC::<none>::{name}"


def hierarchy_node_id(dimension: str, hierarchy: str) -> str:
    return f"hierarchy::{dimension}::{hierarchy}"


def dimension_node_id(dimension: str) -> str:
    return f"dimension::{dimension}"


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round((len(ordered) - 1) * fraction)),
    )
    return ordered[index]


def collect_subsets(
    tm1: Any,
    *,
    hierarchy_catalog_path: Path | None = None,
    hierarchy_scope: list[tuple[str, str]] | None = None,
    snapshot_root: Path = SNAPSHOT_ROOT,
    current_root: Path = CURRENT_ROOT,
    timestamp: datetime | None = None,
    publish_current: bool = True,
    progress: bool = False,
) -> dict[str, Any]:
    started = timestamp or utc_now()
    started_clock = perf_counter()
    run_snapshot_id = build_snapshot_id(started)
    collected_at = started.isoformat()
    snapshot_dir = snapshot_root / run_snapshot_id

    if hierarchy_scope is not None:
        scope = sorted(
            hierarchy_scope,
            key=lambda pair: (pair[0].casefold(), pair[1].casefold()),
        )
        hierarchy_source = "EXPLICIT_HIERARCHIES"
    elif hierarchy_catalog_path is not None and hierarchy_catalog_path.is_file():
        scope = hierarchy_pairs(read_json(hierarchy_catalog_path))
        hierarchy_source = "HIERARCHY_CATALOG"
    else:
        raise ValueError("A hierarchy catalog or explicit hierarchy scope is required.")

    subset_service = get_subset_service(tm1)
    records_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    relationships: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    hierarchy_metrics: list[dict[str, Any]] = []

    for index, (dimension_name, hierarchy_name) in enumerate(scope, start=1):
        request_started = perf_counter()
        try:
            definitions, retrieval_mode = public_subset_definitions(
                subset_service,
                dimension_name,
                hierarchy_name,
            )
            request_seconds = perf_counter() - request_started
            hierarchy_subset_count = 0

            for subset in definitions:
                subset_name = subset_name_of(subset)
                if not subset_name:
                    raise ValueError(
                        "A public subset definition has no Name for "
                        f"{dimension_name}::{hierarchy_name}."
                    )
                expression = expression_of(subset)
                subset_kind = "MDX" if expression else "STATIC"
                node_id = subset_node_id(
                    dimension_name,
                    hierarchy_name,
                    subset_name,
                )
                record_key = (
                    normalized_key(dimension_name),
                    normalized_key(hierarchy_name),
                    normalized_key(subset_name),
                )
                element_count = (
                    element_count_of_compatibility_subset(subset)
                    if retrieval_mode == "COMPATIBILITY_FULL_GET"
                    and subset_kind == "STATIC"
                    else None
                )
                records_by_key.setdefault(
                    record_key,
                    {
                        "snapshot_id": run_snapshot_id,
                        "collected_at": collected_at,
                        "node_id": node_id,
                        "node_type": "SUBSET",
                        "object_type": "subset",
                        "object_name": (
                            f"{dimension_name}::{hierarchy_name}::{subset_name}"
                        ),
                        "qualified_name": (
                            f"{dimension_name}::{hierarchy_name}::{subset_name}"
                        ),
                        "dimension_name": dimension_name,
                        "hierarchy_name": hierarchy_name,
                        "subset_name": subset_name,
                        "visibility": "PUBLIC",
                        "owner": None,
                        "subset_kind": subset_kind,
                        "mdx_expression": expression,
                        "mdx_hash": hash_text(expression),
                        "alias": alias_of(subset),
                        "element_count": element_count,
                        "elements_collected": (
                            retrieval_mode == "COMPATIBILITY_FULL_GET"
                            and element_count is not None
                        ),
                        "retrieval_mode": retrieval_mode,
                        "is_control": (
                            is_control_name(dimension_name)
                            or is_control_name(hierarchy_name)
                            or is_control_name(subset_name)
                        ),
                    },
                )
                hierarchy_subset_count += 1

                ownership = (
                    (
                        "BELONGS_TO_HIERARCHY",
                        hierarchy_node_id(dimension_name, hierarchy_name),
                        "HIERARCHY",
                        "hierarchy",
                    ),
                    (
                        "BELONGS_TO_DIMENSION",
                        dimension_node_id(dimension_name),
                        "DIMENSION",
                        "dimension",
                    ),
                )
                for relationship_type, target_id, target_type, suffix in ownership:
                    edge_id = (
                        "subset-ownership::"
                        f"{dimension_name}::{hierarchy_name}::{subset_name}::{suffix}"
                    )
                    relationships.append(
                        {
                            "snapshot_id": run_snapshot_id,
                            "relationship_id": edge_id,
                            "source_id": node_id,
                            "source_type": "SUBSET",
                            "target_id": target_id,
                            "target_type": target_type,
                            "relationship_type": relationship_type,
                            "relationship_origin": "METADATA",
                            "resolution_method": "CATALOG_MATCH",
                        }
                    )
                    validations.append(
                        {
                            "snapshot_id": run_snapshot_id,
                            "validation_id": f"validation::{edge_id}",
                            "relationship_id": edge_id,
                            "validation_status": "VALID",
                            "source_id": node_id,
                            "target_id": target_id,
                        }
                    )

            hierarchy_metrics.append(
                {
                    "dimension_name": dimension_name,
                    "hierarchy_name": hierarchy_name,
                    "public_subset_count": hierarchy_subset_count,
                    "request_seconds": round(request_seconds, 6),
                    "retrieval_mode": retrieval_mode,
                    "elements_collected": retrieval_mode == "COMPATIBILITY_FULL_GET",
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
                    "stage": "GET_PUBLIC_SUBSET_DEFINITIONS",
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            hierarchy_subset_count = 0

        if progress:
            print(
                f"hierarchy={index}/{len(scope)} "
                f"name={dimension_name}::{hierarchy_name} "
                f"public_subsets={hierarchy_subset_count} "
                f"catalog_subsets={len(records_by_key)} "
                f"errors={len(errors)} "
                f"elapsed={perf_counter() - started_clock:.1f}s"
            )

    records = sorted(
        records_by_key.values(),
        key=lambda item: (
            item["dimension_name"].casefold(),
            item["hierarchy_name"].casefold(),
            item["subset_name"].casefold(),
        ),
    )
    relationships.sort(key=lambda item: item["relationship_id"].casefold())
    validations.sort(key=lambda item: item["relationship_id"].casefold())

    elapsed = perf_counter() - started_clock
    durations = [float(item["request_seconds"]) for item in hierarchy_metrics]
    kind_counts = Counter(record["subset_kind"] for record in records)
    mode_counts = Counter(record["retrieval_mode"] for record in records)
    status = "PARTIAL" if errors else "COMPLETE"

    manifest = {
        "snapshot_id": run_snapshot_id,
        "status": status,
        "visibility_scope": "PUBLIC",
        "started_at": collected_at,
        "completed_at": utc_now().isoformat(),
        "hierarchy_count": len(scope),
        "subset_count": len(records),
        "relationship_count": len(relationships),
        "validation_count": len(validations),
        "error_count": len(errors),
        "publish_current_requested": publish_current,
        "published_current": bool(status == "COMPLETE" and publish_current),
    }
    metrics = {
        "hierarchy_source": hierarchy_source,
        "hierarchy_count": len(scope),
        "completed_hierarchy_count": len(hierarchy_metrics),
        "subset_count": len(records),
        "subset_kind_counts": dict(sorted(kind_counts.items())),
        "retrieval_mode_counts": dict(sorted(mode_counts.items())),
        "relationship_count": len(relationships),
        "validation_count": len(validations),
        "definition_request_count": len(scope),
        "elements_collected": any(
            bool(item["elements_collected"]) for item in hierarchy_metrics
        ),
        "total_seconds": round(elapsed, 6),
        "mean_hierarchy_request_seconds": (
            round(statistics.mean(durations), 6) if durations else 0.0
        ),
        "median_hierarchy_request_seconds": (
            round(statistics.median(durations), 6) if durations else 0.0
        ),
        "p95_hierarchy_request_seconds": round(
            percentile(durations, 0.95), 6
        ),
        "maximum_hierarchy_request_seconds": (
            round(max(durations), 6) if durations else 0.0
        ),
        "slow_request_count": sum(
            bool(item["slow_request"]) for item in hierarchy_metrics
        ),
        "hierarchies": hierarchy_metrics,
    }

    outputs = {
        "subsets.json": records,
        "subset_relationships.json": relationships,
        "subset_relationship_validations.json": validations,
        "subset_manifest.json": manifest,
        "subset_collection_metrics.json": metrics,
        "subset_collection_errors.json": errors,
    }
    for file_name, payload in outputs.items():
        write_json(snapshot_dir / file_name, payload)
    if status == "COMPLETE" and publish_current:
        for file_name, payload in outputs.items():
            write_json(current_root / file_name, payload)
    return manifest


def print_manifest(manifest: dict[str, Any]) -> None:
    print("=" * 70)
    print("TM1 PUBLIC SUBSET INVENTORY")
    print("=" * 70)
    print(f"Snapshot      : {manifest.get('snapshot_id', 'UNKNOWN')}")
    print(f"Status        : {manifest.get('status', 'UNKNOWN')}")
    print(f"Visibility    : {manifest.get('visibility_scope', 'UNKNOWN')}")
    print(f"Hierarchies   : {int(manifest.get('hierarchy_count', 0) or 0):,}")
    print(f"Subsets       : {int(manifest.get('subset_count', 0) or 0):,}")
    print(f"Relationships : {int(manifest.get('relationship_count', 0) or 0):,}")
    print(f"Validations   : {int(manifest.get('validation_count', 0) or 0):,}")
    print(f"Errors        : {int(manifest.get('error_count', 0) or 0):,}")
    print(f"Published     : {bool(manifest.get('published_current', False))}")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect public TM1 subset definitions selectively."
    )
    parser.add_argument(
        "--hierarchy-catalog",
        type=Path,
        default=HIERARCHIES_FILE,
    )
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        with get_tm1_connection() as tm1:
            manifest = collect_subsets(
                tm1,
                hierarchy_catalog_path=args.hierarchy_catalog,
                publish_current=not args.no_publish,
                progress=not args.quiet,
            )
        print_manifest(manifest)
        return 0 if manifest.get("status") == "COMPLETE" else 1
    except Exception as error:
        print(
            "Subset collection failed: "
            f"{type(error).__name__}: {error}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
