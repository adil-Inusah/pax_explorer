import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


# ============================================================
# Project Imports
# ============================================================

ROOT_DIR = Path(__file__).resolve().parent.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utilities.tm1_connection import get_tm1_connection


# ============================================================
# Paths
# ============================================================

SNAPSHOT_ROOT = ROOT_DIR / "data" / "snapshots"
LATEST_ROOT = ROOT_DIR / "data" / "current"


# ============================================================
# Collection Definitions
# ============================================================

COLLECTIONS: dict[str, Callable[[Any], list[str]]] = {
    "cube": lambda tm1: list(tm1.cubes.get_all_names()),
    "dimension": lambda tm1: list(tm1.dimensions.get_all_names()),
    "process": lambda tm1: list(tm1.processes.get_all_names()),
    "chore": lambda tm1: list(tm1.chores.get_all_names()),
}


# ============================================================
# Helpers
# ============================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_snapshot_id(timestamp: datetime) -> str:
    return timestamp.strftime("%Y%m%dT%H%M%SZ")


def ensure_directories() -> None:
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    LATEST_ROOT.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    with temporary_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            payload,
            file,
            indent=2,
            ensure_ascii=False,
        )

    temporary_path.replace(path)


def collect_names(
    tm1,
    object_type: str,
    retrieval_function: Callable[[Any], list[str]],
    snapshot_id: str,
    collected_at: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    start_time = time.perf_counter()

    try:
        names = retrieval_function(tm1)
        names = sorted(
            {str(name) for name in names},
            key=str.casefold,
        )

        records = [
            {
                "snapshot_id": snapshot_id,
                "object_type": object_type,
                "object_name": name,
                "collected_at": collected_at,
            }
            for name in names
        ]

        result = {
            "object_type": object_type,
            "status": "PASS",
            "record_count": len(records),
            "elapsed_seconds": round(
                time.perf_counter() - start_time,
                3,
            ),
            "error": None,
        }

        return records, result

    except Exception as error:
        result = {
            "object_type": object_type,
            "status": "FAIL",
            "record_count": 0,
            "elapsed_seconds": round(
                time.perf_counter() - start_time,
                3,
            ),
            "error": (
                f"{type(error).__name__}: {error}"
            ),
        }

        return [], result


# ============================================================
# Collector
# ============================================================

def collect_tm1_metadata() -> dict[str, Any]:
    ensure_directories()

    started_at = utc_now()
    snapshot_id = build_snapshot_id(started_at)
    collected_at = started_at.isoformat()

    snapshot_directory = (
        SNAPSHOT_ROOT / snapshot_id
    )
    snapshot_directory.mkdir(
        parents=True,
        exist_ok=False,
    )

    all_objects: list[dict[str, Any]] = []
    collection_results: list[dict[str, Any]] = []

    manifest: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "status": "RUNNING",
        "started_at": collected_at,
        "completed_at": None,
        "tm1_version": None,
        "object_count": 0,
        "collections": [],
    }

    write_json(
        snapshot_directory / "manifest.json",
        manifest,
    )

    try:
        with get_tm1_connection() as tm1:
            manifest["tm1_version"] = (
                tm1.server.version
            )

            for object_type, retrieval_function in (
                COLLECTIONS.items()
            ):
                records, result = collect_names(
                    tm1=tm1,
                    object_type=object_type,
                    retrieval_function=retrieval_function,
                    snapshot_id=snapshot_id,
                    collected_at=collected_at,
                )

                all_objects.extend(records)
                collection_results.append(result)

        failed_collections = [
            result
            for result in collection_results
            if result["status"] != "PASS"
        ]

        completed_at = utc_now()

        manifest.update(
            {
                "status": (
                    "PARTIAL"
                    if failed_collections
                    else "COMPLETE"
                ),
                "completed_at": (
                    completed_at.isoformat()
                ),
                "object_count": len(all_objects),
                "collections": collection_results,
            }
        )

        write_json(
            snapshot_directory / "objects.json",
            all_objects,
        )
        write_json(
            snapshot_directory / "manifest.json",
            manifest,
        )

        if manifest["status"] == "COMPLETE":
            write_json(
                LATEST_ROOT / "objects.json",
                all_objects,
            )
            write_json(
                LATEST_ROOT / "manifest.json",
                manifest,
            )

        return manifest

    except Exception as error:
        manifest.update(
            {
                "status": "FAILED",
                "completed_at": utc_now().isoformat(),
                "error": (
                    f"{type(error).__name__}: {error}"
                ),
                "collections": collection_results,
            }
        )

        write_json(
            snapshot_directory / "manifest.json",
            manifest,
        )

        raise


# ============================================================
# Console Output
# ============================================================

def print_manifest(manifest: dict[str, Any]) -> None:
    print()
    print("=" * 70)
    print("TM1 METADATA COLLECTION")
    print("=" * 70)
    print(
        f"Snapshot ID : {manifest['snapshot_id']}"
    )
    print(
        f"TM1 Version : {manifest['tm1_version']}"
    )
    print(
        f"Status      : {manifest['status']}"
    )
    print(
        f"Objects     : {manifest['object_count']:,}"
    )

    print()
    print("Collections:")

    for result in manifest["collections"]:
        print(
            f"  {result['object_type']:<12} "
            f"{result['status']:<7} "
            f"{result['record_count']:>8,} "
            f"{result['elapsed_seconds']:>8.3f}s"
        )

        if result["error"]:
            print(f"    {result['error']}")


def main() -> int:
    try:
        manifest = collect_tm1_metadata()
        print_manifest(manifest)

        return (
            0
            if manifest["status"] == "COMPLETE"
            else 1
        )

    except Exception as error:
        print()
        print("TM1 metadata collection failed.")
        print(
            f"{type(error).__name__}: {error}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
