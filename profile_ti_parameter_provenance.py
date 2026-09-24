from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utilities.tm1_connection import get_tm1_connection

CURRENT_ROOT = ROOT_DIR / "data" / "current"
OUTPUT_FILES = {
    "parameters": "ti_process_parameters.json",
    "bindings": "ti_parameter_bindings.json",
    "aliases": "ti_parameter_aliases.json",
    "candidates": "ti_parameter_resolution_candidates.json",
    "summary": "ti_parameter_provenance_summary.json",
}
FOCUS_EXPRESSIONS = {
    "pdimension",
    "pdim",
    "pcube",
    "sattrcube",
    "stgtcube",
    "scube",
}
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ASSIGNMENT_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*;?\s*$",
    re.DOTALL,
)
QUOTED_RE = re.compile(r"^\s*(['\"])(.*?)\1\s*$", re.DOTALL)
PROCEDURE_ORDER = ("Prolog", "Metadata", "Data", "Epilog")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect TI parameter definitions, ExecuteProcess bindings, "
            "local parameter aliases, and deterministic resolution candidates."
        )
    )
    parser.add_argument(
        "--current-dir",
        type=Path,
        default=CURRENT_ROOT,
        help="Current catalog directory (default: data/current)",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def records(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        candidate = payload
    elif isinstance(payload, dict):
        candidate = None
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                candidate = value
                break
        if candidate is None:
            return []
    else:
        return []
    return [item for item in candidate if isinstance(item, dict)]


def get_case_insensitive(record: dict[str, Any], *keys: str) -> Any:
    mapping = {str(key).casefold(): value for key, value in record.items()}
    for key in keys:
        value = mapping.get(key.casefold())
        if value is not None:
            return value
    return None


def normalize_parameter(process_name: str, raw: Any, position: int) -> dict[str, Any]:
    if isinstance(raw, dict):
        name = get_case_insensitive(raw, "Name", "name")
        prompt = get_case_insensitive(raw, "Prompt", "prompt")
        value = get_case_insensitive(raw, "Value", "value")
        parameter_type = get_case_insensitive(raw, "Type", "type")
    else:
        name = getattr(raw, "name", None) or getattr(raw, "Name", None)
        prompt = getattr(raw, "prompt", None) or getattr(raw, "Prompt", None)
        value = getattr(raw, "value", None)
        if value is None:
            value = getattr(raw, "Value", None)
        parameter_type = getattr(raw, "type", None) or getattr(raw, "Type", None)

    name_text = str(name or "").strip()
    return {
        "process_name": process_name,
        "parameter_name": name_text,
        "parameter_key": normalized(name_text),
        "parameter_type": str(parameter_type or "UNKNOWN").strip(),
        "prompt": str(prompt or "").strip(),
        "default_value": value,
        "has_nonempty_default": value is not None and str(value).strip() != "",
        "position": position,
    }


def extract_parameters(process: Any, process_name: str) -> list[dict[str, Any]]:
    raw_parameters = getattr(process, "parameters", None)
    if raw_parameters is None:
        raw_parameters = getattr(process, "_parameters", None)
    if raw_parameters is None:
        return []
    return [
        normalize_parameter(process_name, raw, position)
        for position, raw in enumerate(raw_parameters, start=1)
        if str(
            get_case_insensitive(raw, "Name", "name")
            if isinstance(raw, dict)
            else getattr(raw, "name", None) or getattr(raw, "Name", None) or ""
        ).strip()
    ]


def get_procedures(process: Any) -> dict[str, str]:
    candidates = {
        "Prolog": ("prolog_procedure", "PrologProcedure"),
        "Metadata": ("metadata_procedure", "MetadataProcedure"),
        "Data": ("data_procedure", "DataProcedure"),
        "Epilog": ("epilog_procedure", "EpilogProcedure"),
    }
    result: dict[str, str] = {}
    for procedure, names in candidates.items():
        value = ""
        for name in names:
            if hasattr(process, name):
                value = getattr(process, name) or ""
                break
        result[procedure] = str(value)
    return result


def mask_comments(text: str) -> str:
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
            if i < len(chars) - 1:
                chars[i] = chars[i + 1] = " "
                i += 2
            continue
        i += 1
    return "".join(chars)


def split_arguments(text: str) -> list[str]:
    values: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            if char == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    i += 2
                    continue
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            values.append(text[start:i].strip())
            start = i + 1
        i += 1
    tail = text[start:].strip()
    if tail or values:
        values.append(tail)
    return values


def iter_function_calls(text: str, function_names: set[str]) -> Iterator[dict[str, Any]]:
    masked = mask_comments(text)
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(name) for name in sorted(function_names, key=len, reverse=True)) + r")\s*\(",
        re.IGNORECASE,
    )
    for match in pattern.finditer(masked):
        opening = masked.find("(", match.start())
        depth = 1
        quote: str | None = None
        i = opening + 1
        while i < len(masked) and depth:
            char = masked[i]
            if quote:
                if char == quote:
                    if i + 1 < len(masked) and masked[i + 1] == quote:
                        i += 2
                        continue
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            i += 1
        if depth:
            continue
        end = i
        yield {
            "function_name": match.group(1),
            "arguments": split_arguments(text[opening + 1 : end - 1]),
            "line_number": text.count("\n", 0, match.start()) + 1,
            "raw_expression": text[match.start() : end].strip(),
        }


def unquote(expression: str) -> str | None:
    match = QUOTED_RE.match(expression.strip())
    if not match:
        return None
    quote = match.group(1)
    return match.group(2).replace(quote * 2, quote)


def split_concatenation(expression: str) -> list[str] | None:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(expression):
        char = expression[i]
        if quote:
            if char == quote:
                if i + 1 < len(expression) and expression[i + 1] == quote:
                    i += 2
                    continue
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "|" and depth == 0:
            parts.append(expression[start:i].strip())
            start = i + 1
        i += 1
    if not parts:
        return None
    parts.append(expression[start:].strip())
    return parts


def expression_dependencies(expression: str) -> list[str]:
    literal = unquote(expression)
    if literal is not None:
        return []
    parts = split_concatenation(expression)
    if parts:
        result: list[str] = []
        for part in parts:
            result.extend(expression_dependencies(part))
        return sorted(set(result), key=str.casefold)
    value = expression.strip().rstrip(";").strip()
    return [value] if IDENTIFIER_RE.match(value) else []


def resolve_expression(
    expression: str,
    values: dict[str, str],
) -> tuple[str | None, str, list[str]]:
    literal = unquote(expression)
    if literal is not None:
        return literal, "CONFIRMED_LITERAL", []
    parts = split_concatenation(expression)
    if parts:
        resolved_parts: list[str] = []
        dependencies: list[str] = []
        for part in parts:
            value, _, deps = resolve_expression(part, values)
            dependencies.extend(deps)
            if value is None:
                return None, "UNRESOLVED_CONCATENATION", sorted(set(dependencies))
            resolved_parts.append(value)
        return "".join(resolved_parts), "RESOLVED_CONCATENATION", sorted(set(dependencies))
    token = expression.strip().rstrip(";").strip()
    if IDENTIFIER_RE.match(token):
        key = normalized(token)
        if key in values:
            return values[key], "RESOLVED_SYMBOL", [token]
        return None, "UNRESOLVED_SYMBOL", [token]
    return None, "UNRESOLVED_EXPRESSION", expression_dependencies(expression)


def iter_statements(code: str) -> Iterator[tuple[str, int]]:
    masked = mask_comments(code)
    start = 0
    quote: str | None = None
    depth = 0
    line = 1
    statement_line = 1
    i = 0
    while i < len(masked):
        char = masked[i]
        if char == "\n":
            line += 1
        if quote:
            if char == quote:
                if i + 1 < len(masked) and masked[i + 1] == quote:
                    i += 2
                    continue
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            statement = code[start : i + 1].strip()
            if statement:
                yield statement, statement_line
            start = i + 1
            statement_line = line
        i += 1
    tail = code[start:].strip()
    if tail:
        yield tail, statement_line


def collect_aliases(
    process_name: str,
    procedures: dict[str, str],
    parameter_values: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    values = dict(parameter_values)
    aliases: list[dict[str, Any]] = []
    for procedure in PROCEDURE_ORDER:
        code = procedures.get(procedure, "")
        for statement, line_number in iter_statements(code):
            match = ASSIGNMENT_RE.match(statement)
            if not match:
                continue
            variable = match.group(1)
            expression = match.group(2).strip().rstrip(";").strip()
            dependencies = expression_dependencies(expression)
            if not dependencies:
                literal = unquote(expression)
                if literal is not None:
                    values[normalized(variable)] = literal
                continue
            if not any(normalized(dep) in FOCUS_EXPRESSIONS or normalized(variable) in FOCUS_EXPRESSIONS for dep in dependencies):
                continue
            resolved_value, status, resolved_dependencies = resolve_expression(expression, values)
            if resolved_value is not None:
                values[normalized(variable)] = resolved_value
            aliases.append(
                {
                    "process_name": process_name,
                    "procedure": procedure,
                    "line_number": line_number,
                    "variable_name": variable,
                    "expression": expression,
                    "dependencies": dependencies,
                    "resolved_dependencies": resolved_dependencies,
                    "resolved_value": resolved_value,
                    "resolution_status": status,
                    "focus_expression": normalized(variable) in FOCUS_EXPRESSIONS,
                }
            )
    return aliases, values


def collect_bindings(
    caller: str,
    procedures: dict[str, str],
    symbol_values: dict[str, str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for procedure in PROCEDURE_ORDER:
        code = procedures.get(procedure, "")
        for call in iter_function_calls(code, {"EXECUTEPROCESS", "RUNPROCESS"}):
            arguments = call["arguments"]
            if not arguments:
                continue
            called_value, called_status, called_deps = resolve_expression(arguments[0], symbol_values)
            parameter_pairs = arguments[1:]
            for offset in range(0, len(parameter_pairs) - 1, 2):
                name_expression = parameter_pairs[offset]
                value_expression = parameter_pairs[offset + 1]
                parameter_name, name_status, _ = resolve_expression(name_expression, symbol_values)
                resolved_value, value_status, value_deps = resolve_expression(value_expression, symbol_values)
                result.append(
                    {
                        "calling_process": caller,
                        "called_process_expression": arguments[0],
                        "called_process": called_value,
                        "called_process_resolution": called_status,
                        "called_process_dependencies": called_deps,
                        "procedure": procedure,
                        "line_number": call["line_number"],
                        "function_name": call["function_name"].upper(),
                        "parameter_name_expression": name_expression,
                        "parameter_name": parameter_name,
                        "parameter_name_resolution": name_status,
                        "argument_expression": value_expression,
                        "resolved_value": resolved_value,
                        "argument_resolution": value_status,
                        "argument_dependencies": value_deps,
                        "raw_expression": call["raw_expression"],
                    }
                )
    return result


def validation_expression(record: dict[str, Any]) -> str:
    value = record.get("target_expression")
    if value is not None and str(value).strip():
        return str(value).strip()
    many = record.get("target_expressions")
    if isinstance(many, list) and many:
        return " | ".join(str(item).strip() for item in many if str(item).strip())
    return ""


def build_resolution_candidates(
    validations: list[dict[str, Any]],
    parameter_defaults: dict[tuple[str, str], str],
    bindings: list[dict[str, Any]],
    aliases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    binding_values: dict[tuple[str, str], set[str]] = defaultdict(set)
    binding_sources: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for binding in bindings:
        called_process = normalized(binding.get("called_process"))
        parameter_name = normalized(binding.get("parameter_name"))
        resolved_value = binding.get("resolved_value")
        if called_process and parameter_name and resolved_value is not None and str(resolved_value).strip():
            key = (called_process, parameter_name)
            binding_values[key].add(str(resolved_value).strip())
            binding_sources[key].append(binding)

    alias_by_process: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for alias in aliases:
        alias_by_process[(normalized(alias.get("process_name")), normalized(alias.get("variable_name")))].append(alias)

    candidates: list[dict[str, Any]] = []
    for validation in validations:
        status = str(validation.get("validation_status") or validation.get("status") or "").upper()
        if status != "UNRESOLVED_DYNAMIC_REFERENCE":
            continue
        process_name = str(validation.get("process_name") or validation.get("source_name") or "").strip()
        expression = validation_expression(validation)
        expression_key = normalized(expression)
        if expression_key not in FOCUS_EXPRESSIONS:
            continue
        key = (normalized(process_name), expression_key)
        values: set[str] = set()
        sources: list[dict[str, Any]] = []

        default_value = parameter_defaults.get(key)
        if default_value is not None:
            values.add(default_value)
            sources.append({"source_kind": "PARAMETER_DEFAULT", "value": default_value})

        for value in binding_values.get(key, set()):
            values.add(value)
        for binding in binding_sources.get(key, []):
            sources.append(
                {
                    "source_kind": "EXECUTE_PROCESS_ARGUMENT",
                    "calling_process": binding["calling_process"],
                    "procedure": binding["procedure"],
                    "line_number": binding["line_number"],
                    "value": binding["resolved_value"],
                }
            )

        for alias in alias_by_process.get(key, []):
            if alias.get("resolved_value") is not None:
                values.add(str(alias["resolved_value"]))
                sources.append(
                    {
                        "source_kind": "LOCAL_ALIAS",
                        "procedure": alias["procedure"],
                        "line_number": alias["line_number"],
                        "value": alias["resolved_value"],
                        "expression": alias["expression"],
                    }
                )

        if not values:
            resolution_status = "NO_STATIC_CANDIDATE"
        elif len(values) == 1:
            resolution_status = "SINGLE_STATIC_CANDIDATE"
        else:
            resolution_status = "FINITE_CANDIDATE_SET"

        candidates.append(
            {
                "process_name": process_name,
                "relationship_type": validation.get("relationship_type"),
                "target_type": validation.get("target_type") or validation.get("target_object_type"),
                "target_expression": expression,
                "candidate_values": sorted(values, key=str.casefold),
                "candidate_count": len(values),
                "resolution_status": resolution_status,
                "provenance": sources,
            }
        )
    return candidates


def main() -> int:
    args = parse_args()
    current_dir = args.current_dir
    validation_path = current_dir / "ti_relationship_validations.json"
    if not validation_path.is_file():
        print(f"Missing validation file: {validation_path}")
        return 1

    process_parameters: list[dict[str, Any]] = []
    parameter_bindings: list[dict[str, Any]] = []
    parameter_aliases: list[dict[str, Any]] = []
    parameter_defaults: dict[tuple[str, str], str] = {}
    process_count = 0
    collected_at = utc_now()

    with get_tm1_connection() as tm1:
        process_names = sorted(tm1.processes.get_all_names(), key=str.casefold)
        for process_name in process_names:
            process_count += 1
            process = tm1.processes.get(process_name)
            parameters = extract_parameters(process, process_name)
            process_parameters.extend(parameters)
            defaults = {
                parameter["parameter_key"]: str(parameter["default_value"]).strip()
                for parameter in parameters
                if parameter["has_nonempty_default"]
            }
            for parameter in parameters:
                if parameter["has_nonempty_default"]:
                    parameter_defaults[(normalized(process_name), parameter["parameter_key"])] = str(parameter["default_value"]).strip()
            procedures = get_procedures(process)
            aliases, symbol_values = collect_aliases(process_name, procedures, defaults)
            parameter_aliases.extend(aliases)
            parameter_bindings.extend(
                collect_bindings(process_name, procedures, symbol_values)
            )

    validations = records(read_json(validation_path), "validations", "records", "items", "data", "results")
    candidates = build_resolution_candidates(
        validations,
        parameter_defaults,
        parameter_bindings,
        parameter_aliases,
    )

    summary = {
        "collected_at": collected_at,
        "process_count": process_count,
        "parameter_count": len(process_parameters),
        "nonempty_default_count": sum(item["has_nonempty_default"] for item in process_parameters),
        "binding_count": len(parameter_bindings),
        "resolved_binding_count": sum(bool(item.get("resolved_value")) for item in parameter_bindings),
        "alias_count": len(parameter_aliases),
        "candidate_record_count": len(candidates),
        "candidate_status_counts": dict(Counter(item["resolution_status"] for item in candidates)),
        "focus_expression_counts": dict(Counter(normalized(validation_expression(item)) for item in validations if normalized(validation_expression(item)) in FOCUS_EXPRESSIONS)),
    }

    write_json(current_dir / OUTPUT_FILES["parameters"], process_parameters)
    write_json(current_dir / OUTPUT_FILES["bindings"], parameter_bindings)
    write_json(current_dir / OUTPUT_FILES["aliases"], parameter_aliases)
    write_json(current_dir / OUTPUT_FILES["candidates"], candidates)
    write_json(current_dir / OUTPUT_FILES["summary"], summary)

    print("=" * 72)
    print("TM1 TI PARAMETER PROVENANCE")
    print("=" * 72)
    for key, value in summary.items():
        print(f"{key}={json.dumps(value, sort_keys=True) if isinstance(value, dict) else value}")
    for logical_name, file_name in OUTPUT_FILES.items():
        print(f"{logical_name}_file={current_dir / file_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
