import json
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

import scripts.collect_tm1_metadata as collector


@pytest.fixture
def mock_tm1():
    tm1 = MagicMock()
    tm1.server.version = "12.0.0"
    tm1.cubes.get_all_names.return_value = [
        "Cube B",
        "Cube A",
        "Cube A",
    ]
    tm1.dimensions.get_all_names.return_value = [
        "Dimension A",
    ]
    tm1.processes.get_all_names.return_value = [
        "Process A",
    ]
    tm1.chores.get_all_names.return_value = [
        "Chore A",
    ]
    return tm1


def test_collect_names_sorts_and_deduplicates(mock_tm1):
    records, result = collector.collect_names(
        tm1=mock_tm1,
        object_type="cube",
        retrieval_function=(
            lambda tm1: list(
                tm1.cubes.get_all_names()
            )
        ),
        snapshot_id="snapshot-1",
        collected_at="2026-09-21T00:00:00+00:00",
    )

    assert [
        record["object_name"]
        for record in records
    ] == ["Cube A", "Cube B"]
    assert result["status"] == "PASS"
    assert result["record_count"] == 2


def test_collect_names_captures_failure(mock_tm1):
    def failed_retrieval(tm1):
        raise PermissionError("denied")

    records, result = collector.collect_names(
        tm1=mock_tm1,
        object_type="process",
        retrieval_function=failed_retrieval,
        snapshot_id="snapshot-1",
        collected_at="2026-09-21T00:00:00+00:00",
    )

    assert records == []
    assert result["status"] == "FAIL"
    assert result["record_count"] == 0
    assert result["error"] == (
        "PermissionError: denied"
    )


def test_write_json_replaces_target_atomically(tmp_path):
    output = tmp_path / "output.json"

    collector.write_json(
        output,
        {"status": "complete"},
    )

    assert output.exists()
    assert not (
        tmp_path / "output.json.tmp"
    ).exists()

    with output.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    assert payload == {"status": "complete"}


def test_collect_tm1_metadata_creates_complete_snapshot(
    monkeypatch,
    mock_tm1,
    tmp_path,
):
    snapshot_root = tmp_path / "snapshots"
    latest_root = tmp_path / "current"

    monkeypatch.setattr(
        collector,
        "SNAPSHOT_ROOT",
        snapshot_root,
    )
    monkeypatch.setattr(
        collector,
        "LATEST_ROOT",
        latest_root,
    )
    monkeypatch.setattr(
        collector,
        "get_tm1_connection",
        lambda: nullcontext(mock_tm1),
    )

    manifest = collector.collect_tm1_metadata()

    assert manifest["status"] == "COMPLETE"
    assert manifest["tm1_version"] == "12.0.0"
    assert manifest["object_count"] == 5

    snapshot_directory = (
        snapshot_root / manifest["snapshot_id"]
    )

    assert (
        snapshot_directory / "manifest.json"
    ).exists()
    assert (
        snapshot_directory / "objects.json"
    ).exists()
    assert (
        latest_root / "manifest.json"
    ).exists()
    assert (
        latest_root / "objects.json"
    ).exists()


def test_partial_snapshot_does_not_replace_current(
    monkeypatch,
    mock_tm1,
    tmp_path,
):
    mock_tm1.chores.get_all_names.side_effect = (
        PermissionError("chore access denied")
    )

    snapshot_root = tmp_path / "snapshots"
    latest_root = tmp_path / "current"

    monkeypatch.setattr(
        collector,
        "SNAPSHOT_ROOT",
        snapshot_root,
    )
    monkeypatch.setattr(
        collector,
        "LATEST_ROOT",
        latest_root,
    )
    monkeypatch.setattr(
        collector,
        "get_tm1_connection",
        lambda: nullcontext(mock_tm1),
    )

    manifest = collector.collect_tm1_metadata()

    assert manifest["status"] == "PARTIAL"
    assert not (
        latest_root / "manifest.json"
    ).exists()
    assert not (
        latest_root / "objects.json"
    ).exists()


def test_main_returns_zero_for_complete(monkeypatch):
    monkeypatch.setattr(
        collector,
        "collect_tm1_metadata",
        lambda: {
            "snapshot_id": "snapshot-1",
            "tm1_version": "12.0.0",
            "status": "COMPLETE",
            "object_count": 0,
            "collections": [],
        },
    )

    assert collector.main() == 0


def test_main_returns_one_for_partial(monkeypatch):
    monkeypatch.setattr(
        collector,
        "collect_tm1_metadata",
        lambda: {
            "snapshot_id": "snapshot-1",
            "tm1_version": "12.0.0",
            "status": "PARTIAL",
            "object_count": 0,
            "collections": [],
        },
    )

    assert collector.main() == 1
