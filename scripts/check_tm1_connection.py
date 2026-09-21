import argparse
import inspect
import sys
import time
from pathlib import Path
from typing import Any

from mdxpy import MdxHierarchySet


# ============================================================
# Project Imports
# ============================================================

ROOT_DIR = Path(__file__).resolve().parent.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utilities.tm1_connection import get_tm1_connection


# ============================================================
# MDXpy Methods to Inspect
# ============================================================

MDX_HIERARCHY_METHODS = [
    "members",
    "member",
    "all_members",
    "tm1_subset_all",
    "subset",
    "filter_by_level",
    "filter_by_attribute",
    "order",
    "children",
    "descendants",
    "ancestors",
    "range",
    "all_consolidations",
    "all_leaves",
    "filter_by_pattern",
]


# ============================================================
# Console Helpers
# ============================================================

def print_section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def print_result(
    label: str,
    value: Any,
) -> None:
    print(f"{label:<24}: {value}")


# ============================================================
# Safe Metadata Retrieval
# ============================================================
def get_metadata_names(
    object_type: str,
    retrieval_function,
) -> dict[str, Any]:
    """
    Retrieve one category of TM1 metadata without preventing
    the remaining metadata checks from running if it fails.
    """

    try:
        names = list(retrieval_function())

        return {
            "object_type": object_type,
            "status": "PASS",
            "names": names,
            "count": len(names),
            "error": None,
        }

    except Exception as error:
        return {
            "object_type": object_type,
            "status": "FAIL",
            "names": [],
            "count": 0,
            "error": (
                f"{type(error).__name__}: {error}"
            ),
        }

# ============================================================
# Read-Only Metadata Tests
# ============================================================

def test_metadata_access(tm1) -> dict[str, dict]:
    """
    Perform read-only metadata checks.

    No processes are executed, no dimensions are changed,
    and no cube values are written.
    """

    return {
        "cubes": get_metadata_names(
            object_type="Cubes",
            retrieval_function=(
                tm1.cubes.get_all_names
            ),
        ),
        "dimensions": get_metadata_names(
            object_type="Dimensions",
            retrieval_function=(
                tm1.dimensions.get_all_names
            ),
        ),
        "processes": get_metadata_names(
            object_type="Processes",
            retrieval_function=(
                tm1.processes.get_all_names
            ),
        ),
        "chores": get_metadata_names(
            object_type="Chores",
            retrieval_function=(
                tm1.chores.get_all_names
            ),
        ),
    }


def display_metadata_results(
    metadata_results: dict[str, dict],
) -> None:
    print_section("READ-ONLY METADATA ACCESS")

    for result in metadata_results.values():
        label = result["object_type"]

        if result["status"] == "PASS":
            value = (
                f"{result['count']:,} accessible"
            )
        else:
            value = (
                f"FAILED: {result['error']}"
            )

        print_result(label, value)


# ============================================================
# Sample Metadata
# ============================================================

def display_sample_objects(
    metadata_results: dict[str, dict],
    sample_size: int,
) -> None:
    """
    Display samples from metadata already retrieved.

    This avoids requesting the same TM1 metadata twice.
    """

    print_section("SAMPLE ACCESSIBLE OBJECTS")

    for result in metadata_results.values():
        object_type = result["object_type"]
        names = result["names"]

        print()
        print(f"{object_type}:")

        if result["status"] != "PASS":
            print(
                f"  Retrieval failed: "
                f"{result['error']}"
            )
            continue

        if not names:
            print("  No accessible objects found.")
            continue

        for name in sorted(
            names,
            key=str.casefold,
        )[:sample_size]:
            print(f"  - {name}")


# ============================================================
# MDXpy API Inspection
# ============================================================

def inspect_mdx_hierarchy_methods() -> None:
    """
    Display signatures for selected MdxHierarchySet methods.

    This checks the locally installed MDXpy API and does not
    query or modify TM1.
    """

    print_section(
        "MDXPY HIERARCHY METHOD SIGNATURES"
    )

    for method_name in MDX_HIERARCHY_METHODS:
        method = getattr(
            MdxHierarchySet,
            method_name,
            None,
        )

        if method is None:
            print(
                f"{method_name:<24}: "
                "Not available in installed MDXpy version"
            )
            continue

        try:
            signature = inspect.signature(method)

            print(
                f"{method_name:<24}: "
                f"{signature}"
            )

        except (TypeError, ValueError) as error:
            print(
                f"{method_name:<24}: "
                f"Signature unavailable: {error}"
            )


# ============================================================
# TM1 Connection Test
# ============================================================
def check_tm1_connection(
    show_samples: bool = False,
    inspect_mdx: bool = False,
    sample_size: int = 5,
) -> bool:
    """
    Connect to TM1 and perform read-only validation.

    Returns True only when the connection succeeds and all
    core metadata categories are accessible.
    """

    print_section("TM1 CONNECTION TEST")

    test_start = time.perf_counter()

    try:
        connection_start = time.perf_counter()

        with get_tm1_connection() as tm1:
            connection_elapsed = (
                time.perf_counter()
                - connection_start
            )

            print_result(
                "Connection Status",
                "Successful",
            )
            print_result(
                "TM1 Version",
                tm1.server.version,
            )
            print_result(
                "Connection Time",
                f"{connection_elapsed:.2f} seconds",
            )

            metadata_start = time.perf_counter()

            metadata_results = test_metadata_access(
                tm1
            )

            metadata_elapsed = (
                time.perf_counter()
                - metadata_start
            )

            display_metadata_results(
                metadata_results
            )

            print_result(
                "Metadata Read Time",
                f"{metadata_elapsed:.2f} seconds",
            )

            if show_samples:
                display_sample_objects(
                    metadata_results=metadata_results,
                    sample_size=sample_size,
                )

        if inspect_mdx:
            inspect_mdx_hierarchy_methods()

        failed_checks = [
            result
            for result in metadata_results.values()
            if result["status"] != "PASS"
        ]

        total_elapsed = (
            time.perf_counter()
            - test_start
        )

        print_section("TEST RESULT")

        if failed_checks:
            print_result(
                "Overall Status",
                "Partial Pass",
            )
            print_result(
                "Metadata Failures",
                len(failed_checks),
            )
        else:
            print_result(
                "Overall Status",
                "Pass",
            )

        print_result(
            "Mode",
            "Read-only",
        )
        print_result(
            "Test Duration",
            f"{total_elapsed:.2f} seconds",
        )

        return not failed_checks

    except Exception as error:
        total_elapsed = (
            time.perf_counter()
            - test_start
        )

        print_section("TEST RESULT")

        print_result(
            "Connection Status",
            "Failed",
        )
        print_result(
            "Error Type",
            type(error).__name__,
        )
        print_result(
            "Error",
            str(error),
        )
        print_result(
            "Test Duration",
            f"{total_elapsed:.2f} seconds",
        )
        print_result(
            "Overall Status",
            "Fail",
        )

        return False



    # ============================================================
# Command-Line Arguments
# ============================================================

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test the configured TM1 connection and perform "
            "read-only metadata checks."
        )
    )

    parser.add_argument(
        "--show-samples",
        action="store_true",
        help=(
            "Display sample cube, dimension, process, "
            "and chore names."
        ),
    )

    parser.add_argument(
        "--sample-size",
        type=int,
        default=5,
        help=(
            "Number of sample objects to display. "
            "Default: 5."
        ),
    )

    parser.add_argument(
        "--inspect-mdx",
        action="store_true",
        help=(
            "Display selected MdxHierarchySet "
            "method signatures."
        ),
    )

    return parser.parse_args()


# ============================================================
# Entry Point
# ============================================================

def main() -> int:
    arguments = parse_arguments()

    if arguments.sample_size < 1:
        print(
            "Error: --sample-size must be at least 1."
        )
        return 2

    successful = check_tm1_connection(
        show_samples=arguments.show_samples,
        inspect_mdx=arguments.inspect_mdx,
        sample_size=arguments.sample_size,
    )

    return 0 if successful else 1


if __name__ == "__main__":
    raise SystemExit(main())