"""Money helpers. Money is stored as integer minor units (kobo); never floats.

``₦1.00 == 100 kobo``. Python-side arithmetic uses ``Decimal``.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Union

Number = Union[int, float, str, Decimal]
NAIRA = "₦"
KOBO = Decimal(100)


def to_minor(amount: Number) -> int:
    """Naira (any numeric) -> integer kobo, rounding half up."""
    d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    return int((d * KOBO).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def from_minor(minor: int) -> Decimal:
    return Decimal(int(minor)) / KOBO


def round_minor(value: float) -> int:
    """Round a float amount already expressed in kobo to an int (half up)."""
    return int(Decimal(repr(float(value))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fmt_ngn(minor: int | float | None, exact: bool = False, signed: bool = False) -> str:
    """Format kobo as ₦1.23bn / ₦456.7m / ₦12,345 (compact) or exact ₦1,234,567.89."""
    if minor is None or minor != minor:      # None or NaN
        return "–"
    naira = Decimal(int(round(float(minor)))) / KOBO
    sign = "-" if naira < 0 else ("+" if signed and naira > 0 else "")
    a = abs(naira)
    if exact:
        return f"{sign}{NAIRA}{a:,.2f}"
    if a >= Decimal("1e12"):
        return f"{sign}{NAIRA}{a / Decimal('1e12'):.2f}tn"
    if a >= Decimal("1e9"):
        return f"{sign}{NAIRA}{a / Decimal('1e9'):.2f}bn"
    if a >= Decimal("1e6"):
        return f"{sign}{NAIRA}{a / Decimal('1e6'):.1f}m"
    return f"{sign}{NAIRA}{a:,.0f}"


def fmt_usd(minor: int | float | None) -> str:
    if minor is None:
        return "–"
    usd = float(minor) / 100.0
    a = abs(usd)
    sign = "-" if usd < 0 else ""
    if a >= 1e9:
        return f"{sign}US${a / 1e9:.2f}bn"
    if a >= 1e6:
        return f"{sign}US${a / 1e6:.1f}m"
    return f"{sign}US${a:,.0f}"


def fmt_pct(x: float | None, digits: int = 1) -> str:
    return "–" if x is None else f"{x * 100:.{digits}f}%"


def fmt_days(x: float | None) -> str:
    return "–" if x is None else f"{x:.1f} d"


def fmt_num(x: float | int | None) -> str:
    return "–" if x is None else f"{x:,.0f}"


def pct_of(part: int | float, whole: int | float) -> float | None:
    return None if not whole else float(part) / float(whole)
