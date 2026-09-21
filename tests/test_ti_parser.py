from parsers.ti_parser import (
    parse_process,
    summarize_relationships,
    validate_relationships,
)


def test_parses_multiple_relationships_and_resolves_variables():
    procedures = {
        "Prolog": """
sSrcCube = 'Profit and Loss';
sTgtCube = 'Project Spending';
sChild = 'Validate Project Spending';
""",
        "Data": """
nValue = CellGetN(sSrcCube, Period, Account, 'USD');
sREA = CellGetS('}ElementAttributes_WBS Element', Value, 'User field 1 WBS element');
CellPutN(nValue, sTgtCube, Period, sREA, 'APPR AMT');
CellIncrementN(nValue, sTgtCube, Period, sREA, 'MO SPD');
ExecuteProcess(sChild, 'pPeriod', Period);
""",
    }

    evidence = parse_process("Actual Load", procedures)
    relationships = {(item.relationship_type, item.target_name) for item in evidence}

    assert ("READS_FROM_CUBE", "Profit and Loss") in relationships
    assert (
        "READS_ATTRIBUTE",
        "WBS Element.User field 1 WBS element",
    ) in relationships
    assert ("WRITES_TO_CUBE", "Project Spending") in relationships
    assert ("INCREMENTS_CUBE", "Project Spending") in relationships
    assert ("CALLS_PROCESS", "Validate Project Spending") in relationships


def test_preserves_multiple_links_to_same_cube():
    procedures = {
        "Data": """
nValue = CellGetN('Cube A', 'Jan', 'Value');
CellPutN(nValue, 'Cube A', 'Jan', 'Value');
CellIncrementN(nValue, 'Cube A', 'Jan', 'Value');
"""
    }

    evidence = parse_process("Mixed Process", procedures)
    summaries = summarize_relationships(evidence)

    relationship_types = {
        summary.relationship_type for summary in summaries
    }
    assert relationship_types == {
        "READS_FROM_CUBE",
        "WRITES_TO_CUBE",
        "INCREMENTS_CUBE",
    }


def test_aggregates_duplicate_evidence():
    procedures = {
        "Data": """
CellGetN('Cube A', 'Jan', 'Value');
CellGetN('Cube A', 'Feb', 'Value');
"""
    }

    summaries = summarize_relationships(
        parse_process("Read Process", procedures)
    )

    assert len(summaries) == 1
    assert summaries[0].reference_count == 2
    assert summaries[0].functions == ("CellGetN",)


def test_detects_clear_and_view_relationships():
    procedures = {
        "Prolog": "ViewZeroOut('Project Spending', 'Load View');"
    }

    relationships = {
        (item.relationship_type, item.target_name)
        for item in parse_process("Clear Process", procedures)
    }

    assert ("CLEARS_CUBE", "Project Spending") in relationships
    assert ("USES_VIEW", "Project Spending.Load View") in relationships


def test_detects_attribute_write():
    procedures = {
        "Metadata": "AttrPutS('R1001', 'AFE Number', 'R1001', 'Funded/Unfunded');"
    }

    evidence = parse_process("Attribute Process", procedures)

    assert len(evidence) == 1
    assert evidence[0].relationship_type == "WRITES_ATTRIBUTE"
    assert evidence[0].target_name == "AFE Number.Funded/Unfunded"


def test_unresolved_dynamic_reference_is_retained():
    procedures = {
        "Data": "CellGetN(sPrefix | sScenario, 'Jan', 'Value');"
    }

    evidence = parse_process("Dynamic Process", procedures)

    assert len(evidence) == 1
    assert evidence[0].confidence == "UNRESOLVED"
    assert evidence[0].target_name == "sPrefix | sScenario"


def test_validates_known_and_broken_objects():
    procedures = {
        "Data": """
CellGetN('Cube A', 'Jan', 'Value');
CellPutN(1, 'Missing Cube', 'Jan', 'Value');
"""
    }

    summaries = summarize_relationships(
        parse_process("Validation Process", procedures)
    )
    catalog = [
        {"object_type": "cube", "object_name": "Cube A"},
    ]
    validations = validate_relationships(summaries, catalog)
    status_by_target = {
        item["target_name"]: item["validation_status"]
        for item in validations
    }

    assert status_by_target["Cube A"] == "VALID"
    assert status_by_target["Missing Cube"] == "BROKEN_REFERENCE"
