from __future__ import annotations

import argparse
import sys

from .aggregate import stable_report
from .reader import CsvInputError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the deterministic SCORP V4 CSV report")
    parser.add_argument("csv_path")
    args = parser.parse_args(argv)
    try:
        sys.stdout.write(stable_report(args.csv_path))
    except CsvInputError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
