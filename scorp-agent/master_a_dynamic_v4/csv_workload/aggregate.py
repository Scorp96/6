from __future__ import annotations

import pathlib
from decimal import Decimal

from ..models import canonical_json
from .reader import OrderRow, read_rows


def aggregate_by_category(rows: tuple[OrderRow, ...]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for row in rows:
        totals[row.category] = totals.get(row.category, Decimal("0.00")) + row.amount
    return {key: totals[key] for key in sorted(totals)}


def stable_report(path: str | pathlib.Path) -> str:
    rows = read_rows(path)
    totals = aggregate_by_category(rows)
    document = {
        "categories": {key: format(value, ".2f") for key, value in totals.items()},
        "row_count": len(rows),
        "total": format(sum(totals.values(), Decimal("0.00")), ".2f"),
    }
    return canonical_json(document) + "\n"
