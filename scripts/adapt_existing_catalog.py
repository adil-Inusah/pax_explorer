from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from models.catalog_adapter import (
    CatalogJsonAdapter,
    publish_adapter_result,
)


DEFAULT_CURRENT_ROOT = ROOT_DIR / "data" / "current"
DEFAULT_OUTPUT_ROOT = ROOT_DIR / "data" / "catalog"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert existing TM1 inventory and lineage JSON files "
            "to the unified catalog data model."
        )
    )
    parser.add_argument(
        "--environment",
        required=True,
        help="Environment name, for example STAGING.",
    )
    parser.add_argument(
        "--database",
        required=True,
        help="TM1 database name.",
    )
    parser.add_argument(
        "--current-root",
        type=Path,
        default=DEFAULT_CURRENT_ROOT,
        help="Folder containing the existing current JSON files.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Folder for unified catalog JSON files.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()

    try:
        adapter = CatalogJsonAdapter(
            environment=arguments.environment,
            database_name=arguments.database,
        )
        result = adapter.adapt_directory(
            arguments.current_root
        )
        publish_adapter_result(
            result,
            arguments.output_root,
        )

        manifest = result.snapshot.manifest
        print("=" * 70)
        print("TM1 JSON CATALOG ADAPTER")
        print("=" * 70)
        print(f"Snapshot      : {manifest.snapshot_id}")
        print(f"Environment   : {manifest.environment}")
        print(f"Database      : {manifest.database_name}")
        print(f"Status        : {manifest.status.value}")
        print(f"Objects       : {sum(manifest.object_counts.values()):,}")
        print(
            f"Relationships : "
            f"{sum(manifest.relationship_counts.values()):,}"
        )
        print(
            f"Validations   : "
            f"{sum(manifest.validation_counts.values()):,}"
        )
        print(f"Warnings      : {len(result.warnings):,}")
        print(f"Output        : {arguments.output_root}")

        return 0 if manifest.status.value == "COMPLETE" else 1

    except Exception as error:
        print(
            "Catalog adaptation failed: "
            f"{type(error).__name__}: {error}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
