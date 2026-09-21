import pytest

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
    SnapshotStatus,
    ValidationStatus,
    build_object_id,
    build_qualified_name,
    stable_hash,
)


SNAPSHOT_ID = "20260921T180000Z"
ENVIRONMENT = "STAGING"
DATABASE = "Finance"


def test_stable_hash_is_deterministic():
    first = stable_hash({"b": 2, "a": 1})
    second = stable_hash({"a": 1, "b": 2})
    assert first == second


def test_build_qualified_name():
    assert build_qualified_name(
        ObjectType.CUBE,
        "Project Spending",
    ) == "cube::Project Spending"

    assert build_qualified_name(
        ObjectType.VIEW,
        "sTgtView",
        "cube::Project Spending",
    ) == "cube::Project Spending::view::sTgtView"


def test_object_id_is_case_and_space_insensitive():
    first = build_object_id(
        "STAGING",
        "Finance",
        "cube::Project Spending",
    )
    second = build_object_id(
        " staging ",
        "finance",
        " CUBE::PROJECT SPENDING ",
    )
    assert first == second


def test_create_catalog_object_sets_control_flags():
    cube = CatalogObject.create(
        snapshot_id=SNAPSHOT_ID,
        environment=ENVIRONMENT,
        database_name=DATABASE,
        object_type=ObjectType.CUBE,
        object_name="}ElementAttributes_WBS Element",
        definition={"dimensions": ["WBS Element", "}ElementAttributes_WBS Element"]},
    )

    assert cube.is_control_object is True
    assert cube.is_system_object is True
    assert cube.definition_hash is not None
    assert cube.qualified_name == (
        "cube::}ElementAttributes_WBS Element"
    )


def test_child_object_uses_parent_identity():
    cube = CatalogObject.create(
        snapshot_id=SNAPSHOT_ID,
        environment=ENVIRONMENT,
        database_name=DATABASE,
        object_type=ObjectType.CUBE,
        object_name="Project Spending",
    )
    view = CatalogObject.create(
        snapshot_id=SNAPSHOT_ID,
        environment=ENVIRONMENT,
        database_name=DATABASE,
        object_type=ObjectType.VIEW,
        object_name="sTgtView",
        parent=cube,
    )

    assert view.parent_object_id == cube.object_id
    assert view.qualified_name == (
        "cube::Project Spending::view::sTgtView"
    )


def test_relationship_creation():
    process = CatalogObject.create(
        snapshot_id=SNAPSHOT_ID,
        environment=ENVIRONMENT,
        database_name=DATABASE,
        object_type=ObjectType.PROCESS,
        object_name="Actual Load",
    )
    cube = CatalogObject.create(
        snapshot_id=SNAPSHOT_ID,
        environment=ENVIRONMENT,
        database_name=DATABASE,
        object_type=ObjectType.CUBE,
        object_name="Project Spending",
    )

    relationship = CatalogRelationship.create(
        snapshot_id=SNAPSHOT_ID,
        source=process,
        target=cube,
        target_type=ObjectType.CUBE,
        target_qualified_name=cube.qualified_name,
        relationship_type=RelationshipType.INCREMENTS_CUBE,
        confidence=ConfidenceLevel.RESOLVED,
        validation_status=ValidationStatus.VALID,
        discovery_method="ti_parser",
        function_name="CellIncrementN",
        procedure="Data",
        line_number=25,
    )

    assert relationship.source_object_id == process.object_id
    assert relationship.target_object_id == cube.object_id
    assert relationship.relationship_type == (
        RelationshipType.INCREMENTS_CUBE
    )
    assert relationship.to_dict()["confidence"] == "RESOLVED"


def test_parameterized_relationship_can_have_no_target_object():
    process = CatalogObject.create(
        snapshot_id=SNAPSHOT_ID,
        environment=ENVIRONMENT,
        database_name=DATABASE,
        object_type=ObjectType.PROCESS,
        object_name="Generic Clear",
    )

    relationship = CatalogRelationship.create(
        snapshot_id=SNAPSHOT_ID,
        source=process,
        target=None,
        target_type=ObjectType.CUBE,
        target_qualified_name="pCube",
        relationship_type=RelationshipType.CLEARS_CUBE,
        confidence=ConfidenceLevel.PARAMETERIZED,
        validation_status=(
            ValidationStatus.PARAMETERIZED_REFERENCE
        ),
        discovery_method="ti_parser",
    )

    assert relationship.target_object_id is None
    assert relationship.target_qualified_name == "pCube"


def test_evidence_creation():
    evidence = CatalogEvidence.create(
        snapshot_id=SNAPSHOT_ID,
        source_object_id="process-id",
        relationship_type=RelationshipType.READS_FROM_CUBE,
        target_expression="sSrcCube",
        procedure="Data",
        function_name="CellGetN",
        line_number=7,
        confidence=ConfidenceLevel.PARAMETERIZED,
    )

    assert evidence.evidence_id
    assert evidence.to_dict()["relationship_type"] == (
        "READS_FROM_CUBE"
    )


def test_snapshot_integrity_detects_missing_parent():
    orphan = CatalogObject(
        snapshot_id=SNAPSHOT_ID,
        environment=ENVIRONMENT,
        database_name=DATABASE,
        object_type=ObjectType.VIEW,
        object_name="Orphan View",
        qualified_name="cube::Missing::view::Orphan View",
        object_id="orphan-id",
        parent_object_id="missing-parent-id",
    )
    manifest = CatalogManifest(
        snapshot_id=SNAPSHOT_ID,
        environment=ENVIRONMENT,
        database_name=DATABASE,
    )
    snapshot = CatalogSnapshot(
        manifest=manifest,
        objects=[orphan],
    )

    validations = snapshot.validate_integrity()

    assert len(validations) == 1
    assert validations[0].validation_status == (
        ValidationStatus.BROKEN_REFERENCE
    )


def test_snapshot_finalize_counts_objects_and_relationships():
    process = CatalogObject.create(
        snapshot_id=SNAPSHOT_ID,
        environment=ENVIRONMENT,
        database_name=DATABASE,
        object_type=ObjectType.PROCESS,
        object_name="Actual Load",
    )
    cube = CatalogObject.create(
        snapshot_id=SNAPSHOT_ID,
        environment=ENVIRONMENT,
        database_name=DATABASE,
        object_type=ObjectType.CUBE,
        object_name="Project Spending",
    )
    relationship = CatalogRelationship.create(
        snapshot_id=SNAPSHOT_ID,
        source=process,
        target=cube,
        target_type=ObjectType.CUBE,
        target_qualified_name=cube.qualified_name,
        relationship_type=RelationshipType.WRITES_TO_CUBE,
        discovery_method="ti_parser",
    )
    manifest = CatalogManifest(
        snapshot_id=SNAPSHOT_ID,
        environment=ENVIRONMENT,
        database_name=DATABASE,
    )
    snapshot = CatalogSnapshot(
        manifest=manifest,
        objects=[process, cube],
        relationships=[relationship],
    )

    snapshot.finalize()

    assert snapshot.manifest.status == SnapshotStatus.COMPLETE
    assert snapshot.manifest.object_counts == {
        "cube": 1,
        "process": 1,
    }
    assert snapshot.manifest.relationship_counts == {
        "WRITES_TO_CUBE": 1,
    }


def test_validation_creation_uppercases_severity():
    validation = CatalogValidation.create(
        snapshot_id=SNAPSHOT_ID,
        validation_status=ValidationStatus.NOT_YET_CATALOGED,
        message="View is not yet cataloged",
        severity="info",
    )

    assert validation.severity == "INFO"
