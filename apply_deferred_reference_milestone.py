from __future__ import annotations

import argparse
from pathlib import Path

CORE_CONSTANTS = '''\nCATALOG_GOVERNED_TARGET_TYPES = frozenset(\n    {"cube", "dimension", "process", "chore"}\n)\nDEFERRED_TARGET_STATUS = {\n    "attribute": "ATTRIBUTE_CATALOG_DEFERRED",\n    "hierarchy": "HIERARCHY_CATALOG_DEFERRED",\n    "subset": "SUBSET_CATALOG_DEFERRED",\n    "view": "VIEW_CATALOG_DEFERRED",\n    "file": "DYNAMIC_EXTERNAL_FILE",\n    "command": "DYNAMIC_EXTERNAL_COMMAND",\n}\n'''

OLD_REJECTION_FIELDS = '''    rejection_reason: str\n    provenance: ResolutionContext\n'''
NEW_REJECTION_FIELDS = '''    rejection_reason: str\n    reference_class: str\n    catalog_status: str\n    requires_quality_exception: bool\n    provenance: ResolutionContext\n'''

OLD_MISSING_BLOCK = '''            if catalog_match is None:\n                rejections.append(\n                    RejectedCandidate(\n                        process_name=process_name,\n                        relationship_type=str(summary.relationship_type),\n                        target_type=str(summary.target_type),\n                        target_expression=expression,\n                        candidate_value=context.value,\n                        rejection_reason="CANDIDATE_NOT_IN_OBJECT_CATALOG",\n                        provenance=context,\n                    )\n                )\n                continue\n'''

NEW_MISSING_BLOCK = '''            if catalog_match is None:\n                if target_type in CATALOG_GOVERNED_TARGET_TYPES:\n                    reference_class = "CORE_OBJECT"\n                    catalog_status = "CANDIDATE_NOT_IN_OBJECT_CATALOG"\n                    requires_quality_exception = True\n                else:\n                    reference_class = "DEFERRED_NONCORE"\n                    catalog_status = DEFERRED_TARGET_STATUS.get(\n                        target_type,\n                        "TARGET_TYPE_NOT_CATALOGED",\n                    )\n                    requires_quality_exception = False\n\n                rejections.append(\n                    RejectedCandidate(\n                        process_name=process_name,\n                        relationship_type=str(summary.relationship_type),\n                        target_type=str(summary.target_type),\n                        target_expression=expression,\n                        candidate_value=context.value,\n                        rejection_reason=catalog_status,\n                        reference_class=reference_class,\n                        catalog_status=catalog_status,\n                        requires_quality_exception=requires_quality_exception,\n                        provenance=context,\n                    )\n                )\n                continue\n'''

OLD_METRICS = '''    metrics = {\n        "input_summary_count": len(summaries),\n        "output_summary_count": len(result_summaries),\n        "replaced_unresolved_count": replaced_count,\n        "derived_relationship_count": derived_count,\n        "rejection_count": len(rejections),\n        "parameter_count": len(parameter_definitions),\n        "binding_count": len(bindings),\n        "alias_count": len(aliases),\n    }\n'''

NEW_METRICS = '''    missing_object_candidates = [\n        item for item in rejections if item.requires_quality_exception\n    ]\n    deferred_references = [\n        item for item in rejections if not item.requires_quality_exception\n    ]\n\n    def distinct_target_count(items: list[RejectedCandidate]) -> int:\n        return len(\n            {\n                (normalized(item.target_type), normalized(item.candidate_value))\n                for item in items\n            }\n        )\n\n    def counts_by_type(items: list[RejectedCandidate]) -> dict[str, int]:\n        counts: dict[str, int] = {}\n        for item in items:\n            key = normalized(item.target_type)\n            counts[key] = counts.get(key, 0) + 1\n        return dict(sorted(counts.items()))\n\n    metrics = {\n        "input_summary_count": len(summaries),\n        "output_summary_count": len(result_summaries),\n        "replaced_unresolved_count": replaced_count,\n        "derived_relationship_count": derived_count,\n        "rejection_count": len(rejections),\n        "missing_object_candidate_count": len(missing_object_candidates),\n        "deferred_reference_count": len(deferred_references),\n        "missing_object_target_count": distinct_target_count(\n            missing_object_candidates\n        ),\n        "deferred_reference_target_count": distinct_target_count(\n            deferred_references\n        ),\n        "missing_object_counts_by_type": counts_by_type(\n            missing_object_candidates\n        ),\n        "deferred_reference_counts_by_type": counts_by_type(\n            deferred_references\n        ),\n        "parameter_count": len(parameter_definitions),\n        "binding_count": len(bindings),\n        "alias_count": len(aliases),\n    }\n'''

COLLECTOR_MARKER = '''    summaries = resolution.summaries\n    validations = validate_relationships(\n'''
COLLECTOR_INSERT = '''    summaries = resolution.summaries\n    missing_object_candidates = [\n        record\n        for record in resolution.rejections\n        if record.get("requires_quality_exception") is True\n    ]\n    deferred_reference_inventory = [\n        record\n        for record in resolution.rejections\n        if record.get("requires_quality_exception") is not True\n    ]\n    validations = validate_relationships(\n'''

MANIFEST_MARKER = '''        "parameter_rejection_count": len(resolution.rejections),\n        "parameter_resolution_metrics": resolution.metrics,\n'''
MANIFEST_INSERT = '''        "parameter_rejection_count": len(resolution.rejections),\n        "missing_object_candidate_count": len(missing_object_candidates),\n        "deferred_reference_count": len(deferred_reference_inventory),\n        "parameter_resolution_metrics": resolution.metrics,\n'''

CURRENT_MARKER = '''            "ti_parameter_resolution_rejections.json": resolution.rejections,\n            "ti_parameter_resolution_metrics.json": resolution.metrics,\n'''
CURRENT_INSERT = '''            "ti_parameter_resolution_rejections.json": resolution.rejections,\n            "ti_missing_object_candidates.json": missing_object_candidates,\n            "ti_deferred_reference_inventory.json": deferred_reference_inventory,\n            "ti_parameter_resolution_metrics.json": resolution.metrics,\n'''

PRINT_MARKER = '''    print(\n        "Rejected candidates    : "\n        f"{parameter_rejection_count:,}"\n    )\n'''
PRINT_INSERT = '''    print(\n        "Rejected candidates    : "\n        f"{parameter_rejection_count:,}"\n    )\n    print(\n        "Missing core candidates: "\n        f"{int(manifest.get('missing_object_candidate_count', 0) or 0):,}"\n    )\n    print(\n        "Deferred references    : "\n        f"{int(manifest.get('deferred_reference_count', 0) or 0):,}"\n    )\n'''

TEST_MARKER = '''        "ti_parameter_resolution_rejections.json",\n        "ti_parameter_resolution_metrics.json",\n'''
TEST_INSERT = '''        "ti_parameter_resolution_rejections.json",\n        "ti_missing_object_candidates.json",\n        "ti_deferred_reference_inventory.json",\n        "ti_parameter_resolution_metrics.json",\n'''
TEST_LIST_MARKER = '''        "ti_parameter_resolution_rejections.json",\n    }\n'''
TEST_LIST_INSERT = '''        "ti_parameter_resolution_rejections.json",\n        "ti_missing_object_candidates.json",\n        "ti_deferred_reference_inventory.json",\n    }\n'''
TEST_METRIC_MARKER = '''    assert (\n        metrics["input_summary_count"]\n        >= metrics["replaced_unresolved_count"]\n    )\n'''
TEST_METRIC_INSERT = '''    assert (\n        metrics["input_summary_count"]\n        >= metrics["replaced_unresolved_count"]\n    )\n    assert (\n        metrics["missing_object_candidate_count"]\n        + metrics["deferred_reference_count"]\n        == metrics["rejection_count"]\n    )\n\n    missing_candidates = read_json(\n        current_root / "ti_missing_object_candidates.json"\n    )\n    deferred_references = read_json(\n        current_root / "ti_deferred_reference_inventory.json"\n    )\n    assert all(\n        record["requires_quality_exception"] is True\n        for record in missing_candidates\n    )\n    assert all(\n        record["requires_quality_exception"] is False\n        for record in deferred_references\n    )\n'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise ValueError(f"Unable to locate {label}; inspect the current file before retrying.")
    return text.replace(old, new, 1)


def patch_resolver(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "CATALOG_GOVERNED_TARGET_TYPES" not in text:
        marker = 'PROCEDURE_ORDER = ("Prolog", "Metadata", "Data", "Epilog")\n'
        if marker not in text:
            raise ValueError("Unable to locate resolver constants marker.")
        text = text.replace(marker, marker + CORE_CONSTANTS, 1)
    text = replace_once(text, OLD_REJECTION_FIELDS, NEW_REJECTION_FIELDS, "rejection fields")
    text = replace_once(text, OLD_MISSING_BLOCK, NEW_MISSING_BLOCK, "catalog rejection block")
    text = replace_once(text, OLD_METRICS, NEW_METRICS, "resolver metrics")
    path.write_text(text, encoding="utf-8")


def patch_collector(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, COLLECTOR_MARKER, COLLECTOR_INSERT, "collector split")
    text = replace_once(text, MANIFEST_MARKER, MANIFEST_INSERT, "collector manifest counts")
    text = replace_once(text, CURRENT_MARKER, CURRENT_INSERT, "collector current artifacts")
    text = replace_once(text, PRINT_MARKER, PRINT_INSERT, "collector console metrics")
    path.write_text(text, encoding="utf-8")


def patch_tests(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, TEST_MARKER, TEST_INSERT, "current expected files")
    text = replace_once(text, TEST_LIST_MARKER, TEST_LIST_INSERT, "list-file validation")
    text = replace_once(text, TEST_METRIC_MARKER, TEST_METRIC_INSERT, "metric assertions")
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply the deferred-reference classification milestone."
    )
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(), help="Pax_explorer project root"
    )
    args = parser.parse_args()
    root = args.root.resolve()
    targets = {
        "resolver": root / "parsers" / "ti_parameter_resolver.py",
        "collector": root / "scripts" / "collect_tm1_ti_lineage.py",
        "tests": root / "tests" / "test_collect_tm1_ti_lineage.py",
    }
    for label, path in targets.items():
        if not path.is_file():
            raise FileNotFoundError(f"Missing {label} file: {path}")
        backup = path.with_suffix(path.suffix + ".before_deferred_refactor.bak")
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
    patch_resolver(targets["resolver"])
    patch_collector(targets["collector"])
    patch_tests(targets["tests"])
    print("deferred_reference_milestone=APPLIED")
    for label, path in targets.items():
        print(f"{label}={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
