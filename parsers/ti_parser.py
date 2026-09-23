from __future__ import annotations

import re
from dataclasses import asdict, dataclass,field
from typing import Any, Iterable

from parsers.catalog_validation import (
    build_catalog_index,
    build_exception_index,
    classify_target,
)


# ============================================================
# Relationship Models
# ============================================================

@dataclass(frozen=True)
class RelationshipEvidence:
    process_name: str
    procedure: str
    line_number: int
    function_name: str
    relationship_type: str
    target_type: str
    target_name: str
    target_expression: str
    confidence: str
    code_reference: str
    attribute_name: str | None = None
    target_measure: str | None = None
    sequence: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RelationshipSummary:
    process_name: str
    relationship_type: str
    target_type: str
    target_name: str | None
    confidence: str
    reference_count: int
    procedures: list[str]
    functions: list[str]
    evidence_lines: list[int]

    target_expression: str | None = None
    target_expressions: list[str] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["procedures"] = list(self.procedures)
        result["functions"] = list(self.functions)
        result["evidence_lines"] = list(self.evidence_lines)
        return result


# ============================================================
# Supported TI Functions
# ============================================================

FUNCTION_SPECS: dict[str, dict[str, Any]] = {
    # Cube reads
    "CELLGETN": {
        "relationship": "READS_FROM_CUBE",
        "target_type": "cube",
        "target_arg": 0,
    },
    "CELLGETS": {
        "relationship": "READS_FROM_CUBE",
        "target_type": "cube",
        "target_arg": 0,
    },
    # Cube writes
    "CELLPUTN": {
        "relationship": "WRITES_TO_CUBE",
        "target_type": "cube",
        "target_arg": 1,
    },
    "CELLPUTS": {
        "relationship": "WRITES_TO_CUBE",
        "target_type": "cube",
        "target_arg": 1,
    },
    "CELLINCREMENTN": {
        "relationship": "INCREMENTS_CUBE",
        "target_type": "cube",
        "target_arg": 1,
    },
    "CELLPUTPROPORTIONALSPREAD": {
        "relationship": "SPREADS_TO_CUBE",
        "target_type": "cube",
        "target_arg": 1,
    },
    # Attribute reads
    "ATTRS": {
        "relationship": "READS_ATTRIBUTE",
        "target_type": "attribute",
        "target_arg": 0,
        "attribute_arg": 2,
    },
    "ATTRN": {
        "relationship": "READS_ATTRIBUTE",
        "target_type": "attribute",
        "target_arg": 0,
        "attribute_arg": 2,
    },
    # Attribute writes
    "ATTRPUTS": {
        "relationship": "WRITES_ATTRIBUTE",
        "target_type": "attribute",
        "target_arg": 1,
        "attribute_arg": 3,
    },
    "ATTRPUTN": {
        "relationship": "WRITES_ATTRIBUTE",
        "target_type": "attribute",
        "target_arg": 1,
        "attribute_arg": 3,
    },
    "ATTRINSERT": {
        "relationship": "CREATES_ATTRIBUTE",
        "target_type": "attribute",
        "target_arg": 0,
        "attribute_arg": 2,
    },
    "ATTRDELETE": {
        "relationship": "DELETES_ATTRIBUTE",
        "target_type": "attribute",
        "target_arg": 0,
        "attribute_arg": 1,
    },
    # Process calls
    "EXECUTEPROCESS": {
        "relationship": "CALLS_PROCESS",
        "target_type": "process",
        "target_arg": 0,
    },
    "RUNPROCESS": {
        "relationship": "CALLS_PROCESS",
        "target_type": "process",
        "target_arg": 0,
    },
    # Clears
    "VIEWZEROOUT": {
        "relationship": "CLEARS_CUBE",
        "target_type": "cube",
        "target_arg": 0,
        "secondary": {
            "relationship": "USES_VIEW",
            "target_type": "view",
            "target_arg": 1,
            "parent_arg": 0,
        },
    },
    "CUBECLEARDATA": {
        "relationship": "CLEARS_CUBE",
        "target_type": "cube",
        "target_arg": 0,
    },
    # Dimension and hierarchy maintenance
    "DIMENSIONELEMENTINSERT": {
        "relationship": "UPDATES_DIMENSION",
        "target_type": "dimension",
        "target_arg": 0,
    },
    "DIMENSIONELEMENTDELETE": {
        "relationship": "UPDATES_DIMENSION",
        "target_type": "dimension",
        "target_arg": 0,
    },
    "DIMENSIONELEMENTCOMPONENTADD": {
        "relationship": "UPDATES_DIMENSION",
        "target_type": "dimension",
        "target_arg": 0,
    },
    "DIMENSIONELEMENTCOMPONENTDELETE": {
        "relationship": "UPDATES_DIMENSION",
        "target_type": "dimension",
        "target_arg": 0,
    },
    "HIERARCHYELEMENTINSERT": {
        "relationship": "UPDATES_HIERARCHY",
        "target_type": "hierarchy",
        "target_arg": 0,
        "hierarchy_arg": 1,
    },
    "HIERARCHYELEMENTDELETE": {
        "relationship": "UPDATES_HIERARCHY",
        "target_type": "hierarchy",
        "target_arg": 0,
        "hierarchy_arg": 1,
    },
    "HIERARCHYELEMENTCOMPONENTADD": {
        "relationship": "UPDATES_HIERARCHY",
        "target_type": "hierarchy",
        "target_arg": 0,
        "hierarchy_arg": 1,
    },
    "HIERARCHYELEMENTCOMPONENTDELETE": {
        "relationship": "UPDATES_HIERARCHY",
        "target_type": "hierarchy",
        "target_arg": 0,
        "hierarchy_arg": 1,
    },
    # Subsets
    "SUBSETCREATE": {
        "relationship": "CREATES_SUBSET",
        "target_type": "subset",
        "target_arg": 1,
        "parent_arg": 0,
    },
    "SUBSETCREATEBYMDX": {
        "relationship": "CREATES_SUBSET",
        "target_type": "subset",
        "target_arg": 0,
    },
    "SUBSETELEMENTINSERT": {
        "relationship": "UPDATES_SUBSET",
        "target_type": "subset",
        "target_arg": 1,
        "parent_arg": 0,
    },
    "SUBSETELEMENTDELETE": {
        "relationship": "UPDATES_SUBSET",
        "target_type": "subset",
        "target_arg": 1,
        "parent_arg": 0,
    },
    "SUBSETDESTROY": {
        "relationship": "DELETES_SUBSET",
        "target_type": "subset",
        "target_arg": 1,
        "parent_arg": 0,
    },
    # Views
    "VIEWCREATE": {
        "relationship": "CREATES_VIEW",
        "target_type": "view",
        "target_arg": 1,
        "parent_arg": 0,
    },
    "VIEWDESTROY": {
        "relationship": "DELETES_VIEW",
        "target_type": "view",
        "target_arg": 1,
        "parent_arg": 0,
    },
    "VIEWSUBSETASSIGN": {
        "relationship": "USES_VIEW",
        "target_type": "view",
        "target_arg": 1,
        "parent_arg": 0,
    },
    # Files and commands
    "ASCIIOUTPUT": {
        "relationship": "WRITES_FILE",
        "target_type": "file",
        "target_arg": 0,
    },
    "TEXTOUTPUT": {
        "relationship": "WRITES_FILE",
        "target_type": "file",
        "target_arg": 0,
    },
    "EXECUTECOMMAND": {
        "relationship": "EXECUTES_COMMAND",
        "target_type": "command",
        "target_arg": 0,
    },
    "EXECUTECOMMANDEX": {
        "relationship": "EXECUTES_COMMAND",
        "target_type": "command",
        "target_arg": 0,
    },
}


PROCEDURE_ORDER = {
    "Prolog": 1,
    "Metadata": 2,
    "Data": 3,
    "Epilog": 4,
}

CONFIDENCE_RANK = {
    "UNRESOLVED": 1,
    "INFERRED": 2,
    "RESOLVED": 3,
    "CONFIRMED": 4,
}


# ============================================================
# Lexical Helpers
# ============================================================

ASSIGNMENT_PATTERN = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*;?\s*$"
)
FUNCTION_PATTERN = re.compile(
    r"\b(" + "|".join(
        sorted(FUNCTION_SPECS, key=len, reverse=True)
    ) + r")\s*\(",
    re.IGNORECASE,
)
STRING_LITERAL_PATTERN = re.compile(
    r"^\s*(['\"])(.*?)\1\s*$",
    re.DOTALL,
)
IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$"
)


def strip_comments(line: str) -> str:
    """Remove trailing TI comments while preserving quoted text."""

    result: list[str] = []
    quote: str | None = None
    index = 0

    while index < len(line):
        character = line[index]

        if quote:
            result.append(character)
            if character == quote:
                if index + 1 < len(line) and line[index + 1] == quote:
                    result.append(line[index + 1])
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if character in {"'", '"'}:
            quote = character
            result.append(character)
            index += 1
            continue

        if character == "#":
            break

        result.append(character)
        index += 1

    return "".join(result).strip()


def split_arguments(argument_text: str) -> list[str]:
    """Split a TI function argument list, respecting quotes and nesting."""

    arguments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    depth = 0
    index = 0

    while index < len(argument_text):
        character = argument_text[index]

        if quote:
            current.append(character)
            if character == quote:
                if index + 1 < len(argument_text) and argument_text[index + 1] == quote:
                    current.append(argument_text[index + 1])
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if character in {"'", '"'}:
            quote = character
            current.append(character)
        elif character == "(":
            depth += 1
            current.append(character)
        elif character == ")":
            depth -= 1
            current.append(character)
        elif character == "," and depth == 0:
            arguments.append("".join(current).strip())
            current = []
        else:
            current.append(character)

        index += 1

    if current or argument_text.strip():
        arguments.append("".join(current).strip())

    return arguments


def extract_function_calls(code: str) -> list[tuple[str, str, int, int]]:
    """Return function name, argument text, and character boundaries."""

    calls: list[tuple[str, str, int, int]] = []

    for match in FUNCTION_PATTERN.finditer(code):
        function_name = match.group(1)
        open_parenthesis = match.end() - 1
        quote: str | None = None
        depth = 1
        index = open_parenthesis + 1

        while index < len(code) and depth:
            character = code[index]

            if quote:
                if character == quote:
                    if index + 1 < len(code) and code[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
                index += 1
                continue

            if character in {"'", '"'}:
                quote = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1

            index += 1

        if depth == 0:
            calls.append(
                (
                    function_name,
                    code[open_parenthesis + 1:index - 1],
                    match.start(),
                    index,
                )
            )

    return calls


# ============================================================
# Expression Resolution
# ============================================================

def unquote(expression: str) -> str | None:
    match = STRING_LITERAL_PATTERN.match(expression)
    if not match:
        return None
    return match.group(2).replace(match.group(1) * 2, match.group(1))


def split_concatenation(expression: str) -> list[str] | None:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    depth = 0

    for character in expression:
        if quote:
            current.append(character)
            if character == quote:
                quote = None
            continue

        if character in {"'", '"'}:
            quote = character
            current.append(character)
        elif character == "(":
            depth += 1
            current.append(character)
        elif character == ")":
            depth -= 1
            current.append(character)
        elif character == "|" and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(character)

    if parts:
        parts.append("".join(current).strip())
        return parts
    return None


def resolve_expression(
    expression: str,
    symbols: dict[str, str],
) -> tuple[str, str]:
    """Resolve a literal, simple variable, or resolvable concatenation."""

    normalized = expression.strip().rstrip(";").strip()

    literal = unquote(normalized)
    if literal is not None:
        return literal, "CONFIRMED"

    if IDENTIFIER_PATTERN.match(normalized):
        if normalized in symbols:
            return symbols[normalized], "RESOLVED"
        return normalized, "UNRESOLVED"

    parts = split_concatenation(normalized)
    if parts:
        resolved_parts: list[str] = []
        confidence = "RESOLVED"

        for part in parts:
            value, part_confidence = resolve_expression(part, symbols)
            if part_confidence == "UNRESOLVED":
                return normalized, "UNRESOLVED"
            resolved_parts.append(value)
            if part_confidence == "INFERRED":
                confidence = "INFERRED"

        return "".join(resolved_parts), confidence

    return normalized, "UNRESOLVED"


def update_symbol_table(
    code_line: str,
    symbols: dict[str, str],
) -> None:
    """Capture deterministic string assignments for later references."""

    match = ASSIGNMENT_PATTERN.match(code_line)
    if not match:
        return

    variable_name = match.group(1)
    expression = match.group(2)
    value, confidence = resolve_expression(expression, symbols)

    if confidence in {"CONFIRMED", "RESOLVED"}:
        symbols[variable_name] = value
    elif variable_name in symbols:
        symbols.pop(variable_name)


# ============================================================
# TI Process Parser
# ============================================================

def build_target_name(
    arguments: list[str],
    spec: dict[str, Any],
    symbols: dict[str, str],
) -> tuple[str, str, str]:
    target_index = spec["target_arg"]
    if target_index >= len(arguments):
        return "<missing argument>", "<missing argument>", "UNRESOLVED"

    target_expression = arguments[target_index]
    target_name, confidence = resolve_expression(target_expression, symbols)

    parent_index = spec.get("parent_arg")
    hierarchy_index = spec.get("hierarchy_arg")

    if parent_index is not None and parent_index < len(arguments):
        parent_name, parent_confidence = resolve_expression(
            arguments[parent_index], symbols
        )
        target_name = f"{parent_name}.{target_name}"
        if parent_confidence == "UNRESOLVED":
            confidence = "UNRESOLVED"
        elif confidence == "CONFIRMED" and parent_confidence == "RESOLVED":
            confidence = "RESOLVED"

    if hierarchy_index is not None and hierarchy_index < len(arguments):
        hierarchy_name, hierarchy_confidence = resolve_expression(
            arguments[hierarchy_index], symbols
        )
        target_name = f"{target_name}.{hierarchy_name}"
        if hierarchy_confidence == "UNRESOLVED":
            confidence = "UNRESOLVED"
        elif confidence == "CONFIRMED" and hierarchy_confidence == "RESOLVED":
            confidence = "RESOLVED"

    return target_name, target_expression, confidence


def derive_attribute_relationship(
    function_name: str,
    arguments: list[str],
    spec: dict[str, Any],
    symbols: dict[str, str],
    target_name: str,
    confidence: str,
) -> tuple[str, str, str | None, str]:
    attribute_name: str | None = None
    target_type = spec["target_type"]
    relationship_type = spec["relationship"]

    attribute_index = spec.get("attribute_arg")
    if attribute_index is not None and attribute_index < len(arguments):
        attribute_name, attribute_confidence = resolve_expression(
            arguments[attribute_index], symbols
        )
        target_name = f"{target_name}.{attribute_name}"
        if attribute_confidence == "UNRESOLVED":
            confidence = "UNRESOLVED"
        elif confidence == "CONFIRMED" and attribute_confidence == "RESOLVED":
            confidence = "RESOLVED"

    # Convert direct access to control attribute cubes into an attribute edge.
    if function_name.upper() in {"CELLGETN", "CELLGETS"} and target_name.startswith(
        "}ElementAttributes_"
    ):
        dimension_name = target_name.removeprefix("}ElementAttributes_")
        if len(arguments) >= 3:
            attribute_name, attribute_confidence = resolve_expression(
                arguments[-1], symbols
            )
            target_name = f"{dimension_name}.{attribute_name}"
            target_type = "attribute"
            relationship_type = "READS_ATTRIBUTE"
            if attribute_confidence == "UNRESOLVED":
                confidence = "UNRESOLVED"

    return target_name, target_type, attribute_name, relationship_type


def parse_process(
    process_name: str,
    procedures: dict[str, str],
) -> list[RelationshipEvidence]:
    """Parse all TI procedures and return one evidence record per reference."""

    evidence: list[RelationshipEvidence] = []
    symbols: dict[str, str] = {}
    sequence = 0

    ordered_procedures = sorted(
        procedures.items(),
        key=lambda item: PROCEDURE_ORDER.get(item[0], 99),
    )

    for procedure_name, procedure_code in ordered_procedures:
        if not procedure_code:
            continue

        for line_number, original_line in enumerate(
            procedure_code.splitlines(), start=1
        ):
            code_line = strip_comments(original_line)
            if not code_line:
                continue

            update_symbol_table(code_line, symbols)

            for function_name, argument_text, start, end in extract_function_calls(
                code_line
            ):
                sequence += 1
                spec = FUNCTION_SPECS[function_name.upper()]
                arguments = split_arguments(argument_text)

                target_name, target_expression, confidence = build_target_name(
                    arguments, spec, symbols
                )
                (
                    target_name,
                    target_type,
                    attribute_name,
                    relationship_type,
                ) = derive_attribute_relationship(
                    function_name=function_name,
                    arguments=arguments,
                    spec=spec,
                    symbols=symbols,
                    target_name=target_name,
                    confidence=confidence,
                )

                evidence.append(
                    RelationshipEvidence(
                        process_name=process_name,
                        procedure=procedure_name,
                        line_number=line_number,
                        function_name=function_name,
                        relationship_type=relationship_type,
                        target_type=target_type,
                        target_name=target_name,
                        target_expression=target_expression,
                        confidence=confidence,
                        code_reference=code_line[start:end],
                        attribute_name=attribute_name,
                        target_measure=(
                            resolve_expression(arguments[-1], symbols)[0]
                            if relationship_type
                            in {
                                "WRITES_TO_CUBE",
                                "INCREMENTS_CUBE",
                                "SPREADS_TO_CUBE",
                            }
                            and arguments
                            else None
                        ),
                        sequence=sequence,
                    )
                )

                secondary = spec.get("secondary")
                if secondary:
                    secondary_name, secondary_expression, secondary_confidence = (
                        build_target_name(arguments, secondary, symbols)
                    )
                    evidence.append(
                        RelationshipEvidence(
                            process_name=process_name,
                            procedure=procedure_name,
                            line_number=line_number,
                            function_name=function_name,
                            relationship_type=secondary["relationship"],
                            target_type=secondary["target_type"],
                            target_name=secondary_name,
                            target_expression=secondary_expression,
                            confidence=secondary_confidence,
                            code_reference=code_line[start:end],
                            sequence=sequence,
                        )
                    )

    return evidence


# ============================================================
# Relationship Aggregation and Validation
# ============================================================
def summarize_relationships(
    evidence_records: Iterable[RelationshipEvidence],
) -> list[RelationshipSummary]:
    """Aggregate evidence while preserving successful resolution.

    A variable-backed expression remains RESOLVED after the symbol resolver
    determines a concrete target. The original expression is retained for
    provenance and profiling.
    """
    groups: dict[
        tuple[str, str, str, str],
        list[RelationshipEvidence],
    ] = {}
    for evidence in evidence_records:
        key = (
            evidence.process_name,
            evidence.relationship_type,
            evidence.target_type,
            evidence.target_name,
        )
        groups.setdefault(key, []).append(evidence)

    summaries: list[RelationshipSummary] = []
    for key, records in groups.items():
        process_name, relationship_type, target_type, target_name = key
        target_expressions = sorted(
            {
                record.target_expression.strip()
                for record in records
                if record.target_expression and record.target_expression.strip()
            }
        )
        target_expression = (
            target_expressions[0]
            if len(target_expressions) == 1
            else None
        )
        summary_confidence = max(
            (record.confidence for record in records),
            key=CONFIDENCE_RANK.__getitem__,
        )
        summaries.append(
            RelationshipSummary(
                process_name=process_name,
                relationship_type=relationship_type,
                target_type=target_type,
                target_name=target_name,
                confidence=summary_confidence,
                reference_count=len(records),
                procedures=sorted({record.procedure for record in records}),
                functions=sorted({record.function_name for record in records}),
                evidence_lines=sorted({record.line_number for record in records}),
                target_expression=target_expression,
                target_expressions=target_expressions,
            )
        )

    return sorted(
        summaries,
        key=lambda item: (
            item.process_name.casefold(),
            item.relationship_type,
            (item.target_name or "").casefold(),
        ),
    )


def validate_relationships(
    summaries: Iterable[RelationshipSummary],
    object_catalog: Any,
    quality_exceptions: Any = None,
) -> list[dict[str, Any]]:
    """Validate TI relationships and apply registered quality exceptions."""
    catalog_index = build_catalog_index(object_catalog)
    exception_index = build_exception_index(quality_exceptions)
    validations: list[dict[str, Any]] = []

    for summary in summaries:
        decision = classify_target(
            target_type=summary.target_type,
            target_name=summary.target_name,
            confidence=summary.confidence,
            catalog_index=catalog_index,
            exception_index=exception_index,
        )
        validation = {
            "source_name": summary.process_name,
            "process_name": summary.process_name,
            "relationship_type": summary.relationship_type,
            "target_type": summary.target_type,
            "target_name": summary.target_name,
            "target_expression": summary.target_expression,
            "target_expressions": list(summary.target_expressions),
            "confidence": summary.confidence,
            "reference_count": summary.reference_count,
            "procedures": list(summary.procedures),
            "functions": list(summary.functions),
            "evidence_lines": list(summary.evidence_lines),
        }
        validation.update(decision.to_dict())
        validations.append(validation)

    return validations
