from __future__ import annotations

from scripts.profile_tm1_rule_coverage import build_profiles, discover_function_calls


REGISTRY = {
    "functions": {
        "DB": {"category": "DIRECT", "parser_status": "SUPPORTED"},
        "ATTRS": {"category": "DIRECT", "parser_status": "SUPPORTED"},
        "IF": {"category": "DYNAMIC", "parser_status": "PARTIALLY_SUPPORTED"},
        "ABS": {"category": "NON_LINEAGE", "parser_status": "INTENTIONALLY_IGNORED"},
    }
}


def test_discovers_nested_calls_and_preserves_line_numbers():
    calls, malformed = discover_function_calls(
        "P&L",
        "['Amount'] = N: DB(IF(!Version @= 'Actual', 'A', 'P'), !Period);",
    )
    assert malformed == []
    assert [item.function_name for item in calls] == ["DB", "IF"]
    assert all(item.line_number == 1 for item in calls)


def test_ignores_calls_in_comments():
    calls, malformed = discover_function_calls(
        "P&L",
        "# DB('Ignored', !Period)\n/* ATTRS('Dim', !Dim, 'Alias') */\nABS(1);",
    )
    assert malformed == []
    assert [item.function_name for item in calls] == ["ABS"]


def test_reports_unbalanced_call():
    calls, malformed = discover_function_calls("P&L", "DB('Cube', !Period")
    assert calls == []
    assert malformed[0]["function_name"] == "DB"
    assert malformed[0]["reason"] == "UNBALANCED_PARENTHESES"


def test_build_profiles_classifies_supported_dynamic_and_unknown_functions():
    definitions = [
        {
            "cube_name": "P&L",
            "rule_text": "DB('GL', !Period) + MYSTERY(!Period); IF(1, ATTRS('Dim', !Dim, 'Alias'), '');",
        },
        {"cube_name": "Empty", "rule_text": ""},
    ]
    profile, coverage = build_profiles(definitions, REGISTRY, sample_limit=2)
    by_name = {item["function_name"]: item for item in profile}
    assert by_name["DB"]["parser_status"] == "SUPPORTED"
    assert by_name["IF"]["lineage_category"] == "DYNAMIC"
    assert by_name["MYSTERY"]["registry_status"] == "UNCLASSIFIED"
    assert by_name["MYSTERY"]["parser_status"] == "REVIEW_REQUIRED"
    assert coverage["ruled_cube_count"] == 1
    assert coverage["unique_function_count"] == 4
    assert coverage["unclassified_function_count"] == 1
    assert coverage["lineage_coverage_percent"] == 100.0


def test_profile_limits_sample_evidence():
    definitions = [{"cube_name": "P&L", "rule_text": "DB('A', !P); DB('B', !P); DB('C', !P);"}]
    profile, _ = build_profiles(definitions, REGISTRY, sample_limit=2)
    db_profile = next(item for item in profile if item["function_name"] == "DB")
    assert db_profile["occurrence_count"] == 3
    assert len(db_profile["sample_evidence"]) == 2



def test_does_not_treat_parenthesized_element_name_as_function():
    calls, malformed = discover_function_calls(
        "Manual Input",
        "['Count (System)'] = N: NUMBR('1');",
    )
    assert malformed == []
    assert [item.function_name for item in calls] == ["NUMBR"]


def test_ignores_business_labels_with_parentheses():
    calls, malformed = discover_function_calls(
        "Balance Sheet",
        """
['USD (System)'] = N: 1;
['Local (System)'] = N: 2;
['Upload (System)'] = N: 3;
""",
    )
    assert malformed == []
    assert calls == []


def test_detects_function_but_not_text_inside_arguments():
    calls, malformed = discover_function_calls(
        "P&L",
        "DB('Cube (Reporting)', !Period);",
    )
    assert malformed == []
    assert [item.function_name for item in calls] == ["DB"]
    assert calls[0].expression == "DB('Cube (Reporting)', !Period)"


def test_masks_doubled_quotes_inside_string():
    calls, malformed = discover_function_calls(
        "P&L",
        "DB('Manager''s Cube (Actual)', !Period);",
    )
    assert malformed == []
    assert [item.function_name for item in calls] == ["DB"]
    assert calls[0].expression == "DB('Manager''s Cube (Actual)', !Period)"
