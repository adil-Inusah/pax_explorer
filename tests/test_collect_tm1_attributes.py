from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import scripts.collect_tm1_attributes as module


# ============================================================
# Test Models
# ============================================================


@dataclass(frozen=True)
class Attribute:
    name: str
    attribute_type: Any


class Dimensions:
    def __init__(
        self,
        names: list[str],
    ) -> None:
        self.names = list(names)

    def get_all_names(
        self,
    ) -> list[str]:
        return list(self.names)


class Hierarchies:
    def __init__(
        self,
        names_by_dimension: dict[
            str,
            list[str],
        ],
        objects: dict[
            tuple[str, str],
            Any,
        ],
        failures: set[
            tuple[str, str]
        ]
        | None = None,
    ) -> None:
        self.names_by_dimension = {
            dimension: list(names)
            for dimension, names
            in names_by_dimension.items()
        }

        self.objects = dict(objects)
        self.failures = set(
            failures or set()
        )

    def get_all_names(
        self,
        dimension_name: str,
    ) -> list[str]:
        return list(
            self.names_by_dimension.get(
                dimension_name,
                [],
            )
        )

    def get(
        self,
        dimension_name: str,
        hierarchy_name: str,
    ) -> Any:
        key = (
            dimension_name,
            hierarchy_name,
        )

        if key in self.failures:
            raise RuntimeError(
                "hierarchy unavailable"
            )

        return self.objects[key]


class TM1:
    def __init__(
        self,
        dimension_names: list[str],
        hierarchy_names: dict[
            str,
            list[str],
        ],
        hierarchy_objects: dict[
            tuple[str, str],
            Any,
        ],
        failures: set[
            tuple[str, str]
        ]
        | None = None,
    ) -> None:
        self.dimensions = Dimensions(
            dimension_names
        )

        self.hierarchies = Hierarchies(
            hierarchy_names,
            hierarchy_objects,
            failures,
        )


class ConnectionContext:
    def __init__(
        self,
        value: Any | None = None,
    ) -> None:
        self.value = (
            value
            if value is not None
            else object()
        )

    def __enter__(
        self,
    ) -> Any:
        return self.value

    def __exit__(
        self,
        exception_type: Any,
        exception: Any,
        traceback: Any,
    ) -> bool:
        return False


# ============================================================
# Test Fixtures and Helpers
# ============================================================


@pytest.fixture
def collection_time() -> datetime:
    return datetime(
        2026,
        9,
        22,
        23,
        30,
        tzinfo=timezone.utc,
    )


def read_attributes(
    current_root: Path,
) -> list[dict[str, Any]]:
    payload = module.read_json(
        current_root / "attributes.json"
    )

    assert isinstance(payload, list)

    return payload


def complete_manifest() -> dict[str, Any]:
    return {
        "snapshot_id": "test-snapshot",
        "status": "COMPLETE",
        "dimension_count": 0,
        "hierarchy_count": 0,
        "attribute_count": 0,
        "error_count": 0,
    }


def partial_manifest() -> dict[str, Any]:
    return {
        "snapshot_id": "test-snapshot",
        "status": "PARTIAL",
        "dimension_count": 1,
        "hierarchy_count": 1,
        "attribute_count": 0,
        "error_count": 1,
    }


# ============================================================
# Attribute-Type Tests
# ============================================================


@pytest.mark.parametrize(
    (
        "attribute_type",
        "expected_data_type",
        "expected_is_alias",
    ),
    [
        (
            "N",
            "NUMERIC",
            False,
        ),
        (
            "S",
            "STRING",
            False,
        ),
        (
            "A",
            "ALIAS",
            True,
        ),
        (
            1,
            "NUMERIC",
            False,
        ),
        (
            2,
            "STRING",
            False,
        ),
        (
            3,
            "ALIAS",
            True,
        ),
    ],
)
def test_attribute_type_normalization(
    attribute_type: Any,
    expected_data_type: str,
    expected_is_alias: bool,
) -> None:
    (
        _,
        actual_data_type,
        actual_is_alias,
    ) = module.normalize_attribute_type(
        attribute_type
    )

    assert (
        actual_data_type
        == expected_data_type
    )

    assert (
        actual_is_alias
        is expected_is_alias
    )


def test_attribute_type_normalization_supports_enum_shape(
) -> None:
    alias_type = SimpleNamespace(
        name="ALIAS",
        value=3,
    )

    (
        raw_type,
        data_type,
        is_alias,
    ) = module.normalize_attribute_type(
        alias_type
    )

    assert raw_type == "3"
    assert data_type == "ALIAS"
    assert is_alias is True


# ============================================================
# Collection Tests
# ============================================================


def test_collects_sorts_and_deduplicates_attributes(
    tmp_path: Path,
    collection_time: datetime,
) -> None:
    tm1 = TM1(
        dimension_names=[
            "Material",
            "Fiscal Period",
            "material",
        ],
        hierarchy_names={
            "Fiscal Period": [
                "Fiscal Period",
            ],
            "Material": [
                "Material",
            ],
        },
        hierarchy_objects={
            (
                "Fiscal Period",
                "Fiscal Period",
            ): SimpleNamespace(
                element_attributes=[
                    Attribute(
                        "Period Index",
                        "N",
                    ),
                    Attribute(
                        "Alias",
                        "A",
                    ),
                    Attribute(
                        "alias",
                        "A",
                    ),
                ]
            ),
            (
                "Material",
                "Material",
            ): SimpleNamespace(
                element_attributes=[
                    Attribute(
                        "Description",
                        "S",
                    )
                ]
            ),
        },
    )

    current_root = (
        tmp_path / "current"
    )

    manifest = module.collect_attributes(
        tm1,
        snapshot_root=(
            tmp_path / "snapshots"
        ),
        current_root=current_root,
        timestamp=collection_time,
    )

    assert (
        manifest["status"]
        == "COMPLETE"
    )

    assert (
        manifest["dimension_count"]
        == 2
    )

    assert (
        manifest["hierarchy_count"]
        == 2
    )

    assert (
        manifest["attribute_count"]
        == 3
    )

    assert (
        manifest["error_count"]
        == 0
    )

    records = read_attributes(
        current_root
    )

    assert [
        item["attribute_name"]
        for item in records
    ] == [
        "Alias",
        "Period Index",
        "Description",
    ]

    assert records[0]["object_name"] == (
        "Fiscal Period::"
        "Fiscal Period::"
        "Alias"
    )

    assert (
        records[0][
            "attribute_data_type"
        ]
        == "ALIAS"
    )

    assert (
        records[0]["is_alias"]
        is True
    )

    assert (
        records[0][
            "is_default_hierarchy"
        ]
        is True
    )


def test_marks_control_and_alternate_hierarchy(
    tmp_path: Path,
    collection_time: datetime,
) -> None:
    tm1 = TM1(
        dimension_names=[
            "}Clients",
        ],
        hierarchy_names={
            "}Clients": [
                "Leaves",
            ]
        },
        hierarchy_objects={
            (
                "}Clients",
                "Leaves",
            ): SimpleNamespace(
                element_attributes=[
                    Attribute(
                        "}Security",
                        "S",
                    )
                ]
            )
        },
    )

    current_root = (
        tmp_path / "current"
    )

    manifest = module.collect_attributes(
        tm1,
        snapshot_root=(
            tmp_path / "snapshots"
        ),
        current_root=current_root,
        timestamp=collection_time,
    )

    assert (
        manifest["status"]
        == "COMPLETE"
    )

    records = read_attributes(
        current_root
    )

    assert len(records) == 1

    record = records[0]

    assert record["is_control"] is True

    assert (
        record["is_default_hierarchy"]
        is False
    )

    assert (
        record["dimension_name"]
        == "}Clients"
    )

    assert (
        record["hierarchy_name"]
        == "Leaves"
    )

    assert (
        record["attribute_name"]
        == "}Security"
    )


def test_partial_collection_preserves_current_output(
    tmp_path: Path,
    collection_time: datetime,
) -> None:
    current_root = (
        tmp_path / "current"
    )

    current_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    existing_payload = [
        {
            "sentinel": True,
        }
    ]

    module.write_json(
        current_root / "attributes.json",
        existing_payload,
    )

    tm1 = TM1(
        dimension_names=[
            "Good",
            "Bad",
        ],
        hierarchy_names={
            "Good": [
                "Good",
            ],
            "Bad": [
                "Bad",
            ],
        },
        hierarchy_objects={
            (
                "Good",
                "Good",
            ): SimpleNamespace(
                element_attributes=[
                    Attribute(
                        "Alias",
                        "A",
                    )
                ]
            ),
            (
                "Bad",
                "Bad",
            ): SimpleNamespace(
                element_attributes=[]
            ),
        },
        failures={
            (
                "Bad",
                "Bad",
            )
        },
    )

    snapshot_root = (
        tmp_path / "snapshots"
    )

    manifest = module.collect_attributes(
        tm1,
        snapshot_root=snapshot_root,
        current_root=current_root,
        timestamp=collection_time,
    )

    assert (
        manifest["status"]
        == "PARTIAL"
    )

    assert (
        manifest["error_count"]
        == 1
    )

    assert module.read_json(
        current_root / "attributes.json"
    ) == existing_payload
    snapshot_directory = (
    snapshot_root
    / manifest["snapshot_id"]
)

    snapshot_records = module.read_json(
        snapshot_directory
        / "attributes.json"
    )

    assert isinstance(
        snapshot_records,
        list,
    )

    assert len(snapshot_records) == 1

    assert (
        snapshot_records[0][
            "attribute_name"
        ]
        == "Alias"
    )

    snapshot_manifest = module.read_json(
        snapshot_directory
        / "attribute_manifest.json"
    )

    assert isinstance(
        snapshot_manifest,
        dict,
    )

    assert (
        snapshot_manifest["status"]
        == "PARTIAL"
    )

    assert (
        snapshot_manifest["error_count"]
        == 1
    )

    snapshot_manifest = module.read_json(
        snapshot_directory
        / "attribute_manifest.json"
    )

    assert (
        snapshot_manifest["status"]
        == "PARTIAL"
    )


def test_empty_dimension_inventory_completes(
    tmp_path: Path,
    collection_time: datetime,
) -> None:
    tm1 = TM1(
        dimension_names=[],
        hierarchy_names={},
        hierarchy_objects={},
    )

    current_root = (
        tmp_path / "current"
    )

    manifest = module.collect_attributes(
        tm1,
        snapshot_root=(
            tmp_path / "snapshots"
        ),
        current_root=current_root,
        timestamp=collection_time,
    )

    assert (
        manifest["status"]
        == "COMPLETE"
    )

    assert (
        manifest["dimension_count"]
        == 0
    )

    assert (
        manifest["hierarchy_count"]
        == 0
    )

    assert (
        manifest["attribute_count"]
        == 0
    )

    assert (
        manifest["error_count"]
        == 0
    )

    assert read_attributes(
        current_root
    ) == []


# ============================================================
# Runtime Type-Safety Tests
# ============================================================


def test_iterable_items_rejects_string(
) -> None:
    with pytest.raises(
        TypeError,
        match="not a string",
    ):
        module.iterable_items(
            "Fiscal Period",
            description="hierarchies",
        )


def test_iterable_items_rejects_noniterable(
) -> None:
    with pytest.raises(
        TypeError,
        match="not iterable",
    ):
        module.iterable_items(
            object(),
            description="hierarchies",
        )


def test_iterable_items_supports_mapping_values(
) -> None:
    first = SimpleNamespace(
        name="Default",
    )

    second = SimpleNamespace(
        name="Leaves",
    )

    result = module.iterable_items(
        {
            "Default": first,
            "Leaves": second,
        },
        description="hierarchies",
    )

    assert result == [
        first,
        second,
    ]


# ============================================================
# JSON Tests
# ============================================================


def test_read_and_write_json_round_trip(
    tmp_path: Path,
) -> None:
    target = (
        tmp_path / "payload.json"
    )

    payload = {
        "attributes": [
            "Alias",
            "Description",
        ]
    }

    module.write_json(
        target,
        payload,
    )

    assert module.read_json(
        target
    ) == payload


# ============================================================
# CLI Tests
# ============================================================


def test_main_returns_zero_for_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        module,
        "get_tm1_connection",
        lambda: ConnectionContext(),
    )

    monkeypatch.setattr(
        module,
        "collect_attributes",
        lambda tm1: complete_manifest(),
    )

    assert module.main([]) == 0


def test_main_returns_one_for_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        module,
        "get_tm1_connection",
        lambda: ConnectionContext(),
    )

    monkeypatch.setattr(
        module,
        "collect_attributes",
        lambda tm1: partial_manifest(),
    )

    assert module.main([]) == 1


def test_main_returns_one_when_collection_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        module,
        "get_tm1_connection",
        lambda: ConnectionContext(),
    )

    def raise_collection_error(
        tm1: Any,
    ) -> dict[str, Any]:
        raise RuntimeError(
            "collection failed"
        )

    monkeypatch.setattr(
        module,
        "collect_attributes",
        raise_collection_error,
    )

    assert module.main([]) == 1