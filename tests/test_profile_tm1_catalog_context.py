from scripts import profile_tm1_catalog_matches as module


def test_extracts_dotted_attribute_target() -> None:
    context = module.extract_context(
        {
            "relationship_type": "WRITES_ATTRIBUTE",
            "target_name": "Employee.Cost Center",
        },
        "ATTRIBUTE",
    )
    assert context["dimension_name"] == "Employee"
    assert context["attribute_name"] == "Cost Center"


def test_extracts_control_dimension_attribute_target() -> None:
    context = module.extract_context(
        {
            "relationship_type": "READS_ATTRIBUTE",
            "target_name": "}Processes.Source File",
        },
        "ATTRIBUTE",
    )
    assert context["dimension_name"] == "}Processes"
    assert context["attribute_name"] == "Source File"


def test_extracts_dotted_subset_variable_and_marks_dynamic() -> None:
    context = module.extract_context(
        {
            "relationship_type": "UPDATES_SUBSET",
            "target_name": "Fiscal Period.sSrcView",
        },
        "SUBSET",
    )
    assert context["dimension_name"] == "Fiscal Period"
    assert context["subset_name"] == "sSrcView"
    assert module.dynamic_value(context["subset_name"]) is True


def test_extracts_dotted_view_variable_and_marks_dynamic() -> None:
    context = module.extract_context(
        {
            "relationship_type": "USES_VIEW",
            "target_name": "Balance Sheet.sTgtView",
        },
        "VIEW",
    )
    assert context["cube_name"] == "Balance Sheet"
    assert context["view_name"] == "sTgtView"
    assert module.dynamic_value(context["view_name"]) is True


def test_literal_names_with_spaces_are_not_dynamic() -> None:
    assert module.dynamic_value("Source File") is False
    assert module.dynamic_value("Cost Center") is False
