"""A record of what has actually been lodged with the ATO or ASIC.

The compliance calendar knows when things are due. It cannot know what your
tax agent already filed on your behalf, so anything lodged gets recorded here
and stops being reported as outstanding.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import store
from .money import ZERO, money
from .periods import parse_date


@dataclass
class Lodgement:
    kind: str          # BAS, TPAR, STP, TAX_RETURN, ASIC
    period: str        # 'Q3 FY2026', 'FY2026', '2027'
    lodged_date: date
    reference: str = ''    # ATO document ID or receipt number
    amount: str = ''       # what was payable or refundable
    lodged_by: str = ''    # 'Woori Accounting', 'self'
    notes: str = ''

    @classmethod
    def from_row(cls, row: dict) -> 'Lodgement':
        return cls(kind=row['kind'], period=row['period'],
                   lodged_date=parse_date(row['lodged_date']),
                   reference=row.get('reference', ''), amount=row.get('amount', ''),
                   lodged_by=row.get('lodged_by', ''), notes=row.get('notes', ''))

    def to_row(self) -> dict:
        return {'kind': self.kind, 'period': self.period,
                'lodged_date': self.lodged_date.isoformat(),
                'reference': self.reference, 'amount': self.amount,
                'lodged_by': self.lodged_by, 'notes': self.notes}

    @property
    def key(self) -> tuple:
        return (self.kind.upper(), self.period.upper())


def all_lodgements() -> list:
    return [Lodgement.from_row(row) for row in store.LODGEMENTS.read()]


def index() -> dict:
    return {item.key: item for item in all_lodgements()}


def find(kind: str, period: str):
    return index().get((kind.upper(), str(period).upper()))


def record(kind, period, lodged_date, reference='', amount='', lodged_by='',
           notes='') -> Lodgement:
    kind = str(kind).upper()
    existing = find(kind, period)
    if existing:
        raise KeyError(
            f'{kind} for {period} is already recorded as lodged on '
            f'{existing.lodged_date}')
    item = Lodgement(kind=kind, period=str(period), lodged_date=parse_date(lodged_date),
                     reference=reference,
                     amount=f'{money(amount):.2f}' if amount not in ('', None) else '',
                     lodged_by=lodged_by, notes=notes)
    store.LODGEMENTS.append(item.to_row())
    return item


def remove(kind: str, period: str) -> bool:
    """Undo a lodgement record, for when it was entered by mistake."""
    rows = store.LODGEMENTS.read()
    remaining = [r for r in rows
                 if not (r['kind'].upper() == kind.upper()
                         and r['period'].upper() == str(period).upper())]
    if len(remaining) == len(rows):
        return False
    store.LODGEMENTS.write_all(remaining)
    return True
