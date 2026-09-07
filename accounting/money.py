"""Decimal money helpers.

All monetary values in this package are Decimals rounded to cents using
ROUND_HALF_UP, which is the rounding the ATO expects on invoices and BAS
labels. Never use floats for money.
"""
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal('0.01')
ZERO = Decimal('0.00')


def money(value) -> Decimal:
    """Coerce anything sane (str, int, float, Decimal, None) to a 2dp Decimal."""
    if value is None or value == '':
        return ZERO
    if isinstance(value, float):
        value = repr(value)
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(value) -> str:
    """Format for display: -1234.5 -> '-1,234.50'."""
    d = money(value)
    return f'{d:,.2f}'


def gst_from_inclusive(amount_incl) -> Decimal:
    """GST component of a GST-inclusive amount (1/11th)."""
    return money(money(amount_incl) / Decimal('11'))


def gst_from_exclusive(amount_ex) -> Decimal:
    """GST to add to a GST-exclusive amount (10%)."""
    return money(money(amount_ex) * Decimal('0.10'))


def ex_gst(amount_incl) -> Decimal:
    """GST-exclusive value of a GST-inclusive amount."""
    return money(money(amount_incl) - gst_from_inclusive(amount_incl))
