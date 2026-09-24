from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utilities.tm1_connection import get_tm1_connection


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def sorted_unique(values: Iterable[Any]) -> list[str]:
    by_key: dict[str, str] = {}
    for value in values:
        name = str(getattr(value, "name", value) or "").strip()
        if name:
            by_key.setdefault(normalized(name), name)
    return sorted(by_key.values(), key=str.casefold)

def normalize_name_collection(
    value: Any,
    *,
    source: str,
) -> list[str]:
    """Normalize a dynamically returned TM1 name collection.

    TM1py compatibility paths may expose names as a mapping,
    list, tuple, generator, or another iterable. Strings and
    scalar objects are rejected explicitly.
    """

    if value is None:
        return []

    if isinstance(value, Mapping):
        return sorted_unique(
            value.keys()
        )

    if isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise TypeError(
            f"{source} returned a string rather than "
            "a collection of names."
        )

    if not isinstance(value, Iterable):
        raise TypeError(
            f"{source} returned a noniterable value: "
            f"{type(value).__name__}"
        )

    return sorted_unique(value)

def hierarchy_names(
    tm1: Any,
    dimension_name: str,
) -> tuple[list[str], str]:
    service = getattr(
        tm1,
        "hierarchies",
        None,
    )

    method = getattr(
        service,
        "get_all_names",
        None,
    )

    if callable(method):
        source = (
            "tm1.hierarchies.get_all_names"
        )

        raw_names = method(
            dimension_name
        )

        return (
            normalize_name_collection(
                raw_names,
                source=source,
            ),
            source,
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

    nested_service = getattr(
        dimension_service,
        "hierarchies",
        None,
    )

    nested_method = getattr(
        nested_service,
        "get_all_names",
        None,
    )

    if callable(nested_method):
        source = (
            "tm1.dimensions.hierarchies."
            "get_all_names"
        )

        raw_names = nested_method(
            dimension_name
        )

        return (
            normalize_name_collection(
                raw_names,
                source=source,
            ),
            source,
        )

    get_dimension = getattr(
        dimension_service,
        "get",
        None,
    )

    if not callable(get_dimension):
        raise AttributeError(
            "The TM1 dimensions service cannot "
            "retrieve a dimension."
        )

    dimension = get_dimension(
        dimension_name
    )

    collection = getattr(
        dimension,
        "hierarchies",
        None,
    )

    if isinstance(collection, Mapping):
        return (
            sorted_unique(
                collection.keys()
            ),
            "dimension.hierarchies mapping",
        )

    if isinstance(
        collection,
        Iterable,
    ) and not isinstance(
        collection,
        (str, bytes, bytearray),
    ):
        return (
            sorted_unique(collection),
            "dimension.hierarchies iterable",
        )

    raise TypeError(
        "No iterable hierarchy collection found "
        f"for dimension: {dimension_name}"
    )


def get_hierarchy(tm1: Any, dimension_name: str, hierarchy_name: str) -> tuple[Any, str]:
    service = getattr(tm1, "hierarchies", None)
    method = getattr(service, "get", None)
    if callable(method):
        return method(dimension_name, hierarchy_name), "tm1.hierarchies.get"

    dimension_service = getattr(tm1, "dimensions", None)
    nested_service = getattr(dimension_service, "hierarchies", None)
    nested_method = getattr(nested_service, "get", None)
    if callable(nested_method):
        return nested_method(dimension_name, hierarchy_name), "tm1.dimensions.hierarchies.get"

    get_dimension = getattr(dimension_service, "get", None)
    if not callable(get_dimension):
        raise AttributeError("The TM1 dimensions service cannot retrieve a dimension.")
    dimension = get_dimension(dimension_name)
    collection = getattr(dimension, "hierarchies", None)
    if isinstance(collection, Mapping):
        direct = collection.get(hierarchy_name)
        if direct is not None:
            return direct, "dimension.hierarchies mapping"
        for name, hierarchy in collection.items():
            if normalized(name) == normalized(hierarchy_name):
                return hierarchy, "dimension.hierarchies mapping"
    if isinstance(collection, Iterable) and not isinstance(collection, (str, bytes, bytearray)):
        for hierarchy in collection:
            if normalized(getattr(hierarchy, "name", "")) == normalized(hierarchy_name):
                return hierarchy, "dimension.hierarchies iterable"
    raise KeyError(f"Hierarchy not found: {dimension_name}::{hierarchy_name}")


def attribute_records(hierarchy: Any) -> tuple[list[dict[str, Any]], str]:
    candidates = (
        "element_attributes",
        "elementAttributes",
        "attributes",
    )
    collection: Any = None
    source = ""
    for name in candidates:
        value = getattr(hierarchy, name, None)
        if value is not None:
            collection = value
            source = f"hierarchy.{name}"
            break

    if collection is None:
        return [], "no attribute collection exposed"

    if isinstance(collection, Mapping):
        items = list(collection.values())
    elif isinstance(collection, Iterable) and not isinstance(collection, (str, bytes, bytearray)):
        items = list(collection)
    else:
        raise TypeError("Hierarchy attribute collection is not iterable.")

    records: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Mapping):
            mapping = {str(key).casefold(): value for key, value in item.items()}
            name = mapping.get("name")
            attribute_type = mapping.get("type") or mapping.get("attribute_type")
        else:
            name = getattr(item, "name", None) or getattr(item, "Name", None)
            attribute_type = (
                getattr(item, "attribute_type", None)
                or getattr(item, "type", None)
                or getattr(item, "Type", None)
            )
        name_text = str(name or "").strip()
        if name_text:
            records.append(
                {
                    "attribute_name": name_text,
                    "attribute_type": str(attribute_type or "UNKNOWN"),
                }
            )
    unique = {
        normalized(record["attribute_name"]): record
        for record in records
    }
    return sorted(unique.values(), key=lambda item: item["attribute_name"].casefold()), source


def benchmark_once(tm1: Any, dimension_name: str) -> dict[str, Any]:
    total_started = perf_counter()

    started = perf_counter()
    names, hierarchy_name_source = hierarchy_names(tm1, dimension_name)
    list_seconds = perf_counter() - started

    hierarchy_results: list[dict[str, Any]] = []
    total_attributes = 0
    for hierarchy_name in names:
        started = perf_counter()
        hierarchy, hierarchy_get_source = get_hierarchy(
            tm1,
            dimension_name,
            hierarchy_name,
        )
        get_seconds = perf_counter() - started

        started = perf_counter()
        attributes, attribute_source = attribute_records(hierarchy)
        parse_seconds = perf_counter() - started
        total_attributes += len(attributes)

        hierarchy_results.append(
            {
                "hierarchy_name": hierarchy_name,
                "attribute_count": len(attributes),
                "hierarchy_get_seconds": round(get_seconds, 6),
                "attribute_parse_seconds": round(parse_seconds, 6),
                "hierarchy_get_source": hierarchy_get_source,
                "attribute_source": attribute_source,
                "attributes": attributes,
            }
        )

    return {
        "dimension_name": dimension_name,
        "hierarchy_count": len(names),
        "attribute_count": total_attributes,
        "hierarchy_list_seconds": round(list_seconds, 6),
        "total_seconds": round(perf_counter() - total_started, 6),
        "hierarchy_name_source": hierarchy_name_source,
        "hierarchies": hierarchy_results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark attribute-definition retrieval for one TM1 dimension."
    )
    parser.add_argument(
        "dimension",
        nargs="?",
        default="Material",
        help="Dimension to benchmark (default: Material)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Measured runs after one warm-up run (default: 3)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/current/attribute_dimension_benchmark.json"),
        help="Benchmark output path",
    )
    args = parser.parse_args(argv)
    if args.runs < 1 or args.runs > 20:
        parser.error("--runs must be between 1 and 20")

    with get_tm1_connection() as tm1:
        warmup = benchmark_once(tm1, args.dimension)
        measured = [benchmark_once(tm1, args.dimension) for _ in range(args.runs)]

    durations = [record["total_seconds"] for record in measured]
    payload = {
        "benchmark_type": "SINGLE_DIMENSION_ATTRIBUTE_DEFINITIONS",
        "collected_at": utc_now(),
        "dimension_name": args.dimension,
        "warmup": warmup,
        "runs": measured,
        "summary": {
            "run_count": len(measured),
            "hierarchy_count": measured[-1]["hierarchy_count"],
            "attribute_count": measured[-1]["attribute_count"],
            "minimum_seconds": round(min(durations), 6),
            "median_seconds": round(statistics.median(durations), 6),
            "maximum_seconds": round(max(durations), 6),
            "mean_seconds": round(statistics.mean(durations), 6),
            "estimated_902_dimension_seconds_at_mean": round(
                statistics.mean(durations) * 902,
                2,
            ),
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    summary = payload["summary"]
    print("=" * 72)
    print("TM1 ATTRIBUTE SINGLE-DIMENSION BENCHMARK")
    print("=" * 72)
    print(f"Dimension         : {args.dimension}")
    print(f"Hierarchies       : {summary['hierarchy_count']}")
    print(f"Attributes        : {summary['attribute_count']}")
    print(f"Measured runs     : {summary['run_count']}")
    print(f"Minimum           : {summary['minimum_seconds']:.3f}s")
    print(f"Median            : {summary['median_seconds']:.3f}s")
    print(f"Maximum           : {summary['maximum_seconds']:.3f}s")
    print(f"Mean              : {summary['mean_seconds']:.3f}s")
    print(
        "Naive 902-dim estimate: "
        f"{summary['estimated_902_dimension_seconds_at_mean']:.2f}s"
    )
    print(f"Output            : {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
