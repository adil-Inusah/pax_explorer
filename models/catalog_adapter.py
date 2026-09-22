from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TypeVar

from models.catalog import (
    CatalogEvidence,
    CatalogManifest,
    CatalogObject,
    CatalogRelationship,
    CatalogSnapshot,
    CatalogValidation,
    ConfidenceLevel,
    ObjectType,
    RelationshipType,
    ValidationStatus,
    build_qualified_name,
    normalize_name,
    normalized_key,
)

T = TypeVar("T")

OBJECT_TYPE_MAP: dict[str, ObjectType] = {
    "server": ObjectType.SERVER,
    "database": ObjectType.DATABASE,
    "cube": ObjectType.CUBE,
    "dimension": ObjectType.DIMENSION,
    "hierarchy": ObjectType.HIERARCHY,
    "element": ObjectType.ELEMENT,
    "attribute": ObjectType.ATTRIBUTE,
    "process": ObjectType.PROCESS,
    "process_parameter": ObjectType.PROCESS_PARAMETER,
    "process_variable": ObjectType.PROCESS_VARIABLE,
    "process_data_source": ObjectType.PROCESS_DATA_SOURCE,
    "chore": ObjectType.CHORE,
    "chore_task": ObjectType.CHORE_TASK,
    "rule": ObjectType.RULE,
    "feeder": ObjectType.FEEDER,
    "view": ObjectType.VIEW,
    "subset": ObjectType.SUBSET,
    "application": ObjectType.APPLICATION,
    "paw_book": ObjectType.PAW_BOOK,
    "paw_view": ObjectType.PAW_VIEW,
    "process_group": ObjectType.PROCESS_GROUP,
    "script": ObjectType.SCRIPT,
    "scheduled_task": ObjectType.SCHEDULED_TASK,
    "file": ObjectType.FILE,
    "command": ObjectType.COMMAND,
    "odbc_source": ObjectType.ODBC_SOURCE,
}
RELATIONSHIP_TYPE_MAP = {item.value.upper(): item for item in RelationshipType}
CONFIDENCE_MAP = {item.value.upper(): item for item in ConfidenceLevel}
VALIDATION_STATUS_MAP = {item.value.upper(): item for item in ValidationStatus}

# Parser terminology is intentionally more specific than the catalog enum.
# These aliases preserve the strongest catalog semantics that are available.
RELATIONSHIP_TYPE_ALIASES = {
    "READS_FROM": "READS_FROM_CUBE",
    "FEEDS": "FEEDS_CUBE",
    "USES_ATTRIBUTE": "REFERENCES",
    "REFERENCES_DIMENSION": "REFERENCES",
    "REFERENCES_HIERARCHY": "REFERENCES",
}
CONFIDENCE_ALIASES = {
    "HIGH": "RESOLVED",
    "MEDIUM": "PARAMETERIZED",
    "LOW": "UNRESOLVED",
}


@dataclass
class AdapterResult:
    snapshot: CatalogSnapshot
    warnings: list[dict[str, Any]] = field(default_factory=list)
    source_manifests: dict[str, Any] = field(default_factory=dict)

    def to_payloads(self) -> dict[str, Any]:
        payloads = self.snapshot.to_payloads()
        payloads["adapter_warnings"] = list(self.warnings)
        payloads["source_manifests"] = dict(self.source_manifests)
        return payloads



def read_json(path: Path, *, required: bool = True) -> Any:
    path = Path(path)
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Required JSON file was not found: {path}")
        return None
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in {path}. Line {error.lineno}, "
            f"column {error.colno}: {error.msg}"
        ) from error


def write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
    temporary_path.replace(path)


def as_record_list(payload: Any, label: str) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise ValueError(f"{label} must contain a JSON list.")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"{label}[{index}] must be a JSON object.")
        records.append(item)
    return records


def optional_manifest(payload: Any, label: str) -> dict[str, Any] | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object.")
    return payload


def map_object_type(value: str | None) -> ObjectType:
    return OBJECT_TYPE_MAP.get(normalized_key(value), ObjectType.UNKNOWN)


def map_relationship_type(value: str | None) -> RelationshipType:
    normalized_value = normalize_name(value).upper()
    alias = RELATIONSHIP_TYPE_ALIASES.get(normalized_value, normalized_value)
    return RELATIONSHIP_TYPE_MAP.get(alias, RelationshipType.REFERENCES)


def map_confidence(value: str | None) -> ConfidenceLevel:
    normalized_value = normalize_name(value).upper()
    alias = CONFIDENCE_ALIASES.get(normalized_value, normalized_value)
    return CONFIDENCE_MAP.get(alias, ConfidenceLevel.UNRESOLVED)


def map_validation_status(value: str | None) -> ValidationStatus:
    return VALIDATION_STATUS_MAP.get(
        normalize_name(value).upper(), ValidationStatus.NOT_CROSS_CHECKED
    )


def fallback_qualified_name(object_type: ObjectType, object_name: str) -> str:
    return build_qualified_name(
        object_type=object_type,
        object_name=normalize_name(object_name) or "<blank target>",
    )


def catalog_lookup_key(
    object_type: ObjectType, object_name: str
) -> tuple[str, str]:
    return object_type.value, normalized_key(object_name)


class CatalogJsonAdapter:
    """Adapt existing TM1 inventory and lineage JSON into CatalogSnapshot."""

    @staticmethod
    def _is_literal_target_expression(
        expression: Any,
    ) -> bool:
        value = normalize_name(expression)

        if len(value) < 2:
            return False

        return (
            value.startswith("'")
            and value.endswith("'")
        )


    def _effective_relationship_confidence(
        self,
        *,
        record: dict[str, Any],
        target_type: ObjectType,
        target_name: str,
    ) -> ConfidenceLevel:
        confidence = map_confidence(
            record.get("confidence")
        )

        target_expression = normalize_name(
            record.get("target_expression")
        )

        if not target_expression:
            return confidence

        target = self._find_object(
            object_type=target_type,
            object_name=target_name,
        )

        if target is not None:
            return ConfidenceLevel.RESOLVED

        if self._is_literal_target_expression(
            target_expression
        ):
            return confidence

        return ConfidenceLevel.UNRESOLVED
    @staticmethod
    def _source_object_type(
        record: dict[str, Any],
    ) -> ObjectType:
        source_type = map_object_type(
            record.get("source_type")
        )

        if source_type != ObjectType.UNKNOWN:
            return source_type

        if normalize_name(
            record.get("lineage_source")
        ).upper() == "RULE":
            return ObjectType.CUBE

        return ObjectType.PROCESS

    def __init__(
        self,
        *,
        environment: str,
        database_name: str,
        source_connector: str = "tm1_rest",
    ) -> None:
        self.environment = normalize_name(environment)
        self.database_name = normalize_name(database_name)
        self.source_connector = normalize_name(source_connector)
        if not self.environment:
            raise ValueError("environment cannot be blank")
        if not self.database_name:
            raise ValueError("database_name cannot be blank")
        if not self.source_connector:
            raise ValueError("source_connector cannot be blank")
        self.warnings: list[dict[str, Any]] = []
        self._objects_by_lookup: dict[tuple[str, str], CatalogObject] = {}
        self._objects_by_id: dict[str, CatalogObject] = {}

    def adapt(
        self,
        *,
        objects_payload: list[dict[str, Any]],
        relationships_payload: list[dict[str, Any]] | None = None,
        evidence_payload: list[dict[str, Any]] | None = None,
        validations_payload: list[dict[str, Any]] | None = None,
        metadata_manifest: dict[str, Any] | None = None,
        lineage_manifest: dict[str, Any] | None = None,
    ) -> AdapterResult:
        self._reset_state()
        snapshot_id = self._determine_snapshot_id(
            objects_payload=objects_payload,
            metadata_manifest=metadata_manifest,
            lineage_manifest=lineage_manifest,
        )
        manifest = CatalogManifest(
            snapshot_id=snapshot_id,
            environment=self.environment,
            database_name=self.database_name,
            collector_version="0.3.0-json-adapter",
            tm1_version=self._first_value(
                lineage_manifest, metadata_manifest, key="tm1_version"
            ),
        )
        snapshot = CatalogSnapshot(manifest=manifest)
        snapshot.objects.extend(
            self._adapt_objects(payload=objects_payload, snapshot_id=snapshot_id)
        )
        adapted_relationships = self._adapt_relationships(
            payload=relationships_payload or [],
            snapshot_id=snapshot_id,
            snapshot=snapshot,
        )
        snapshot.relationships.extend(
            self._aggregate_relationships(adapted_relationships)
        )
        snapshot.evidence.extend(
            self._adapt_evidence(
                payload=evidence_payload or [],
                snapshot_id=snapshot_id,
                snapshot=snapshot,
            )
        )
        snapshot.validations.extend(
            self._adapt_validations(
                payload=validations_payload or [],
                snapshot_id=snapshot_id,
                snapshot=snapshot,
            )
        )
        snapshot.finalize(
            partial=self._is_partial(metadata_manifest, lineage_manifest)
        )
        return AdapterResult(
            snapshot=snapshot,
            warnings=list(self.warnings),
            source_manifests=self._build_source_manifest_summary(
                metadata_manifest=metadata_manifest,
                lineage_manifest=lineage_manifest,
            ),
        )

    def adapt_directory(self, current_root: Path) -> AdapterResult:
        """Load source-specific TI and rule lineage, with legacy TI fallback."""
        current_root = Path(current_root)

        ti_relationships_raw = read_json(
            current_root / "ti_relationships.json", required=False
        )
        ti_evidence_raw = read_json(
            current_root / "ti_relationship_evidence.json", required=False
        )
        ti_validations_raw = read_json(
            current_root / "ti_relationship_validations.json", required=False
        )
        ti_manifest = optional_manifest(
            read_json(current_root / "ti_lineage_manifest.json", required=False),
            "ti_lineage_manifest.json",
        )
        used_legacy = False
        if ti_relationships_raw is None:
            ti_relationships_raw = read_json(
                current_root / "relationships.json", required=False
            )
            ti_evidence_raw = read_json(
                current_root / "relationship_evidence.json", required=False
            )
            ti_validations_raw = read_json(
                current_root / "relationship_validations.json", required=False
            )
            ti_manifest = optional_manifest(
                read_json(current_root / "lineage_manifest.json", required=False),
                "lineage_manifest.json",
            )
            used_legacy = ti_relationships_raw is not None

        ti_relationships = [
            self._normalize_ti_relationship(record)
            for record in as_record_list(
                ti_relationships_raw, "ti_relationships.json"
            )
        ]
        ti_evidence = [
            self._normalize_ti_evidence(record)
            for record in as_record_list(
                ti_evidence_raw, "ti_relationship_evidence.json"
            )
        ]
        ti_validations = [
            self._normalize_ti_validation(record)
            for record in as_record_list(
                ti_validations_raw, "ti_relationship_validations.json"
            )
        ]
        rule_relationships = [
            self._normalize_rule_relationship(record)
            for record in as_record_list(
                read_json(current_root / "rule_relationships.json", required=False),
                "rule_relationships.json",
            )
        ]
        rule_evidence = [
            self._normalize_rule_evidence(record)
            for record in as_record_list(
                read_json(
                    current_root / "rule_relationship_evidence.json",
                    required=False,
                ),
                "rule_relationship_evidence.json",
            )
        ]
        rule_validations = [
            self._normalize_rule_validation(record)
            for record in as_record_list(
                read_json(
                    current_root / "rule_relationship_validations.json",
                    required=False,
                ),
                "rule_relationship_validations.json",
            )
        ]
        rule_manifest = optional_manifest(
            read_json(current_root / "rule_lineage_manifest.json", required=False),
            "rule_lineage_manifest.json",
        )

        result = self.adapt(
            objects_payload=as_record_list(
                read_json(current_root / "objects.json"), "objects.json"
            ),
            relationships_payload=ti_relationships + rule_relationships,
            evidence_payload=ti_evidence + rule_evidence,
            validations_payload=ti_validations + rule_validations,
            metadata_manifest=optional_manifest(
                read_json(current_root / "manifest.json", required=False),
                "manifest.json",
            ),
            lineage_manifest=ti_manifest,
        )
        result.source_manifests["ti_lineage"] = ti_manifest or {}
        result.source_manifests["rule_lineage"] = rule_manifest or {}
        if used_legacy:
            result.warnings.append({
                "warning_type": "LEGACY_TI_INPUT_USED",
                "message": "Legacy generic TI lineage files were used as fallback.",
                "record": {},
            })
        if ti_manifest is None:
            result.warnings.append({
                "warning_type": "MISSING_TI_LINEAGE",
                "message": "TI lineage input was not found.",
                "record": {},
            })
        if rule_manifest is None:
            result.warnings.append({
                "warning_type": "MISSING_RULE_LINEAGE",
                "message": "Rule lineage input was not found.",
                "record": {},
            })
        return result

    @staticmethod
    def _normalize_ti_relationship(record: dict[str, Any]) -> dict[str, Any]:
        return {**record, "lineage_source": "TI", "source_type": "process",
                "source_name": record.get("source_name") or record.get("process_name")}

    @staticmethod
    def _normalize_rule_relationship(record: dict[str, Any]) -> dict[str, Any]:
        target_type = normalize_name(record.get("target_object_type"))
        target_name = normalize_name(record.get("target_name"))
        dimension_name = normalize_name(record.get("dimension_name"))
        attribute_name = normalize_name(record.get("attribute_name"))

        # Attribute names are only unique within their owning dimension.
        # Preserve both pieces so separate attributes do not collapse into one edge.
        if (
            target_type.upper() == "ATTRIBUTE"
            and dimension_name
            and attribute_name
        ):
            target_name = f"{dimension_name}::{attribute_name}"

        return {
            **record,
            "lineage_source": "RULE",
            "source_type": "cube",
            "source_name": record.get("source_cube"),
            "target_type": target_type,
            "target_name": target_name,
            "reference_count": record.get("evidence_count", 1),
            "procedures": [],
            "functions": [record.get("function_name")],
            "evidence_lines": [record.get("first_line")],
            "dimension_name": dimension_name or None,
            "attribute_name": attribute_name or None,
        }

    @staticmethod
    def _normalize_ti_evidence(record: dict[str, Any]) -> dict[str, Any]:
        return {**record, "lineage_source": "TI", "source_type": "process",
                "source_name": record.get("source_name") or record.get("process_name")}

    @staticmethod
    def _normalize_rule_evidence(record: dict[str, Any]) -> dict[str, Any]:
        return {**record, "lineage_source": "RULE", "source_type": "cube",
                "source_name": record.get("source_cube"),
                "target_type": record.get("target_object_type"),
                "code_reference": record.get("expression")}

    @staticmethod
    def _normalize_ti_validation(record: dict[str, Any]) -> dict[str, Any]:
        return {**record, "lineage_source": "TI", "source_type": "process",
                "source_name": record.get("source_name") or record.get("process_name")}

    @staticmethod
    def _normalize_rule_validation(record: dict[str, Any]) -> dict[str, Any]:
        return {**record, "lineage_source": "RULE", "source_type": "cube",
                "source_name": record.get("source_cube"),
                "target_type": record.get("target_object_type"),
                "validation_status": record.get("status")}

    def _reset_state(self) -> None:
        self.warnings.clear()
        self._objects_by_lookup.clear()
        self._objects_by_id.clear()

    def _register_object(self, item: CatalogObject) -> None:
        self._objects_by_lookup[
            catalog_lookup_key(item.object_type, item.object_name)
        ] = item
        self._objects_by_id[item.object_id] = item

    def _find_object(
        self, *, object_type: ObjectType, object_name: str
    ) -> CatalogObject | None:
        return self._objects_by_lookup.get(
            catalog_lookup_key(object_type, object_name)
        )

    def _determine_snapshot_id(
        self,
        *,
        objects_payload: list[dict[str, Any]],
        metadata_manifest: dict[str, Any] | None,
        lineage_manifest: dict[str, Any] | None,
    ) -> str:
        for payload in (lineage_manifest, metadata_manifest):
            if payload:
                snapshot_id = normalize_name(payload.get("snapshot_id"))
                if snapshot_id:
                    return snapshot_id
        for record in objects_payload:
            snapshot_id = normalize_name(record.get("snapshot_id"))
            if snapshot_id:
                return snapshot_id
        raise ValueError("A snapshot_id could not be determined from the JSON files.")

    @staticmethod
    def _first_value(*payloads: dict[str, Any] | None, key: str) -> Any:
        for payload in payloads:
            if payload and payload.get(key) is not None:
                return payload[key]
        return None

    @staticmethod
    def _is_partial(*payloads: dict[str, Any] | None) -> bool:
        return any(
            normalize_name(payload.get("status")).upper() in {"PARTIAL", "FAILED"}
            for payload in payloads
            if payload
        )

    @staticmethod
    def _build_source_manifest_summary(
        *,
        metadata_manifest: dict[str, Any] | None,
        lineage_manifest: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "metadata": {
                "snapshot_id": metadata_manifest.get("snapshot_id")
                if metadata_manifest
                else None,
                "status": metadata_manifest.get("status")
                if metadata_manifest
                else None,
                "tm1_version": metadata_manifest.get("tm1_version")
                if metadata_manifest
                else None,
            },
            "lineage": {
                "snapshot_id": lineage_manifest.get("snapshot_id")
                if lineage_manifest
                else None,
                "status": lineage_manifest.get("status")
                if lineage_manifest
                else None,
                "process_count": lineage_manifest.get("process_count")
                if lineage_manifest
                else None,
                "relationship_count": lineage_manifest.get("relationship_count")
                if lineage_manifest
                else None,
            },
        }

    def _adapt_objects(
        self, *, payload: list[dict[str, Any]], snapshot_id: str
    ) -> list[CatalogObject]:
        objects: list[CatalogObject] = []
        for record in payload:
            object_type = map_object_type(record.get("object_type"))
            object_name = normalize_name(record.get("object_name"))
            if not object_name:
                self._warn(
                    warning_type="SKIPPED_BLANK_OBJECT",
                    message="An object record had a blank object_name.",
                    record=record,
                )
                continue
            lookup_key = catalog_lookup_key(object_type, object_name)
            if lookup_key in self._objects_by_lookup:
                self._warn(
                    warning_type="DUPLICATE_LEGACY_OBJECT",
                    message=(
                        "A duplicate legacy object was ignored: "
                        f"{object_type.value}::{object_name}"
                    ),
                    record=record,
                )
                continue
            item = CatalogObject.create(
                snapshot_id=snapshot_id,
                environment=self.environment,
                database_name=self.database_name,
                object_type=object_type,
                object_name=object_name,
                definition=record,
                source_connector=self.source_connector,
                properties={
                    "legacy_snapshot_id": record.get("snapshot_id"),
                    "legacy_collected_at": record.get("collected_at"),
                    "adapter_generated": False,
                },
            )
            self._register_object(item)
            objects.append(item)
        return objects

    def _ensure_source_object(
        self, *, source_type: ObjectType, source_name: str,
        snapshot_id: str, snapshot: CatalogSnapshot,
    ) -> CatalogObject:
        existing = self._find_object(
            object_type=source_type, object_name=source_name
        )
        if existing is not None:
            return existing
        generated = CatalogObject.create(
            snapshot_id=snapshot_id, environment=self.environment,
            database_name=self.database_name, object_type=source_type,
            object_name=source_name, definition={"adapter_generated": True},
            source_connector="json_adapter",
            properties={"adapter_generated": True},
        )
        self._register_object(generated)
        snapshot.objects.append(generated)
        snapshot.validations.append(CatalogValidation.create(
            snapshot_id=snapshot_id,
            validation_status=ValidationStatus.NOT_YET_CATALOGED,
            object_id=generated.object_id, severity="WARNING",
            message=("The relationship source object was missing from objects.json "
                     f"and was created as a placeholder: "
                     f"{source_type.value}::{source_name}"),
        ))
        return generated

    def _resolve_target(
        self,
        *,
        target_type: ObjectType,
        target_name: str,
        confidence: ConfidenceLevel,
    ) -> tuple[CatalogObject | None, str, ValidationStatus]:
        target = self._find_object(
            object_type=target_type, object_name=target_name
        )
        if target is not None:
            return target, target.qualified_name, ValidationStatus.VALID
        return (
            None,
            fallback_qualified_name(target_type, target_name),
            self._derive_missing_target_status(
                confidence=confidence, target_type=target_type
            ),
        )

    def _adapt_relationships(
        self,
        *,
        payload: list[dict[str, Any]],
        snapshot_id: str,
        snapshot: CatalogSnapshot,
    ) -> list[CatalogRelationship]:
        relationships: list[CatalogRelationship] = []
        for record in payload:
            source_name = normalize_name(
                record.get("source_name") or record.get("process_name")
            )
            if not source_name:
                self._warn(
                    warning_type="SKIPPED_RELATIONSHIP_WITHOUT_SOURCE",
                    message="A relationship had no source process name.",
                    record=record,
                )
                continue
            source = self._ensure_source_object(
                    source_type=self._source_object_type(record),
                    source_name=source_name,
                snapshot_id=snapshot_id,
                    snapshot=snapshot,
                )
            target_type = map_object_type(record.get("target_type"))
            target_name = normalize_name(record.get("target_name"))
            relationship_type = map_relationship_type(
                record.get("relationship_type")
            )
            confidence = (
                self._effective_relationship_confidence(
                    record=record,
                    target_type=target_type,
                    target_name=target_name,
                )
            )
            target, target_qualified_name, validation_status = self._resolve_target(
                target_type=target_type,
                target_name=target_name,
                confidence=confidence,
            )
            procedures = self._string_list(record.get("procedures"))
            functions = self._string_list(record.get("functions"))
            evidence_lines = self._integer_list(record.get("evidence_lines"))
            relationships.append(
                CatalogRelationship.create(
                    snapshot_id=snapshot_id,
                    source=source,
                    target=target,
                    target_type=target_type,
                    target_qualified_name=target_qualified_name,
                    relationship_type=relationship_type,
                    confidence=confidence,
                    validation_status=validation_status,
                    discovery_method=(
                        "tm1_rule_parser" if record.get("lineage_source") == "RULE"
                        else "tm1_ti_parser"
                    ),
                    reference_count=self._positive_integer(
                        record.get("reference_count"), default=1
                    ),
                    procedure=self._only_item(procedures),
                    function_name=self._only_item(functions),
                    line_number=self._only_item(evidence_lines),
                    properties={
                        "lineage_source": record.get("lineage_source"),
                        "legacy_target_name": target_name,
                        "dimension_name": record.get("dimension_name"),
                        "attribute_name": record.get("attribute_name"),
                        "procedures": procedures,
                        "functions": functions,
                        "evidence_lines": evidence_lines,
                    },
                )
            )
        return relationships

    def _adapt_evidence(
        self,
        *,
        payload: list[dict[str, Any]],
        snapshot_id: str,
        snapshot: CatalogSnapshot,
    ) -> list[CatalogEvidence]:
        evidence_records: list[CatalogEvidence] = []
        for record in payload:
            process_name = normalize_name(
                record.get("source_name") or record.get("process_name")
            )
            if not process_name:
                self._warn(
                    warning_type="SKIPPED_EVIDENCE_WITHOUT_SOURCE",
                    message="An evidence record had no process_name.",
                    record=record,
                )
                continue
            source = self._ensure_source_object(
                source_type=self._source_object_type(
                    record
                ),
                source_name=process_name,
                snapshot_id=snapshot_id,
                snapshot=snapshot,
            )
            evidence_records.append(
                CatalogEvidence.create(
                    snapshot_id=snapshot_id,
                    source_object_id=source.object_id,
                    relationship_type=map_relationship_type(
                        record.get("relationship_type")
                    ),
                    target_expression=normalize_name(
                        record.get("target_expression")
                        or record.get("target_name")
                    ),
                    procedure=self._optional_string(record.get("procedure")),
                    function_name=self._optional_string(
                        record.get("function_name")
                    ),
                    line_number=self._optional_integer(
                        record.get("line_number")
                    ),
                    code_reference=self._optional_string(
                        record.get("code_reference")
                    ),
                    confidence=map_confidence(record.get("confidence")),
                    properties={
                        "legacy_target_name": record.get("target_name"),
                        "legacy_target_type": record.get("target_type"),
                        "attribute_name": record.get("attribute_name"),
                        "target_measure": record.get("target_measure"),
                        "sequence": record.get("sequence"),
                    },
                )
            )
        return evidence_records

    def _aggregate_relationships(
        self,
        relationships: list[CatalogRelationship],
    ) -> list[CatalogRelationship]:
        """Return one catalog row per deterministic relationship identity.

        Function, procedure, line, and discovery differences are evidence about
        the same graph edge. They are retained in aggregate properties instead
        of producing duplicate relationship rows.
        """
        grouped: dict[str, list[CatalogRelationship]] = {}
        for relationship in relationships:
            grouped.setdefault(relationship.relationship_id, []).append(relationship)

        aggregated: list[CatalogRelationship] = []
        for relationship_id, group in grouped.items():
            representative = group[0]
            functions: set[str] = set()
            procedures: set[str] = set()
            evidence_lines: set[int] = set()
            discovery_methods: set[str] = set()
            source_counts: dict[tuple[Any, ...], int] = {}

            for item in group:
                item_functions = self._string_list(
                    item.properties.get("functions") or item.function_name
                )
                item_procedures = self._string_list(
                    item.properties.get("procedures") or item.procedure
                )
                item_lines = self._integer_list(
                    item.properties.get("evidence_lines") or item.line_number
                )
                functions.update(item_functions)
                procedures.update(item_procedures)
                evidence_lines.update(item_lines)
                discovery_methods.add(item.discovery_method)

                signature = (
                    item.discovery_method,
                    tuple(sorted(item_functions)),
                    tuple(sorted(item_procedures)),
                    tuple(sorted(item_lines)),
                )
                source_counts[signature] = max(
                    source_counts.get(signature, 0), item.reference_count
                )

            properties = dict(representative.properties)
            properties.update(
                {
                    "functions": sorted(functions),
                    "procedures": sorted(procedures),
                    "evidence_lines": sorted(evidence_lines),
                    "discovery_methods": sorted(discovery_methods),
                    "aggregated_row_count": len(group),
                }
            )
            aggregated.append(
                replace(
                    representative,
                    reference_count=sum(source_counts.values()),
                    function_name=self._only_item(sorted(functions)),
                    procedure=self._only_item(sorted(procedures)),
                    line_number=self._only_item(sorted(evidence_lines)),
                    properties=properties,
                )
            )
        return aggregated

    def _adapt_validations(
        self,
        *,
        payload: list[dict[str, Any]],
        snapshot_id: str,
        snapshot: CatalogSnapshot,
    ) -> list[CatalogValidation]:
        """Create one canonical, linked validation per final relationship.

        Collector validation records are retained as matching source records in
        properties, but canonical linkage and status come from the normalized
        relationship that the adapter actually published.
        """
        source_records_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for record in payload:
            source_name = normalize_name(record.get("source_name"))
            source_type = self._source_object_type(record)
            source = self._find_object(
                object_type=source_type, object_name=source_name
            )
            target_type = map_object_type(record.get("target_type"))
            target_name = normalize_name(record.get("target_name"))
            target = self._find_object(
                object_type=target_type, object_name=target_name
            )
            target_qualified_name = (
                target.qualified_name
                if target is not None
                else fallback_qualified_name(target_type, target_name)
            )
            relationship_type = map_relationship_type(record.get("relationship_type"))
            if source is None:
                continue
            key = (
                normalized_key(source.qualified_name),
                relationship_type.value,
                normalized_key(target_qualified_name),
            )
            source_records_by_key.setdefault(key, []).append(record)

        validations: list[CatalogValidation] = []
        for relationship in snapshot.relationships:
            key = (
                normalized_key(relationship.source_qualified_name),
                relationship.relationship_type.value,
                normalized_key(relationship.target_qualified_name),
            )
            source_records = source_records_by_key.get(key, [])
            status = relationship.validation_status
            validations.append(
                CatalogValidation.create(
                    snapshot_id=snapshot_id,
                    validation_status=status,
                    relationship_id=relationship.relationship_id,
                    severity=self._severity_for_status(status),
                    message=(
                        f"Catalog validation {status.value}: "
                        f"{relationship.source_qualified_name} "
                        f"{relationship.relationship_type.value} "
                        f"{relationship.target_qualified_name}"
                    ),
                    properties={
                        "source_validation_records": source_records,
                        "source_validation_record_count": len(source_records),
                    },
                )
            )
        return validations

    @staticmethod
    def _derive_missing_target_status(
        *, confidence: ConfidenceLevel, target_type: ObjectType
    ) -> ValidationStatus:
        if confidence == ConfidenceLevel.PARAMETERIZED:
            return ValidationStatus.PARAMETERIZED_REFERENCE
        if confidence == ConfidenceLevel.UNRESOLVED:
            return ValidationStatus.UNRESOLVED_DYNAMIC_REFERENCE
        if target_type in {
            ObjectType.ATTRIBUTE,
            ObjectType.HIERARCHY,
            ObjectType.ELEMENT,
            ObjectType.VIEW,
            ObjectType.SUBSET,
            ObjectType.FILE,
            ObjectType.COMMAND,
        }:
            return ValidationStatus.NOT_YET_CATALOGED
        return ValidationStatus.BROKEN_REFERENCE

    @staticmethod
    def _severity_for_status(status: ValidationStatus) -> str:
        if status in {
            ValidationStatus.BROKEN_REFERENCE,
            ValidationStatus.DUPLICATE_OBJECT,
            ValidationStatus.INVALID_DEFINITION,
        }:
            return "ERROR"
        if status == ValidationStatus.VALID:
            return "INFO"
        return "WARNING"

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        values = value if isinstance(value, list) else ([] if value is None else [value])
        return [
            normalized
            for item in values
            if (normalized := normalize_name(item))
        ]

    @staticmethod
    def _integer_list(value: Any) -> list[int]:
        values = value if isinstance(value, list) else ([] if value is None else [value])
        results: list[int] = []
        for item in values:
            try:
                results.append(int(item))
            except (TypeError, ValueError):
                continue
        return results

    @staticmethod
    def _positive_integer(value: Any, *, default: int) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError):
            return default
        return result if result > 0 else default

    @staticmethod
    def _optional_integer(value: Any) -> int | None:
        try:
            return None if value is None else int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return normalize_name(value) or None

    @staticmethod
    def _only_item(values: list[T]) -> T | None:
        return values[0] if len(values) == 1 else None

    def _warn(
        self,
        *,
        warning_type: str,
        message: str,
        record: dict[str, Any],
    ) -> None:
        self.warnings.append(
            {
                "warning_type": warning_type,
                "message": message,
                "record": record,
            }
        )


def publish_adapter_result(result: AdapterResult, output_root: Path) -> None:
    output_root = Path(output_root)
    payloads = result.to_payloads()
    file_names = {
        "manifest": "catalog_manifest.json",
        "objects": "catalog_objects.json",
        "relationships": "catalog_relationships.json",
        "evidence": "catalog_evidence.json",
        "validations": "catalog_validations.json",
        "adapter_warnings": "catalog_adapter_warnings.json",
        "source_manifests": "catalog_source_manifests.json",
    }
    for payload_name, file_name in file_names.items():
        write_json(output_root / file_name, payloads[payload_name])
