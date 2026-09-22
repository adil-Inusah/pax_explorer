
from parsers.rule_parser import parse_rules, split_arguments, summarize_relationships


def test_split_arguments_handles_nested_calls():
    assert split_arguments("'Cube', !Version, ATTRS('Dim', !Dim, 'Alias')") == (
        "'Cube'", "!Version", "ATTRS('Dim', !Dim, 'Alias')"
    )


def test_parse_db_read_and_feeder():
    rules = """
# comment DB('Ignored', !X)
['Amount'] = N: DB('Source Cube', !Version, !Period);
['Driver'] => DB('Target Cube', !Version, !Period);
"""
    evidence = parse_rules("Reporting Cube", rules)
    assert [(item.relationship_type, item.target_name) for item in evidence] == [
        ("READS_FROM", "Source Cube"),
        ("FEEDS", "Target Cube"),
    ]


def test_parse_attributes_and_dynamic_target():
    rules = """
['Region'] = S: ATTRS('Cost Centre', !CostCentre, 'Region');
['Amount'] = N: DB(IF(!Version @= 'Actual', 'Actual Cube', 'Plan Cube'), !Period);
"""
    evidence = parse_rules("P&L", rules)
    assert evidence[0].dimension_name == "Cost Centre"
    assert evidence[0].attribute_name == "Region"
    assert evidence[1].target_name is None
    assert evidence[1].confidence == "MEDIUM"


def test_summary_deduplicates_evidence():
    evidence = parse_rules("P&L", "DB('GL', !Period); DB('GL', !Period);")
    summary = summarize_relationships(evidence)
    assert len(summary) == 1
    assert summary[0].evidence_count == 2

def test_parse_elcompn_dimension_reference():
    evidence = parse_rules(
        "Reporting Cube",
        """
        ['Child Count'] = N:
            ELCOMPN(
                'Cost Centre',
                !CostCentre
            );
        """,
    )

    assert len(evidence) == 1

    record = evidence[0]

    assert record.function_name == "ELCOMPN"
    assert record.relationship_type == (
        "REFERENCES_DIMENSION"
    )
    assert record.target_object_type == "DIMENSION"
    assert record.target_name == "Cost Centre"
    assert record.dimension_name == "Cost Centre"
    assert record.confidence == "HIGH"

def test_parse_elcompn_dynamic_dimension_reference():
    evidence = parse_rules(
        "Reporting Cube",
        """
        ['Child Count'] = N:
            ELCOMPN(
                ATTRS(
                    'Configuration',
                    'Current',
                    'Dimension Name'
                ),
                !CostCentre
            );
        """,
    )

    elcompn = next(
        item
        for item in evidence
        if item.function_name == "ELCOMPN"
    )

    assert elcompn.target_name is None
    assert elcompn.target_expression is not None
    assert elcompn.target_expression.startswith(
        "ATTRS("
    )
    assert elcompn.confidence == "MEDIUM"


def test_parse_consolidatedmax_cube_reference():
    evidence = parse_rules(
        "Manual Input",
        """
        ['Maximum Value'] = N:
            ConsolidatedMax(
                3,
                'Manual Input',
                !Scenario,
                !Fiscal Period,
                !Manual Input LineNo,
                !mManual Input
            );
        """,
    )

    assert len(evidence) == 1

    record = evidence[0]

    assert record.function_name == "CONSOLIDATEDMAX"
    assert record.relationship_type == "READS_FROM"
    assert record.target_object_type == "CUBE"
    assert record.target_name == "Manual Input"
    assert record.target_expression == "'Manual Input'"
    assert record.confidence == "HIGH"

def test_parse_consolidatedavg_cube_reference():
    evidence = parse_rules(
        "Cost Estimate",
        """
        ['Average Value'] = N:
            ConsolidatedAvg(
                2,
                'Cost Estimate',
                !Currency Type,
                !Fiscal Period,
                !Plant,
                !Profit Center,
                !Material,
                !Component,
                !ELEMENT,
                !mCost Estimate
            );
        """,
    )

    assert len(evidence) == 1

    record = evidence[0]

    assert record.function_name == "CONSOLIDATEDAVG"
    assert record.relationship_type == "READS_FROM"
    assert record.target_object_type == "CUBE"
    assert record.target_name == "Cost Estimate"
    assert record.target_expression == "'Cost Estimate'"
    assert record.confidence == "HIGH"

def test_parse_consolidatechildren_dimension_reference():
    evidence = parse_rules(
        "SYS Parent Child",
        """
        ['Consolidated Value'] = C:
            ConsolidateChildren(
                'SYS Parent Child Hierarchy'
            );
        """,
    )

    assert len(evidence) == 1

    record = evidence[0]

    assert record.function_name == "CONSOLIDATECHILDREN"
    assert record.relationship_type == (
        "REFERENCES_DIMENSION"
    )
    assert record.target_object_type == "DIMENSION"
    assert record.target_name == (
        "SYS Parent Child Hierarchy"
    )
    assert record.dimension_name == (
        "SYS Parent Child Hierarchy"
    )
    assert record.confidence == "HIGH"

def test_parse_consolidatedmax_dynamic_cube_reference():
    evidence = parse_rules(
        "Reporting Cube",
        """
        ['Maximum Value'] = N:
            ConsolidatedMax(
                3,
                ATTRS(
                    'Configuration',
                    'Current',
                    'Cube Name'
                ),
                !Scenario,
                !Period,
                !Measure
            );
        """,
    )

    consolidated_max = next(
        item
        for item in evidence
        if item.function_name == "CONSOLIDATEDMAX"
    )

    assert consolidated_max.target_name is None
    assert consolidated_max.target_expression is not None
    assert consolidated_max.target_expression.startswith(
        "ATTRS("
    )
    assert consolidated_max.confidence == "MEDIUM"