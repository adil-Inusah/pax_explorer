from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    return RELATIONSHIP_TYPE_MAP.get(
        normalize_name(value).upper(), RelationshipType.REFERENCES
    )


def map_confidence(value: str | None) -> ConfidenceLevel:
    return CONFIDENCE_MAP.get(
        normalize_name(value).upper(), ConfidenceLevel.UNRESOLVED
    )


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
        snapshot.relationships.extend(
            self._adapt_relationships(
                payload=relationships_payload or [],
                snapshot_id=snapshot_id,
                snapshot=snapshot,
            )
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
        current_root = Path(current_root)
        return self.adapt(
            objects_payload=as_record_list(
                read_json(current_root / "objects.json"), "objects.json"
            ),
            relationships_payload=as_record_list(
                read_json(current_root / "relationships.json", required=False),
                "relationships.json",
            ),
            evidence_payload=as_record_list(
                read_json(
                    current_root / "relationship_evidence.json", required=False
                ),
                "relationship_evidence.json",
            ),
            validations_payload=as_record_list(
                read_json(
                    current_root / "relationship_validations.json", required=False
                ),
                "relationship_validations.json",
            ),
            metadata_manifest=optional_manifest(
                read_json(current_root / "manifest.json", required=False),
                "manifest.json",
            ),
            lineage_manifest=optional_manifest(
                read_json(current_root / "lineage_manifest.json", required=False),
                "lineage_manifest.json",
            ),
        )

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

    def _ensure_source_process(
        self,
        *,
        process_name: str,
        snapshot_id: str,
        snapshot: CatalogSnapshot,
    ) -> CatalogObject:
        existing = self._find_object(
            object_type=ObjectType.PROCESS, object_name=process_name
        )
        if existing is not None:
            return existing
        generated = CatalogObject.create(
            snapshot_id=snapshot_id,
            environment=self.environment,
            database_name=self.database_name,
            object_type=ObjectType.PROCESS,
            object_name=process_name,
            definition={"adapter_generated": True},
            source_connector="json_adapter",
            properties={"adapter_generated": True},
        )
        self._register_object(generated)
        snapshot.objects.append(generated)
        snapshot.validations.append(
            CatalogValidation.create(
                snapshot_id=snapshot_id,
                validation_status=ValidationStatus.NOT_YET_CATALOGED,
                object_id=generated.object_id,
                severity="WARNING",
                message=(
                    "The relationship source process was missing from objects.json "
                    f"and was created as an adapter placeholder: {process_name}"
                ),
            )
        )
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
            source = self._ensure_source_process(
                process_name=source_name,
                snapshot_id=snapshot_id,
                snapshot=snapshot,
            )
            target_type = map_object_type(record.get("target_type"))
            target_name = normalize_name(record.get("target_name"))
            relationship_type = map_relationship_type(
                record.get("relationship_type")
            )
            confidence = map_confidence(record.get("confidence"))
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
                    discovery_method="legacy_ti_json_adapter",
                    reference_count=self._positive_integer(
                        record.get("reference_count"), default=1
                    ),
                    procedure=self._only_item(procedures),
                    function_name=self._only_item(functions),
                    line_number=self._only_item(evidence_lines),
                    properties={
                        "legacy_target_name": target_name,
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
            process_name = normalize_name(record.get("process_name"))
            if not process_name:
                self._warn(
                    warning_type="SKIPPED_EVIDENCE_WITHOUT_SOURCE",
                    message="An evidence record had no process_name.",
                    record=record,
                )
                continue
            source = self._ensure_source_process(
                process_name=process_name,
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

    def _adapt_validations(
        self,
        *,
        payload: list[dict[str, Any]],
        snapshot_id: str,
        snapshot: CatalogSnapshot,
    ) -> list[CatalogValidation]:
        validations: list[CatalogValidation] = []
        relationship_index = {
            (
                normalized_key(item.source_qualified_name),
                item.relationship_type.value,
                normalized_key(item.target_qualified_name),
            ): item
            for item in snapshot.relationships
        }
        for record in payload:
            source_name = normalize_name(record.get("source_name"))
            target_name = normalize_name(record.get("target_name"))
            target_type = map_object_type(record.get("target_type"))
            relationship_type = map_relationship_type(
                record.get("relationship_type")
            )
            source = self._find_object(
                object_type=ObjectType.PROCESS, object_name=source_name
            )
            target = self._find_object(
                object_type=target_type, object_name=target_name
            )
            target_qualified_name = (
                target.qualified_name
                if target is not None
                else fallback_qualified_name(target_type, target_name)
            )
            relationship: CatalogRelationship | None = None
            if source is not None:
                relationship = relationship_index.get(
                    (
                        normalized_key(source.qualified_name),
                        relationship_type.value,
                        normalized_key(target_qualified_name),
                    )
                )
            status = map_validation_status(record.get("validation_status"))
            validations.append(
                CatalogValidation.create(
                    snapshot_id=snapshot_id,
                    validation_status=status,
                    relationship_id=(
                        relationship.relationship_id
                        if relationship is not None
                        else None
                    ),
                    severity=self._severity_for_status(status),
                    message=(
                        f"Legacy validation {status.value}: {source_name} "
                        f"{relationship_type.value} {target_name}"
                    ),
                    properties={"legacy_record": record},
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
