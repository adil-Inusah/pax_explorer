from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from utilities.unified_graph_query import UnifiedGraphQuery

ROOT_DIR = Path(__file__).resolve().parent.parent
CURRENT_ROOT = ROOT_DIR / "data" / "current"
SEMANTICS_PATH = ROOT_DIR / "config" / "graph_relationship_semantics.json"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Query the governed PAX Explorer unified graph.")
    root.add_argument("--current-root", type=Path, default=CURRENT_ROOT)
    root.add_argument("--semantics", type=Path, default=SEMANTICS_PATH)
    commands = root.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search")
    search.add_argument("--text", required=True)
    search.add_argument("--type")
    search.add_argument("--limit", type=int, default=25)
    node = commands.add_parser("node")
    node.add_argument("--id", required=True)
    dependencies = commands.add_parser("dependencies")
    dependencies.add_argument("--id", required=True)
    dependencies.add_argument("--direction", choices=("inbound", "outbound", "both"), default="both")
    impact = commands.add_parser("impact")
    impact.add_argument("--id", required=True)
    impact.add_argument("--direction", choices=("inbound", "outbound", "both"), default="inbound")
    impact.add_argument("--depth", type=int, default=3)
    story = commands.add_parser("process-story")
    story.add_argument("--name", required=True)
    explain = commands.add_parser("explain")
    explain.add_argument("--relationship-id", required=True)
    return root


def execute(args: argparse.Namespace) -> dict[str, Any]:
    graph = UnifiedGraphQuery(args.current_root, semantics_path=args.semantics)
    if args.command == "search":
        return graph.search(args.text, node_type=args.type, limit=args.limit)
    if args.command == "node":
        return graph.node_summary(args.id)
    if args.command == "dependencies":
        return graph.neighborhood(args.id, direction=args.direction, depth=1)
    if args.command == "impact":
        return graph.impact(args.id, direction=args.direction, depth=args.depth)
    if args.command == "process-story":
        return graph.process_story(args.name)
    return graph.explain(args.relationship_id)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        print(json.dumps(execute(args), indent=2, ensure_ascii=False))
        return 0
    except (KeyError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
