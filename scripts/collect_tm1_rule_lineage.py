from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from parsers.rule_parser import (
    parse_rules,
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


def get_cube_rule_text(cube: Any) -> str:
    """Normalize likely TM1py cube rule property names.

    TM1py versions and mocked test objects may expose cube rules under
    different property names. Return an empty string when the cube has no
    rules. Raise only when the value is present but cannot be normalized.
    """
    candidates = (
        "rules",
        "rule",
        "Rules",
        "Rule",
        "rule_text",
        "RuleText",
    )
    for property_name in candidates:
        if not hasattr(cube, property_name):
            continue
        value = getattr(cube, property_name)
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        # Some clients wrap rule text in an object.
        for text_property in ("text", "Text", "rules", "Rules"):
            if hasattr(value, text_property):
                nested_value = getattr(value, text_property)
                if nested_value is None:
                    return ""
                if isinstance(nested_value, str):
                    return nested_value
        raise TypeError(
            f"Unsupported rule value type for {property_name}: "
            f"{type(value).__name__}"
        )
    return ""


def collect_rule_lineage() -> dict[str, Any]:
    current_objects_path = CURRENT_ROOT / "objects.json"
    if not current_objects_path.exists():
        raise FileNotFoundError(
            "Current object catalog was not found. "
            "Run collect_tm1_metadata.py first."
        )

    object_catalog = read_json(current_objects_path)
    started_at = utc_now()
    run_snapshot_id = snapshot_id(started_at)
    output_directory = (
        SNAPSHOT_ROOT / run_snapshot_id / "rule_lineage"
    )

    cube_rule_definitions: list[dict[str, Any]] = []
    all_evidence = []
    errors: list[dict[str, str]] = []
    cubes_with_rules = 0

    with get_tm1_connection() as tm1:
        cube_names = sorted(
            tm1.cubes.get_all_names(),
            key=str.casefold,
        )
        for cube_name in cube_names:
            try:
                cube = tm1.cubes.get(cube_name)
                rule_text = get_cube_rule_text(cube)
                evidence = parse_rules(cube_name, rule_text)
                all_evidence.extend(evidence)

                has_rules = bool(rule_text.strip())
                if has_rules:
                    cubes_with_rules += 1

                cube_rule_definitions.append(
                    {
                        "cube_name": cube_name,
                        "has_rules": has_rules,
                        "rule_text": rule_text,
                        "rule_character_count": len(rule_text),
                        "relationship_evidence_count": len(evidence),
                    }
                )
            except Exception as error:
                errors.append(
                    {
                        "cube_name": cube_name,
                        "error": f"{type(error).__name__}: {error}",
                    }
                )

    summaries = summarize_relationships(all_evidence)
    validations = validate_relationships(summaries, object_catalog)

    evidence_payload = [record.to_dict() for record in all_evidence]
    summary_payload = [record.to_dict() for record in summaries]
    completed_at = utc_now()

    manifest = {
        "snapshot_id": run_snapshot_id,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "status": "PARTIAL" if errors else "COMPLETE",
        "cube_count": len(cube_rule_definitions),
        "cube_with_rules_count": cubes_with_rules,
        "evidence_count": len(evidence_payload),
        "relationship_count": len(summary_payload),
        "validation_count": len(validations),
        "error_count": len(errors),
    }

    write_json(
        output_directory / "cube_rule_definitions.json",
        cube_rule_definitions,
    )
    write_json(
        output_directory / "relationship_evidence.json",
        evidence_payload,
    )
    write_json(
        output_directory / "relationships.json",
        summary_payload,
    )
    write_json(
        output_directory / "relationship_validations.json",
        validations,
    )
    write_json(output_directory / "errors.json", errors)
    write_json(output_directory / "manifest.json", manifest)

    # Publish current data only for a complete run. Rule output names remain
    # distinct from TI lineage output so neither collector overwrites the other.
    if manifest["status"] == "COMPLETE":
        write_json(
            CURRENT_ROOT / "cube_rule_definitions.json",
            cube_rule_definitions,
        )
        write_json(
            CURRENT_ROOT / "rule_relationship_evidence.json",
            evidence_payload,
        )
        write_json(
            CURRENT_ROOT / "rule_relationships.json",
            summary_payload,
        )
        write_json(
            CURRENT_ROOT / "rule_relationship_validations.json",
            validations,
        )
        write_json(
            CURRENT_ROOT / "rule_lineage_manifest.json",
            manifest,
        )

    return manifest


def print_manifest(manifest: dict[str, Any]) -> None:
    print("=" * 70)
    print("TM1 CUBE RULE LINEAGE")
    print("=" * 70)
    print(f"Snapshot        : {manifest['snapshot_id']}")
    print(f"Status          : {manifest['status']}")
    print(f"Cubes           : {manifest['cube_count']:,}")
    print(f"Cubes with rules: {manifest['cube_with_rules_count']:,}")
    print(f"Evidence        : {manifest['evidence_count']:,}")
    print(f"Relationships   : {manifest['relationship_count']:,}")
    print(f"Validations     : {manifest['validation_count']:,}")
    print(f"Errors          : {manifest['error_count']:,}")


def main() -> int:
    try:
        manifest = collect_rule_lineage()
        print_manifest(manifest)
        return 0 if manifest["status"] == "COMPLETE" else 1
    except Exception as error:
        print(
            "Rule lineage collection failed: "
            f"{type(error).__name__}: {error}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
