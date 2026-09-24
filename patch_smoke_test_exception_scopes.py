from __future__ import annotations

import argparse
from pathlib import Path

OLD_EXPECTED = '''    expected_registered_edges = sum(
        record.get("relationship_count", 0)
        for record in exception_by_key.values()
        if isinstance(record.get("relationship_count"), int)
    )
'''
NEW_EXPECTED = '''    published_exceptions = [
        record
        for record in exception_by_key.values()
        if record.get("applies_to_validation_records", True) is True
    ]
    provenance_exceptions = [
        record
        for record in exception_by_key.values()
        if record.get("applies_to_validation_records", True) is not True
    ]
    expected_registered_edges = sum(
        record.get("relationship_count", 0)
        for record in published_exceptions
        if isinstance(record.get("relationship_count"), int)
    )
    provenance_relationship_count = sum(
        record.get("relationship_count", 0)
        for record in provenance_exceptions
        if isinstance(record.get("relationship_count"), int)
    )
'''
OLD_RETURN = '''    return {
        "registered_exception_targets": len(exception_by_key),
        "registered_exception_records": len(applied_records),
        "excluded_from_quality_score": len(excluded_records),
        "raw_missing_target_records": len(raw_issue_records),
        "unregistered_missing_target_records": len(unregistered_issue_records),
    }
'''
NEW_RETURN = '''    return {
        "registered_exception_targets": len(exception_by_key),
        "published_exception_targets": len(published_exceptions),
        "registered_exception_records": len(applied_records),
        "provenance_candidate_targets": len(provenance_exceptions),
        "provenance_candidate_records": provenance_relationship_count,
        "excluded_from_quality_score": len(excluded_records),
        "raw_missing_target_records": len(raw_issue_records),
        "unregistered_missing_target_records": len(unregistered_issue_records),
    }
'''
OLD_SCALARS = '''        "registered_exception_targets",
        "registered_exception_records",
        "excluded_from_quality_score",
'''
NEW_SCALARS = '''        "registered_exception_targets",
        "published_exception_targets",
        "registered_exception_records",
        "provenance_candidate_targets",
        "provenance_candidate_records",
        "excluded_from_quality_score",
'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch holistic_smoke_test.py for published/provenance exception scopes.")
    parser.add_argument("path", nargs="?", type=Path, default=Path("holistic_smoke_test.py"))
    args = parser.parse_args()
    path: Path = args.path
    text = path.read_text(encoding="utf-8")
    replacements = (
        (OLD_EXPECTED, NEW_EXPECTED, "expected edge calculation"),
        (OLD_RETURN, NEW_RETURN, "exception metrics return"),
        (OLD_SCALARS, NEW_SCALARS, "summary scalar keys"),
    )
    for old, new, label in replacements:
        if new in text:
            continue
        if old not in text:
            raise ValueError(f"Unable to locate {label}; the smoke test differs from the expected refactor baseline.")
        text = text.replace(old, new, 1)
    backup = path.with_suffix(path.suffix + ".pre_parameter_refactor.bak")
    backup.write_bytes(path.read_bytes())
    path.write_text(text, encoding="utf-8")
    print(f"path={path}")
    print(f"backup={backup}")
    print("patch=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
