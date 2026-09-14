from __future__ import annotations

import csv
import dataclasses
import pathlib
from decimal import Decimal, InvalidOperation


HEADERS = ["order_id", "category", "amount"]


class CsvInputError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class OrderRow:
    order_id: str
    category: str
    amount: Decimal


def read_rows(path: str | pathlib.Path) -> tuple[OrderRow, ...]:
    source = pathlib.Path(path)
    parsed: list[OrderRow] = []
    try:
        handle = source.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise CsvInputError("CSV_OPEN_FAILED") from exc
    with handle:
        reader = csv.DictReader(handle, strict=True)
        if reader.fieldnames != HEADERS:
            raise CsvInputError("CSV_HEADER_INVALID")
        try:
            for line_number, row in enumerate(reader, start=2):
                if None in row or any(value is None for value in row.values()):
                    raise CsvInputError(f"CSV_COLUMN_COUNT_INVALID:{line_number}")
                order_id = str(row["order_id"]).strip()
                category = str(row["category"]).strip()
                if not order_id or not category:
                    raise CsvInputError(f"CSV_TEXT_EMPTY:{line_number}")
                try:
                    amount = Decimal(str(row["amount"]).strip())
                except (InvalidOperation, ValueError) as exc:
                    raise CsvInputError(f"CSV_AMOUNT_INVALID:{line_number}") from exc
                if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
                    raise CsvInputError(f"CSV_AMOUNT_INVALID:{line_number}")
                parsed.append(OrderRow(order_id=order_id, category=category, amount=amount))
        except csv.Error as exc:
            raise CsvInputError(f"CSV_SYNTAX_INVALID:{reader.line_num}") from exc
    return tuple(parsed)
