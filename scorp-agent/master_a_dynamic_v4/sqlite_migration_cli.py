"""Operator-invoked explicit SQLite migration command."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Sequence

from .sqlite_migration import migrate_sqlite_snapshot
from .state_store import StoreInvariantError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scorp-sqlite-migrate")
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--allowed-root", action="append", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        receipt = migrate_sqlite_snapshot(
            args.source,
            args.destination,
            allowed_roots=args.allowed_root,
        )
    except (OSError, StoreInvariantError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "BLOCKED", "error": f"{type(exc).__name__}:{exc}"},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(dataclasses.asdict(receipt), ensure_ascii=False, sort_keys=True))
    return 0 if receipt.status == "IMPORTED_SNAPSHOT" else 3


if __name__ == "__main__":
    raise SystemExit(main())
