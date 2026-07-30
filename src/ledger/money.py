"""Decimal money helpers: every stored amount is quantized to two places."""

from decimal import ROUND_HALF_UP, Decimal

type Amount = Decimal | int | str

TWO_PLACES = Decimal("0.01")


def money(value: Amount | float) -> Decimal:
    """Coerce a numeric value into a two-decimal Decimal."""
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


ZERO = money(0)
