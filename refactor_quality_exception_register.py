from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

NEW_EXCEPTIONS: tuple[dict[str, Any], ...] = (
    {"exception_id":"TM1-PARAM-EXC-001","exception_type":"PARAMETER_DEFAULT_NOT_IN_CATALOG","target_type":"dimension","target_name":"Validation","relationship_count":2,"target_expressions":["pDim"],"source_processes":["Sys_Dim_Create_Derived_Dim_FromAttribute","Sys_Dim_Create_Derived_Dim_FromAttributeList"],"provenance_sources":["PARAMETER_DEFAULT"]},
    {"exception_id":"TM1-PARAM-EXC-002","exception_type":"PARAMETER_DEFAULT_NOT_IN_CATALOG","target_type":"dimension","target_name":"Sys_Check_Cost Center","relationship_count":1,"target_expressions":["pDim"],"source_processes":["Sys_Dim_Create_Derived_Dim_FromMDX"],"provenance_sources":["PARAMETER_DEFAULT"]},
    {"exception_id":"TM1-PARAM-EXC-003","exception_type":"PARAMETER_DEFAULT_NOT_IN_CATALOG","target_type":"dimension","target_name":"Cost Centre","relationship_count":1,"target_expressions":["pDimension"],"source_processes":["Sys_Dim_Hierarchy_Create_FromAttribute"],"provenance_sources":["PARAMETER_DEFAULT"]},
    {"exception_id":"TM1-PARAM-EXC-004","exception_type":"PARAMETER_DEFAULT_NOT_IN_CATALOG","target_type":"dimension","target_name":"Account","relationship_count":2,"target_expressions":["pDimension","pDim"],"source_processes":["Sys_Dim_Hierarchy_Create_FromAttribute_v2","Sys_Dim_Import_From_File"],"provenance_sources":["PARAMETER_DEFAULT"]},
    {"exception_id":"TM1-PARAM-EXC-005","exception_type":"PARAMETER_DEFAULT_NOT_IN_CATALOG","target_type":"dimension","target_name":"Account Test","relationship_count":5,"target_expressions":["pDim","pdim"],"source_processes":["Sys_Dim_Hierarchy_Create_FromMDXSubset","Sys_Dim_Hierarchy_Create_FromMultipleLevelAttributes","Sys_Dim_Hierarchy_Create_Orphans","SYS_Dim_Hierarchy_CreateFromConsolidations"],"provenance_sources":["PARAMETER_DEFAULT"]},
    {"exception_id":"TM1-PARAM-EXC-006","exception_type":"DERIVED_CONTROL_OBJECT_NOT_IN_CATALOG","target_type":"cube","target_name":"}ElementAttributes_Account","relationship_count":1,"target_expressions":["sAttrCube"],"source_processes":["Sys_Dim_Import_From_File"],"provenance_sources":["LOCAL_ALIAS","PARAMETER_DEFAULT"]},
)


def normalized(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upgrade the canonical TM1 quality exception register for parameter provenance.")
    parser.add_argument("path", nargs="?", type=Path, default=Path("config/catalog_quality_exceptions.json"))
    parser.add_argument("--snapshot-id", required=True, help="Current metadata snapshot ID")
    parser.add_argument("--date", default="2026-09-23", help="Registration/verification date")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path: Path = args.path
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    exceptions = payload.get("exceptions")
    if not isinstance(exceptions, list):
        raise ValueError("Exception register must contain an exceptions list.")

    existing_by_id = {str(item.get("exception_id")): item for item in exceptions if isinstance(item, dict)}
    existing_by_target = {
        (normalized(item.get("target_type")), normalized(item.get("target_name"))): item
        for item in exceptions if isinstance(item, dict)
    }

    for item in exceptions:
        if not isinstance(item, dict):
            continue
        item.setdefault("exception_type", "CONFIRMED_LITERAL_NOT_IN_CATALOG" if item.get("source_validation_status") == "BROKEN_REFERENCE" else "RESOLVED_VARIABLE_NOT_IN_CATALOG")
        item["application_scope"] = "PUBLISHED_VALIDATION"
        item["applies_to_validation_records"] = True
        item["last_verified_snapshot_id"] = args.snapshot_id
        item["last_verified_on"] = args.date

    for template in NEW_EXCEPTIONS:
        item = dict(template)
        exception_id = item["exception_id"]
        target_key = (normalized(item["target_type"]), normalized(item["target_name"]))
        if exception_id in existing_by_id:
            target = existing_by_id[exception_id]
            target.update(item)
        elif target_key in existing_by_target:
            target = existing_by_target[target_key]
            target.update(item)
        else:
            target = item
            exceptions.append(target)
        target.update({
            "source_validation_status":"UNRESOLVED_DYNAMIC_REFERENCE",
            "exception_status":"REGISTERED",
            "disposition":"PROVENANCE_CANDIDATE_NOT_IN_CATALOG",
            "application_scope":"PROVENANCE_CANDIDATE",
            "applies_to_validation_records":False,
            "exclude_from_quality_score":True,
            "required_for_holistic_success":False,
            "reason": "A deterministic parameter-derived target name is present in TI code provenance but is absent from the current full TM1 object catalog.",
            "catalog_snapshot_id":args.snapshot_id,
            "registered_on":args.date,
            "last_verified_snapshot_id":args.snapshot_id,
            "last_verified_on":args.date,
        })

    published = [item for item in exceptions if item.get("applies_to_validation_records", True)]
    provenance = [item for item in exceptions if not item.get("applies_to_validation_records", True)]
    payload.update({
        "schema_version":"1.1",
        "description":"Approved exceptions for confirmed, resolved, or deterministically parameter-derived TM1 relationship targets absent from the current full object catalog.",
        "catalog_snapshot_id":args.snapshot_id,
        "exception_count":len(exceptions),
        "relationship_count":sum(int(item.get("relationship_count", 0)) for item in exceptions),
        "published_validation_exception_count":len(published),
        "published_validation_relationship_count":sum(int(item.get("relationship_count", 0)) for item in published),
        "provenance_candidate_exception_count":len(provenance),
        "provenance_candidate_relationship_count":sum(int(item.get("relationship_count", 0)) for item in provenance),
    })

    ids = [str(item.get("exception_id") or "") for item in exceptions]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate exception IDs exist after refactor.")
    keys = [(normalized(item.get("target_type")), normalized(item.get("target_name")), str(item.get("exception_type"))) for item in exceptions]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate exception target/type/category keys exist after refactor.")

    backup = path.with_suffix(path.suffix + ".pre_parameter_refactor.bak")
    backup.write_bytes(path.read_bytes())
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)

    print(f"path={path}")
    print(f"backup={backup}")
    print(f"exception_count={payload['exception_count']}")
    print(f"relationship_count={payload['relationship_count']}")
    print(f"published_validation_exception_count={payload['published_validation_exception_count']}")
    print(f"published_validation_relationship_count={payload['published_validation_relationship_count']}")
    print(f"provenance_candidate_exception_count={payload['provenance_candidate_exception_count']}")
    print(f"provenance_candidate_relationship_count={payload['provenance_candidate_relationship_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
