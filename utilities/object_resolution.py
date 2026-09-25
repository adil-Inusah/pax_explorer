from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from utilities.object_identity import normalize, token


class ObjectResolver:
    def __init__(
        self,
        canonical_objects: list[dict[str, Any]],
    ) -> None:
        self.views: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.subsets: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        self.files: dict[str, list[str]] = defaultdict(list)

        for item in canonical_objects:
            object_id = str(
                item.get("object_id")
                or item.get("node_id")
                or ""
            )
            kind = token(
                item.get("object_type")
                or item.get("node_type")
            )

            raw_properties = item.get("properties")
            properties: Mapping[str, Any]
            if isinstance(raw_properties, Mapping):
                properties = raw_properties
            else:
                properties = item

            if kind == "VIEW":
                cube_name = normalize(properties.get("cube_name"))
                view_name = normalize(properties.get("view_name"))
                if object_id and cube_name and view_name:
                    self.views[(cube_name, view_name)].append(object_id)

            elif kind == "SUBSET":
                dimension_name = normalize(
                    properties.get("dimension_name")
                )
                hierarchy_name = normalize(
                    properties.get("hierarchy_name")
                    or properties.get("dimension_name")
                )
                subset_name = normalize(properties.get("subset_name"))
                if (
                    object_id
                    and dimension_name
                    and hierarchy_name
                    and subset_name
                ):
                    self.subsets[
                        (dimension_name, hierarchy_name, subset_name)
                    ].append(object_id)

            elif kind == "FILE":
                path_value = (
                    properties.get("display_value")
                    or properties.get("path")
                )
                if object_id and path_value:
                    normalized_path = normalize(
                        str(path_value).replace("/", "\\")
                    )
                    self.files[normalized_path].append(object_id)

    @staticmethod
    def _result(
        status: str,
        candidates: list[str],
    ) -> dict[str, Any]:
        unique_candidates = sorted(set(candidates))
        resolved_target = (
            unique_candidates[0]
            if len(unique_candidates) == 1
            else None
        )
        return {
            "resolution_status": status,
            "resolved_target_object_id": resolved_target,
            "candidate_object_ids": unique_candidates,
            "review_required": len(unique_candidates) != 1,
        }

    def resolve_view(
        self,
        cube_name: str,
        view_name: str,
    ) -> dict[str, Any]:
        normalized_cube = normalize(cube_name)
        normalized_view = normalize(view_name)
        if not normalized_cube or not normalized_view:
            return self._result("INSUFFICIENT_CONTEXT", [])

        candidates = self.views.get(
            (normalized_cube, normalized_view),
            [],
        )
        if len(candidates) == 1:
            return self._result("CANONICAL_EXACT_MATCH", candidates)
        if len(candidates) > 1:
            return self._result("AMBIGUOUS_REFERENCE", candidates)
        return self._result("TARGET_NOT_IN_PUBLIC_CATALOG", [])

    def resolve_subset(
        self,
        dimension: str,
        hierarchy: str,
        subset: str,
    ) -> dict[str, Any]:
        normalized_dimension = normalize(dimension)
        normalized_hierarchy = normalize(hierarchy or dimension)
        normalized_subset = normalize(subset)
        if (
            not normalized_dimension
            or not normalized_hierarchy
            or not normalized_subset
        ):
            return self._result("INSUFFICIENT_CONTEXT", [])

        candidates = self.subsets.get(
            (
                normalized_dimension,
                normalized_hierarchy,
                normalized_subset,
            ),
            [],
        )
        if len(candidates) == 1:
            return self._result("CANONICAL_EXACT_MATCH", candidates)
        if len(candidates) > 1:
            return self._result("AMBIGUOUS_REFERENCE", candidates)
        return self._result("TARGET_NOT_IN_PUBLIC_CATALOG", [])

    def resolve_file(
        self,
        path_value: str,
    ) -> dict[str, Any]:
        normalized_path = normalize(
            str(path_value).replace("/", "\\")
        )
        if not normalized_path:
            return self._result("INSUFFICIENT_CONTEXT", [])

        candidates = self.files.get(normalized_path, [])
        if len(candidates) == 1:
            return self._result("CANONICAL_EXACT_MATCH", candidates)
        if len(candidates) > 1:
            return self._result("AMBIGUOUS_REFERENCE", candidates)
        return self._result("EXTERNAL_UNVERIFIED", [])
