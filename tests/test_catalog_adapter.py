import json

from models.catalog import (
    ConfidenceLevel,
    ObjectType,
    RelationshipType,
    SnapshotStatus,
    ValidationStatus,
)
from models.catalog_adapter import (
    CatalogJsonAdapter,
    publish_adapter_result,
)


OBJECTS = [
    {
        "snapshot_id": "20260921T180000Z",
        "object_type": "cube",
        "object_name": "Project Spending",
        "collected_at": "2026-09-21T18:00:00+00:00",
    },
    {
        "snapshot_id": "20260921T180000Z",
        "object_type": "cube",
        "object_name": "Profit and Loss",
        "collected_at": "2026-09-21T18:00:00+00:00",
    },
    {
        "snapshot_id": "20260921T180000Z",
        "object_type": "process",
        "object_name": "Actual Load",
        "collected_at": "2026-09-21T18:00:00+00:00",
    },
]

RELATIONSHIPS = [
    {
        "source_type": "process",
        "source_name": "Actual Load",
        "relationship_type": "READS_FROM_CUBE",
        "target_type": "cube",
        "target_name": "Profit and Loss",
        "reference_count": 2,
        "procedures": ["Data"],
        "functions": ["CellGetN"],
        "confidence": "RESOLVED",
        "evidence_lines": [5, 7],
    },
    {
        "source_type": "process",
        "source_name": "Actual Load",
        "relationship_type": "INCREMENTS_CUBE",
        "target_type": "cube",
        "target_name": "pCube",
        "reference_count": 1,
        "procedures": ["Data"],
        "functions": ["CellIncrementN"],
        "confidence": "UNRESOLVED",
        "evidence_lines": [10],
    },
]

EVIDENCE = [
    {
        "process_name": "Actual Load",
        "procedure": "Data",
        "line_number": 5,
        "function_name": "CellGetN",
        "relationship_type": "READS_FROM_CUBE",
        "target_type": "cube",
        "target_name": "Profit and Loss",
        "target_expression": "sSrcCube",
        "confidence": "RESOLVED",
        "code_reference": "CellGetN(sSrcCube, 'Jan', 'Value')",
        "sequence": 1,
    }
]

VALIDATIONS = [
    {
        "source_name": "Actual Load",
        "relationship_type": "READS_FROM_CUBE",
        "target_type": "cube",
        "target_name": "Profit and Loss",
        "confidence": "RESOLVED",
        "validation_status": "VALID",
    }
]


def build_adapter():
    return CatalogJsonAdapter(
        environment="STAGING",
        database_name="Finance",
    )


def test_adapter_converts_objects_and_relationships():
    result = build_adapter().adapt(
        objects_payload=OBJECTS,
        relationships_payload=RELATIONSHIPS,
        evidence_payload=EVIDENCE,
        validations_payload=VALIDATIONS,
    )

    snapshot = result.snapshot
    assert snapshot.manifest.status == SnapshotStatus.COMPLETE
    assert len(snapshot.objects) == 3
    assert len(snapshot.relationships) == 2
    assert len(snapshot.evidence) == 1

    valid_read = next(
        item
        for item in snapshot.relationships
        if item.relationship_type
        == RelationshipType.READS_FROM_CUBE
    )
    assert valid_read.target_object_id is not None
    assert valid_read.confidence == ConfidenceLevel.RESOLVED
    assert valid_read.validation_status == ValidationStatus.VALID

    unresolved_write = next(
        item
        for item in snapshot.relationships
        if item.relationship_type
        == RelationshipType.INCREMENTS_CUBE
    )
    assert unresolved_write.target_object_id is None
    assert unresolved_write.validation_status == (
        ValidationStatus.UNRESOLVED_DYNAMIC_REFERENCE
    )


def test_adapter_uses_manifest_snapshot_id():
    result = build_adapter().adapt(
        objects_payload=OBJECTS,
        metadata_manifest={
            "snapshot_id": "metadata-snapshot",
            "status": "COMPLETE",
            "tm1_version": "12.0.0",
        },
        lineage_manifest={
            "snapshot_id": "lineage-snapshot",
            "status": "COMPLETE",
        },
    )

    assert result.snapshot.manifest.snapshot_id == (
        "lineage-snapshot"
    )
    assert result.snapshot.manifest.tm1_version == "12.0.0"


def test_adapter_marks_partial_from_source_manifest():
    result = build_adapter().adapt(
        objects_payload=OBJECTS,
        metadata_manifest={
            "snapshot_id": "snapshot-1",
            "status": "PARTIAL",
        },
    )

    assert result.snapshot.manifest.status == SnapshotStatus.PARTIAL


def test_adapter_creates_placeholder_for_missing_source_process():
    relationship = {
        "source_name": "Missing Process",
        "relationship_type": "WRITES_TO_CUBE",
        "target_type": "cube",
        "target_name": "Project Spending",
        "reference_count": 1,
        "procedures": ["Data"],
        "functions": ["CellPutN"],
        "confidence": "CONFIRMED",
        "evidence_lines": [1],
    }

    result = build_adapter().adapt(
        objects_payload=OBJECTS,
        relationships_payload=[relationship],
    )

    generated = [
        item
        for item in result.snapshot.objects
        if item.object_name == "Missing Process"
    ]
    assert len(generated) == 1
    assert generated[0].properties["adapter_generated"] is True
    assert any(
        item.validation_status
        == ValidationStatus.NOT_YET_CATALOGED
        for item in result.snapshot.validations
    )


def test_adapter_deduplicates_legacy_objects():
    duplicate_objects = OBJECTS + [dict(OBJECTS[0])]

    result = build_adapter().adapt(
        objects_payload=duplicate_objects,
    )

    assert len(result.snapshot.objects) == 3
    assert any(
        warning["warning_type"]
        == "DUPLICATE_LEGACY_OBJECT"
        for warning in result.warnings
    )


def test_publish_adapter_result(tmp_path):
    result = build_adapter().adapt(
        objects_payload=OBJECTS,
        relationships_payload=RELATIONSHIPS,
        evidence_payload=EVIDENCE,
        validations_payload=VALIDATIONS,
    )

    publish_adapter_result(result, tmp_path)

    expected_files = {
    "catalog_manifest.json",
    "catalog_objects.json",
    "catalog_relationships.json",
    "catalog_evidence.json",
    "catalog_validations.json",
    "catalog_adapter_warnings.json",
    "catalog_source_manifests.json",
}
    assert expected_files == {
        path.name for path in tmp_path.iterdir()
    }

    with (tmp_path / "catalog_objects.json").open(
        "r", encoding="utf-8"
    ) as file:
        objects = json.load(file)

    assert len(objects) == 3
    assert objects[0]["environment"] == "STAGING"


def test_unknown_relationship_type_maps_to_references():
    relationship = {
        "source_name": "Actual Load",
        "relationship_type": "SOMETHING_NEW",
        "target_type": "cube",
        "target_name": "Project Spending",
        "confidence": "CONFIRMED",
    }

    result = build_adapter().adapt(
        objects_payload=OBJECTS,
        relationships_payload=[relationship],
    )

    assert result.snapshot.relationships[0].relationship_type == (
        RelationshipType.REFERENCES
    )


def test_attribute_target_is_not_invented():
    relationship = {
        "source_name": "Actual Load",
        "relationship_type": "READS_ATTRIBUTE",
        "target_type": "attribute",
        "target_name": "WBS Element.REA Number",
        "confidence": "CONFIRMED",
    }

    result = build_adapter().adapt(
        objects_payload=OBJECTS,
        relationships_payload=[relationship],
    )

    adapted = result.snapshot.relationships[0]
    assert adapted.target_object_id is None
    assert adapted.target_type == ObjectType.ATTRIBUTE
    assert adapted.validation_status == (
        ValidationStatus.NOT_YET_CATALOGED
    )

    def test_missing_inferred_variable_target_is_dynamic_not_broken():
        result = build_adapter().adapt(
            objects_payload=[
                {
                    "snapshot_id": "20260922T000000Z",
                    "object_type": "process",
                    "object_name": "Load Process",
                }
            ],
            relationships_payload=[
                {
                    "source_type": "process",
                    "source_name": "Load Process",
                    "target_type": "cube",
                    "target_name": "Missing Cube",
                    "target_expression": "sCube",
                    "relationship_type": "READS_FROM_CUBE",
                    "confidence": "RESOLVED",
                }
            ],
        )

        relationship = result.snapshot.relationships[0]

        assert (
            relationship.validation_status
            == ValidationStatus.UNRESOLVED_DYNAMIC_REFERENCE
        )

    def test_missing_inferred_variable_target_is_dynamic():
        result = build_adapter().adapt(
            objects_payload=[
                {
                    "snapshot_id": (
                        "20260922T000000Z"
                    ),
                    "object_type": "process",
                    "object_name": "Load Process",
                }
            ],
            relationships_payload=[
                {
                    "source_type": "process",
                    "source_name": "Load Process",
                    "target_type": "cube",
                    "target_name": "Missing Cube",
                    "target_expression": "sCube",
                    "relationship_type": (
                        "READS_FROM_CUBE"
                    ),
                    "confidence": "RESOLVED",
                }
            ],
        )

        assert len(
            result.snapshot.relationships
        ) == 1

        relationship = (
            result.snapshot.relationships[0]
        )

        assert relationship.validation_status == (
            ValidationStatus
            .UNRESOLVED_DYNAMIC_REFERENCE
        )

        assert (
            relationship.properties[
                "target_expression"
            ]
            == "sCube"
        )


    def test_missing_literal_target_remains_broken():
        result = build_adapter().adapt(
            objects_payload=[
                {
                    "snapshot_id": (
                        "20260922T000000Z"
                    ),
                    "object_type": "process",
                    "object_name": "Load Process",
                }
            ],
            relationships_payload=[
                {
                    "source_type": "process",
                    "source_name": "Load Process",
                    "target_type": "cube",
                    "target_name": "grpdesc",
                    "target_expression": "'grpdesc'",
                    "relationship_type": (
                        "READS_FROM_CUBE"
                    ),
                    "confidence": "RESOLVED",
                }
            ],
        )

        assert len(
            result.snapshot.relationships
        ) == 1

        relationship = (
            result.snapshot.relationships[0]
        )

        assert relationship.validation_status == (
            ValidationStatus.BROKEN_REFERENCE
        )

    def test_existing_inferred_variable_target_is_valid():
        result = build_adapter().adapt(
            objects_payload=[
                {
                    "snapshot_id": (
                        "20260922T000000Z"
                    ),
                    "object_type": "process",
                    "object_name": "Load Process",
                },
                {
                    "snapshot_id": (
                        "20260922T000000Z"
                    ),
                    "object_type": "cube",
                    "object_name": "Target Cube",
                },
            ],
            relationships_payload=[
                {
                    "source_type": "process",
                    "source_name": "Load Process",
                    "target_type": "cube",
                    "target_name": "Target Cube",
                    "target_expression": "sCube",
                    "relationship_type": (
                        "READS_FROM_CUBE"
                    ),
                    "confidence": "RESOLVED",
                }
            ],
        )

        relationship = (
            result.snapshot.relationships[0]
        )

        assert relationship.validation_status == (
            ValidationStatus.VALID
        )