from __future__ import annotations

import json
from collections import Counter, deque
from pathlib import Path
from typing import Any, Iterable

from utilities.graph_response_models import edge_view, empty_view, node_view


class GraphContractError(RuntimeError):
    pass


class UnifiedGraphQuery:
    REQUIRED = (
        "graph_manifest.json",
        "graph_nodes.json",
        "graph_relationships.json",
        "graph_validations.json",
        "graph_provenance.json",
        "graph_outbound_index.json",
        "graph_inbound_index.json",
    )

    def __init__(
        self,
        current_root: Path,
        *,
        semantics_path: Path | None = None,
        require_published: bool = True,
    ) -> None:
        self.root = current_root
        missing = [name for name in self.REQUIRED if not (current_root / name).is_file()]
        if missing:
            raise GraphContractError(f"Missing graph artifacts: {', '.join(missing)}")
        self.manifest = self._read_object("graph_manifest.json")
        if self.manifest.get("status") != "COMPLETE":
            raise GraphContractError("Graph manifest status must be COMPLETE")
        if require_published and self.manifest.get("published_current") is not True:
            raise GraphContractError("Graph must be published_current=true")
        if int(self.manifest.get("error_count", 0) or 0) != 0:
            raise GraphContractError("Graph manifest error_count must be zero")

        self.nodes = self._read_records("graph_nodes.json")
        self.relationships = self._read_records("graph_relationships.json")
        self.validations = self._read_records("graph_validations.json")
        self.provenance = self._read_records("graph_provenance.json")
        self.outbound = self._read_object("graph_outbound_index.json")
        self.inbound = self._read_object("graph_inbound_index.json")
        self.nodes_by_id = {str(item["node_id"]): item for item in self.nodes}
        self.relationships_by_id = {
            str(item["graph_relationship_id"]): item for item in self.relationships
        }
        self.validations_by_relationship_id = {
            str(item["graph_relationship_id"]): item for item in self.validations
        }
        self.provenance_by_relationship_id = {
            str(item["graph_relationship_id"]): item for item in self.provenance
        }
        self.semantics = self._load_semantics(semantics_path)
        self._validate_indexes()

    def _read_json(self, name: str) -> Any:
        with (self.root / name).open("r", encoding="utf-8-sig") as stream:
            return json.load(stream)

    def _read_object(self, name: str) -> dict[str, Any]:
        payload = self._read_json(name)
        if not isinstance(payload, dict):
            raise GraphContractError(f"{name} must contain a JSON object")
        return payload

    def _read_records(self, name: str) -> list[dict[str, Any]]:
        payload = self._read_json(name)
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise GraphContractError(f"{name} must contain a JSON object array")
        return payload

    def _load_semantics(self, path: Path | None) -> dict[str, Any]:
        if path is None or not path.is_file():
            return {}
        with path.open("r", encoding="utf-8-sig") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            raise GraphContractError("Relationship semantics must be a JSON object")
        return payload

    def _validate_indexes(self) -> None:
        relationship_ids = set(self.relationships_by_id)
        indexed_out = {item for values in self.outbound.values() for item in values}
        indexed_in = {item for values in self.inbound.values() for item in values}
        if indexed_out != relationship_ids or indexed_in != relationship_ids:
            raise GraphContractError("Inbound/outbound indexes do not reconcile to relationships")

    def _node(self, node_id: str) -> dict[str, Any]:
        try:
            return self.nodes_by_id[node_id]
        except KeyError as error:
            raise KeyError(f"Unknown graph node: {node_id}") from error

    def _node_view(self, node_id: str) -> dict[str, Any]:
        return node_view(
            self._node(node_id),
            inbound_count=len(self.inbound.get(node_id, [])),
            outbound_count=len(self.outbound.get(node_id, [])),
        )

    def _edge_view(self, relationship_id: str) -> dict[str, Any]:
        relationship = self.relationships_by_id[relationship_id]
        return edge_view(
            relationship,
            semantics=self.semantics,
            validation=self.validations_by_relationship_id.get(relationship_id),
            provenance=self.provenance_by_relationship_id.get(relationship_id),
        )

    def search(self, text: str, *, node_type: str | None = None, limit: int = 25, offset: int = 0) -> dict[str, Any]:
        needle = " ".join(text.casefold().split())
        requested_type = (node_type or "").upper()
        matches: list[tuple[int, str]] = []
        for node_id, node in self.nodes_by_id.items():
            view = self._node_view(node_id)
            if requested_type and view["type"] != requested_type:
                continue
            haystack = f"{view['label']} {node_id}".casefold()
            if needle not in haystack:
                continue
            score = 0 if view["label"].casefold() == needle else 1 if needle in view["label"].casefold() else 2
            matches.append((score, node_id))
        matches.sort(key=lambda item: (item[0], self._node_view(item[1])["label"].casefold(), item[1]))
        total = len(matches)
        selected = matches[offset : offset + max(1, min(limit, 500))]
        response = empty_view(query={"operation": "search", "text": text, "type": node_type})
        response["nodes"] = [self._node_view(node_id) for _, node_id in selected]
        response["facets"] = {"node_types": dict(Counter(item["type"] for item in response["nodes"]))}
        response["page"] = {"offset": offset, "limit": limit, "returned": len(selected), "total": total}
        response["summary"] = {"match_count": total, "graph_snapshot_id": self.manifest.get("snapshot_id")}
        return response

    def node_summary(self, node_id: str) -> dict[str, Any]:
        response = self.neighborhood(node_id, direction="both", depth=1, max_nodes=250, max_edges=1000)
        response["query"]["operation"] = "node"
        response["summary"]["selected_node"] = self._node_view(node_id)
        return response

    def neighborhood(
        self,
        node_id: str,
        *,
        direction: str = "both",
        depth: int = 1,
        relationship_types: Iterable[str] | None = None,
        max_nodes: int = 500,
        max_edges: int = 5000,
    ) -> dict[str, Any]:
        self._node(node_id)
        if direction not in {"inbound", "outbound", "both"}:
            raise ValueError("direction must be inbound, outbound, or both")
        if not 0 <= depth <= 5:
            raise ValueError("depth must be between 0 and 5")
        allowed = {item.upper() for item in relationship_types or []}
        seen_nodes = {node_id}
        seen_edges: set[str] = set()
        queue = deque([(node_id, 0, [node_id], [])])
        paths: list[dict[str, Any]] = []
        truncated = False
        while queue:
            current, level, path_nodes, path_edges = queue.popleft()
            if level >= depth:
                if path_edges:
                    paths.append(self._path_view(path_nodes, path_edges))
                continue
            candidates: list[str] = []
            if direction in {"outbound", "both"}:
                candidates.extend(self.outbound.get(current, []))
            if direction in {"inbound", "both"}:
                candidates.extend(self.inbound.get(current, []))
            expanded = False
            for relationship_id in candidates:
                relationship = self.relationships_by_id[relationship_id]
                if allowed and str(relationship.get("relationship_type") or "").upper() not in allowed:
                    continue
                if len(seen_edges) >= max_edges:
                    truncated = True
                    break
                other = relationship["target_id"] if relationship["source_id"] == current else relationship["source_id"]
                if len(seen_nodes) >= max_nodes and other not in seen_nodes:
                    truncated = True
                    continue
                seen_edges.add(relationship_id)
                expanded = True
                new_nodes = path_nodes + [other]
                new_edges = path_edges + [relationship_id]
                if other in path_nodes:
                    paths.append(self._path_view(new_nodes, new_edges, cycle=True))
                    continue
                seen_nodes.add(other)
                queue.append((other, level + 1, new_nodes, new_edges))
            if not expanded and path_edges:
                paths.append(self._path_view(path_nodes, path_edges))
            if truncated and len(seen_edges) >= max_edges:
                break
        response = empty_view(query={"operation": "neighborhood", "node_id": node_id, "direction": direction, "depth": depth})
        response["nodes"] = [self._node_view(item) for item in sorted(seen_nodes)]
        response["edges"] = [self._edge_view(item) for item in sorted(seen_edges)]
        response["paths"] = paths
        response["facets"] = {
            "node_types": dict(Counter(item["type"] for item in response["nodes"])),
            "relationship_types": dict(Counter(item["type"] for item in response["edges"])),
            "relationship_categories": dict(Counter(item["category"] for item in response["edges"])),
        }
        if truncated:
            response["warnings"].append("Traversal was truncated by max_nodes or max_edges")
        response["summary"] = {
            "selected_node_id": node_id,
            "node_count": len(response["nodes"]),
            "edge_count": len(response["edges"]),
            "path_count": len(paths),
            "graph_snapshot_id": self.manifest.get("snapshot_id"),
        }
        response["page"] = {"offset": 0, "limit": max_nodes, "returned": len(response["nodes"]), "total": len(response["nodes"])}
        return response

    def impact(self, node_id: str, *, direction: str = "inbound", depth: int = 3, max_nodes: int = 1000, max_edges: int = 5000) -> dict[str, Any]:
        response = self.neighborhood(node_id, direction=direction, depth=depth, max_nodes=max_nodes, max_edges=max_edges)
        response["query"]["operation"] = "impact"
        return response

    def process_story(self, name_or_id: str) -> dict[str, Any]:
        node_id = name_or_id if name_or_id.startswith("process::") else f"process::{name_or_id}"
        response = self.neighborhood(node_id, direction="both", depth=1, max_nodes=1000, max_edges=5000)
        groups: dict[str, list[str]] = {}
        for edge in response["edges"]:
            groups.setdefault(edge["category"], []).append(edge["id"])
        response["query"]["operation"] = "process-story"
        response["summary"]["relationship_groups"] = {key: len(value) for key, value in sorted(groups.items())}
        return response

    def explain(self, relationship_id: str) -> dict[str, Any]:
        relationship = self.relationships_by_id.get(relationship_id)
        if relationship is None:
            raise KeyError(f"Unknown graph relationship: {relationship_id}")
        return {
            "summary": {"graph_snapshot_id": self.manifest.get("snapshot_id")},
            "relationship": self._edge_view(relationship_id),
            "source_node": self._node_view(str(relationship["source_id"])),
            "target_node": self._node_view(str(relationship["target_id"])),
            "validation": self.validations_by_relationship_id.get(relationship_id, {}),
            "provenance": self.provenance_by_relationship_id.get(relationship_id, {}),
        }

    def _path_view(self, node_ids: list[str], relationship_ids: list[str], cycle: bool = False) -> dict[str, Any]:
        labels = [self._node_view(item)["label"] for item in node_ids]
        return {
            "path_id": f"path::{len(relationship_ids)}::{relationship_ids[-1]}",
            "depth": len(relationship_ids),
            "node_ids": node_ids,
            "relationship_ids": relationship_ids,
            "cycle": cycle,
            "interpretation": " -> ".join(labels),
        }
