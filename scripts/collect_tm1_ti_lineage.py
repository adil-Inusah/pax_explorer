from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parsers.ti_parser import (
    parse_process,
    summarize_relationships,
    validate_relationships,
)
from utilities.tm1_connection import get_tm1_connection


CURRENT_ROOT = ROOT_DIR / "data" / "current"
SNAPSHOT_ROOT = ROOT_DIR / "data" / "snapshots"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def snapshot_id(timestamp: datetime) -> str:
    return timestamp.strftime("%Y%m%dT%H%M%SZ")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
    temporary_path.replace(path)


def get_process_procedures(process: Any) -> dict[str, str]:
    """Normalize TM1py process procedure property names."""

    candidates = {
        "Prolog": ("prolog_procedure", "PrologProcedure"),
        "Metadata": ("metadata_procedure", "MetadataProcedure"),
        "Data": ("data_procedure", "DataProcedure"),
        "Epilog": ("epilog_procedure", "EpilogProcedure"),
    }

    procedures: dict[str, str] = {}
    for procedure_name, property_names in candidates.items():
        value = ""
        for property_name in property_names:
            if hasattr(process, property_name):
                value = getattr(process, property_name) or ""
                break
        procedures[procedure_name] = value

    return procedures


def collect_ti_lineage() -> dict[str, Any]:
    current_objects_path = CURRENT_ROOT / "objects.json"
    if not current_objects_path.exists():
        raise FileNotFoundError(
            "Current object catalog was not found. Run collect_tm1_metadata.py first."
        )

    object_catalog = read_json(current_objects_path)
    started_at = utc_now()
    run_snapshot_id = snapshot_id(started_at)
    output_directory = SNAPSHOT_ROOT / run_snapshot_id / "ti_lineage"

    process_definitions: list[dict[str, Any]] = []
    all_evidence = []
    errors: list[dict[str, str]] = []

    with get_tm1_connection() as tm1:
        process_names = sorted(
            tm1.processes.get_all_names(), key=str.casefold
        )

        for process_name in process_names:
            try:
                process = tm1.processes.get(process_name)
                procedures = get_process_procedures(process)
                evidence = parse_process(process_name, procedures)
                all_evidence.extend(evidence)

                process_definitions.append(
                    {
                        "process_name": process_name,
                        "procedures": procedures,
                        "relationship_count": len(evidence),
                    }
                )
            except Exception as error:
                errors.append(
                    {
                        "process_name": process_name,
                        "error": f"{type(error).__name__}: {error}",
                    }
                )

    summaries = summarize_relationships(all_evidence)
    validations = validate_relationships(summaries, object_catalog)

    evidence_payload = [record.to_dict() for record in all_evidence]
    summary_payload = [record.to_dict() for record in summaries]

    manifest = {
        "snapshot_id": run_snapshot_id,
        "started_at": started_at.isoformat(),
        "completed_at": utc_now().isoformat(),
        "status": "PARTIAL" if errors else "COMPLETE",
        "process_count": len(process_definitions),
        "evidence_count": len(evidence_payload),
        "relationship_count": len(summary_payload),
        "error_count": len(errors),
    }

    write_json(output_directory / "process_definitions.json", process_definitions)
    write_json(output_directory / "relationship_evidence.json", evidence_payload)
    write_json(output_directory / "relationships.json", summary_payload)
    write_json(output_directory / "relationship_validations.json", validations)
    write_json(output_directory / "errors.json", errors)
    write_json(output_directory / "manifest.json", manifest)

    if manifest["status"] == "COMPLETE":
        write_json(
            CURRENT_ROOT / "ti_process_definitions.json",
            process_definitions,
        )
        write_json(
            CURRENT_ROOT / "ti_relationship_evidence.json",
            evidence_payload,
        )
        write_json(
            CURRENT_ROOT / "ti_relationships.json",
            summary_payload,
        )
        write_json(
            CURRENT_ROOT / "ti_relationship_validations.json",
            validations,
        )
        write_json(
            CURRENT_ROOT / "ti_lineage_manifest.json",
            manifest,
        )

    return manifest


def main() -> int:
    try:
        manifest = collect_ti_lineage()
        print("=" * 70)
        print("TM1 TI PROCESS LINEAGE")
        print("=" * 70)
        print(f"Snapshot      : {manifest['snapshot_id']}")
        print(f"Status        : {manifest['status']}")
        print(f"Processes     : {manifest['process_count']:,}")
        print(f"Evidence      : {manifest['evidence_count']:,}")
        print(f"Relationships : {manifest['relationship_count']:,}")
        print(f"Errors        : {manifest['error_count']:,}")
        return 0 if manifest["status"] == "COMPLETE" else 1
    except Exception as error:
        print(f"Lineage collection failed: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
