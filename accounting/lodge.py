"""Lodgement packs: exactly what to type into each ATO form.

These are transcription aids, not lodgements. Nothing here talks to the ATO.
Each pack lists the fields of one form in the order the ATO screen presents
them, with the figure to enter and where it came from, so the person lodging
can work down the screen without interpreting anything.

The declaration on every one of these forms is made by a person who is
authorised to make it. Read what you are signing before you sign it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import accounts as coa
from . import config
from . import contacts as contacts_mod
from . import ledger
from . import reports as rp
from .money import ZERO, money
from .periods import fy_range, parse_date, quarters_in_fy


@dataclass
class Field:
    label: str          # the ATO's own field label
    description: str
    value: str
    source: str = ''    # where the figure came from, so it can be checked


@dataclass
class Pack:
    title: str
    where: str          # the path through the ATO's online services
    due: str
    fields: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    rows: list = field(default_factory=list)      # for table-shaped forms
    row_headers: list = field(default_factory=list)


def _money(value) -> str:
    return f'{money(value):,.2f}'


def _whole(value) -> str:
    """The BAS takes whole dollars, rounded down, at most labels."""
    return f'{int(money(value)):,}'


# ---------------------------------------------------------------------- BAS

def bas_pack(start, end, label='', payg_instalment=0, company=None) -> Pack:
    company = company or config.load()
    report = rp.bas(start, end, label, payg_instalment=payg_instalment,
                    company=company)
    pack = Pack(
        title=f'Business activity statement - {report.label}',
        where='ATO Online services for business > Lodgments > Activity statements',
        due=f'{report.due} (lodging through {company.tax_agent})'
            if company.uses_tax_agent else str(report.due),
        fields=[
            Field('G1', 'Total sales (including GST)', _whole(report.g1),
                  'money received from customers this quarter, cash basis'),
            Field('G1 - Does the amount include GST?', '', 'Yes', ''),
            Field('G2', 'Export sales', '0', 'no export sales'),
            Field('G3', 'Other GST-free sales', _whole(report.g3), ''),
            Field('G10', 'Capital purchases (including GST)', _whole(report.g10),
                  'tools, plant and vehicles bought this quarter'),
            Field('G11', 'Non-capital purchases (including GST)',
                  _whole(report.g11), 'everything else you paid for'),
            Field('1A', 'GST on sales', _whole(report.gst_on_sales), ''),
            Field('1B', 'GST on purchases', _whole(report.gst_on_purchases), ''),
            Field('W1', 'Total salary, wages and other payments',
                  _whole(report.w1), 'gross wages paid this quarter'),
            Field('W2', 'Amount withheld from payments shown at W1',
                  _whole(report.w2), ''),
            Field('W3', 'Other amounts withheld', '0', ''),
            Field('W4', 'Amount withheld where no ABN was quoted',
                  _whole(report.w4), ''),
            Field('W5', 'Total amounts withheld', _whole(report.w5), 'W2 + W4'),
            Field('5A', 'PAYG income tax instalment',
                  _whole(report.payg_instalment),
                  'only if the ATO has notified an instalment amount'),
        ],
        notes=[
            f'GST accounting method on this statement: {report.basis}. It must '
            'match what the ATO has on file for the company.',
            'The BAS takes whole dollars. Cents are dropped at every label.',
        ],
    )
    net = report.net_amount
    pack.fields.append(Field(
        '7' if net >= ZERO else '8',
        'Amount payable to the ATO' if net >= ZERO else 'Amount refundable',
        _whole(abs(net)), '1A - 1B + W5 + 5A'))

    if report.basis == 'cash' and report.deferred_gst_sales:
        pack.notes.append(
            f'{_money(report.deferred_gst_sales)} of GST sits on invoices that '
            'have not been paid yet. On a cash basis that is not reportable '
            'until the money arrives, so it is deliberately not in 1A.')
    pack.warnings.extend(report.checks)
    if report.w1 > ZERO and report.w2 == ZERO:
        pack.warnings.append(
            f'W1 shows {_money(report.w1)} of wages with nothing withheld at W2. '
            'Check that PAYG withholding was actually calculated on those pays '
            'and that they were reported through STP.')
    return pack


# --------------------------------------------------------------------- TPAR

def tpar_pack(fy: int, company=None) -> Pack:
    company = company or config.load()
    report = rp.tpar(fy)
    pack = Pack(
        title=f'Taxable payments annual report - FY{report.fy}',
        where='ATO Online services for business > Lodgments > '
              'Taxable payments annual report',
        due=str(report.due),
        row_headers=['Payee', 'ABN', 'Address', 'Gross paid (incl GST)',
                     'GST included', 'Tax withheld'],
        notes=[
            'Painting is a building and construction service, so payments to '
            'contractors are reportable.',
            'This is a cash-basis report: it counts what was actually paid '
            f'between {report.start} and {report.end}, not what was invoiced.',
            'Gross paid includes GST and any amount withheld.',
        ],
    )
    for row in report.rows:
        pack.rows.append([
            row.contact.name,
            row.contact.abn_formatted or 'MISSING',
            row.contact.address or 'MISSING',
            _money(row.gross_paid),
            _money(row.gst),
            _money(row.tax_withheld),
        ])
        for issue in row.issues:
            pack.warnings.append(f'{row.contact.name}: {issue}. '
                                 'The form will not accept the payee without it.')
    if report.unattributed:
        pack.warnings.append(
            f'{_money(report.unattributed)} was paid to contractor accounts with '
            'no payee recorded and is missing from the rows above.')
    if not report.rows:
        pack.notes.append('No reportable payments found for this year.')
    return pack


# ---------------------------------------------------------------------- SGC

def sgc_pack(as_at=None, company=None) -> Pack:
    """One row per employee per quarter, which is how the SGC statement works."""
    company = company or config.load()
    as_at = parse_date(as_at) if as_at else date.today()
    pack = Pack(
        title='Superannuation guarantee charge statement',
        where='ATO Online services for business > Lodgments > '
              'Super guarantee charge statement',
        due='one month after the contribution deadline for each quarter',
        row_headers=['Quarter', 'Employee', 'Salary & wages', 'Super paid',
                     'Shortfall', 'Interest', 'Admin fee', 'Total'],
        notes=[
            'The charge is calculated on SALARY AND WAGES, not on ordinary '
            'time earnings, so it can exceed what the guarantee itself would '
            'have been.',
            'Nominal interest runs from the FIRST day of the quarter, not from '
            'the due date, and keeps running until the statement is lodged.',
            'None of the SGC is deductible. The contribution would have been.',
            'If the super has since been paid to the fund, ask about the late '
            'payment offset before lodging - it can reduce the charge, but '
            'only if you elect it.',
        ],
        warnings=[
            'The SGC is one of the amounts a director is personally liable for '
            'under a director penalty notice.',
            'These figures are an estimate to work from, not the lodged '
            'calculation. The ATO publishes an SGC statement spreadsheet that '
            'does the official arithmetic.',
        ],
    )

    directors = company.directors or []
    for fy in rp.financial_years(as_at, company):
        for quarter in quarters_in_fy(fy):
            if quarter.start > as_at:
                continue
            wages = ZERO
            for account in coa.by_role('wages'):
                wages += ledger.net(account.code, quarter.start,
                                    min(quarter.end, as_at))
            if wages <= ZERO:
                continue
            paid = ledger.net(coa.first_with_role('super_expense').code,
                              quarter.start, min(quarter.end, as_at))
            shortfall = money(wages * company.super_rate - paid)
            if shortfall <= ZERO:
                continue
            headcount = max(1, len(directors))
            estimate = rp.sgc_estimate(quarter.start, shortfall,
                                       employees=headcount, as_at=as_at,
                                       company=company)
            # Split evenly across the people paid, which is what the form wants.
            share = money(shortfall / headcount)
            for person in (directors or [None]):
                name = person.name if person else 'employee'
                pack.rows.append([
                    estimate.quarter_label, name,
                    _money(money(wages / headcount)),
                    _money(money(paid / headcount)),
                    _money(share),
                    _money(money(estimate.nominal_interest / headcount)),
                    _money(rp.SGC_ADMIN_FEE_PER_EMPLOYEE),
                    _money(money(share
                                 + estimate.nominal_interest / headcount
                                 + rp.SGC_ADMIN_FEE_PER_EMPLOYEE)),
                ])
            pack.notes.append(
                f'{estimate.quarter_label}: statement was due '
                f'{estimate.statement_due}, total charge about '
                f'{_money(estimate.total)}.')
    if not pack.rows:
        pack.notes.append('No superannuation shortfall found.')
        pack.warnings = []
    return pack


# ---------------------------------------------------------------------- STP

def stp_pack(fy: int, company=None) -> Pack:
    company = company or config.load()
    start, end = fy_range(fy)
    wages = ZERO
    for account in coa.by_role('wages'):
        wages += ledger.net(account.code, start, end)
    withheld = ZERO
    withholding_account = coa.first_with_role('payg_withholding').code
    for line in ledger.lines(start=start, end=end, account=withholding_account):
        withheld += money(line.credit - line.debit)

    pack = Pack(
        title=f'STP finalisation - FY{fy}',
        where='your payroll software (Payroller or Xero), not the ATO website',
        due=str(date(fy, 7, 14)),
        row_headers=['Employee', 'Gross for the year', 'PAYG withheld'],
        notes=[
            'Finalising is what releases each person\'s income statement in '
            'myGov so they can lodge their own return.',
            'Select the 2025-26 financial year, not 2026-27.',
            'Allow up to 72 hours for the ATO to show it as finalised.',
        ],
    )
    headcount = max(1, len(company.directors))
    for person in company.directors:
        pack.rows.append([person.name, _money(money(wages / headcount)),
                          _money(money(withheld / headcount))])
    if wages > ZERO and withheld == ZERO:
        pack.warnings.append(
            f'{_money(wages)} of wages with no PAYG withheld. Before finalising, '
            'confirm these pays were reported through STP at all - if they were '
            'never sent, an update event has to go first, and the withholding '
            'itself needs checking.')
    if wages == ZERO:
        pack.notes.append('No wages recorded for this year, so there is '
                          'nothing to finalise.')
        pack.warnings = []
    return pack


PACKS = {'bas': bas_pack, 'tpar': tpar_pack, 'sgc': sgc_pack, 'stp': stp_pack}
