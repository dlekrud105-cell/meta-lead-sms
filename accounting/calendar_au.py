"""ATO and ASIC deadlines for the company, generated from its own profile.

Dates here are the standard self-lodgement dates. A registered BAS or tax
agent normally gets around four extra weeks on each BAS and a later date for
the company return, so treat these as the conservative version.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import accounts as coa
from . import config
from . import ledger
from . import lodgements as lodgements_mod
from .periods import (PAYDAY_SUPER_START, fy_ending, fy_range, parse_date,
                      quarters_in_fy)

BAS = 'BAS'
SUPER = 'SUPER'
TPAR = 'TPAR'
STP = 'STP'
TAX_RETURN = 'TAX_RETURN'
ASIC = 'ASIC'


@dataclass
class Obligation:
    kind: str
    label: str
    period: str
    due: date
    note: str = ''
    lodged: object = None      # a Lodgement, when this one has been filed
    informational: bool = False  # a standing rule, not a dated deadline

    @property
    def is_done(self) -> bool:
        return self.lodged is not None

    def status(self, today) -> str:
        today = parse_date(today)
        if self.is_done:
            return 'LODGED'
        if self.informational:
            return 'IN FORCE'
        if self.due < today:
            return 'OVERDUE'
        if (self.due - today).days <= 21:
            return 'DUE SOON'
        return 'UPCOMING'

    def days_out(self, today) -> int:
        return (self.due - parse_date(today)).days


def _wages_paid(start, end) -> bool:
    """Whether any wage was actually paid in a window.

    Superannuation and STP only bite if someone was paid. Emitting those
    deadlines for a company whose directors take nothing as wages is noise
    that trains you to ignore the calendar.
    """
    for account in coa.by_role('wages'):
        if ledger.net(account.code, start, end) != 0:
            return True
    return False


def obligations(company: config.Company | None = None, through=None) -> list:
    """Every obligation from registration through `through` (default +18 months)."""
    company = company or config.load()
    start = company.registered or date.today()
    through = parse_date(through) if through else date.fromordinal(
        date.today().toordinal() + 550)

    first_fy = fy_ending(start)
    last_fy = fy_ending(through) + 1
    items = []

    for fy in range(first_fy, last_fy + 1):
        fy_start, fy_end = fy_range(fy)
        if fy_end < start:
            continue

        for quarter in quarters_in_fy(fy):
            if quarter.end < start or quarter.start > through:
                continue
            if company.gst_registered and company.gst_cycle == 'quarterly':
                due = quarter.due(company.uses_tax_agent)
                note = ('GST (1A/1B), wages and PAYG withheld (W1/W2), '
                        'PAYG instalment (5A).')
                if company.uses_tax_agent:
                    note += (f' Lodging through {company.tax_agent or "your agent"}, '
                             f'so the concession date applies; self-lodging it '
                             f'would be due {quarter.bas_due}.')
                items.append(Obligation(
                    BAS, 'Lodge and pay BAS', quarter.label, due, note))
            if quarter.quarterly_super_applies and _wages_paid(quarter.start,
                                                               quarter.end):
                items.append(Obligation(
                    SUPER, 'Pay superannuation guarantee', quarter.label,
                    quarter.super_due,
                    'Must be RECEIVED by the fund by this date. Late super becomes '
                    'the superannuation guarantee charge and stops being '
                    'deductible. This is the last quarter on the old rules if it '
                    f'ends before {PAYDAY_SUPER_START}.'))

        if fy_start < start and fy_end < start:
            continue

        if _wages_paid(fy_start, fy_end):
            items.append(Obligation(
                STP, 'Finalise STP for the year', f'FY{fy}', date(fy, 7, 14),
                'Finalisation is what releases the directors\' income '
                'statements for their own tax returns.'))

        if fy_start >= PAYDAY_SUPER_START:
            items.append(Obligation(
                SUPER, 'Pay Day Super applies all year', f'FY{fy}', fy_start,
                'Since 1 July 2026 super must reach the fund within 7 days of '
                'each pay day, not quarterly. There is no quarterly deadline to '
                'work to any more - run `check` after every pay run instead.',
                informational=True))

        if company.reports_tpar:
            items.append(Obligation(
                TPAR, 'Lodge Taxable Payments Annual Report', f'FY{fy}',
                date(fy, 8, 28),
                'Painting is a building and construction service, so payments '
                'to subcontractors are reportable.'))

        is_first_return = (fy == first_fy)
        items.append(Obligation(
            TAX_RETURN, 'Lodge company income tax return', f'FY{fy}',
            date(fy + 1, 2, 28) if is_first_return else date(fy + 1, 5, 15),
            'First return for a newly registered company is due 28 February.'
            if is_first_return else
            'Standard date when lodging through a registered tax agent; '
            '31 October if self-lodging.'))

    if company.registered:
        reg = company.registered
        # The first annual review falls on the first anniversary, not on
        # the registration date itself.
        for year in range(reg.year + 1, fy_ending(through) + 2):
            review = _safe_date(year, reg.month, reg.day)
            if review < start or review > through:
                continue
            items.append(Obligation(
                ASIC, 'ASIC annual review', str(year), review,
                'Annual review fee is payable within two months of this date, '
                'and the directors must pass a solvency resolution.'))

    filed = lodgements_mod.index()
    for item in items:
        item.lodged = filed.get((item.kind.upper(), item.period.upper()))
    return sorted(items, key=lambda o: (o.due, o.kind))


def _safe_date(year: int, month: int, day: int) -> date:
    """Handle a 29 February registration date in a non-leap year."""
    while day > 28:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, day)


def overdue(today=None, company=None) -> list:
    """Past its due date and not recorded as lodged."""
    today = parse_date(today) if today else date.today()
    return [o for o in obligations(company)
            if o.due < today and not o.is_done and not o.informational]


def upcoming(today=None, within_days=120, company=None) -> list:
    today = parse_date(today) if today else date.today()
    horizon = date.fromordinal(today.toordinal() + within_days)
    return [o for o in obligations(company)
            if today <= o.due <= horizon and not o.is_done and not o.informational]
