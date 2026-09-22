
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
