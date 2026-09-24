from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

DEPENDABLE_CONFIDENCES = {"CONFIRMED", "RESOLVED", "HIGH"}
DYNAMIC_STATUS_BY_TARGET_TYPE = {
    "view": "RUNTIME_VIEW_REFERENCE",
    "subset": "RUNTIME_SUBSET_REFERENCE",
    "file": "DYNAMIC_EXTERNAL_FILE",
    "command": "DYNAMIC_EXTERNAL_COMMAND",
    "attribute": "ATTRIBUTE_CATALOG_DEFERRED",
}
DEFERRED_STATUS_BY_TARGET_TYPE = {
    "attribute": "ATTRIBUTE_CATALOG_DEFERRED",
    "hierarchy": "HIERARCHY_CATALOG_DEFERRED",
    "element": "ELEMENT_CATALOG_DEFERRED",
    "view": "NOT_CROSS_CHECKED",
    "subset": "NOT_CROSS_CHECKED",
    "file": "NOT_CROSS_CHECKED",
    "command": "NOT_CROSS_CHECKED",
}


def normalized_key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def records_from_payload(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [record for record in payload if isinstance(record, dict)]
    if isinstance(payload, dict):
        for key in keys:
            candidate: Any = payload.get(key)
            if isinstance(candidate, list):
                return [record for record in candidate if isinstance(record, dict)]
    return []


def first_value(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        candidate = record.get(key)
        if candidate is not None and str(candidate).strip():
            return candidate
    return None


def build_catalog_index(object_catalog: Any) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records_from_payload(
        object_catalog,
        ("objects", "items", "records", "data", "results"),
    ):
        object_type = normalized_key(
            first_value(record, "object_type", "ObjectType", "type")
        )
        object_name = normalized_key(
            first_value(record, "object_name", "ObjectName", "name")
        )
        if object_type and object_name:
            index.setdefault((object_type, object_name), record)
    return index


def build_exception_index(exception_payload: Any) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records_from_payload(
        exception_payload,
        ("exceptions", "items", "records", "data", "results"),
    ):
        if normalized_key(record.get("exception_status")) not in {
            "registered",
            "active",
        }:
            continue
        target_type = normalized_key(record.get("target_type"))
        target_name = normalized_key(record.get("target_name"))
        if target_type and target_name:
            index.setdefault((target_type, target_name), record)
    return index


@dataclass(frozen=True)
class ValidationDecision:
    status: str
    reason: str
    catalog_match_name: str = ""
    exception_id: str = ""
    exception_status: str = ""
    exclude_from_quality_score: bool = False
    required_for_holistic_success: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_match_name": self.catalog_match_name,
            "validation_status": self.status,
            "reason": self.reason,
            "exception_id": self.exception_id,
            "exception_status": self.exception_status,
            "exclude_from_quality_score": self.exclude_from_quality_score,
            "required_for_holistic_success": self.required_for_holistic_success,
        }


def classify_target(
    *,
    target_type: Any,
    target_name: Any,
    confidence: Any,
    catalog_index: dict[tuple[str, str], dict[str, Any]],
    exception_index: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> ValidationDecision:
    type_key = normalized_key(target_type)
    name_text = str(target_name or "").strip()
    name_key = normalized_key(name_text)
    confidence_key = str(confidence or "").strip().upper()
    dependable = bool(name_text) and confidence_key in DEPENDABLE_CONFIDENCES

    if not dependable:
        status = DYNAMIC_STATUS_BY_TARGET_TYPE.get(
            type_key,
            "UNRESOLVED_DYNAMIC_REFERENCE",
        )
        reason = {
            "RUNTIME_VIEW_REFERENCE": "The view name is computed at runtime.",
            "RUNTIME_SUBSET_REFERENCE": "The subset name is computed at runtime.",
            "DYNAMIC_EXTERNAL_FILE": "The external file path is computed at runtime.",
            "DYNAMIC_EXTERNAL_COMMAND": "The operating-system command is computed at runtime.",
            "ATTRIBUTE_CATALOG_DEFERRED": "The attribute target is unresolved and attribute catalog collection is deferred.",
        }.get(status, "The target could not be resolved to a dependable catalog name.")
        return ValidationDecision(status=status, reason=reason)

    if type_key in DEFERRED_STATUS_BY_TARGET_TYPE:
        status = DEFERRED_STATUS_BY_TARGET_TYPE[type_key]
        reason = (
            "The target was resolved, but this target type is outside the current object catalog scope."
        )
        return ValidationDecision(status=status, reason=reason)

    match = catalog_index.get((type_key, name_key))
    if match is not None:
        canonical_name = str(
            first_value(match, "object_name", "ObjectName", "name") or name_text
        )
        return ValidationDecision(
            status="VALID",
            reason="The resolved target matched an object in objects.json.",
            catalog_match_name=canonical_name,
        )

    if confidence_key in {"CONFIRMED", "HIGH"}:
        status = "BROKEN_REFERENCE"
        reason = "The confirmed literal target does not exist in objects.json."
    else:
        status = "RESOLVED_TARGET_NOT_IN_CATALOG"
        reason = (
            "The variable-backed target was resolved, but the resolved name does not exist in objects.json."
        )

    exception = (exception_index or {}).get((type_key, name_key))
    if exception is None:
        return ValidationDecision(status=status, reason=reason)

    return ValidationDecision(
        status=status,
        reason=reason,
        exception_id=str(exception.get("exception_id") or ""),
        exception_status=str(exception.get("exception_status") or ""),
        exclude_from_quality_score=bool(
            exception.get("exclude_from_quality_score", False)
        ),
        required_for_holistic_success=bool(
            exception.get("required_for_holistic_success", True)
        ),
    )
