"""Exact price <-> integer-tick conversion.

All liquidity comparisons are done on integers. Prices are never rounded,
truncated or otherwise modified: an off-grid price raises OffGridError.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

FIXED_POINT_SCALE = 1_000_000_000  # Databento: 1 unit = 1e-9


class OffGridError(ValueError):
    """Raised when a price is not an exact multiple of the tick size."""


def to_decimal(value) -> Decimal:
    """Convert str/int/Decimal to Decimal exactly. Floats are refused."""
    if isinstance(value, float):
        raise TypeError("float prices are not accepted (inexact); pass str, int or Decimal")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"unparseable price {value!r}") from exc


def fixed_point_to_decimal(raw: int) -> Decimal:
    return Decimal(int(raw)) / Decimal(FIXED_POINT_SCALE)


@dataclass(frozen=True)
class TickGrid:
    tick_size: Decimal
    origin: Decimal = Decimal(0)

    def __post_init__(self):
        if not isinstance(self.tick_size, Decimal) or self.tick_size <= 0:
            raise ValueError("tick_size must be a positive Decimal")

    def to_ticks(self, price) -> int:
        p = to_decimal(price)
        q = (p - self.origin) / self.tick_size
        if q != q.to_integral_value():
            raise OffGridError(f"price {p} is not on the {self.tick_size} grid")
        return int(q)

    def is_on_grid(self, price) -> bool:
        try:
            self.to_ticks(price)
            return True
        except OffGridError:
            return False

    def to_price(self, ticks: int) -> Decimal:
        return self.origin + self.tick_size * ticks
