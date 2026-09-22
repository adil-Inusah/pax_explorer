from scripts.profile_catalog_quality import build_quality_report


def base_payloads():
    objects = [
        {"object_id": "cube-1", "object_type": "cube", "object_name": "Source"},
        {"object_id": "cube-2", "object_type": "cube", "object_name": "Target"},
    ]
    relationships = [{
        "relationship_id": "rel-1",
        "source_object_id": "cube-1",
        "target_object_id": "cube-2",
        "source_qualified_name": "cube::Source",
        "target_qualified_name": "cube::Target",
        "relationship_type": "READS_FROM",
        "discovery_method": "tm1_rule_parser",
        "validation_status": "VALID",
    }]
    evidence = [{
        "evidence_id": "ev-1",
        "source_object_id": "cube-1",
        "target_expression": "Target",
        "relationship_type": "READS_FROM",
    }]
    validations = [{
        "validation_id": "val-1",
        "relationship_id": "rel-1",
        "validation_status": "VALID",
    }]
    source_manifests = {
        "metadata": {"status": "COMPLETE"},
        "ti_lineage": {"status": "COMPLETE"},
        "rule_lineage": {"status": "COMPLETE"},
    }
    return objects, relationships, evidence, validations, source_manifests


def test_quality_report_passes_clean_catalog():
    objects, relationships, evidence, validations, sources = base_payloads()
    report = build_quality_report(
        manifest={"status": "COMPLETE"},
        objects=objects,
        relationships=relationships,
        evidence=evidence,
        validations=validations,
        warnings=[],
        source_manifests=sources,
    )
    assert report["quality_status"] == "PASS"
    assert report["quality_control_score_percent"] == 100.0
    assert report["relationship_validation_coverage_percent"] == 100.0
    assert report["validation_resolution_rate_percent"] == 100.0


def test_quality_report_detects_orphan_relationship_source():
    objects, relationships, evidence, validations, sources = base_payloads()
    relationships[0]["source_object_id"] = "missing"
    report = build_quality_report(
        manifest={"status": "COMPLETE"}, objects=objects,
        relationships=relationships, evidence=evidence,
        validations=validations, warnings=[], source_manifests=sources,
    )
    assert report["quality_status"] == "FAIL"
    assert report["counts"]["orphan_relationship_sources"] == 1

def test_quality_report_detects_missing_linked_validation():
    (
        objects,
        relationships,
        evidence,
        validations,
        sources,
    ) = base_payloads()

    validations[0].pop("relationship_id")

    report = build_quality_report(
        manifest={"status": "COMPLETE"},
        objects=objects,
        relationships=relationships,
        evidence=evidence,
        validations=validations,
        warnings=[],
        source_manifests=sources,
    )

    assert (
        report[
            "relationship_validation_coverage_percent"
        ]
        == 0.0
    )

    assert (
        report["quality_gates"][
            "relationship_validation_coverage_100"
        ]
        is False
    )

    assert (
        report["counts"][
            "relationships_without_linked_validation"
        ]
        == 1
    )

    assert report["quality_status"] == "FAIL"


def test_dynamic_status_is_tracked_but_not_counted_as_valid():
    (
        objects,
        relationships,
        evidence,
        validations,
        sources,
    ) = base_payloads()

    validations[0]["validation_status"] = (
        "UNRESOLVED_DYNAMIC_REFERENCE"
    )

    report = build_quality_report(
        manifest={"status": "COMPLETE"},
        objects=objects,
        relationships=relationships,
        evidence=evidence,
        validations=validations,
        warnings=[],
        source_manifests=sources,
    )

    assert (
        report["counts"][
            "tracked_nonerror_validations"
        ]
        == 1
    )

    assert (
        report[
            "validation_resolution_rate_percent"
        ]
        == 0.0
    )

    assert (
        report["counts"][
            "raw_error_validations"
        ]
        == 0
    )

    assert (
        report["counts"][
            "accepted_error_validations"
        ]
        == 0
    )

    assert (
        report["counts"][
            "unaccepted_error_validations"
        ]
        == 0
    )

    assert (
        report["quality_gates"][
            "unaccepted_error_validation_count_zero"
        ]
        is True
    )

def test_broken_reference_fails_error_gate():
    objects, relationships, evidence, validations, sources = base_payloads()
    validations[0]["validation_status"] = "BROKEN_REFERENCE"
    report = build_quality_report(
        manifest={"status": "COMPLETE"}, objects=objects,
        relationships=relationships, evidence=evidence,
        validations=validations, warnings=[], source_manifests=sources,
    )
    assert (
        report["counts"][
            "raw_error_validations"
        ]
        == 1
    )

    assert (
        report["counts"][
            "accepted_error_validations"
        ]
        == 0
    )

    assert (
        report["counts"][
            "unaccepted_error_validations"
        ]
        == 1
    )

    assert (
        report["quality_gates"][
            "unaccepted_error_validation_count_zero"
        ]
        is False
    )
    assert report["quality_status"] == "FAIL"
