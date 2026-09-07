"""GST tax codes and how each one lands on the BAS.

The BAS labels used here:
  G1  Total sales (GST inclusive) - includes GST-free and input-taxed sales
  G3  Other GST-free sales
  G10 Capital purchases (GST inclusive)
  G11 Non-capital purchases (GST inclusive)
  1A  GST on sales
  1B  GST on purchases (input tax credits)

Purchases with no GST in them (GST-free, input-taxed) still belong in G11 /
G10 - the label asks for total purchases, not just taxable ones. That matches
how Xero and MYOB report it.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TaxCode:
    code: str
    name: str
    rate: Decimal
    on_sales: bool          # may be used on income lines
    on_purchases: bool      # may be used on expense/asset lines
    sale_labels: tuple      # BAS labels a sale using this code feeds
    purchase_labels: tuple  # BAS labels a purchase using this code feeds
    note: str = ''

    @property
    def has_gst(self) -> bool:
        return self.rate > 0


TAX_CODES = {
    tc.code: tc for tc in [
        TaxCode('GST', 'GST 10%', Decimal('0.10'), True, True,
                ('G1',), ('G11',),
                'Standard taxable supply. Needs a valid tax invoice to claim.'),
        TaxCode('CAP', 'GST 10% on capital', Decimal('0.10'), False, True,
                (), ('G10',),
                'Capital purchase (tools, vehicle, plant) - reported at G10, not G11.'),
        TaxCode('FRE', 'GST-free', Decimal('0'), True, True,
                ('G1', 'G3'), ('G11',),
                'ASIC fees, most government charges, water rates, basic food.'),
        TaxCode('INP', 'Input taxed', Decimal('0'), True, True,
                ('G1',), ('G11',),
                'Bank fees and interest. No input tax credit available.'),
        TaxCode('NT', 'BAS excluded', Decimal('0'), True, True,
                (), (),
                'Wages, super, PAYG, dividends, loans, depreciation, ATO payments, '
                'private amounts. Never appears anywhere on the BAS.'),
    ]
}

DEFAULT_SALE_CODE = 'GST'
DEFAULT_PURCHASE_CODE = 'GST'


def get(code: str) -> TaxCode:
    key = (code or 'NT').strip().upper()
    if key not in TAX_CODES:
        raise KeyError(
            f'unknown tax code {code!r}; valid codes: {", ".join(sorted(TAX_CODES))}')
    return TAX_CODES[key]


def gst_on(amount_ex, code: str) -> Decimal:
    """GST payable/claimable on a GST-exclusive amount for this tax code."""
    from .money import money
    return money(money(amount_ex) * get(code).rate)
