from __future__ import annotations

import argparse
import sys

from .aggregate import aggregate_by_category, stable_report
from ..models import canonical_json
from .reader import CsvInputError, read_rows


def main(argv: list[str] | None = None) -> int:
    # The Windows bundled runtime may select the system code page for a pipe.
    # The workload contract is UTF-8 JSON, so make the child stream encoding
    # explicit before writing non-ASCII category names.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")
    parser = argparse.ArgumentParser(description="Generate the deterministic SCORP V4 CSV report")
    parser.add_argument("csv_path")
    parser.add_argument(
        "--output",
        help="optional exact output path; the report is written only after the CSV is fully validated",
    )
    parser.add_argument(
        "--operation",
        choices=("validate", "aggregate", "report"),
        default="report",
        help="bounded workload operation for the assigned Worker task",
    )
    args = parser.parse_args(argv)
    try:
        if args.operation == "validate":
            rows = read_rows(args.csv_path)
            report = canonical_json({"valid": True, "row_count": len(rows)}) + "\n"
        elif args.operation == "aggregate":
            rows = read_rows(args.csv_path)
            totals = aggregate_by_category(rows)
            report = canonical_json(
                {"categories": {key: format(value, ".2f") for key, value in totals.items()}}
            ) + "\n"
        else:
            report = stable_report(args.csv_path)
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8", newline="") as handle:
                    handle.write(report)
            except OSError as exc:
                sys.stderr.write(f"CSV_OUTPUT_FAILED:{exc}\n")
                return 2
        sys.stdout.write(report)
    except CsvInputError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
