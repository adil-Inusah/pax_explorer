from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

WRAPPER_KEYS = ("validations", "records", "items", "data", "results")
DYNAMIC_STATUSES = {
    "UNRESOLVED_DYNAMIC_REFERENCE",
    "PENDING_DYNAMIC_RESOLUTION",
}
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
QUOTED_RE = re.compile(r"^\s*(['\"]).*\1\s*$", re.DOTALL)
FUNCTION_RE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*\(")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile unresolved TM1 relationship references."
    )
    parser.add_argument(
        "catalog_dir",
        nargs="?",
        type=Path,
        default=Path("data/current"),
        help="Catalog directory (default: data/current)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=30,
        help="Top groups printed in each section (default: 30)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: catalog directory)",
    )
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def records(payload: Any, source: Path) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        values = None
        for key in WRAPPER_KEYS:
            candidate = payload.get(key)
            if isinstance(candidate, list):
                values = candidate
                break
        if values is None:
            raise ValueError(
                f"Cannot find a record list in {source}; keys={sorted(payload)}"
            )
    else:
        raise ValueError(f"Unsupported JSON root in {source}")
    return [item for item in values if isinstance(item, dict)]


def value(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        candidate = record.get(key)
        if candidate is not None and str(candidate).strip():
            return candidate
    return None


def status(record: dict[str, Any]) -> str:
    return str(value(record, "validation_status", "status") or "").strip().upper()


def expression(record: dict[str, Any]) -> str:
    direct = value(record, "target_expression", "TargetExpression")
    if direct:
        return str(direct).strip()
    many = record.get("target_expressions")
    if isinstance(many, list) and many:
        return " | ".join(str(item).strip() for item in many if str(item).strip())
    return "<missing expression>"


def expression_pattern(text: str) -> str:
    stripped = text.strip()
    if stripped == "<missing expression>":
        return "MISSING_EXPRESSION"
    if QUOTED_RE.match(stripped):
        return "QUOTED_LITERAL"
    if IDENTIFIER_RE.match(stripped):
        return "SIMPLE_VARIABLE"
    if "|" in stripped:
        return "CONCATENATION"
    if FUNCTION_RE.match(stripped):
        return "FUNCTION_RESULT"
    if "%" in stripped:
        return "PARAMETER_OR_EXPAND_TOKEN"
    if any(token in stripped for token in ("[", "]", "{", "}")):
        return "STRUCTURED_EXPRESSION"
    return "COMPLEX_EXPRESSION"


def normalized(text: Any) -> str:
    return " ".join(str(text or "").strip().casefold().split())


def print_counter(title: str, counter: Counter[str], top: int) -> None:
    print()
    print(title)
    print("-" * len(title))
    for name, count in counter.most_common(top):
        print(f"{count:>6}  {name}")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    catalog_dir = args.catalog_dir
    output_dir = args.output_dir or catalog_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    sources = [
        ("TI", catalog_dir / "ti_relationship_validations.json"),
        ("RULE", catalog_dir / "rule_relationship_validations.json"),
    ]

    all_dynamic: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()

    for source_type, path in sources:
        if not path.is_file():
            print(f"WARNING: missing {path}")
            continue
        for record in records(read_json(path), path):
            if status(record) not in DYNAMIC_STATUSES:
                continue
            item = dict(record)
            item["source_catalog"] = source_type
            item["profile_status"] = status(record)
            item["profile_expression"] = expression(record)
            item["expression_pattern"] = expression_pattern(item["profile_expression"])
            item["profile_source"] = str(
                value(record, "process_name", "source_name", "source_cube")
                or "<missing source>"
            )
            item["profile_relationship_type"] = str(
                value(record, "relationship_type", "RelationshipType")
                or "<missing relationship type>"
            )
            item["profile_target_type"] = str(
                value(record, "target_type", "target_object_type", "TargetType")
                or "<missing target type>"
            )
            item["profile_confidence"] = str(
                value(record, "confidence", "EvidenceConfidence")
                or "<missing confidence>"
            )
            all_dynamic.append(item)
            source_counts[source_type] += 1

    pattern_counts = Counter(item["expression_pattern"] for item in all_dynamic)
    expression_counts = Counter(item["profile_expression"] for item in all_dynamic)
    relationship_counts = Counter(item["profile_relationship_type"] for item in all_dynamic)
    process_counts = Counter(item["profile_source"] for item in all_dynamic)
    target_type_counts = Counter(item["profile_target_type"] for item in all_dynamic)
    confidence_counts = Counter(item["profile_confidence"] for item in all_dynamic)
    status_counts = Counter(item["profile_status"] for item in all_dynamic)

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in all_dynamic:
        key = (
            item["expression_pattern"],
            normalized(item["profile_expression"]),
            item["profile_relationship_type"],
        )
        grouped[key].append(item)

    pattern_rows: list[dict[str, Any]] = []
    for (pattern, _, rel_type), group in grouped.items():
        expressions = sorted({item["profile_expression"] for item in group})
        processes = sorted({item["profile_source"] for item in group})
        pattern_rows.append(
            {
                "count": len(group),
                "expression_pattern": pattern,
                "relationship_type": rel_type,
                "example_expression": expressions[0] if expressions else "",
                "distinct_processes": len(processes),
                "example_processes": "; ".join(processes[:5]),
                "candidate_action": {
                    "SIMPLE_VARIABLE": "Trace assignment, process parameter, or caller argument",
                    "CONCATENATION": "Attempt constant folding and partial-symbol propagation",
                    "FUNCTION_RESULT": "Add resolver only for deterministic string functions",
                    "PARAMETER_OR_EXPAND_TOKEN": "Resolve known parameters or Expand tokens",
                    "STRUCTURED_EXPRESSION": "Review parser extraction and nested syntax",
                    "COMPLEX_EXPRESSION": "Manual pattern review",
                    "MISSING_EXPRESSION": "Fix validation/evidence propagation",
                    "QUOTED_LITERAL": "Investigate unexpected unresolved literal",
                }.get(pattern, "Review"),
            }
        )
    pattern_rows.sort(key=lambda row: (-int(row["count"]), str(row["example_expression"]).casefold()))

    detail_fields = [
        "source_catalog",
        "profile_status",
        "expression_pattern",
        "profile_expression",
        "profile_source",
        "profile_relationship_type",
        "profile_target_type",
        "profile_confidence",
        "target_name",
        "target_expression",
        "reason",
    ]
    detail_path = output_dir / "dynamic_reference_profile_detail.csv"
    pattern_path = output_dir / "dynamic_reference_profile_patterns.csv"
    summary_path = output_dir / "dynamic_reference_profile_summary.json"
    write_csv(detail_path, all_dynamic, detail_fields)
    write_csv(
        pattern_path,
        pattern_rows,
        [
            "count",
            "expression_pattern",
            "relationship_type",
            "example_expression",
            "distinct_processes",
            "example_processes",
            "candidate_action",
        ],
    )

    summary = {
        "catalog_directory": str(catalog_dir.resolve()),
        "dynamic_reference_count": len(all_dynamic),
        "source_counts": dict(source_counts),
        "status_counts": dict(status_counts),
        "pattern_counts": dict(pattern_counts),
        "relationship_type_counts": dict(relationship_counts),
        "target_type_counts": dict(target_type_counts),
        "confidence_counts": dict(confidence_counts),
        "distinct_expressions": len(expression_counts),
        "distinct_sources": len(process_counts),
        "top_expressions": expression_counts.most_common(args.top),
        "top_sources": process_counts.most_common(args.top),
        "output_files": [str(detail_path), str(pattern_path)],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("=" * 72)
    print("TM1 DYNAMIC REFERENCE PROFILE")
    print("=" * 72)
    print(f"catalog_directory={catalog_dir.resolve()}")
    print(f"dynamic_references={len(all_dynamic)}")
    print(f"distinct_expressions={len(expression_counts)}")
    print(f"distinct_sources={len(process_counts)}")
    print_counter("By source catalog", source_counts, args.top)
    print_counter("By final status", status_counts, args.top)
    print_counter("By expression pattern", pattern_counts, args.top)
    print_counter("By relationship type", relationship_counts, args.top)
    print_counter("By target type", target_type_counts, args.top)
    print_counter("Top expressions", expression_counts, args.top)
    print_counter("Top source objects", process_counts, args.top)
    print()
    print(f"summary_json={summary_path}")
    print(f"pattern_csv={pattern_path}")
    print(f"detail_csv={detail_path}")

    # expected = 642  # 640 TI unresolved + 2 rule pending in the current baseline.
    # if len(all_dynamic) != expected:
    #     print(
    #         "WARNING: current dynamic count differs from the documented "
    #         f"baseline of {expected}."
    #     )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
