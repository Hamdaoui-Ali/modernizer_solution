from __future__ import annotations

import argparse
import sys

from .agent import TransformationAgentError, run_transformation_agent
from .plan import MigrationPlanError
from .rewrite import RewritePluginError


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        result = run_transformation_agent(
            args.modernized_app_path,
            args.openrewrite_plugin_txt,
            args.migration_plan,
            start_unit=args.start_unit,
            dry_run=args.dry_run,
            stream_output=not args.quiet,
            wait_for_continue=not args.no_wait,
        )
    except (MigrationPlanError, RewritePluginError, TransformationAgentError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Ledger: {result.ledger_file}")
    print(f"Status: {result.status}")
    if result.blocked_unit:
        print(f"Blocked unit: {result.blocked_unit}")
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transformation-agent",
        description="Apply a migration plan unit by unit and pause for Build Agent validation.",
    )
    parser.add_argument("modernized_app_path", help="Path to the target modernized app workspace")
    parser.add_argument("openrewrite_plugin_txt", help="Path to a txt file containing OpenRewrite Maven plugin XML")
    parser.add_argument("migration_plan", help="Path to the migration plan YAML")
    parser.add_argument("--start-unit", help="Start or resume from a specific migration unit id")
    parser.add_argument("--dry-run", action="store_true", help="Record actions without injecting plugin or running commands")
    parser.add_argument("--quiet", action="store_true", help="Do not stream command output")
    parser.add_argument("--no-wait", action="store_true", help="Do not pause for manual Build Agent validation")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
