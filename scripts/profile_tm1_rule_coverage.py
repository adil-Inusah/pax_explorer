from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEFAULT_DEFINITIONS = ROOT_DIR / "data" / "current" / "cube_rule_definitions.json"
DEFAULT_REGISTRY = ROOT_DIR / "config" / "tm1_lineage_functions.json"
DEFAULT_CURRENT_ROOT = ROOT_DIR / "data" / "current"
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class FunctionOccurrence:
    cube_name: str
    function_name: str
    line_number: int
    expression: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
    temporary.replace(path)


def _mask_comments(text: str) -> str:
    chars = list(text)
    i = 0
    quote: str | None = None
    while i < len(chars):
        if quote:
            if chars[i] == quote:
                if i + 1 < len(chars) and chars[i + 1] == quote:
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if chars[i] in "'\"":
            quote = chars[i]
            i += 1
            continue
        if text.startswith("#", i) or text.startswith("//", i):
            marker_size = 1 if text.startswith("#", i) else 2
            for offset in range(marker_size):
                chars[i + offset] = " "
            i += marker_size
            while i < len(chars) and chars[i] not in "\r\n":
                chars[i] = " "
                i += 1
            continue
        if text.startswith("/*", i):
            chars[i] = chars[i + 1] = " "
            i += 2
            while i < len(chars) and not text.startswith("*/", i):
                if chars[i] not in "\r\n":
                    chars[i] = " "
                i += 1
            if i + 1 < len(chars):
                chars[i] = chars[i + 1] = " "
                i += 2
            continue
        i += 1
    return "".join(chars)


def _mask_string_contents(text: str) -> str:
    """Mask characters inside quoted strings while preserving offsets and lines.

    Quote delimiters remain in place, but all non-newline content between them
    is replaced with spaces. Doubled quote characters are treated as escaped
    quote content. This prevents business labels such as ``Count (System)``
    from being detected as function calls.
    """
    chars = list(text)
    quote: str | None = None
    i = 0
    while i < len(chars):
        char = text[i]
        if quote is None:
            if char in "'\"":
                quote = char
            i += 1
            continue
        if char == quote:
            if i + 1 < len(chars) and text[i + 1] == quote:
                chars[i] = " "
                chars[i + 1] = " "
                i += 2
                continue
            quote = None
            i += 1
            continue
        if chars[i] not in "\r\n":
            chars[i] = " "
        i += 1
    return "".join(chars)


def _find_matching_parenthesis(text: str, opening: int) -> int | None:
    depth = 0
    quote: str | None = None
    i = opening
    while i < len(text):
        char = text[i]
        if quote:
            if char == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def discover_function_calls(cube_name: str, rule_text: str) -> tuple[list[FunctionOccurrence], list[dict[str, Any]]]:
    """Discover every syntactically complete function call in rule text."""
    comment_masked = _mask_comments(rule_text)
    discovery_text = _mask_string_contents(comment_masked)
    occurrences: list[FunctionOccurrence] = []
    malformed: list[dict[str, Any]] = []
    for match in _IDENTIFIER_RE.finditer(discovery_text):
        opening = match.end()
        while opening < len(discovery_text) and discovery_text[opening].isspace():
            opening += 1
        if opening >= len(discovery_text) or discovery_text[opening] != "(":
            continue
        closing = _find_matching_parenthesis(comment_masked, opening)
        function_name = match.group(0).upper()
        line_number = rule_text.count("\n", 0, match.start()) + 1
        if closing is None:
            malformed.append({
                "cube_name": cube_name,
                "function_name": function_name,
                "line_number": line_number,
                "reason": "UNBALANCED_PARENTHESES",
            })
            continue
        occurrences.append(FunctionOccurrence(
            cube_name=cube_name,
            function_name=function_name,
            line_number=line_number,
            expression=rule_text[match.start():closing + 1].strip(),
        ))
    return occurrences, malformed


def _registry_functions(registry: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    functions = registry.get("functions")
    if not isinstance(functions, dict):
        raise ValueError("Registry must contain a 'functions' JSON object.")
    return {str(name).upper(): value for name, value in functions.items()}


def build_profiles(
    definitions: Iterable[Mapping[str, Any]],
    registry: Mapping[str, Any],
    *,
    sample_limit: int = 5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    functions = _registry_functions(registry)
    grouped: dict[str, list[FunctionOccurrence]] = defaultdict(list)
    malformed: list[dict[str, Any]] = []
    ruled_cube_count = 0
    for definition in definitions:
        cube_name = str(definition.get("cube_name") or "").strip()
        rule_text = str(definition.get("rule_text") or "")
        if not rule_text.strip():
            continue
        ruled_cube_count += 1
        occurrences, failures = discover_function_calls(cube_name, rule_text)
        malformed.extend(failures)
        for occurrence in occurrences:
            grouped[occurrence.function_name].append(occurrence)

    profile: list[dict[str, Any]] = []
    for function_name in sorted(grouped):
        records = grouped[function_name]
        metadata = dict(functions.get(function_name, {}))
        known = bool(metadata)
        profile.append({
            "function_name": function_name,
            "occurrence_count": len(records),
            "cube_count": len({item.cube_name for item in records}),
            "parser_status": metadata.get("parser_status", "REVIEW_REQUIRED"),
            "lineage_category": metadata.get("category", "REVIEW_REQUIRED"),
            "relationship_type": metadata.get("relationship_type"),
            "target_object_type": metadata.get("target_object_type"),
            "registry_status": "CLASSIFIED" if known else "UNCLASSIFIED",
            "sample_evidence": [item.to_dict() for item in records[:sample_limit]],
        })

    total_occurrences = sum(item["occurrence_count"] for item in profile)
    classified_occurrences = sum(
        item["occurrence_count"] for item in profile
        if item["registry_status"] == "CLASSIFIED"
    )
    supported_occurrences = sum(
        item["occurrence_count"] for item in profile
        if item["parser_status"] == "SUPPORTED"
    )
    lineage_items = [item for item in profile if item["lineage_category"] in {"DIRECT", "CONTEXTUAL"}]
    lineage_occurrences = sum(item["occurrence_count"] for item in lineage_items)
    supported_lineage_occurrences = sum(
        item["occurrence_count"] for item in lineage_items
        if item["parser_status"] == "SUPPORTED"
    )
    dynamic_occurrences = sum(
        item["occurrence_count"] for item in profile
        if item["lineage_category"] == "DYNAMIC"
    )

    def percentage(numerator: int, denominator: int) -> float:
        return round(100.0 * numerator / denominator, 2) if denominator else 100.0

    coverage = {
        "ruled_cube_count": ruled_cube_count,
        "unique_function_count": len(profile),
        "classified_function_count": sum(item["registry_status"] == "CLASSIFIED" for item in profile),
        "unclassified_function_count": sum(item["registry_status"] == "UNCLASSIFIED" for item in profile),
        "total_function_occurrences": total_occurrences,
        "classified_occurrences": classified_occurrences,
        "supported_occurrences": supported_occurrences,
        "lineage_function_occurrences": lineage_occurrences,
        "supported_lineage_occurrences": supported_lineage_occurrences,
        "dynamic_function_occurrences": dynamic_occurrences,
        "malformed_call_count": len(malformed),
        "syntactic_coverage_percent": percentage(classified_occurrences, total_occurrences),
        "lineage_coverage_percent": percentage(supported_lineage_occurrences, lineage_occurrences),
        "unsupported_or_review_functions": [
            item["function_name"] for item in profile
            if item["parser_status"] in {"NOT_SUPPORTED", "PARTIALLY_SUPPORTED", "REVIEW_REQUIRED"}
        ],
        "malformed_calls": malformed,
    }
    return profile, coverage


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile TM1 rule-function lineage coverage.")
    parser.add_argument("--definitions", type=Path, default=DEFAULT_DEFINITIONS)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_CURRENT_ROOT)
    parser.add_argument("--sample-limit", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        if arguments.sample_limit < 1:
            raise ValueError("sample-limit must be at least 1")
        definitions = read_json(arguments.definitions)
        registry = read_json(arguments.registry)
        if not isinstance(definitions, list):
            raise ValueError("Cube rule definitions must be a JSON list.")
        profile, coverage = build_profiles(definitions, registry, sample_limit=arguments.sample_limit)
        write_json(arguments.output_root / "rule_function_profile.json", profile)
        write_json(arguments.output_root / "rule_parser_coverage.json", coverage)
        print("=" * 70)
        print("TM1 RULE FUNCTION COVERAGE")
        print("=" * 70)
        print(f"Ruled cubes          : {coverage['ruled_cube_count']:,}")
        print(f"Unique functions     : {coverage['unique_function_count']:,}")
        print(f"Unclassified         : {coverage['unclassified_function_count']:,}")
        print(f"Function occurrences : {coverage['total_function_occurrences']:,}")
        print(f"Malformed calls      : {coverage['malformed_call_count']:,}")
        print(f"Syntactic coverage   : {coverage['syntactic_coverage_percent']:.2f}%")
        print(f"Lineage coverage     : {coverage['lineage_coverage_percent']:.2f}%")
        print(f"Output               : {arguments.output_root}")
        return 0
    except Exception as error:
        print(f"Rule coverage profiling failed: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
