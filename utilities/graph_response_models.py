from __future__ import annotations

from typing import Any


def empty_view(*, query: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "summary": {},
        "query": query or {},
        "nodes": [],
        "edges": [],
        "paths": [],
        "facets": {},
        "warnings": [],
        "page": {"offset": 0, "limit": 0, "returned": 0, "total": 0},
    }


def node_view(
    node: dict[str, Any],
    *,
    inbound_count: int,
    outbound_count: int,
) -> dict[str, Any]:
    node_id = str(node.get("node_id") or "")
    node_type = str(node.get("node_type") or node.get("object_type") or "UNKNOWN").upper()
    label = str(
        node.get("display_value")
        or node.get("object_name")
        or node.get("attribute_name")
        or node.get("view_name")
        or node.get("subset_name")
        or node_id.rsplit("::", 1)[-1]
    )
    state = str(node.get("endpoint_state") or "CANONICAL").upper()
    return {
        "id": node_id,
        "type": node_type,
        "label": label,
        "subtitle": node_type.replace("_", " ").title(),
        "status": state,
        "control_object": bool(node.get("is_control", False)),
        "review_required": state in {"DYNAMIC", "PLANNED"},
        "inbound_count": inbound_count,
        "outbound_count": outbound_count,
        "properties": node,
        "visual": {
            "group": visual_group(node_type),
            "shape": visual_shape(node_type),
            "state": state.casefold(),
        },
    }


def edge_view(
    relationship: dict[str, Any],
    *,
    semantics: dict[str, Any],
    validation: dict[str, Any] | None,
    provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    relationship_type = str(relationship.get("relationship_type") or "UNKNOWN").upper()
    rule = semantics.get(relationship_type, {})
    endpoint = str(relationship.get("endpoint_state") or "CANONICAL").upper()
    validation = validation or {}
    return {
        "id": str(relationship.get("graph_relationship_id") or ""),
        "source": str(relationship.get("source_id") or ""),
        "target": str(relationship.get("target_id") or ""),
        "type": relationship_type,
        "label": rule.get("label") or relationship_type.replace("_", " ").title(),
        "category": rule.get("category", "OTHER"),
        "source_role": rule.get("source_role", "Source"),
        "target_role": rule.get("target_role", "Target"),
        "impact_behavior": rule.get("impact_behavior", "EDGE_DIRECTION"),
        "endpoint_state": endpoint,
        "validation_status": validation.get("validation_status", "UNKNOWN"),
        "review_required": bool(
            relationship.get("review_required")
            or validation.get("review_required")
            or endpoint in {"DYNAMIC", "PLANNED"}
        ),
        "provenance_available": provenance is not None,
        "visual": {
            "line_style": "dashed" if endpoint == "DYNAMIC" else "dotted" if endpoint == "PLANNED" else "solid",
            "state": "review" if endpoint in {"DYNAMIC", "PLANNED"} else "validated",
            "group": rule.get("visual_group", rule.get("category", "OTHER")),
        },
    }


def visual_group(node_type: str) -> str:
    if node_type in {"CUBE", "DIMENSION", "HIERARCHY", "ATTRIBUTE", "SUBSET", "VIEW"}:
        return "MODEL_OBJECT"
    if node_type in {"PROCESS", "CHORE", "CHORE_TASK"}:
        return "EXECUTION"
    if node_type in {"FILE", "COMMAND", "SCRIPT", "EXECUTABLE", "EXTERNAL_DATA_SOURCE"}:
        return "OPERATIONAL"
    if node_type in {"REFERENCE_EXPRESSION", "PLANNED_TARGET"}:
        return "UNRESOLVED"
    return "OTHER"


def visual_shape(node_type: str) -> str:
    return {
        "CUBE": "database",
        "DIMENSION": "layers",
        "PROCESS": "workflow",
        "CHORE": "calendar",
        "FILE": "file",
        "COMMAND": "terminal",
        "REFERENCE_EXPRESSION": "code",
        "PLANNED_TARGET": "help-circle",
    }.get(node_type, "box")
