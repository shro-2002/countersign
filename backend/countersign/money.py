"""Decimal-safe money parsing. Amounts cross every boundary as strings with at most 2 decimals."""

import re
from decimal import Decimal

AMOUNT_PATTERN = re.compile(r"^\d{1,9}(\.\d{1,2})?$")
SUPPORTED_CURRENCIES = frozenset({"USD"})
CENT = Decimal("0.01")


class MoneyError(ValueError):
    pass


def parse_amount(raw: object) -> Decimal:
    text = str(raw).strip().lstrip("$")
    if not AMOUNT_PATTERN.fullmatch(text):
        raise MoneyError(f"invalid amount {raw!r}: expected digits with up to 2 decimals")
    value = Decimal(text).quantize(CENT)
    if value <= 0:
        raise MoneyError("amount must be positive")
    return value


def parse_currency(raw: object) -> str:
    code = str(raw).strip().upper()
    if code not in SUPPORTED_CURRENCIES:
        raise MoneyError(f"unsupported currency {raw!r}")
    return code


def fmt(value: Decimal) -> str:
    return str(value.quantize(CENT))
