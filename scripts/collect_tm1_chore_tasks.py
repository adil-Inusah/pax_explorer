from __future__ import annotations

"""Collect TM1 chore tasks, ordered process calls, and parameter bindings."""

import argparse
import json
import sys
from collections.abc import Iterable as IterableABC, Mapping
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utilities.tm1_connection import get_tm1_connection

SNAPSHOT_ROOT = ROOT_DIR / "data" / "snapshots"
CURRENT_ROOT = ROOT_DIR / "data" / "current"
OBJECTS_FILE = CURRENT_ROOT / "objects.json"
PROCESS_PARAMETERS_FILE = CURRENT_ROOT / "ti_process_parameters.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_snapshot_id(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def clean(value: Any) -> str:
    return str(value or "").strip()


def normalized_key(value: Any) -> str:
    return " ".join(clean(value).casefold().split())


def is_control_name(value: Any) -> bool:
    return clean(value).startswith("}")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write("\n")
    temporary.replace(path)


def iterable_items(value: Any, *, description: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return list(value.values())
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{description} must be a collection, not a string.")
    if not isinstance(value, IterableABC):
        raise TypeError(
            f"{description} is not iterable. Received: {type(value).__name__}"
        )
    return list(value)


def property_value(value: Any, *names: str) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        result = getattr(value, name, None)
        if result is not None:
            return result
    return None


def catalog_names(payload: Any, object_type: str) -> set[str]:
    if not isinstance(payload, list):
        raise TypeError("objects.json must contain a JSON array.")
    return {
        normalized_key(record.get("object_name"))
        for record in payload
        if isinstance(record, Mapping)
        and normalized_key(record.get("object_type")) == normalized_key(object_type)
        and clean(record.get("object_name"))
    }


def process_parameter_keys(payload: Any) -> set[tuple[str, str]]:
    if not isinstance(payload, list):
        return set()
    result: set[tuple[str, str]] = set()
    for record in payload:
        if not isinstance(record, Mapping):
            continue
        process_name = clean(
            record.get("process_name") or record.get("source_process")
        )
        parameter_name = clean(
            record.get("parameter_name") or record.get("name")
        )
        if process_name and parameter_name:
            result.add((normalized_key(process_name), normalized_key(parameter_name)))
    return result


def get_chore_service(tm1: Any) -> Any:
    service = getattr(tm1, "chores", None)
    if service is None:
        raise AttributeError("TM1 connection does not expose a chore service.")
    return service


def get_chores(
    service: Any,
) -> list[Any]:
    get_all = getattr(
        service,
        "get_all",
        None,
    )

    if callable(get_all):
        chore_collection = get_all()

        return iterable_items(
            chore_collection,
            description="TM1 chore collection",
        )

    get_all_names = getattr(
        service,
        "get_all_names",
        None,
    )

    get_chore = getattr(
        service,
        "get",
        None,
    )

    if not callable(get_all_names):
        raise AttributeError(
            "The chore service does not support "
            "get_all() or get_all_names()."
        )

    if not callable(get_chore):
        raise AttributeError(
            "The chore service does not support "
            "get_all() or get()."
        )

    name_collection = get_all_names()

    chore_names = sorted_unique_names(
        iterable_items(
            name_collection,
            description=(
                "TM1 chore-name collection"
            ),
        )
    )

    chores: list[Any] = []

    for chore_name in chore_names:
        chores.append(
            get_chore(chore_name)
        )

    return chores


def sorted_unique_names(
    values: Iterable[Any],
) -> list[str]:
    names_by_key: dict[str, str] = {}

    for value in values:
        name = clean(
            getattr(
                value,
                "name",
                value,
            )
        )

        if not name:
            continue

        names_by_key.setdefault(
            normalized_key(name),
            name,
        )

    return sorted(
        names_by_key.values(),
        key=str.casefold,
    )

def chore_name_of(chore: Any) -> str:
    return clean(property_value(chore, "name", "Name"))


def chore_active_of(chore: Any) -> bool | None:
    value = property_value(chore, "active", "Active")
    return value if isinstance(value, bool) else None


def chore_tasks_of(chore: Any) -> list[Any]:
    return iterable_items(
        property_value(chore, "tasks", "Tasks"),
        description=f"Tasks for chore {chore_name_of(chore)}",
    )


def task_step_of(task: Any, fallback: int) -> int:
    value = property_value(task, "step", "Step")
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def task_process_of(task: Any) -> str:
    direct = clean(property_value(task, "process_name", "ProcessName"))
    if direct:
        return direct
    process = property_value(task, "Process", "process")
    if process is not None:
        return clean(property_value(process, "Name", "name") or process)
    return ""


def task_parameters_of(task: Any) -> list[dict[str, str]]:
    raw = iterable_items(
        property_value(task, "parameters", "Parameters"),
        description="Chore task parameter collection",
    )
    result: list[dict[str, str]] = []
    for parameter in raw:
        name = clean(property_value(parameter, "Name", "name"))
        value = clean(property_value(parameter, "Value", "value"))
        if name:
            result.append({"parameter_name": name, "parameter_value": value})
    return result


def chore_node_id(name: str) -> str:
    return f"chore::{name}"


def task_node_id(chore_name: str, step: int) -> str:
    return f"chore-task::{chore_name}::{step:04d}"


def process_node_id(name: str) -> str:
    return f"process::{name}"


def parameter_node_id(process_name: str, parameter_name: str) -> str:
    return f"process-parameter::{process_name}::{parameter_name}"


def collect_chore_tasks(
    tm1: Any,
    *,
    object_catalog_path: Path | None = None,
    process_parameters_path: Path | None = None,
    snapshot_root: Path = SNAPSHOT_ROOT,
    current_root: Path = CURRENT_ROOT,
    timestamp: datetime | None = None,
    publish_current: bool = True,
) -> dict[str, Any]:
    started = timestamp or utc_now()
    started_clock = perf_counter()
    run_id = build_snapshot_id(started)
    collected_at = started.isoformat()
    snapshot_dir = snapshot_root / run_id

    process_catalog_loaded = False
    process_names: set[str] = set()

    if (
        object_catalog_path is not None
        and object_catalog_path.is_file()
    ):
        process_catalog_loaded = True

        process_names = catalog_names(
            read_json(object_catalog_path),
            "process",
        )


    parameter_catalog_loaded = False
    parameter_keys: set[
        tuple[str, str]
    ] = set()

    if (
        process_parameters_path is not None
        and process_parameters_path.is_file()
    ):
        parameter_catalog_loaded = True

        parameter_keys = (
            process_parameter_keys(
                read_json(
                    process_parameters_path
                )
            )
        )



    chores = get_chores(get_chore_service(tm1))
    chores.sort(key=lambda item: chore_name_of(item).casefold())

    task_records: list[dict[str, Any]] = []
    binding_records: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    seen_relationship_ids: set[str] = set()

    for chore in chores:
        chore_name = chore_name_of(chore)
        if not chore_name:
            errors.append({"chore_name": None, "stage": "PARSE_CHORE", "error": "Chore has no name"})
            continue
        tasks = chore_tasks_of(chore)
        parsed_tasks: list[tuple[int, Any]] = [
            (task_step_of(task, index), task)
            for index, task in enumerate(tasks, start=1)
        ]
        parsed_tasks.sort(key=lambda pair: pair[0])

        for step, task in parsed_tasks:
            task_id = task_node_id(chore_name, step)
            if task_id in seen_task_ids:
                errors.append({
                    "chore_name": chore_name,
                    "task_step": step,
                    "stage": "DEDUPLICATE_TASK",
                    "error": f"Duplicate chore task identity: {task_id}",
                })
                continue
            seen_task_ids.add(task_id)
            process_name = task_process_of(task)
            parameters = task_parameters_of(task)
            task_records.append({
                "snapshot_id": run_id,
                "collected_at": collected_at,
                "node_id": task_id,
                "node_type": "CHORE_TASK",
                "object_type": "chore_task",
                "object_name": f"{chore_name}::{step:04d}",
                "qualified_name": f"{chore_name}::{step:04d}",
                "chore_name": chore_name,
                "task_step": step,
                "process_name": process_name,
                "parameter_count": len(parameters),
                "is_chore_active": chore_active_of(chore),
                "is_control": is_control_name(chore_name) or is_control_name(process_name),
            })

            ownership_id = f"chore-task-ownership::{chore_name}::{step:04d}"
            call_id = f"chore-task-call::{chore_name}::{step:04d}::{process_name}"
            for relationship_id, source_id, source_type, target_id, target_type, relationship_type, status in (
                (
                    ownership_id,
                    chore_node_id(chore_name),
                    "CHORE",
                    task_id,
                    "CHORE_TASK",
                    "HAS_TASK",
                    "VALID",
                ),
                (
                    call_id,
                    task_id,
                    "CHORE_TASK",
                    process_node_id(process_name),
                    "PROCESS",
                    "CALLS_PROCESS",
                    (
                        "VALID"
                        if process_name and (
                            not process_catalog_loaded
                            or normalized_key(process_name) in process_names
                        )
                        else "PROCESS_NOT_IN_CATALOG"
                    ),
                ),
            ):
                if relationship_id in seen_relationship_ids:
                    continue
                seen_relationship_ids.add(relationship_id)
                relationships.append({
                    "snapshot_id": run_id,
                    "relationship_id": relationship_id,
                    "source_id": source_id,
                    "source_type": source_type,
                    "target_id": target_id,
                    "target_type": target_type,
                    "relationship_type": relationship_type,
                    "relationship_origin": "CHORE",
                    "resolution_method": "CATALOG_MATCH",
                })
                validations.append({
                    "snapshot_id": run_id,
                    "validation_id": f"validation::{relationship_id}",
                    "relationship_id": relationship_id,
                    "validation_status": status,
                    "source_id": source_id,
                    "target_id": target_id,
                })

            for position, parameter in enumerate(parameters, start=1):
                parameter_name = parameter["parameter_name"]
                parameter_value = parameter["parameter_value"]
                binding_id = f"chore-binding::{chore_name}::{step:04d}::{position:04d}"
                target_id = parameter_node_id(process_name, parameter_name)
                binding_records.append({
                    "snapshot_id": run_id,
                    "binding_id": binding_id,
                    "chore_name": chore_name,
                    "task_step": step,
                    "task_id": task_id,
                    "process_name": process_name,
                    "parameter_name": parameter_name,
                    "parameter_value": parameter_value,
                    "parameter_position": position,
                })
                relationships.append({
                    "snapshot_id": run_id,
                    "relationship_id": binding_id,
                    "source_id": task_id,
                    "source_type": "CHORE_TASK",
                    "target_id": target_id,
                    "target_type": "PROCESS_PARAMETER",
                    "relationship_type": "PASSES_PARAMETER",
                    "relationship_origin": "CHORE",
                    "resolution_method": "LITERAL_BINDING",
                    "parameter_value": parameter_value,
                })
                parameter_known = (
                    not parameter_catalog_loaded
                    or (
                        normalized_key(process_name),
                        normalized_key(parameter_name),
                    ) in parameter_keys
                )
                validations.append({
                    "snapshot_id": run_id,
                    "validation_id": f"validation::{binding_id}",
                    "relationship_id": binding_id,
                    "validation_status": (
                        "VALID_PARAMETER_BINDING"
                        if parameter_known
                        else "UNKNOWN_PROCESS_PARAMETER"
                    ),
                    "source_id": task_id,
                    "target_id": target_id,
                })

    task_records.sort(key=lambda item: (item["chore_name"].casefold(), item["task_step"]))
    binding_records.sort(key=lambda item: item["binding_id"].casefold())
    relationships.sort(key=lambda item: item["relationship_id"].casefold())
    validations.sort(key=lambda item: item["relationship_id"].casefold())
    status = "PARTIAL" if errors else "COMPLETE"
    manifest = {
        "snapshot_id": run_id,
        "status": status,
        "started_at": collected_at,
        "completed_at": utc_now().isoformat(),
        "chore_count": len(chores),
        "task_count": len(task_records),
        "binding_count": len(binding_records),
        "relationship_count": len(relationships),
        "validation_count": len(validations),
        "error_count": len(errors),
        "total_seconds": round(perf_counter() - started_clock, 6),
        "publish_current_requested": publish_current,
        "published_current": bool(status == "COMPLETE" and publish_current),
    }
    outputs = {
        "chore_tasks.json": task_records,
        "chore_task_bindings.json": binding_records,
        "chore_relationships.json": relationships,
        "chore_relationship_validations.json": validations,
        "chore_lineage_manifest.json": manifest,
        "chore_collection_errors.json": errors,
    }
    for file_name, payload in outputs.items():
        write_json(snapshot_dir / file_name, payload)
    if status == "COMPLETE" and publish_current:
        for file_name, payload in outputs.items():
            write_json(current_root / file_name, payload)
    return manifest


def print_manifest(manifest: dict[str, Any]) -> None:
    print("=" * 70)
    print("TM1 CHORE TASK LINEAGE")
    print("=" * 70)
    print(f"Snapshot      : {manifest.get('snapshot_id', 'UNKNOWN')}")
    print(f"Status        : {manifest.get('status', 'UNKNOWN')}")
    print(f"Chores        : {int(manifest.get('chore_count', 0) or 0):,}")
    print(f"Tasks         : {int(manifest.get('task_count', 0) or 0):,}")
    print(f"Bindings      : {int(manifest.get('binding_count', 0) or 0):,}")
    print(f"Relationships : {int(manifest.get('relationship_count', 0) or 0):,}")
    print(f"Validations   : {int(manifest.get('validation_count', 0) or 0):,}")
    print(f"Errors        : {int(manifest.get('error_count', 0) or 0):,}")
    print(f"Published     : {bool(manifest.get('published_current', False))}")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect ordered TM1 chore task lineage.")
    parser.add_argument("--object-catalog", type=Path, default=OBJECTS_FILE)
    parser.add_argument("--process-parameters", type=Path, default=PROCESS_PARAMETERS_FILE)
    parser.add_argument("--no-publish", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        with get_tm1_connection() as tm1:
            manifest = collect_chore_tasks(
                tm1,
                object_catalog_path=args.object_catalog,
                process_parameters_path=args.process_parameters,
                publish_current=not args.no_publish,
            )
        print_manifest(manifest)
        return 0 if manifest.get("status") == "COMPLETE" else 1
    except Exception as error:
        print(f"Chore task collection failed: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
