"""Company profile and the tax rates the reports depend on.

Rates below are the ones in force for the 2025-26 and 2026-27 financial
years. They live in data/company.json so they can be corrected in one place
when the law changes, rather than being scattered through the code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import store
from .periods import parse_date

DEFAULT_RATES = {
    # Superannuation guarantee: 12% from 1 July 2025.
    'super_rate': '0.12',
    # Base rate entity company tax: 25% (aggregated turnover under $50m and
    # no more than 80% passive income). Otherwise 30%.
    'company_tax_rate': '0.25',
    # Withholding when a supplier does not quote an ABN: top rate, no levy.
    'no_abn_withholding_rate': '0.47',
    'gst_rate': '0.10',
    # Car depreciation limit for 2025-26. A vehicle costing more than this is
    # only depreciable up to the limit, and the GST credit is capped at 1/11
    # of it. Vehicles designed to carry a load of one tonne or more, and
    # vehicles not designed principally to carry passengers, are not 'cars'
    # for this purpose and the limit does not apply to them.
    'car_limit': '69674',
}


@dataclass
class Director:
    key: str
    name: str
    wage_account: str = '6000'
    loan_account: str = '2600'
    dividend_account: str = '3200'
    share: str = '50'

    @classmethod
    def from_dict(cls, raw: dict) -> 'Director':
        return cls(**{k: str(v) for k, v in raw.items() if k in cls.__annotations__})

    def to_dict(self) -> dict:
        return {'key': self.key, 'name': self.name,
                'wage_account': self.wage_account,
                'loan_account': self.loan_account,
                'dividend_account': self.dividend_account,
                'share': self.share}


@dataclass
class Company:
    name: str = 'Painting Company Pty Ltd'
    trading_name: str = ''
    abn: str = ''
    acn: str = ''
    state: str = 'NSW'
    address: str = ''
    registered_date: str = ''      # ASIC registration date, ISO format
    gst_registered: bool = True
    gst_cycle: str = 'quarterly'
    # 'cash' reports GST when money moves, 'accruals' when the invoice is
    # raised. It is on the activity statement under 'GST accounting method'
    # and it has to match, or the BAS will not agree with what the ATO expects.
    gst_basis: str = 'cash'
    # A registered BAS or tax agent gets later lodgement dates.
    uses_tax_agent: bool = False
    tax_agent: str = ''
    base_rate_entity: bool = True
    reports_tpar: bool = True      # building & construction industry
    default_bank: str = '1000'
    savings_bank: str = '1010'
    invoice_terms_days: int = 14
    rates: dict = field(default_factory=lambda: dict(DEFAULT_RATES))
    directors: list = field(default_factory=list)

    # ------------------------------------------------------------------ rates
    def rate(self, key: str) -> Decimal:
        return Decimal(str(self.rates.get(key, DEFAULT_RATES[key])))

    @property
    def super_rate(self) -> Decimal:
        return self.rate('super_rate')

    @property
    def company_tax_rate(self) -> Decimal:
        return self.rate('company_tax_rate') if self.base_rate_entity else Decimal('0.30')

    @property
    def no_abn_withholding_rate(self) -> Decimal:
        return self.rate('no_abn_withholding_rate')

    @property
    def cash_basis(self) -> bool:
        return self.gst_basis.strip().lower() == 'cash'

    @property
    def car_limit(self) -> Decimal:
        return self.rate('car_limit')

    # -------------------------------------------------------------- directors
    def director(self, key: str) -> Director:
        wanted = str(key).strip().lower()
        for d in self.directors:
            if d.key.lower() == wanted or d.name.lower() == wanted:
                return d
        known = ', '.join(f'{d.key} ({d.name})' for d in self.directors) or 'none configured'
        raise KeyError(f'unknown director {key!r}; known: {known}')

    @property
    def registered(self) -> date | None:
        return parse_date(self.registered_date) if self.registered_date else None

    # ---------------------------------------------------------------- storage
    @classmethod
    def from_dict(cls, raw: dict) -> 'Company':
        raw = dict(raw)
        directors = [Director.from_dict(d) for d in raw.pop('directors', [])]
        rates = dict(DEFAULT_RATES)
        rates.update({k: str(v) for k, v in raw.pop('rates', {}).items()})
        known = {k: v for k, v in raw.items() if k in cls.__annotations__}
        return cls(directors=directors, rates=rates, **known)

    def to_dict(self) -> dict:
        return {
            'name': self.name, 'trading_name': self.trading_name,
            'abn': self.abn, 'acn': self.acn, 'state': self.state,
            'address': self.address, 'registered_date': self.registered_date,
            'gst_registered': self.gst_registered, 'gst_cycle': self.gst_cycle,
            'gst_basis': self.gst_basis, 'uses_tax_agent': self.uses_tax_agent,
            'tax_agent': self.tax_agent,
            'base_rate_entity': self.base_rate_entity,
            'reports_tpar': self.reports_tpar,
            'default_bank': self.default_bank, 'savings_bank': self.savings_bank,
            'invoice_terms_days': self.invoice_terms_days,
            'rates': self.rates,
            'directors': [d.to_dict() for d in self.directors],
        }

    def save(self) -> None:
        store.write_company(self.to_dict())


def load() -> Company:
    raw = store.read_company()
    return Company.from_dict(raw) if raw else Company()


def default_directors() -> list:
    """Two working directors, each with their own loan and dividend account."""
    return [
        Director(key='d1', name='Director 1', wage_account='6000',
                 loan_account='2600', dividend_account='3200', share='50'),
        Director(key='d2', name='Director 2', wage_account='6000',
                 loan_account='2610', dividend_account='3210', share='50'),
    ]
