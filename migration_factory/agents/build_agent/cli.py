from __future__ import annotations

import argparse
import sys

from migration_factory.contracts.migration import LedgerError

from .agent import run_build_agent


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        result = run_build_agent(
            args.project_path,
            timeout_seconds=args.timeout,
            module=args.module,
            main_class=args.main_class,
            auto_discover_maven_target=not args.no_auto_target,
            output_dir=args.output_dir,
            ledger_file=args.ledger_file,
            stream_output=not args.quiet,
            stop_after_start=not args.keep_running,
        )
    except LedgerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if result.succeeded:
        print(f"SUCCESS: {result.message}")
        if result.matched_line:
            print(f"Matched log: {result.matched_line}")
        for warning in result.warnings:
            print(f"WARNING: {warning}", file=sys.stderr)
        return 0

    print(f"FAILURE: {result.message}", file=sys.stderr)
    print(f"Reason: {result.result_kind}", file=sys.stderr)
    for warning in result.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if result.matched_line:
        print(f"Matched log: {result.matched_line}", file=sys.stderr)
    if result.error_contract_path:
        print(f"Build error contract: {result.error_contract_path}", file=sys.stderr)
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build-agent",
        description="Run a Java/Spring Boot app and write build failure contracts as JSON.",
    )
    parser.add_argument("project_path", help="Path to a Maven or Gradle Java project")
    parser.add_argument("--timeout", type=_positive_int)
    parser.add_argument("--module", help="Maven module to target with -f <module>/pom.xml")
    parser.add_argument("--main-class", help="Spring Boot main class override for Maven")
    parser.add_argument(
        "--no-auto-target",
        action="store_true",
        help="Do not auto-detect Maven module and Spring Boot main class",
    )
    parser.add_argument("--output-dir", help="Directory for build error JSON contracts")
    parser.add_argument("--ledger-file", help="Migration ledger JSON file to update with build validation result")
    parser.add_argument("--quiet", action="store_true", help="Do not stream application logs")
    parser.add_argument("--keep-running", action="store_true", help="Keep app attached after startup is detected")
    return parser


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
