from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

ObjectKey = tuple[str, ...]
RelationshipKey = tuple[str, str, str, str]
NULL_OWNER = "<none>"


def clean(value: Any) -> str:
    return str(value or "").strip()


def token(value: Any) -> str:
    return clean(value).upper().replace(" ", "_")


def normalize(value: Any) -> str:
    return " ".join(clean(value).split()).casefold()


def display_owner(value: Any) -> str:
    return clean(value) or NULL_OWNER


def sha256_parts(parts: Iterable[Any]) -> str:
    material = "\x1f".join(clean(part) for part in parts)
    return hashlib.sha256(material.casefold().encode("utf-8")).hexdigest()


def canonical_key(record: dict[str, Any]) -> ObjectKey:
    kind = token(record.get("node_type") or record.get("object_type"))
    if kind in {"CUBE", "DIMENSION", "PROCESS", "CHORE"}:
        name = clean(record.get("object_name") or record.get("display_value"))
        return (kind, normalize(name)) if name else ()
    if kind == "HIERARCHY":
        return (kind, normalize(record.get("dimension_name")), normalize(record.get("hierarchy_name")))
    if kind == "ATTRIBUTE":
        return (kind, normalize(record.get("dimension_name")), normalize(record.get("hierarchy_name")), normalize(record.get("attribute_name")))
    if kind == "SUBSET":
        return (kind, normalize(record.get("dimension_name")), normalize(record.get("hierarchy_name")), token(record.get("visibility") or "PUBLIC"), normalize(display_owner(record.get("owner"))), normalize(record.get("subset_name")))
    if kind == "VIEW":
        return (kind, normalize(record.get("cube_name")), token(record.get("visibility") or "PUBLIC"), normalize(display_owner(record.get("owner"))), normalize(record.get("view_name")))
    node_id = clean(record.get("node_id"))
    return (kind or "UNKNOWN", normalize(node_id)) if node_id else ()


def canonical_id(record: dict[str, Any]) -> str:
    existing = clean(record.get("node_id"))
    if existing:
        return existing
    key = canonical_key(record)
    if not key:
        return ""
    kind = key[0].casefold()
    raw = [clean(record.get("object_name") or record.get("display_value"))]
    if kind == "attribute":
        raw = [clean(record.get("dimension_name")), clean(record.get("hierarchy_name")), clean(record.get("attribute_name"))]
    return "::".join([kind, *raw])


def configured_source_key(process_id: str, source_type: str, shared_source_id: str) -> ObjectKey:
    return ("CONFIGURED_SOURCE", normalize(process_id), token(source_type), normalize(shared_source_id))


def configured_source_id(process_id: str, source_type: str, shared_source_id: str) -> str:
    return f"configured-source::{sha256_parts(configured_source_key(process_id, source_type, shared_source_id))}"


def view_reference_key(cube_name: str, view_name: str) -> ObjectKey:
    return ("TM1_VIEW_REFERENCE", normalize(cube_name), normalize(view_name))


def subset_reference_key(dimension: str, hierarchy: str, subset: str) -> ObjectKey:
    return ("TM1_SUBSET_REFERENCE", normalize(dimension), normalize(hierarchy or dimension), normalize(subset))


def file_reference_key(path_value: str) -> ObjectKey:
    return ("FILE_REFERENCE", normalize(path_value.replace("/", "\\")))


def reference_id(key: ObjectKey) -> str:
    return f"{key[0].casefold().replace('_', '-')}::{sha256_parts(key)}"


def relationship_key(source_id: str, relationship_type: str, target_id: str, relationship_class: str) -> RelationshipKey:
    return (clean(source_id), token(relationship_type), clean(target_id), token(relationship_class))


def relationship_id(key: RelationshipKey) -> str:
    return f"object-relationship::{sha256_parts(key)}"
