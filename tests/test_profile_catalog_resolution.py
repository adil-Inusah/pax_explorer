from scripts.profile_catalog_resolution import build_resolution_profile


def relationship(
    relationship_id: str,
    target_type: str,
    target: str,
    *,
    relationship_type: str = "REFERENCES",
    method: str = "tm1_ti_parser",
    expression: str | None = None,
    reference_count: int = 1,
):
    return {
        "relationship_id": relationship_id,
        "source_qualified_name": "process::Source",
        "relationship_type": relationship_type,
        "target_type": target_type,
        "target_qualified_name": target,
        "discovery_method": method,
        "confidence": "UNRESOLVED",
        "reference_count": reference_count,
        "properties": {"target_expression": expression},
    }


def validation(relationship_id: str, status: str):
    return {
        "relationship_id": relationship_id,
        "validation_status": status,
    }


def test_profiles_resolution_rate_and_backlog():
    relationships = [
        relationship("r1", "cube", "cube::Known"),
        relationship("r2", "attribute", "attribute::Dim::Alias"),
        relationship("r3", "subset", "subset::Dim::All", expression="sSubset"),
    ]
    validations = [
        validation("r1", "VALID"),
        validation("r2", "NOT_YET_CATALOGED"),
        validation("r3", "UNRESOLVED_DYNAMIC_REFERENCE"),
    ]
    profile = build_resolution_profile(
        relationships=relationships,
        validations=validations,
    )
    assert profile["metrics"]["total_relationships"] == 3
    assert profile["metrics"]["resolved_relationships"] == 1
    assert profile["metrics"]["resolution_backlog_relationships"] == 2
    assert profile["metrics"]["current_resolution_rate_percent"] == 33.33
    assert profile["metrics"]["metadata_only_potential_resolution_rate_percent"] == 66.67


def test_groups_backlog_by_domain_and_status():
    relationships = [
        relationship("r1", "attribute", "attribute::Dim::Alias"),
        relationship("r2", "attribute", "attribute::Dim::Code", expression="sAttr"),
        relationship("r3", "hierarchy", "hierarchy::Dim::Dim"),
    ]
    validations = [
        validation("r1", "NOT_YET_CATALOGED"),
        validation("r2", "UNRESOLVED_DYNAMIC_REFERENCE"),
        validation("r3", "NOT_YET_CATALOGED"),
    ]
    profile = build_resolution_profile(
        relationships=relationships,
        validations=validations,
    )
    distribution = profile["distributions"]["backlog_by_status_and_target_type"]
    assert distribution["NOT_YET_CATALOGED, attribute"] == 1
    assert distribution["UNRESOLVED_DYNAMIC_REFERENCE, attribute"] == 1
    assert distribution["NOT_YET_CATALOGED, hierarchy"] == 1
    opportunities = {item["domain"]: item for item in profile["resolution_opportunities"]}
    assert opportunities["ATTRIBUTE_INVENTORY_AND_RESOLUTION"]["relationship_count"] == 2
    assert opportunities["HIERARCHY_INVENTORY"]["relationship_count"] == 1


def test_preserves_reference_weighted_impact_and_variables():
    relationships = [
        relationship(
            "r1", "attribute", "attribute::Dim::Alias",
            relationship_type="WRITES_ATTRIBUTE",
            expression="sAttr", reference_count=25,
        ),
        relationship(
            "r2", "attribute", "attribute::Dim::Code",
            relationship_type="READS_ATTRIBUTE",
            expression="sAttr", reference_count=5,
        ),
    ]
    validations = [
        validation("r1", "UNRESOLVED_DYNAMIC_REFERENCE"),
        validation("r2", "UNRESOLVED_DYNAMIC_REFERENCE"),
    ]
    profile = build_resolution_profile(
        relationships=relationships,
        validations=validations,
    )
    opportunity = profile["resolution_opportunities"][0]
    assert opportunity["reference_weighted_count"] == 30
    assert profile["top_variable_expressions"][0] == {
        "variable": "sAttr",
        "relationship_count": 2,
    }


def test_marks_accepted_exception_from_quality_report():
    relationships = [relationship("r1", "cube", "cube::Missing")]
    validations = [validation("r1", "BROKEN_REFERENCE")]
    quality = {
        "quality_exceptions": {
            "accepted_errors": [
                {"validation": {"relationship_id": "r1"}}
            ]
        }
    }
    profile = build_resolution_profile(
        relationships=relationships,
        validations=validations,
        quality_report=quality,
    )
    assert profile["metrics"]["accepted_exception_relationships"] == 1
    assert profile["backlog"][0]["accepted_exception"] is True
