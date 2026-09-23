from __future__ import annotations

"""TM1 rule dependency parser.

This module provides a dependency-parser skeleton for Planning Analytics/TM1
rule text. It deliberately separates lexical extraction, function handling,
evidence creation, relationship summarization, and catalog validation so that
coverage can be expanded without rewriting the parser.
"""

from dataclasses import asdict, dataclass
from enum import Enum
import re
from typing import Any, Iterable, Iterator, Mapping, Sequence


class RelationshipType(str, Enum):
    READS_FROM = "READS_FROM"
    FEEDS = "FEEDS"
    USES_ATTRIBUTE = "USES_ATTRIBUTE"
    REFERENCES_DIMENSION = "REFERENCES_DIMENSION"
    REFERENCES_HIERARCHY = "REFERENCES_HIERARCHY"


class TargetObjectType(str, Enum):
    CUBE = "CUBE"
    DIMENSION = "DIMENSION"
    HIERARCHY = "HIERARCHY"
    ATTRIBUTE = "ATTRIBUTE"
    UNKNOWN = "UNKNOWN"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True)
class FunctionSpec:
    name: str
    relationship_type: RelationshipType
    target_object_type: TargetObjectType
    target_argument: int = 0
    attribute_argument: int | None = None
    hierarchy_argument: int | None = None


@dataclass(frozen=True)
class FunctionCall:
    name: str
    arguments: tuple[str, ...]
    start_offset: int
    end_offset: int
    line_number: int
    raw_expression: str
    is_feeder: bool


@dataclass(frozen=True)
class RuleRelationshipEvidence:
    source_cube: str
    relationship_type: str
    target_object_type: str
    target_name: str | None
    function_name: str
    line_number: int
    expression: str
    is_feeder: bool
    confidence: str
    target_expression: str | None = None
    dimension_name: str | None = None
    hierarchy_name: str | None = None
    attribute_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuleRelationship:
    source_cube: str
    relationship_type: str
    target_object_type: str
    target_name: str | None
    function_name: str
    evidence_count: int
    first_line: int
    confidence: str
    dimension_name: str | None = None
    hierarchy_name: str | None = None
    attribute_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Initial registry. Add functions here after the formal function-coverage review.
DEFAULT_FUNCTION_SPECS: dict[str, FunctionSpec] = {
    "DB": FunctionSpec("DB", RelationshipType.READS_FROM, TargetObjectType.CUBE),
    "ATTRS": FunctionSpec(
        "ATTRS", RelationshipType.USES_ATTRIBUTE, TargetObjectType.ATTRIBUTE,
        target_argument=0, attribute_argument=2,
    ),
    "ATTRN": FunctionSpec(
        "ATTRN", RelationshipType.USES_ATTRIBUTE, TargetObjectType.ATTRIBUTE,
        target_argument=0, attribute_argument=2,
    ),
    "DIMIX": FunctionSpec("DIMIX", RelationshipType.REFERENCES_DIMENSION, TargetObjectType.DIMENSION),
    "DIMNM": FunctionSpec("DIMNM", RelationshipType.REFERENCES_DIMENSION, TargetObjectType.DIMENSION),
    "DIMSIZ": FunctionSpec("DIMSIZ", RelationshipType.REFERENCES_DIMENSION, TargetObjectType.DIMENSION),
    "ELPAR": FunctionSpec("ELPAR", RelationshipType.REFERENCES_HIERARCHY, TargetObjectType.HIERARCHY),
    "ELCOMP": FunctionSpec("ELCOMP", RelationshipType.REFERENCES_HIERARCHY, TargetObjectType.HIERARCHY),
    "ELISANC": FunctionSpec("ELISANC", RelationshipType.REFERENCES_HIERARCHY, TargetObjectType.HIERARCHY),
    "ELLEV": FunctionSpec("ELLEV", RelationshipType.REFERENCES_HIERARCHY, TargetObjectType.HIERARCHY),
    "ELCOMPN": FunctionSpec("ELCOMPN", RelationshipType.REFERENCES_DIMENSION,TargetObjectType.DIMENSION, target_argument=0,),
    "CONSOLIDATEDMAX": FunctionSpec( "CONSOLIDATEDMAX", RelationshipType.READS_FROM, TargetObjectType.CUBE, target_argument=1,),
    "CONSOLIDATEDAVG": FunctionSpec( "CONSOLIDATEDAVG", RelationshipType.READS_FROM, TargetObjectType.CUBE, target_argument=1,),
    "CONSOLIDATECHILDREN": FunctionSpec( "CONSOLIDATECHILDREN",  RelationshipType.REFERENCES_DIMENSION,TargetObjectType.DIMENSION,target_argument=0,),
}

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LITERAL_RE = re.compile(r"^\s*(['\"])(.*?)\1\s*$", re.DOTALL)


def _mask_comments(text: str) -> str:
    """Replace TM1 comments with spaces while preserving offsets and lines."""
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
        if text.startswith("#", i):
            while i < len(chars) and chars[i] not in "\r\n":
                chars[i] = " "
                i += 1
            continue
        if text.startswith("//", i):
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


def split_arguments(argument_text: str) -> tuple[str, ...]:
    """Split a function argument list, preserving nested function calls."""
    arguments: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(argument_text):
        char = argument_text[i]
        if quote:
            if char == quote:
                if i + 1 < len(argument_text) and argument_text[i + 1] == quote:
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
            arguments.append(argument_text[start:i].strip())
            start = i + 1
        i += 1
    tail = argument_text[start:].strip()
    if tail or arguments:
        arguments.append(tail)
    return tuple(arguments)


def _literal_value(expression: str | None) -> str | None:
    if expression is None:
        return None
    match = _LITERAL_RE.match(expression)
    return match.group(2).replace(match.group(1) * 2, match.group(1)) if match else None


def _is_feeder_at(text: str, offset: int) -> bool:
    statement_start = max(text.rfind(";", 0, offset), text.rfind("\n", 0, offset)) + 1
    statement_end = text.find(";", offset)
    if statement_end < 0:
        statement_end = len(text)
    return "=>" in text[statement_start:statement_end]


def iter_function_calls(rule_text: str, function_names: Iterable[str]) -> Iterator[FunctionCall]:
    """Yield registered TM1 function calls with balanced-parenthesis parsing."""
    masked = _mask_comments(rule_text)
    selected = {name.upper() for name in function_names}
    for match in _IDENTIFIER_RE.finditer(masked):
        name = match.group(0).upper()
        if name not in selected:
            continue
        opening = match.end()
        while opening < len(masked) and masked[opening].isspace():
            opening += 1
        if opening >= len(masked) or masked[opening] != "(":
            continue
        closing = _find_matching_parenthesis(masked, opening)
        if closing is None:
            continue
        raw = rule_text[match.start():closing + 1]
        yield FunctionCall(
            name=name,
            arguments=split_arguments(rule_text[opening + 1:closing]),
            start_offset=match.start(),
            end_offset=closing + 1,
            line_number=rule_text.count("\n", 0, match.start()) + 1,
            raw_expression=raw.strip(),
            is_feeder=_is_feeder_at(masked, match.start()),
        )


def parse_rules(
    cube_name: str,
    rule_text: str,
    function_specs: Mapping[str, FunctionSpec] | None = None,
) -> list[RuleRelationshipEvidence]:
    """Parse rule text into relationship evidence records."""
    specs = {key.upper(): value for key, value in (function_specs or DEFAULT_FUNCTION_SPECS).items()}
    evidence: list[RuleRelationshipEvidence] = []
    for call in iter_function_calls(rule_text, specs):
        spec = specs[call.name]
        target_expression = (
            call.arguments[spec.target_argument]
            if len(call.arguments) > spec.target_argument else None
        )
        target_name = _literal_value(target_expression)
        relationship = spec.relationship_type
        if call.name == "DB" and call.is_feeder:
            relationship = RelationshipType.FEEDS
        attribute_name = (
            _literal_value(call.arguments[spec.attribute_argument])
            if spec.attribute_argument is not None and len(call.arguments) > spec.attribute_argument
            else None
        )
        hierarchy_name = (
            _literal_value(call.arguments[spec.hierarchy_argument])
            if spec.hierarchy_argument is not None and len(call.arguments) > spec.hierarchy_argument
            else None
        )
        confidence = Confidence.HIGH if target_name else Confidence.MEDIUM
        evidence.append(RuleRelationshipEvidence(
            source_cube=cube_name,
            relationship_type=relationship.value,
            target_object_type=spec.target_object_type.value,
            target_name=target_name,
            target_expression=target_expression,
            function_name=call.name,
            line_number=call.line_number,
            expression=call.raw_expression,
            is_feeder=call.is_feeder,
            confidence=confidence.value,
            dimension_name=target_name if spec.target_object_type in {TargetObjectType.DIMENSION, TargetObjectType.HIERARCHY, TargetObjectType.ATTRIBUTE} else None,
            hierarchy_name=hierarchy_name,
            attribute_name=attribute_name,
        ))
    return evidence


def summarize_relationships(
    evidence: Sequence[RuleRelationshipEvidence],
) -> list[RuleRelationship]:
    """Collapse repeated evidence while preserving traceability counts."""
    grouped: dict[tuple[Any, ...], list[RuleRelationshipEvidence]] = {}
    for item in evidence:
        key = (
            item.source_cube, item.relationship_type, item.target_object_type,
            item.target_name, item.function_name, item.dimension_name,
            item.hierarchy_name, item.attribute_name,
        )
        grouped.setdefault(key, []).append(item)
    results = []
    for key, records in grouped.items():
        first = min(record.line_number for record in records)
        confidence = min((record.confidence for record in records), key=lambda value: list(Confidence).index(Confidence(value)))
        results.append(RuleRelationship(
            source_cube=key[0], relationship_type=key[1], target_object_type=key[2],
            target_name=key[3], function_name=key[4], evidence_count=len(records),
            first_line=first, confidence=confidence, dimension_name=key[5],
            hierarchy_name=key[6], attribute_name=key[7],
        ))
    return sorted(results, key=lambda item: (item.source_cube.casefold(), item.first_line, item.function_name))


def normalized_key(value: Any) -> str:
    """Return a case-insensitive, whitespace-normalized catalog key."""
    return " ".join(
        str(value or "")
        .strip()
        .casefold()
        .split()
    )


def _catalog_records(
    object_catalog: Any,
) -> list[dict[str, Any]]:
    """Normalize a top-level object list or catalog wrapper."""

    if object_catalog is None:
        return []

    if isinstance(object_catalog, list):
        return [
            record
            for record in object_catalog
            if isinstance(record, dict)
        ]

    if isinstance(object_catalog, dict):
        candidate_keys: tuple[str, ...] = (
            "objects",
            "items",
            "records",
            "data",
            "results",
        )

        for key in candidate_keys:
            candidate = object_catalog.get(key)

            if isinstance(candidate, list):
                return [
                    record
                    for record in candidate
                    if isinstance(record, dict)
                ]

    return []


def _catalog_value(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def validate_relationships(
    relationships: Sequence[RuleRelationship],
    object_catalog: Iterable[dict[str, Any]] | Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Validate rule targets using the same resolved-target precedence.

    Rule literals have HIGH confidence and are catalog-checkable. MEDIUM/LOW
    records represent unresolved expressions. Attribute and hierarchy targets
    stay outside cross-checking while the attribute inventory is deferred.
    """
    catalog_index: dict[tuple[str, str], dict[str, Any]] = {}
    for record in _catalog_records(object_catalog):
        object_type = normalized_key(
            _catalog_value(record, "object_type", "ObjectType", "type")
        )
        object_name = normalized_key(
            _catalog_value(record, "object_name", "ObjectName", "name")
        )
        if object_type and object_name:
            catalog_index.setdefault((object_type, object_name), record)

    non_catalog_types = {
        "attribute",
        "hierarchy",
        "element",
        "view",
        "subset",
        "file",
        "command",
    }
    validations: list[dict[str, Any]] = []

    for relationship in relationships:
        target_name = str(relationship.target_name or "").strip()
        target_type = normalized_key(relationship.target_object_type)
        catalog_match_name = ""

        if not target_name or relationship.confidence != Confidence.HIGH.value:
            status = "PENDING_DYNAMIC_RESOLUTION"
            message = "The target is computed at runtime."
        elif target_type in non_catalog_types:
            status = "NOT_CROSS_CHECKED"
            message = (
                "The target was extracted, but this target type is not "
                "included in the current object catalog."
            )
        else:
            match = catalog_index.get(
                (target_type, normalized_key(target_name))
            )
            if match is not None:
                status = "VALID"
                catalog_match_name = str(
                    _catalog_value(
                        match,
                        "object_name",
                        "ObjectName",
                        "name",
                    )
                    or target_name
                )
                message = (
                    "The extracted target matched an object in objects.json."
                )
            else:
                status = "BROKEN_REFERENCE"
                message = (
                    "The extracted target does not exist in objects.json."
                )

        validations.append(
            {
                "source_cube": relationship.source_cube,
                "relationship_type": relationship.relationship_type,
                "target_name": relationship.target_name,
                "target_object_type": relationship.target_object_type,
                "confidence": relationship.confidence,
                "catalog_match_name": catalog_match_name,
                "status": status,
                "message": message,
            }
        )

    return validations
