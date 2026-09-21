from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

import scripts.check_tm1_connection as connection_check


# ============================================================
# Test Fixtures
# ============================================================

@pytest.fixture
def mock_tm1():
    """Return a mock TM1 service with readable metadata."""

    tm1 = MagicMock()
    tm1.server.version = "12.0.0"

    tm1.cubes.get_all_names.return_value = [
        "Zulu Cube",
        "Alpha Cube",
    ]
    tm1.dimensions.get_all_names.return_value = [
        "Fiscal Period",
        "WBS Element",
        "AFE Number",
    ]
    tm1.processes.get_all_names.return_value = [
        "DataUpdate - Project Spending - Actual",
    ]
    tm1.chores.get_all_names.return_value = [
        "Project Spending Load",
    ]

    return tm1


@pytest.fixture
def successful_connection(monkeypatch, mock_tm1):
    """Mock get_tm1_connection as a valid context manager."""

    monkeypatch.setattr(
        connection_check,
        "get_tm1_connection",
        lambda: nullcontext(mock_tm1),
    )

    return mock_tm1


# ============================================================
# Metadata Retrieval Tests
# ============================================================

def test_get_metadata_names_success():
    result = connection_check.get_metadata_names(
        object_type="Cubes",
        retrieval_function=lambda: [
            "Cube A",
            "Cube B",
        ],
    )

    assert result == {
        "object_type": "Cubes",
        "status": "PASS",
        "names": ["Cube A", "Cube B"],
        "count": 2,
        "error": None,
    }


def test_get_metadata_names_converts_iterable_to_list():
    result = connection_check.get_metadata_names(
        object_type="Dimensions",
        retrieval_function=lambda: (
            name for name in ["Dim A", "Dim B"]
        ),
    )

    assert result["status"] == "PASS"
    assert result["names"] == ["Dim A", "Dim B"]
    assert result["count"] == 2
    assert result["error"] is None


def test_get_metadata_names_handles_empty_result():
    result = connection_check.get_metadata_names(
        object_type="Chores",
        retrieval_function=lambda: [],
    )

    assert result == {
        "object_type": "Chores",
        "status": "PASS",
        "names": [],
        "count": 0,
        "error": None,
    }


def test_get_metadata_names_failure_is_captured():
    def failed_retrieval():
        raise PermissionError("Metadata access denied")

    result = connection_check.get_metadata_names(
        object_type="Processes",
        retrieval_function=failed_retrieval,
    )

    assert result["status"] == "FAIL"
    assert result["names"] == []
    assert result["count"] == 0
    assert result["error"] == (
        "PermissionError: Metadata access denied"
    )


def test_metadata_access_reads_expected_services(mock_tm1):
    results = connection_check.test_metadata_access(
        mock_tm1
    )

    assert results["cubes"]["count"] == 2
    assert results["dimensions"]["count"] == 3
    assert results["processes"]["count"] == 1
    assert results["chores"]["count"] == 1

    mock_tm1.cubes.get_all_names.assert_called_once_with()
    mock_tm1.dimensions.get_all_names.assert_called_once_with()
    mock_tm1.processes.get_all_names.assert_called_once_with()
    mock_tm1.chores.get_all_names.assert_called_once_with()


# ============================================================
# Connection Test Behavior
# ============================================================

def test_connection_check_full_success(
    successful_connection,
    capsys,
):
    successful = connection_check.check_tm1_connection()

    output = capsys.readouterr().out

    assert successful is True
    assert "Connection Status" in output
    assert "Successful" in output
    assert "TM1 Version" in output
    assert "12.0.0" in output
    assert "Overall Status" in output
    assert "Pass" in output
    assert "Read-only" in output


def test_connection_check_displays_limited_samples(
    successful_connection,
    capsys,
):
    successful = connection_check.check_tm1_connection(
        show_samples=True,
        sample_size=1,
    )

    output = capsys.readouterr().out

    assert successful is True
    assert "SAMPLE ACCESSIBLE OBJECTS" in output
    assert "Alpha Cube" in output
    assert "Zulu Cube" not in output


def test_connection_check_partial_metadata_failure(
    monkeypatch,
    mock_tm1,
    capsys,
):
    mock_tm1.chores.get_all_names.side_effect = (
        PermissionError("Chore access denied")
    )

    monkeypatch.setattr(
        connection_check,
        "get_tm1_connection",
        lambda: nullcontext(mock_tm1),
    )

    successful = connection_check.check_tm1_connection()

    output = capsys.readouterr().out

    assert successful is False
    assert "Chores" in output
    assert "FAILED" in output
    assert "Chore access denied" in output
    assert "Partial Pass" in output
    assert "Metadata Failures" in output


def test_connection_check_connection_failure(
    monkeypatch,
    capsys,
):
    def failed_connection():
        raise ConnectionError("Unable to reach TM1 staging")

    monkeypatch.setattr(
        connection_check,
        "get_tm1_connection",
        failed_connection,
    )

    successful = connection_check.check_tm1_connection()

    output = capsys.readouterr().out

    assert successful is False
    assert "Connection Status" in output
    assert "Failed" in output
    assert "ConnectionError" in output
    assert "Unable to reach TM1 staging" in output
    assert "Overall Status" in output
    assert "Fail" in output


# ============================================================
# Display Tests
# ============================================================

def test_display_sample_objects_uses_existing_results(capsys):
    metadata_results = {
        "cubes": {
            "object_type": "Cubes",
            "status": "PASS",
            "names": ["zCube", "aCube", "bCube"],
            "count": 3,
            "error": None,
        },
        "chores": {
            "object_type": "Chores",
            "status": "FAIL",
            "names": [],
            "count": 0,
            "error": "PermissionError: denied",
        },
    }

    connection_check.display_sample_objects(
        metadata_results=metadata_results,
        sample_size=2,
    )

    output = capsys.readouterr().out

    assert "aCube" in output
    assert "bCube" in output
    assert "zCube" not in output
    assert "Retrieval failed" in output
    assert "PermissionError: denied" in output


def test_display_metadata_results_shows_empty_access(capsys):
    metadata_results = {
        "chores": {
            "object_type": "Chores",
            "status": "PASS",
            "names": [],
            "count": 0,
            "error": None,
        }
    }

    connection_check.display_metadata_results(
        metadata_results
    )

    output = capsys.readouterr().out

    assert "Chores" in output
    assert "0 accessible" in output


# ============================================================
# MDXpy Inspection Tests
# ============================================================

def test_inspect_mdx_methods_handles_missing_method(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        connection_check,
        "MDX_HIERARCHY_METHODS",
        ["method_that_does_not_exist"],
    )

    connection_check.inspect_mdx_hierarchy_methods()

    output = capsys.readouterr().out

    assert "method_that_does_not_exist" in output
    assert "Not available" in output


def test_inspect_mdx_methods_displays_signature(
    monkeypatch,
    capsys,
):
    class MockHierarchySet:
        @staticmethod
        def members(dimension, hierarchy):
            return None

    monkeypatch.setattr(
        connection_check,
        "MdxHierarchySet",
        MockHierarchySet,
    )
    monkeypatch.setattr(
        connection_check,
        "MDX_HIERARCHY_METHODS",
        ["members"],
    )

    connection_check.inspect_mdx_hierarchy_methods()

    output = capsys.readouterr().out

    assert "members" in output
    assert "dimension" in output
    assert "hierarchy" in output


def test_mdx_inspection_not_called_by_default(
    monkeypatch,
    successful_connection,
):
    inspect_mock = MagicMock()

    monkeypatch.setattr(
        connection_check,
        "inspect_mdx_hierarchy_methods",
        inspect_mock,
    )

    connection_check.check_tm1_connection(
        inspect_mdx=False,
    )

    inspect_mock.assert_not_called()


def test_mdx_inspection_called_when_requested(
    monkeypatch,
    successful_connection,
):
    inspect_mock = MagicMock()

    monkeypatch.setattr(
        connection_check,
        "inspect_mdx_hierarchy_methods",
        inspect_mock,
    )

    connection_check.check_tm1_connection(
        inspect_mdx=True,
    )

    inspect_mock.assert_called_once_with()


# ============================================================
# Entry-Point Tests
# ============================================================

def test_main_returns_two_for_invalid_sample_size(monkeypatch):
    arguments = MagicMock()
    arguments.sample_size = 0
    arguments.show_samples = False
    arguments.inspect_mdx = False

    monkeypatch.setattr(
        connection_check,
        "parse_arguments",
        lambda: arguments,
    )

    assert connection_check.main() == 2


def test_main_forwards_arguments(monkeypatch):
    arguments = MagicMock()
    arguments.sample_size = 10
    arguments.show_samples = True
    arguments.inspect_mdx = True

    captured_arguments = {}

    def mock_check_tm1_connection(**kwargs):
        captured_arguments.update(kwargs)
        return True

    monkeypatch.setattr(
        connection_check,
        "parse_arguments",
        lambda: arguments,
    )
    monkeypatch.setattr(
        connection_check,
        "check_tm1_connection",
        mock_check_tm1_connection,
    )

    result = connection_check.main()

    assert result == 0
    assert captured_arguments == {
        "show_samples": True,
        "inspect_mdx": True,
        "sample_size": 10,
    }


def test_main_returns_zero_on_success(monkeypatch):
    arguments = MagicMock()
    arguments.sample_size = 5
    arguments.show_samples = False
    arguments.inspect_mdx = False

    monkeypatch.setattr(
        connection_check,
        "parse_arguments",
        lambda: arguments,
    )
    monkeypatch.setattr(
        connection_check,
        "check_tm1_connection",
        lambda **kwargs: True,
    )

    assert connection_check.main() == 0


def test_main_returns_one_on_failure(monkeypatch):
    arguments = MagicMock()
    arguments.sample_size = 5
    arguments.show_samples = False
    arguments.inspect_mdx = False

    monkeypatch.setattr(
        connection_check,
        "parse_arguments",
        lambda: arguments,
    )
    monkeypatch.setattr(
        connection_check,
        "check_tm1_connection",
        lambda **kwargs: False,
    )

    assert connection_check.main() == 1
