from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from migration_factory.assessment.writer import AssessmentArtifactError, write_assessment_artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate Assessment artifacts for a migration run.")
    parser.add_argument("--run-id", required=True, help="Migration run id to assess.")
    parser.add_argument(
        "--modernized",
        required=True,
        type=Path,
        help="Path to the modernized application root.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        result = write_assessment_artifacts(args.modernized, args.run_id)
    except AssessmentArtifactError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Wrote assessment report: {result.report_path}")
    print(f"Wrote assessment summary: {result.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
