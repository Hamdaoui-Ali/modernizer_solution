import argparse
import json
from typing import Any

from migration_factory.agents.planning_agent.node import planning_node


def _to_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_to_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m migration_factory.agents.planning_agent.runner",
        description="Run Planning Agent against existing Analysis Agent artifacts.",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--modernized", required=True)
    parser.add_argument("--legacy", required=True)
    parser.add_argument("--ai-hub", required=True)
    parser.add_argument("--profile", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    state = {
        "run_id": args.run_id,
        "legacy_app_path": args.legacy,
        "modernized_app_path": args.modernized,
        "ai_hub_path": args.ai_hub,
        "profile": args.profile,
    }
    result = planning_node(state)
    print(json.dumps(_to_json_safe(result), indent=2))
    return 0 if result.get("planning_status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
