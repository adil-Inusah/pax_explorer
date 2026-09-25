from __future__ import annotations

"""Build first-class file, command, executable, and script dependencies.

The builder consumes governed TI and process-data-source relationships. It never
executes commands or accesses external paths. Expressions are normalized and
sanitized before publication; likely credentials are redacted.
"""

import argparse
import hashlib
import json
import ntpath
import re
import shlex
import sys
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

CURRENT_ROOT = ROOT_DIR / "data" / "current"
SNAPSHOT_ROOT = ROOT_DIR / "data" / "snapshots"
TI_RELATIONSHIPS_FILE = CURRENT_ROOT / "ti_relationships.json"
TI_EVIDENCE_FILE = CURRENT_ROOT / "ti_relationship_evidence.json"
PROCESS_SOURCE_RELATIONSHIPS_FILE = (
    CURRENT_ROOT / "process_data_source_relationships.json"
)
PROCESS_SOURCES_FILE = CURRENT_ROOT / "process_data_sources.json"

FILE_RELATIONSHIP_TYPES = {
    "READS_FILE",
    "READS_FROM_FILE",
    "WRITES_FILE",
    "DELETES_FILE",
    "CHECKS_FILE",
    "MOVES_FILE",
    "RENAMES_FILE",
}
COMMAND_RELATIONSHIP_TYPES = {"EXECUTES_COMMAND"}
SOURCE_FILE_BRIDGE_TYPES = {"RESOLVES_TO_FILE"}
SCRIPT_EXTENSIONS = {
    ".ps1",
    ".bat",
    ".cmd",
    ".py",
    ".sh",
    ".sql",
    ".vbs",
    ".js",
}
EXECUTABLE_EXTENSIONS = {".exe", ".com"}
SECRET_PATTERN = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization)"
    r"(\s*[:=]\s*|\s+)([^;\s,'\"]+|['\"][^'\"]*['\"]])"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def snapshot_id(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def clean(value: Any) -> str:
    return str(value or "").strip()


def token(value: Any) -> str:
    return clean(value).upper().replace(" ", "_")


def normalized_key(value: Any) -> str:
    return " ".join(clean(value).casefold().split())


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def records(path: Path, *, required: bool = True) -> list[dict[str, Any]]:
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"Required input file not found: {path}")
        return []
    payload = read_json(path)
    if not isinstance(payload, list):
        raise TypeError(f"{path.name} must contain a JSON array.")
    invalid = [index for index, item in enumerate(payload) if not isinstance(item, Mapping)]
    if invalid:
        raise TypeError(f"{path.name} contains non-object records at {invalid[:5]}")
    return [dict(item) for item in payload]


def first(record: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = record.get(name)
        if value is not None and clean(value):
            return value
    return None


def sanitize_expression(value: Any) -> tuple[str, bool]:
    text = clean(value)
    redacted, count = SECRET_PATTERN.subn(r"\1=<redacted>", text)
    return redacted, count > 0


def dynamic_expression(value: str) -> bool:
    return any(
        marker in value
        for marker in ("|", "#", "${", "%", "&", "@{", "[", "]")
    ) or bool(re.search(r"(?i)\b(v|p)[A-Za-z0-9_]+\b", value))


def normalize_path(value: str) -> tuple[str, str, bool]:
    text = value.strip().strip("'\"")
    dynamic = dynamic_expression(text)
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https", "ftp", "sftp"}:
        return text, "URL", dynamic
    normalized = text.replace("/", "\\")
    if normalized.startswith("\\\\"):
        return ntpath.normpath(normalized), "UNC", dynamic
    if re.match(r"^[A-Za-z]:\\", normalized):
        return ntpath.normpath(normalized), "LOCAL_ABSOLUTE", dynamic
    if dynamic:
        return normalized, "DYNAMIC", True
    if normalized:
        return ntpath.normpath(normalized), "RELATIVE", False
    return normalized, "UNKNOWN", dynamic


def file_node(value: str) -> dict[str, Any]:
    sanitized, credential_redacted = sanitize_expression(value)
    path_value, location_class, is_dynamic = normalize_path(sanitized)
    path_hash = sha256_text(path_value.casefold())
    extension = PureWindowsPath(path_value).suffix.casefold()
    node_type = "SCRIPT" if extension in SCRIPT_EXTENSIONS else "FILE"
    return {
        "node_id": f"{node_type.casefold()}::{location_class}::{path_hash}",
        "node_type": node_type,
        "object_type": node_type.casefold(),
        "display_value": path_value,
        "location_class": location_class,
        "path_hash": path_hash,
        "extension": extension or None,
        "is_dynamic": is_dynamic,
        "credential_present": credential_redacted,
        "credential_redacted": credential_redacted,
    }


def command_kind(value: str) -> str:
    lower = value.casefold()
    if "powershell" in lower or "pwsh" in lower:
        return "POWERSHELL"
    if re.search(r"(^|[\\\s])cmd(?:\.exe)?\b", lower):
        return "CMD"
    if any(lower.endswith(extension) for extension in SCRIPT_EXTENSIONS):
        return "SCRIPT"
    if dynamic_expression(value):
        return "DYNAMIC"
    if re.search(r"\.exe(?:\s|$)", lower):
        return "EXECUTABLE"
    return "UNKNOWN"


def command_node(value: str) -> dict[str, Any]:
    sanitized, credential_redacted = sanitize_expression(value)
    kind = command_kind(sanitized)
    digest = sha256_text(sanitized.casefold())
    return {
        "node_id": f"command::{kind}::{digest}",
        "node_type": "COMMAND",
        "object_type": "command",
        "command_kind": kind,
        "sanitized_expression": sanitized,
        "expression_hash": digest,
        "is_dynamic": dynamic_expression(sanitized),
        "credential_present": credential_redacted,
        "credential_redacted": credential_redacted,
    }


def command_parts(value: str) -> list[str]:
    try:
        return shlex.split(value, posix=False)
    except ValueError:
        return value.split()


def command_children(command: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    expression = clean(command.get("sanitized_expression"))
    parts = command_parts(expression)
    if not parts:
        return []
    result: list[tuple[str, dict[str, Any]]] = []
    executable = parts[0].strip("'\"")
    extension = PureWindowsPath(executable).suffix.casefold()
    if extension in EXECUTABLE_EXTENSIONS or executable.casefold() in {
        "powershell",
        "pwsh",
        "cmd",
        "python",
        "python3",
        "bash",
        "sh",
    }:
        name = PureWindowsPath(executable).name.casefold()
        result.append(
            (
                "INVOKES_EXECUTABLE",
                {
                    "node_id": f"executable::{name}",
                    "node_type": "EXECUTABLE",
                    "object_type": "executable",
                    "executable_name": name,
                },
            )
        )
    for part in parts[1:]:
        candidate = part.strip("'\",;")
        extension = PureWindowsPath(candidate).suffix.casefold()
        if extension in SCRIPT_EXTENSIONS:
            result.append(("REFERENCES_SCRIPT", file_node(candidate)))
    return result


def name_from_node_id(value: Any, expected_prefix: str) -> str:
    node_id = clean(value)
    prefix = f"{expected_prefix}::"
    if node_id.casefold().startswith(prefix.casefold()):
        return node_id[len(prefix):]
    return ""


def process_name(record: Mapping[str, Any]) -> str:
    direct = clean(first(record, "process_name", "source_name", "source_object_name"))
    if direct:
        return direct
    return name_from_node_id(record.get("source_id"), "process")


def relationship_type(record: Mapping[str, Any]) -> str:
    return token(first(record, "relationship_type", "type", "relationshipType"))


def target_expression(record: Mapping[str, Any]) -> str:
    return clean(
        first(
            record,
            "target_expression",
            "target_name",
            "resolved_target_name",
            "expression",
            "raw_argument",
            "value",
        )
    )


def process_node_id(name: str) -> str:
    return f"process::{name}"


def validation_status(node: Mapping[str, Any]) -> str:
    if node.get("is_dynamic"):
        return (
            "DYNAMIC_EXTERNAL_COMMAND"
            if node.get("node_type") == "COMMAND"
            else "DYNAMIC_EXTERNAL_FILE"
        )
    return "EXTERNAL_DEPENDENCY_NOT_CROSS_CHECKED"


def build_operational_dependencies(
    *,
    ti_relationships_path: Path = TI_RELATIONSHIPS_FILE,
    ti_evidence_path: Path = TI_EVIDENCE_FILE,
    process_source_relationships_path: Path = PROCESS_SOURCE_RELATIONSHIPS_FILE,
    process_sources_path: Path = PROCESS_SOURCES_FILE,
    snapshot_root: Path = SNAPSHOT_ROOT,
    current_root: Path = CURRENT_ROOT,
    timestamp: datetime | None = None,
    publish_current: bool = True,
) -> dict[str, Any]:
    started = timestamp or utc_now()
    run_id = snapshot_id(started)
    snapshot_dir = snapshot_root / run_id

    ti_relationships = records(ti_relationships_path)
    ti_evidence = records(ti_evidence_path, required=False)
    source_relationships = records(process_source_relationships_path, required=False)
    process_sources = records(process_sources_path, required=False)

    evidence_counts: Counter[tuple[str, str, str]] = Counter()
    for record in ti_evidence:
        evidence_counts[
            (
                normalized_key(process_name(record)),
                relationship_type(record),
                normalized_key(target_expression(record)),
            )
        ] += 1

    nodes: dict[str, dict[str, Any]] = {}
    relationships: dict[str, dict[str, Any]] = {}
    validations: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []

    candidates = ti_relationships + source_relationships
    for source_record in candidates:
        rel_type = relationship_type(source_record)
        is_bridge = rel_type in SOURCE_FILE_BRIDGE_TYPES
        if rel_type not in (
            FILE_RELATIONSHIP_TYPES
            | COMMAND_RELATIONSHIP_TYPES
            | SOURCE_FILE_BRIDGE_TYPES
        ):
            continue

        expression = target_expression(source_record)
        source_process = process_name(source_record)
        source_id = clean(source_record.get("source_id"))
        source_type = token(source_record.get("source_type"))
        if is_bridge:
            if not source_id or source_type != "EXTERNAL_DATA_SOURCE" or not expression:
                errors.append({
                    "stage": "NORMALIZE_OPERATIONAL_RELATIONSHIP",
                    "relationship_type": rel_type,
                    "source_id": source_id or None,
                    "error": "Missing external data-source identity or target expression",
                })
                continue
        elif not source_process or not expression:
            errors.append({
                "stage": "NORMALIZE_OPERATIONAL_RELATIONSHIP",
                "relationship_type": rel_type,
                "process_name": source_process or None,
                "error": "Missing process name or target expression",
            })
            continue

        node = (
            command_node(expression)
            if rel_type in COMMAND_RELATIONSHIP_TYPES
            else file_node(expression)
        )
        # Process-definition relationships already carry the canonical FILE ID.
        supplied_target_id = clean(source_record.get("target_id"))
        if supplied_target_id and (
            token(source_record.get("relationship_origin")) == "PROCESS_DEFINITION"
        ):
            node["node_id"] = supplied_target_id
        nodes.setdefault(node["node_id"], node)

        graph_source_id = source_id if is_bridge else process_node_id(source_process)
        graph_source_type = "EXTERNAL_DATA_SOURCE" if is_bridge else "PROCESS"
        relationship_id = (
            f"operational::{normalized_key(graph_source_id)}::{rel_type}::"
            f"{node['node_id']}"
        )
        evidence_key = (
            normalized_key(source_process),
            rel_type,
            normalized_key(expression),
        )
        origin = clean(source_record.get("relationship_origin")) or "TI"
        method = clean(source_record.get("resolution_method")) or (
            "DYNAMIC_EXPRESSION" if node.get("is_dynamic") else "NORMALIZED_LITERAL"
        )
        relationship_payload = {
            "snapshot_id": run_id,
            "relationship_id": relationship_id,
            "source_id": graph_source_id,
            "source_type": graph_source_type,
            "target_id": node["node_id"],
            "target_type": node["node_type"],
            "relationship_type": rel_type,
            "relationship_origin": origin,
            "resolution_method": method,
            "evidence_count": evidence_counts.get(evidence_key, 1),
        }
        configured_id = clean(source_record.get("configured_data_source_id"))
        if configured_id:
            relationship_payload["configured_data_source_id"] = configured_id
        relationships.setdefault(relationship_id, relationship_payload)
        validations.setdefault(
            relationship_id,
            {
                "snapshot_id": run_id,
                "validation_id": f"validation::{relationship_id}",
                "relationship_id": relationship_id,
                "validation_status": validation_status(node),
                "source_id": graph_source_id,
                "target_id": node["node_id"],
            },
        )
    # Enrich command nodes with executable and script dependencies.
    for command in list(nodes.values()):
        if command.get("node_type") != "COMMAND":
            continue
        for child_type, child in command_children(command):
            nodes.setdefault(child["node_id"], child)
            relationship_id = (
                f"operational-command::{command['node_id']}::{child_type}::"
                f"{child['node_id']}"
            )
            relationships.setdefault(
                relationship_id,
                {
                    "snapshot_id": run_id,
                    "relationship_id": relationship_id,
                    "source_id": command["node_id"],
                    "source_type": "COMMAND",
                    "target_id": child["node_id"],
                    "target_type": child["node_type"],
                    "relationship_type": child_type,
                    "relationship_origin": "COMMAND_PARSE",
                    "resolution_method": "TOKEN_PARSE",
                    "evidence_count": 1,
                },
            )
            validations.setdefault(
                relationship_id,
                {
                    "snapshot_id": run_id,
                    "validation_id": f"validation::{relationship_id}",
                    "relationship_id": relationship_id,
                    "validation_status": "EXTERNAL_DEPENDENCY_NOT_CROSS_CHECKED",
                    "source_id": command["node_id"],
                    "target_id": child["node_id"],
                },
            )

    node_payload = sorted(nodes.values(), key=lambda item: item["node_id"])
    relationship_payload = sorted(
        relationships.values(), key=lambda item: item["relationship_id"]
    )
    validation_payload = sorted(
        validations.values(), key=lambda item: item["relationship_id"]
    )
    if len(relationship_payload) != len(validation_payload):
        errors.append({
            "stage": "VALIDATE_OUTPUT",
            "error": "Relationship and validation counts do not reconcile",
        })
    if {item["relationship_id"] for item in relationship_payload} != {
        item["relationship_id"] for item in validation_payload
    }:
        errors.append({
            "stage": "VALIDATE_OUTPUT",
            "error": "Relationship and validation identities do not reconcile",
        })
    node_ids = {item["node_id"] for item in node_payload}
    for relationship in relationship_payload:
        if relationship["target_id"] not in node_ids:
            errors.append({
                "stage": "VALIDATE_OUTPUT",
                "relationship_id": relationship["relationship_id"],
                "error": "Operational relationship target node is missing",
            })

    status = "PARTIAL" if errors else "COMPLETE"
    manifest = {
        "snapshot_id": run_id,
        "status": status,
        "started_at": started.isoformat(),
        "completed_at": utc_now().isoformat(),
        "input_ti_relationship_count": len(ti_relationships),
        "input_ti_evidence_count": len(ti_evidence),
        "input_process_source_relationship_count": len(source_relationships),
        "input_process_source_count": len(process_sources),
        "dependency_count": len(node_payload),
        "relationship_count": len(relationship_payload),
        "validation_count": len(validation_payload),
        "error_count": len(errors),
        "dependency_type_counts": dict(
            sorted(Counter(item["node_type"] for item in node_payload).items())
        ),
        "relationship_type_counts": dict(
            sorted(
                Counter(
                    item["relationship_type"] for item in relationship_payload
                ).items()
            )
        ),
        "validation_counts": dict(
            sorted(
                Counter(
                    item["validation_status"] for item in validation_payload
                ).items()
            )
        ),
        "publish_current_requested": publish_current,
        "published_current": bool(status == "COMPLETE" and publish_current),
    }
    outputs = {
        "operational_dependencies.json": node_payload,
        "operational_relationships.json": relationship_payload,
        "operational_relationship_validations.json": validation_payload,
        "operational_dependency_manifest.json": manifest,
        "operational_dependency_errors.json": errors,
    }
    for name, payload in outputs.items():
        write_json(snapshot_dir / name, payload)
    if status == "COMPLETE" and publish_current:
        for name, payload in outputs.items():
            write_json(current_root / name, payload)
    return manifest


def print_manifest(manifest: Mapping[str, Any]) -> None:
    print("=" * 70)
    print("TM1 OPERATIONAL DEPENDENCY INVENTORY")
    print("=" * 70)
    print(f"Snapshot      : {manifest.get('snapshot_id', 'UNKNOWN')}")
    print(f"Status        : {manifest.get('status', 'UNKNOWN')}")
    print(f"Dependencies  : {int(manifest.get('dependency_count', 0) or 0):,}")
    print(f"Relationships : {int(manifest.get('relationship_count', 0) or 0):,}")
    print(f"Validations   : {int(manifest.get('validation_count', 0) or 0):,}")
    print(f"Errors        : {int(manifest.get('error_count', 0) or 0):,}")
    print(f"Published     : {bool(manifest.get('published_current', False))}")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build normalized TM1 file and command dependencies."
    )
    parser.add_argument("--ti-relationships", type=Path, default=TI_RELATIONSHIPS_FILE)
    parser.add_argument("--ti-evidence", type=Path, default=TI_EVIDENCE_FILE)
    parser.add_argument(
        "--process-source-relationships",
        type=Path,
        default=PROCESS_SOURCE_RELATIONSHIPS_FILE,
    )
    parser.add_argument("--process-sources", type=Path, default=PROCESS_SOURCES_FILE)
    parser.add_argument("--no-publish", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        manifest = build_operational_dependencies(
            ti_relationships_path=args.ti_relationships,
            ti_evidence_path=args.ti_evidence,
            process_source_relationships_path=args.process_source_relationships,
            process_sources_path=args.process_sources,
            publish_current=not args.no_publish,
        )
        print_manifest(manifest)
        return 0 if manifest.get("status") == "COMPLETE" else 1
    except Exception as error:
        print(
            "Operational dependency build failed: "
            f"{type(error).__name__}: {error}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
