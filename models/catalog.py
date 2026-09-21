from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterable


# ============================================================
# Enumerations
# ============================================================

class ObjectType(StrEnum):
    SERVER = "server"
    DATABASE = "database"
    CUBE = "cube"
    DIMENSION = "dimension"
    HIERARCHY = "hierarchy"
    ELEMENT = "element"
    ATTRIBUTE = "attribute"
    PROCESS = "process"
    PROCESS_PARAMETER = "process_parameter"
    PROCESS_VARIABLE = "process_variable"
    PROCESS_DATA_SOURCE = "process_data_source"
    CHORE = "chore"
    CHORE_TASK = "chore_task"
    RULE = "rule"
    FEEDER = "feeder"
    VIEW = "view"
    SUBSET = "subset"
    APPLICATION = "application"
    PAW_BOOK = "paw_book"
    PAW_VIEW = "paw_view"
    PROCESS_GROUP = "process_group"
    SCRIPT = "script"
    SCHEDULED_TASK = "scheduled_task"
    FILE = "file"
    COMMAND = "command"
    ODBC_SOURCE = "odbc_source"
    UNKNOWN = "unknown"


class RelationshipType(StrEnum):
    USES_DIMENSION = "USES_DIMENSION"
    HAS_HIERARCHY = "HAS_HIERARCHY"
    CONTAINS_ELEMENT = "CONTAINS_ELEMENT"
    PARENT_OF = "PARENT_OF"
    HAS_ATTRIBUTE = "HAS_ATTRIBUTE"
    HAS_VIEW = "HAS_VIEW"
    HAS_SUBSET = "HAS_SUBSET"
    HAS_PARAMETER = "HAS_PARAMETER"
    HAS_VARIABLE = "HAS_VARIABLE"
    HAS_DATA_SOURCE = "HAS_DATA_SOURCE"
    RUNS_PROCESS = "RUNS_PROCESS"
    CALLS_PROCESS = "CALLS_PROCESS"
    READS_FROM_CUBE = "READS_FROM_CUBE"
    WRITES_TO_CUBE = "WRITES_TO_CUBE"
    INCREMENTS_CUBE = "INCREMENTS_CUBE"
    SPREADS_TO_CUBE = "SPREADS_TO_CUBE"
    CLEARS_CUBE = "CLEARS_CUBE"
    READS_ATTRIBUTE = "READS_ATTRIBUTE"
    WRITES_ATTRIBUTE = "WRITES_ATTRIBUTE"
    CREATES_ATTRIBUTE = "CREATES_ATTRIBUTE"
    DELETES_ATTRIBUTE = "DELETES_ATTRIBUTE"
    UPDATES_DIMENSION = "UPDATES_DIMENSION"
    UPDATES_HIERARCHY = "UPDATES_HIERARCHY"
    CREATES_VIEW = "CREATES_VIEW"
    USES_VIEW = "USES_VIEW"
    DELETES_VIEW = "DELETES_VIEW"
    CREATES_SUBSET = "CREATES_SUBSET"
    UPDATES_SUBSET = "UPDATES_SUBSET"
    DELETES_SUBSET = "DELETES_SUBSET"
    RULE_READS_CUBE = "RULE_READS_CUBE"
    FEEDS_CUBE = "FEEDS_CUBE"
    READS_FILE = "READS_FILE"
    WRITES_FILE = "WRITES_FILE"
    EXECUTES_COMMAND = "EXECUTES_COMMAND"
    TRIGGERS = "TRIGGERS"
    REFERENCES = "REFERENCES"


class ConfidenceLevel(StrEnum):
    CONFIRMED = "CONFIRMED"
    RESOLVED = "RESOLVED"
    PARAMETERIZED = "PARAMETERIZED"
    INFERRED = "INFERRED"
    EXTERNAL = "EXTERNAL"
    UNRESOLVED = "UNRESOLVED"


class ValidationStatus(StrEnum):
    VALID = "VALID"
    BROKEN_REFERENCE = "BROKEN_REFERENCE"
    PARAMETERIZED_REFERENCE = "PARAMETERIZED_REFERENCE"
    UNRESOLVED_DYNAMIC_REFERENCE = "UNRESOLVED_DYNAMIC_REFERENCE"
    TEMPORARY_OBJECT = "TEMPORARY_OBJECT"
    NOT_YET_CATALOGED = "NOT_YET_CATALOGED"
    NOT_CROSS_CHECKED = "NOT_CROSS_CHECKED"
    DUPLICATE_OBJECT = "DUPLICATE_OBJECT"
    INVALID_DEFINITION = "INVALID_DEFINITION"


class SnapshotStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


# ============================================================
# Utility Functions
# ============================================================

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_name(value: str | None) -> str:
    return "" if value is None else str(value).strip()


def normalized_key(value: str | None) -> str:
    return normalize_name(value).casefold()


def stable_hash(payload: Any) -> str:
    serialized = json.dumps(
        payload,
        sort_keys=True,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_qualified_name(
    object_type: ObjectType | str,
    object_name: str,
    parent_qualified_name: str | None = None,
) -> str:
    object_type_value = str(object_type)
    name = normalize_name(object_name)
    if not name:
        raise ValueError("object_name cannot be blank")

    if parent_qualified_name:
        return f"{normalize_name(parent_qualified_name)}::{object_type_value}::{name}"

    return f"{object_type_value}::{name}"


def build_object_id(
    environment: str,
    database_name: str,
    qualified_name: str,
) -> str:
    identity = {
        "environment": normalized_key(environment),
        "database_name": normalized_key(database_name),
        "qualified_name": normalized_key(qualified_name),
    }
    return stable_hash(identity)[:24]


# ============================================================
# Catalog Objects
# ============================================================

@dataclass(frozen=True)
class CatalogObject:
    snapshot_id: str
    environment: str
    database_name: str
    object_type: ObjectType
    object_name: str
    qualified_name: str
    object_id: str
    parent_object_id: str | None = None
    parent_qualified_name: str | None = None
    description: str | None = None
    is_control_object: bool = False
    is_system_object: bool = False
    is_active: bool = True
    definition_hash: str | None = None
    collected_at: str = field(default_factory=utc_now_iso)
    source_connector: str = "tm1_rest"
    properties: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        snapshot_id: str,
        environment: str,
        database_name: str,
        object_type: ObjectType,
        object_name: str,
        parent: CatalogObject | None = None,
        description: str | None = None,
        is_active: bool = True,
        definition: Any = None,
        source_connector: str = "tm1_rest",
        properties: dict[str, Any] | None = None,
    ) -> CatalogObject:
        name = normalize_name(object_name)
        qualified_name = build_qualified_name(
            object_type=object_type,
            object_name=name,
            parent_qualified_name=(
                parent.qualified_name if parent else None
            ),
        )

        return cls(
            snapshot_id=snapshot_id,
            environment=normalize_name(environment),
            database_name=normalize_name(database_name),
            object_type=object_type,
            object_name=name,
            qualified_name=qualified_name,
            object_id=build_object_id(
                environment=environment,
                database_name=database_name,
                qualified_name=qualified_name,
            ),
            parent_object_id=(parent.object_id if parent else None),
            parent_qualified_name=(
                parent.qualified_name if parent else None
            ),
            description=description,
            is_control_object=name.startswith("}"),
            is_system_object=(
                name.startswith("}")
                or name.casefold().startswith("sys_")
                or name.casefold().startswith("sys ")
            ),
            is_active=is_active,
            definition_hash=(
                stable_hash(definition)
                if definition is not None
                else None
            ),
            source_connector=source_connector,
            properties=properties or {},
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["object_type"] = self.object_type.value
        return result


# ============================================================
# Catalog Relationships
# ============================================================

@dataclass(frozen=True)
class CatalogRelationship:
    snapshot_id: str
    relationship_id: str
    source_object_id: str
    source_qualified_name: str
    source_type: ObjectType
    target_object_id: str | None
    target_qualified_name: str
    target_type: ObjectType
    relationship_type: RelationshipType
    confidence: ConfidenceLevel
    validation_status: ValidationStatus
    discovery_method: str
    reference_count: int = 1
    ordinal: int | None = None
    procedure: str | None = None
    function_name: str | None = None
    line_number: int | None = None
    code_reference: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    collected_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def create(
        cls,
        *,
        snapshot_id: str,
        source: CatalogObject,
        target: CatalogObject | None,
        target_type: ObjectType,
        target_qualified_name: str,
        relationship_type: RelationshipType,
        confidence: ConfidenceLevel = ConfidenceLevel.CONFIRMED,
        validation_status: ValidationStatus = ValidationStatus.VALID,
        discovery_method: str,
        reference_count: int = 1,
        ordinal: int | None = None,
        procedure: str | None = None,
        function_name: str | None = None,
        line_number: int | None = None,
        code_reference: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> CatalogRelationship:
        target_name = normalize_name(target_qualified_name)
        identity = {
            "snapshot_id": snapshot_id,
            "source_object_id": source.object_id,
            "target_object_id": target.object_id if target else None,
            "target_qualified_name": normalized_key(target_name),
            "relationship_type": relationship_type.value,
            "ordinal": ordinal,
            "procedure": procedure,
            "line_number": line_number,
        }

        return cls(
            snapshot_id=snapshot_id,
            relationship_id=stable_hash(identity)[:24],
            source_object_id=source.object_id,
            source_qualified_name=source.qualified_name,
            source_type=source.object_type,
            target_object_id=(target.object_id if target else None),
            target_qualified_name=target_name,
            target_type=target_type,
            relationship_type=relationship_type,
            confidence=confidence,
            validation_status=validation_status,
            discovery_method=discovery_method,
            reference_count=reference_count,
            ordinal=ordinal,
            procedure=procedure,
            function_name=function_name,
            line_number=line_number,
            code_reference=code_reference,
            properties=properties or {},
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["source_type"] = self.source_type.value
        result["target_type"] = self.target_type.value
        result["relationship_type"] = self.relationship_type.value
        result["confidence"] = self.confidence.value
        result["validation_status"] = self.validation_status.value
        return result


# ============================================================
# Catalog Evidence and Validation
# ============================================================

@dataclass(frozen=True)
class CatalogEvidence:
    snapshot_id: str
    evidence_id: str
    source_object_id: str
    relationship_type: RelationshipType
    target_expression: str
    procedure: str | None
    function_name: str | None
    line_number: int | None
    code_reference: str | None
    confidence: ConfidenceLevel
    properties: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        snapshot_id: str,
        source_object_id: str,
        relationship_type: RelationshipType,
        target_expression: str,
        procedure: str | None = None,
        function_name: str | None = None,
        line_number: int | None = None,
        code_reference: str | None = None,
        confidence: ConfidenceLevel = ConfidenceLevel.CONFIRMED,
        properties: dict[str, Any] | None = None,
    ) -> CatalogEvidence:
        identity = {
            "snapshot_id": snapshot_id,
            "source_object_id": source_object_id,
            "relationship_type": relationship_type.value,
            "target_expression": target_expression,
            "procedure": procedure,
            "function_name": function_name,
            "line_number": line_number,
        }
        return cls(
            snapshot_id=snapshot_id,
            evidence_id=stable_hash(identity)[:24],
            source_object_id=source_object_id,
            relationship_type=relationship_type,
            target_expression=target_expression,
            procedure=procedure,
            function_name=function_name,
            line_number=line_number,
            code_reference=code_reference,
            confidence=confidence,
            properties=properties or {},
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["relationship_type"] = self.relationship_type.value
        result["confidence"] = self.confidence.value
        return result


@dataclass(frozen=True)
class CatalogValidation:
    snapshot_id: str
    validation_id: str
    validation_status: ValidationStatus
    object_id: str | None = None
    relationship_id: str | None = None
    severity: str = "WARNING"
    message: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def create(
        cls,
        *,
        snapshot_id: str,
        validation_status: ValidationStatus,
        message: str,
        object_id: str | None = None,
        relationship_id: str | None = None,
        severity: str = "WARNING",
        properties: dict[str, Any] | None = None,
    ) -> CatalogValidation:
        identity = {
            "snapshot_id": snapshot_id,
            "validation_status": validation_status.value,
            "object_id": object_id,
            "relationship_id": relationship_id,
            "message": message,
        }
        return cls(
            snapshot_id=snapshot_id,
            validation_id=stable_hash(identity)[:24],
            validation_status=validation_status,
            object_id=object_id,
            relationship_id=relationship_id,
            severity=severity.upper(),
            message=message,
            properties=properties or {},
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["validation_status"] = self.validation_status.value
        return result


# ============================================================
# Snapshot Manifest
# ============================================================

@dataclass
class CatalogManifest:
    snapshot_id: str
    environment: str
    database_name: str
    status: SnapshotStatus = SnapshotStatus.RUNNING
    started_at: str = field(default_factory=utc_now_iso)
    completed_at: str | None = None
    collector_version: str = "0.1.0"
    tm1_version: str | None = None
    object_counts: dict[str, int] = field(default_factory=dict)
    relationship_counts: dict[str, int] = field(default_factory=dict)
    validation_counts: dict[str, int] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def complete(
        self,
        *,
        objects: Iterable[CatalogObject],
        relationships: Iterable[CatalogRelationship],
        validations: Iterable[CatalogValidation],
        partial: bool = False,
    ) -> None:
        self.object_counts = count_by(
            objects,
            lambda item: item.object_type.value,
        )
        self.relationship_counts = count_by(
            relationships,
            lambda item: item.relationship_type.value,
        )
        self.validation_counts = count_by(
            validations,
            lambda item: item.validation_status.value,
        )
        self.status = (
            SnapshotStatus.PARTIAL
            if partial or self.errors
            else SnapshotStatus.COMPLETE
        )
        self.completed_at = utc_now_iso()

    def fail(self, error: Exception) -> None:
        self.status = SnapshotStatus.FAILED
        self.completed_at = utc_now_iso()
        self.errors.append(
            {
                "error_type": type(error).__name__,
                "error": str(error),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result


def count_by(items: Iterable[Any], key_function) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(key_function(item))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


# ============================================================
# Catalog Container and Integrity Checks
# ============================================================

@dataclass
class CatalogSnapshot:
    manifest: CatalogManifest
    objects: list[CatalogObject] = field(default_factory=list)
    relationships: list[CatalogRelationship] = field(default_factory=list)
    evidence: list[CatalogEvidence] = field(default_factory=list)
    validations: list[CatalogValidation] = field(default_factory=list)

    def object_index(self) -> dict[str, CatalogObject]:
        return {item.object_id: item for item in self.objects}

    def qualified_name_index(self) -> dict[str, CatalogObject]:
        return {
            normalized_key(item.qualified_name): item
            for item in self.objects
        }

    def validate_integrity(self) -> list[CatalogValidation]:
        validations: list[CatalogValidation] = []
        object_ids: set[str] = set()
        qualified_names: set[str] = set()

        for item in self.objects:
            if item.object_id in object_ids:
                validations.append(
                    CatalogValidation.create(
                        snapshot_id=self.manifest.snapshot_id,
                        validation_status=ValidationStatus.DUPLICATE_OBJECT,
                        object_id=item.object_id,
                        severity="ERROR",
                        message=(
                            "Duplicate object_id detected: "
                            f"{item.object_id}"
                        ),
                    )
                )
            object_ids.add(item.object_id)

            qualified_key = normalized_key(item.qualified_name)
            if qualified_key in qualified_names:
                validations.append(
                    CatalogValidation.create(
                        snapshot_id=self.manifest.snapshot_id,
                        validation_status=ValidationStatus.DUPLICATE_OBJECT,
                        object_id=item.object_id,
                        severity="ERROR",
                        message=(
                            "Duplicate qualified_name detected: "
                            f"{item.qualified_name}"
                        ),
                    )
                )
            qualified_names.add(qualified_key)

            if (
                item.parent_object_id
                and item.parent_object_id not in object_ids
                and not any(
                    parent.object_id == item.parent_object_id
                    for parent in self.objects
                )
            ):
                validations.append(
                    CatalogValidation.create(
                        snapshot_id=self.manifest.snapshot_id,
                        validation_status=ValidationStatus.BROKEN_REFERENCE,
                        object_id=item.object_id,
                        severity="ERROR",
                        message=(
                            "Parent object was not found for "
                            f"{item.qualified_name}"
                        ),
                    )
                )

        for relationship in self.relationships:
            if relationship.source_object_id not in object_ids:
                validations.append(
                    CatalogValidation.create(
                        snapshot_id=self.manifest.snapshot_id,
                        validation_status=ValidationStatus.BROKEN_REFERENCE,
                        relationship_id=relationship.relationship_id,
                        severity="ERROR",
                        message=(
                            "Relationship source object was not found: "
                            f"{relationship.source_qualified_name}"
                        ),
                    )
                )

            if (
                relationship.target_object_id
                and relationship.target_object_id not in object_ids
            ):
                validations.append(
                    CatalogValidation.create(
                        snapshot_id=self.manifest.snapshot_id,
                        validation_status=ValidationStatus.BROKEN_REFERENCE,
                        relationship_id=relationship.relationship_id,
                        severity="ERROR",
                        message=(
                            "Relationship target object was not found: "
                            f"{relationship.target_qualified_name}"
                        ),
                    )
                )

        self.validations.extend(validations)
        return validations

    def finalize(self, partial: bool = False) -> None:
        self.validate_integrity()
        self.manifest.complete(
            objects=self.objects,
            relationships=self.relationships,
            validations=self.validations,
            partial=partial,
        )

    def to_payloads(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.to_dict(),
            "objects": [item.to_dict() for item in self.objects],
            "relationships": [
                item.to_dict() for item in self.relationships
            ],
            "evidence": [item.to_dict() for item in self.evidence],
            "validations": [
                item.to_dict() for item in self.validations
            ],
        }
