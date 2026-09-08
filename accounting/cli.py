"""Command line interface: python3 -m accounting <command>."""
from __future__ import annotations

import argparse
import sys
from datetime import date

from . import accounts as coa
from . import bankimport
from . import bankrules
from . import bankstatement
from . import calendar_au as cal
from . import config
from . import contacts as contacts_mod
from . import jobs as jobs_mod
from . import ledger
from . import lodge as lodge_mod
from . import lodgements as lodgements_mod
from . import reports as rp
from . import store
from . import transactions as tx
from .money import ZERO, fmt, money
from .periods import (fy_ending, fy_range, parse_date, quarter_of,
                      quarters_in_fy, resolve_period)
from .render import heading, table


def today() -> str:
    return date.today().isoformat()


def _period(args):
    """Resolve --period, or --from/--to, into (start, end, label)."""
    if getattr(args, 'period', None):
        return resolve_period(args.period)
    start = getattr(args, 'start', None)
    end = getattr(args, 'end', None) or today()
    if not start:
        fy = fy_ending(end)
        start, fy_end = fy_range(fy)
        return start, parse_date(end), f'FY{fy} to {end}'
    return parse_date(start), parse_date(end), f'{start} to {end}'


def _warn(result):
    for message in result.get('warnings', []):
        print(f'  ! {message}')


# --------------------------------------------------------------------- setup

def cmd_setup(args):
    company = config.load()
    company.name = args.name or company.name
    company.trading_name = args.trading_name or company.trading_name
    company.abn = (args.abn or company.abn).replace(' ', '')
    company.acn = (args.acn or company.acn).replace(' ', '')
    company.state = args.state or company.state
    company.address = args.address or company.address
    company.registered_date = args.registered or company.registered_date
    if args.no_gst:
        company.gst_registered = False
    if args.gst_basis:
        company.gst_basis = args.gst_basis
    if args.tax_agent is not None:
        company.tax_agent = args.tax_agent
        company.uses_tax_agent = bool(args.tax_agent)
    if args.directors:
        defaults = config.default_directors()
        company.directors = []
        for index, name in enumerate(args.directors[:len(defaults)]):
            person = defaults[index]
            person.name = name
            company.directors.append(person)
    elif not company.directors:
        company.directors = config.default_directors()
    company.save()

    store.ACCOUNTS_EXPORT.write_all([{
        'code': a.code, 'name': a.name, 'type': a.type, 'tax_code': a.tax_code,
        'normal_side': a.normal_side, 'tpar': 'yes' if a.tpar else 'no',
        'deductible': 'yes' if a.deductible else 'no', 'note': a.note,
    } for a in coa.CHART])

    print(f'Books ready in {store.data_dir()}')
    print(f'  Company     {company.name}')
    print(f'  Registered  {company.registered_date or "(not set - use --registered)"}')
    print(f'  GST         {"registered, " + company.gst_cycle + ", " + company.gst_basis + " basis" if company.gst_registered else "not registered"}')
    if company.uses_tax_agent:
        print(f'  Tax agent   {company.tax_agent} (extended lodgement dates)')
    print(f'  Directors   {", ".join(d.name for d in company.directors)}')
    print(f'  Accounts    {len(coa.CHART)} in accounts.csv')
    if not company.registered_date:
        print('\n  Set the ASIC registration date so the compliance calendar works:')
        print('    python3 -m accounting setup --registered 2026-01-15')
    return 0


def cmd_company(args):
    company = config.load()
    print(heading(company.name))
    rows = [
        ['ABN', company.abn or '(not set)'],
        ['ACN', company.acn or '(not set)'],
        ['Registered', company.registered_date or '(not set)'],
        ['State', company.state],
        ['GST', f'registered, {company.gst_cycle}, {company.gst_basis} basis'
         if company.gst_registered else 'not registered'],
        ['Tax agent', f'{company.tax_agent} - extended lodgement dates'
         if company.uses_tax_agent else 'self-lodging'],
        ['Company tax rate', f'{company.company_tax_rate:.0%}'
         + (' (base rate entity)' if company.base_rate_entity else '')],
        ['Super guarantee', f'{company.super_rate:.0%}'],
        ['No-ABN withholding', f'{company.no_abn_withholding_rate:.0%}'],
        ['TPAR', 'yes - building and construction' if company.reports_tpar else 'no'],
    ]
    print(table(['Setting', 'Value'], rows))
    print(heading('Directors'))
    print(table(['Key', 'Name', 'Wages', 'Loan account', 'Dividends'],
                [[d.key, d.name, d.wage_account, d.loan_account, d.dividend_account]
                 for d in company.directors]))
    return 0


def cmd_accounts(args):
    rows = [[a.code, a.name, a.type, a.tax_code, a.normal_side,
             'TPAR' if a.tpar else ('no-ded' if not a.deductible else '')]
            for a in coa.CHART
            if not args.search or args.search.lower() in
            f'{a.code} {a.name} {a.note}'.lower()]
    print(table(['Code', 'Name', 'Type', 'Tax', 'Nrm', 'Flags'], rows))
    return 0


# ------------------------------------------------------------------- contacts

def cmd_contact_add(args):
    contact = contacts_mod.add(
        args.name, args.type, abn=args.abn or '', gst_registered=args.gst,
        email=args.email or '', phone=args.phone or '',
        address=args.address or '', notes=args.notes or '')
    print(f'{contact.contact_id}  {contact.name}  ({contact.type})')
    if contact.type == contacts_mod.SUBCONTRACTOR and not contact.abn:
        print('  ! No ABN. You must withhold 47% from payments and the TPAR '
              'will be incomplete.')
    return 0


def cmd_contact_list(args):
    rows = [[c.contact_id, c.name, c.type,
             (c.abn_formatted + ('' if c.abn_is_valid else ' !')) if c.abn else '-',
             'yes' if c.gst_registered else 'no', c.phone or c.email or '']
            for c in contacts_mod.all_contacts()
            if not args.type or c.type == args.type]
    print(table(['ID', 'Name', 'Type', 'ABN', 'GST', 'Contact'], rows))
    return 0


def cmd_contact_update(args):
    changes = {k: v for k, v in vars(args).items()
               if k in ('abn', 'email', 'phone', 'address', 'notes') and v is not None}
    if args.gst is not None:
        changes['gst_registered'] = args.gst
        changes['abn_quoted'] = bool(changes.get('abn'))
    if changes.get('abn'):
        changes['abn_quoted'] = True
    contact = contacts_mod.update(args.reference, **changes)
    print(f'{contact.contact_id}  {contact.name}  ABN {contact.abn or "-"}')
    return 0


# ----------------------------------------------------------------------- jobs

def cmd_job_add(args):
    job = jobs_mod.add(args.name, contact_id=args.contact or '',
                       address=args.address or '', quoted_incl=args.quoted or '',
                       started=args.started or '')
    print(f'{job.job_id}  {job.name}')
    return 0


def cmd_job_list(args):
    rows = [[j.job_id, j.name, j.status, j.quoted_incl or '-', j.address]
            for j in jobs_mod.all_jobs()]
    print(table(['ID', 'Name', 'Status', 'Quoted', 'Address'], rows))
    return 0


# ------------------------------------------------------------------- invoices

def cmd_invoice(args):
    result = tx.create_invoice(args.date, args.contact, args.line,
                               due_days=args.terms, job=args.job or '',
                               memo=args.memo or '')
    print(f'{result["doc_id"]}  total {fmt(result["total_incl"])} '
          f'(GST {fmt(result["gst"])})  due {result["due_date"]}')
    return 0


def cmd_receipt(args):
    result = tx.record_receipt(args.date, args.invoice, args.amount)
    print(f'Received {fmt(result["amount"])} on {args.invoice}. '
          f'Outstanding {fmt(result["remaining"])}.')
    return 0


def cmd_bill(args):
    result = tx.create_bill(args.date, args.contact, args.line,
                            due_days=args.terms, job=args.job or '',
                            memo=args.memo or '')
    print(f'{result["doc_id"]}  total {fmt(result["total_incl"])} '
          f'(GST {fmt(result["gst"])})  payable {fmt(result["payable"])}  '
          f'due {result["due_date"]}')
    _warn(result)
    return 0


def cmd_pay_bill(args):
    result = tx.pay_bill(args.date, args.bill, args.amount)
    print(f'Paid {fmt(result["amount"])} on {args.bill}. '
          f'Outstanding {fmt(result["remaining"])}.')
    return 0


def cmd_spend(args):
    result = tx.spend_money(args.date, args.account, args.amount,
                            contact=args.contact or '',
                            description=args.description or '',
                            tax_code=args.tax_code or '', job=args.job or '',
                            bank=args.bank)
    print(f'{result["entry_id"]}  {fmt(result["amount_incl"])} paid '
          f'({fmt(result["amount_ex"])} + GST {fmt(result["gst"])})')
    return 0


def cmd_receive(args):
    result = tx.receive_money(args.date, args.account, args.amount,
                              contact=args.contact or '',
                              description=args.description or '',
                              tax_code=args.tax_code or '', job=args.job or '',
                              bank=args.bank)
    print(f'{result["entry_id"]}  {fmt(result["amount_incl"])} received '
          f'({fmt(result["amount_ex"])} + GST {fmt(result["gst"])})')
    return 0


# ------------------------------------------------------------------- payroll

def cmd_wages(args):
    result = tx.pay_wages(args.date, args.director, args.gross, args.payg,
                          super_amount=args.super, bank=args.bank)
    print(f'{result["entry_id"]}  gross {fmt(result["gross"])}  '
          f'PAYG {fmt(result["payg"])}  net paid {fmt(result["net"])}  '
          f'super accrued {fmt(result["super"])}')
    print('  Remember to lodge this through STP on or before the pay day.')
    return 0


def cmd_super(args):
    result = tx.pay_super(args.date, args.amount, bank=args.bank)
    print(f'{result["entry_id"]}  {fmt(result["amount"])} paid to funds')
    return 0


def cmd_dividend(args):
    result = tx.pay_dividend(args.date, args.director, args.amount,
                             bank=args.bank, franked=not args.unfranked)
    print(f'{result["entry_id"]}  {fmt(result["amount"])} '
          f'{"franked" if result["franked"] else "unfranked"} dividend')
    return 0


def cmd_loan(args):
    direction = 'from_director' if args.repay else 'to_director'
    result = tx.director_loan(args.date, args.director, args.amount,
                              direction=direction, bank=args.bank)
    print(f'{result["entry_id"]}  {fmt(result["amount"])} {direction.replace("_", " ")}')
    if direction == 'to_director':
        print('  ! Division 7A: repay this before the lodgement day for the '
              'financial year, or put it under a complying loan agreement.')
    return 0


def cmd_depreciate(args):
    result = tx.record_depreciation(args.date, args.account, args.amount)
    print(f'{result["entry_id"]}  {fmt(result["amount"])} depreciation')
    return 0


def cmd_journal(args):
    result = tx.manual_journal(args.date, args.memo, args.line)
    print(f'{result["entry_id"]}  posted')
    return 0


# -------------------------------------------------------------------- reports

def cmd_report(args):
    name = args.name
    handler = REPORTS.get(name)
    if handler is None:
        print(f'unknown report {name!r}; try: {", ".join(sorted(REPORTS))}',
              file=sys.stderr)
        return 2
    return handler(args)


def _report_tb(args):
    as_at = args.end or today()
    rows = [[a.code, a.name, fmt(d) if d else '', fmt(c) if c else '']
            for a, d, c in ledger.trial_balance(as_at)]
    total_debit = money(sum((d for _, d, _ in ledger.trial_balance(as_at)), ZERO))
    total_credit = money(sum((c for _, _, c in ledger.trial_balance(as_at)), ZERO))
    print(heading(f'Trial balance as at {as_at}'))
    print(table(['Code', 'Account', 'Debit', 'Credit'], rows, align='llrr'))
    print(f'\n  Totals: debits {fmt(total_debit)}  credits {fmt(total_credit)}  '
          f'{"balanced" if total_debit == total_credit else "OUT OF BALANCE"}')
    return 0


def _print_section(section):
    if not section.rows:
        return
    print(f'\n  {section.title}')
    print(table(['Code', 'Account', 'Amount'],
                [[r.account.code, r.account.name, fmt(r.amount)] for r in section.rows],
                align='llr', indent='    '))
    print(f'    {"Total " + section.title:<44} {fmt(section.total):>12}')


def _report_pl(args):
    start, end, label = _period(args)
    pl = rp.profit_and_loss(start, end, label)
    print(heading(f'Profit and loss  {pl.label}'))
    _print_section(pl.income)
    _print_section(pl.cost_of_sales)
    print(f'\n    {"GROSS PROFIT":<44} {fmt(pl.gross_profit):>12}')
    _print_section(pl.expenses)
    print(f'\n    {"NET PROFIT":<44} {fmt(pl.net_profit):>12}')
    if pl.non_deductible:
        print(f'    {"Add back non-deductible":<44} {fmt(pl.non_deductible):>12}')
        print(f'    {"TAXABLE INCOME":<44} {fmt(pl.taxable_income):>12}')
    return 0


def _report_bs(args):
    as_at = args.end or today()
    bs = rp.balance_sheet(as_at)
    print(heading(f'Balance sheet as at {bs.as_at}'))
    _print_section(bs.assets)
    _print_section(bs.liabilities)
    _print_section(bs.equity)
    print(f'    {"Retained earnings":<44} {fmt(bs.retained_earnings):>12}')
    print(f'    {"Current year earnings":<44} {fmt(bs.current_year_earnings):>12}')
    print(f'    {"TOTAL EQUITY":<44} {fmt(bs.total_equity):>12}')
    print(f'\n    Assets {fmt(bs.assets.total)}  =  Liabilities '
          f'{fmt(bs.liabilities.total)} + Equity {fmt(bs.total_equity)}  '
          f'{"OK" if bs.balances else "OUT BY " + fmt(bs.out_by)}')
    return 0


def _report_bas(args):
    start, end, label = _period(args)
    report = rp.bas(start, end, label, payg_instalment=args.instalment or 0,
                    basis=args.basis)
    print(heading(f'Business activity statement  {report.label}'))
    print(f'  Period {report.start} to {report.end}   Due {report.due}   '
          f'GST accounting method: {report.basis}')
    rows = [
        ['G1', 'Total sales (including GST)', fmt(report.g1)],
        ['G3', 'Other GST-free sales', fmt(report.g3)],
        ['G10', 'Capital purchases (including GST)', fmt(report.g10)],
        ['G11', 'Non-capital purchases (including GST)', fmt(report.g11)],
        ['1A', 'GST on sales', fmt(report.gst_on_sales)],
        ['1B', 'GST on purchases', fmt(report.gst_on_purchases)],
        ['W1', 'Total salary, wages and other payments', fmt(report.w1)],
        ['W2', 'Amounts withheld from W1', fmt(report.w2)],
        ['W4', 'Amounts withheld where no ABN quoted', fmt(report.w4)],
        ['W5', 'Total amounts withheld', fmt(report.w5)],
        ['5A', 'PAYG income tax instalment', fmt(report.payg_instalment)],
        ['7', 'NET AMOUNT ' + ('PAYABLE' if report.net_amount >= ZERO else 'REFUNDABLE'),
         fmt(abs(report.net_amount))],
    ]
    print(table(['Label', 'Description', 'Amount'], rows, align='llr'))
    if report.basis == 'cash' and (report.deferred_gst_sales
                                   or report.deferred_gst_purchases):
        print(f'\n  Not on this BAS because the money has not moved yet:')
        print(f'    GST on unpaid invoices you issued   '
              f'{fmt(report.deferred_gst_sales):>10}  (payable when they pay you)')
        print(f'    GST credits on bills you owe        '
              f'{fmt(report.deferred_gst_purchases):>10}  (claimable when you pay)')
    for message in report.checks:
        print(f'\n  ! {message}')
    if args.pay:
        result = tx.pay_bas(args.pay_date or report.due.isoformat(),
                            report.gst_on_sales, report.gst_on_purchases,
                            report.w5, report.payg_instalment,
                            memo=f'BAS {report.label}')
        print(f'\n  Settled: {result["entry_id"]}  net {fmt(result["net"])} '
              f'{result["direction"]}')
    return 0


def _report_tpar(args):
    fy = args.fy or fy_ending(today())
    report = rp.tpar(fy)
    print(heading(f'Taxable payments annual report  FY{report.fy}'))
    print(f'  Payments made {report.start} to {report.end}   Due {report.due}')
    rows = [[r.contact.name, r.contact.abn_formatted or 'MISSING', fmt(r.gross_paid),
             fmt(r.gst), fmt(r.tax_withheld), '; '.join(r.issues)]
            for r in report.rows]
    print(table(['Payee', 'ABN', 'Gross paid', 'GST', 'Withheld', 'Issues'],
                rows, align='llrrrl'))
    print(f'\n  Total paid {fmt(report.total_paid)}   GST {fmt(report.total_gst)}'
          f'   Withheld {fmt(report.total_withheld)}')
    if report.unattributed:
        print(f'\n  ! {fmt(report.unattributed)} was paid to subcontractor '
              'accounts with no payee recorded. The TPAR cannot be lodged '
              'until every one of those has a name and an ABN against it.')
    problems = [r for r in report.rows if r.issues]
    if problems:
        print(f'\n  ! {len(problems)} payee(s) missing details the TPAR requires. '
              'Fix with: python3 -m accounting contact update <id> --abn ... '
              '--address ...')
    return 0


def _report_ar(args):
    as_at = args.end or today()
    report = rp.aged_receivables(as_at)
    print(heading(f'Aged receivables as at {as_at}'))
    print(table(['Invoice', 'Customer', 'Due', 'Amount', 'Age'],
                [[r.doc_id, r.contact, r.due_date.isoformat(), fmt(r.amount), r.bucket]
                 for r in report.rows], align='lllrl'))
    print(f'\n  Total owed to you {fmt(report.total)}')
    print(table(['Bucket', 'Amount'],
                [[k, fmt(v)] for k, v in report.by_bucket().items() if v],
                align='lr'))
    return 0


def _report_ap(args):
    as_at = args.end or today()
    report = rp.aged_payables(as_at)
    print(heading(f'Aged payables as at {as_at}'))
    print(table(['Bill', 'Supplier', 'Due', 'Amount', 'Age'],
                [[r.doc_id, r.contact, r.due_date.isoformat(), fmt(r.amount), r.bucket]
                 for r in report.rows], align='lllrl'))
    print(f'\n  Total you owe {fmt(report.total)}')
    return 0


def _report_jobs(args):
    start, end, label = _period(args)
    results = rp.job_results(start, end)
    print(heading(f'Job profitability  {label}'))
    print(table(['Job', 'Income', 'Cost', 'Margin', 'Margin %'],
                [[r.job.name, fmt(r.income), fmt(r.cost), fmt(r.margin),
                  f'{r.margin_pct}%'] for r in results], align='lrrrr'))
    return 0


def _report_cash(args):
    as_at = args.end or today()
    position = rp.cash_position(as_at)
    print(heading(f'Cash position as at {as_at}'))
    rows = [
        ['Bank and card balances', fmt(position.bank)],
        ['GST owing to the ATO', fmt(position.gst_owing)],
        ['PAYG withheld, not yet paid', fmt(position.payg_owing)],
        ['Super accrued, not yet paid', fmt(position.super_owing)],
        ['Company tax provision', fmt(position.tax_provision)],
        ['SET ASIDE IN TOTAL', fmt(position.set_aside)],
        ['SAFE TO SPEND', fmt(position.available)],
    ]
    print(table(['Item', 'Amount'], rows, align='lr'))
    if position.available < ZERO:
        print('\n  ! You are holding less cash than you owe the ATO and the '
              'super funds. Chase debtors before drawing anything out.')
    return 0


def _report_tax(args):
    fy = args.fy or fy_ending(today())
    estimate = rp.tax_estimate(fy, as_at=args.end)
    print(heading(f'Company tax estimate  FY{estimate.fy}'))
    rows = [
        ['Net profit', fmt(estimate.net_profit)],
        ['Add back non-deductible', fmt(estimate.non_deductible)],
        ['Taxable income', fmt(estimate.taxable_income)],
        [f'Company tax at {estimate.rate:.0%}', fmt(estimate.tax)],
        ['Less PAYG instalments paid', fmt(estimate.instalments_paid)],
        ['ESTIMATED TAX PAYABLE', fmt(estimate.payable)],
    ]
    print(table(['Item', 'Amount'], rows, align='lr'))
    print('\n  Estimate only: it ignores depreciation timing differences, '
          'carried-forward losses and any private-use adjustments.')
    return 0


def _report_loans(args):
    as_at = args.end or today()
    print(heading(f'Director loan accounts as at {as_at}'))
    positions = rp.director_loans(as_at)
    print(table(['Director', 'Account', 'Company owes director', 'Director owes company'],
                [[p.director, p.account.code, fmt(p.balance) if p.balance > ZERO else '',
                  fmt(p.owed_by_director) if p.owed_by_director else '']
                 for p in positions], align='llrr'))
    for message in rp.division_7a_warnings(as_at):
        print(f'\n  ! {message}')
    return 0


def _report_super(args):
    as_at = args.end or today()
    obligations = rp.super_obligations(as_at)
    print(heading(f'Superannuation obligations as at {as_at}'))
    for fy in rp.financial_years(as_at):
        fy_start, fy_end = fy_range(fy)
        gap = rp.super_shortfall(fy_start, min(fy_end, parse_date(as_at)))
        if gap.wages == ZERO:
            continue
        print(f'  FY{fy}: wages {fmt(gap.wages)}, super required '
              f'{fmt(gap.expected)}, recognised {fmt(gap.recognised)}'
              + (f'  -> SHORT BY {fmt(gap.shortfall)}'
                 if gap.shortfall > ZERO else '  -> ok'))
    print()
    rows = [[o.pay_date.isoformat(), fmt(o.amount), o.due.isoformat(),
             fmt(o.paid), fmt(o.outstanding),
             'LATE' if o.is_late(as_at) else ('paid' if not o.outstanding else 'due')]
            for o in obligations]
    print(table(['Pay date', 'Super', 'Due', 'Paid', 'Outstanding', 'Status'],
                rows, align='lrlrrl'))
    late = [o for o in obligations if o.is_late(as_at)]
    if late:
        print(f'\n  ! {len(late)} pay run(s) with super past its due date, '
              f'{fmt(money(sum((o.outstanding for o in late), ZERO)))} in total. '
              'Late super stops being deductible and turns into the '
              'superannuation guarantee charge, which has to be lodged on an '
              'SGC statement.')
    print('\n  Pay runs from 2026-07-01 are under Pay Day Super: the money must '
          'reach the fund within 7 days of the pay day, not at the end of the '
          'quarter.')

    company = config.load()
    for fy in rp.financial_years(as_at):
        fy_start, fy_end = fy_range(fy)
        gap = rp.super_shortfall(fy_start, min(fy_end, parse_date(as_at)))
        if gap.shortfall <= ZERO:
            continue
        # Attribute the shortfall to the quarter the wages were actually paid.
        for quarter in quarters_in_fy(fy):
            wages = ZERO
            for account in coa.by_role('wages'):
                wages += ledger.net(account.code, quarter.start, quarter.end)
            if wages <= ZERO:
                continue
            shortfall = min(gap.shortfall, money(wages * company.super_rate))
            estimate = rp.sgc_estimate(quarter.start, shortfall,
                                       employees=len(company.directors) or 1,
                                       as_at=as_at, company=company)
            print(heading(f'Superannuation guarantee charge - {estimate.quarter_label}'))
            print(table(['Item', 'Amount'], [
                ['Super shortfall', fmt(estimate.shortfall)],
                [f'Nominal interest at 10% for {estimate.days_of_interest} days',
                 fmt(estimate.nominal_interest)],
                [f'Administration fee, {estimate.employees} employees',
                 fmt(estimate.admin_fee)],
                ['TOTAL SGC (none of it deductible)', fmt(estimate.total)],
            ], align='lr'))
            print(f'\n  SGC statement was due {estimate.statement_due}. Paying '
                  'the fund now does not undo it - the statement still has to '
                  'be lodged, and interest keeps running until it is.')
            print(f'  Being late costs {fmt(estimate.cost_of_being_late)} on top '
                  'of the super itself, plus the deduction you lose on '
                  f'{fmt(estimate.shortfall)}.')
            print('  The SGC is one of the amounts a director is personally '
                  'liable for under a director penalty notice.')
    return 0


def _report_cashflow(args):
    start, end, label = _period(args)
    flow = rp.cashflow(start, end)
    if not flow.periods:
        print('  (no movements in this period)')
        return 0
    months = [p.label for p in flow.periods]
    print(heading(f'Cash flow  {label}'))

    summary = [['Opening'] + [fmt(p.opening) for p in flow.periods] + ['']]
    summary.append(['Money in'] + [fmt(p.total_in) for p in flow.periods]
                   + [fmt(flow.total_in)])
    summary.append(['Money out'] + [fmt(p.total_out) for p in flow.periods]
                   + [fmt(flow.total_out)])
    summary.append(['NET'] + [fmt(p.net) for p in flow.periods]
                   + [fmt(money(flow.total_in - flow.total_out))])
    summary.append(['Closing'] + [fmt(p.closing) for p in flow.periods] + [''])
    align = 'l' + 'r' * (len(months) + 1)
    print(table(['', *months, 'Total'], summary, align=align))

    for direction, title in (('in', 'Where the money came from'),
                             ('out', 'Where the money went')):
        codes = flow.accounts(direction)
        if not codes:
            continue
        rows = []
        for code in codes:
            account = coa.get(code)
            cells = []
            for period in flow.periods:
                source = period.inflows if direction == 'in' else period.outflows
                amount = source.get(code, ZERO)
                cells.append(fmt(amount) if amount else '')
            rows.append([f'{code} {account.name}'[:34], *cells,
                         fmt(flow.total_for(code, direction))])
        print(f'\n  {title}')
        print(table(['Account', *months, 'Total'], rows, align=align,
                    indent='    '))
    return 0


REPORTS = {
    'cashflow': _report_cashflow,
    'super': _report_super,
    'tb': _report_tb, 'pl': _report_pl, 'bs': _report_bs, 'bas': _report_bas,
    'tpar': _report_tpar, 'ar': _report_ar, 'ap': _report_ap, 'jobs': _report_jobs,
    'cash': _report_cash, 'tax': _report_tax, 'loans': _report_loans,
}


def cmd_lodged(args):
    if args.undo:
        removed = lodgements_mod.remove(args.kind, args.period)
        print('Removed.' if removed
              else f'No record of {args.kind} for {args.period}.')
        return 0 if removed else 2
    item = lodgements_mod.record(
        args.kind, args.period, args.date or today(), reference=args.ref or '',
        amount=args.amount if args.amount is not None else '',
        lodged_by=args.by or '', notes=args.notes or '')
    print(f'{item.kind} {item.period} recorded as lodged {item.lodged_date}'
          + (f' by {item.lodged_by}' if item.lodged_by else '')
          + (f', ref {item.reference}' if item.reference else ''))
    print('  It will no longer show as outstanding in calendar or check.')
    return 0


def cmd_lodgements(args):
    rows = [[i.kind, i.period, i.lodged_date.isoformat(), i.reference or '-',
             i.amount or '-', i.lodged_by or '-', i.notes]
            for i in sorted(lodgements_mod.all_lodgements(),
                            key=lambda i: i.lodged_date)]
    print(heading('Lodged with the ATO and ASIC'))
    print(table(['Kind', 'Period', 'Lodged', 'Reference', 'Amount', 'By', 'Notes'],
                rows))
    return 0


def _proposal_rows(proposals, show):
    rows = []
    for proposal in proposals:
        if show and proposal.status not in show:
            continue
        line = proposal.line
        rows.append([
            line.date.isoformat(),
            ('-' if line.direction == 'debit' else '+') + fmt(line.amount),
            line.description[:44],
            proposal.account or '?',
            proposal.tax_code if proposal.account else '',
            proposal.status.upper(),
        ])
    return rows


def cmd_import_bank(args):
    try:
        statement = bankstatement.parse_file(args.file)
    except bankstatement.StatementError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2

    company = config.load()
    proposals = bankimport.propose(statement, company)
    summary = bankimport.summarise(proposals)
    counts = summary['by_status']

    print(heading(f'{args.file}'))
    print(f'  Account {statement.account}   {statement.start} to {statement.end}')
    print(f'  Opening {fmt(statement.opening)}   Closing {fmt(statement.closing)}   '
          f'{len(statement.lines)} transactions')
    print(f'  Debits {fmt(statement.debits)}   Credits {fmt(statement.credits)}   '
          'reconciled against the bank\'s own totals')
    print(f'\n  {counts.get(bankimport.READY, 0)} ready, '
          f'{counts.get(bankimport.REVIEW, 0)} need a decision, '
          f'{counts.get(bankimport.UNMATCHED, 0)} unmatched, '
          f'{counts.get(bankimport.IMPORTED, 0)} already imported')

    show = None
    if args.review:
        show = {bankimport.REVIEW, bankimport.UNMATCHED}
    elif not args.all:
        show = {bankimport.READY, bankimport.REVIEW, bankimport.UNMATCHED}
    rows = _proposal_rows(proposals, show)
    print()
    print(table(['Date', 'Amount', 'Description', 'Acct', 'Tax', 'Status'], rows,
                align='lrlllr'))

    needing = [p for p in proposals
               if p.status in (bankimport.REVIEW, bankimport.UNMATCHED)]
    if needing and not args.quiet:
        print('\n  Why these need a decision:')
        seen_notes = set()
        for proposal in needing:
            note = proposal.note or 'no rule matches this line'
            if note in seen_notes:
                continue
            seen_notes.add(note)
            print(f'   - {proposal.line.description[:40]}')
            print(f'     {note}')

    if not args.post:
        print('\n  Nothing has been posted. Add --post to write the ready lines '
              'to the ledger.')
        print('  Teach it a new rule with:  python3 -m accounting rule add '
              '"MERCHANT" 5100')
        return 0

    postable = [p for p in proposals if p.status == bankimport.READY]
    if args.include_review:
        postable += [p for p in proposals if p.status == bankimport.REVIEW
                     and p.account]
    posted = 0
    for proposal in postable:
        bankimport.post(proposal, company)
        posted += 1
    held = len([p for p in proposals
                if p.status in (bankimport.REVIEW, bankimport.UNMATCHED)])
    print(f'\n  Posted {posted} entries.')
    if held:
        print(f'  {held} line(s) held back. Review them with --review, then '
              'either add a rule or post them with --include-review.')
    return 0


def cmd_rule_add(args):
    rule = bankrules.add(args.pattern, args.account, tax_code=args.tax_code or '',
                         direction=args.direction, contact=args.contact or '',
                         note=args.note or '')
    account = coa.get(rule.account)
    print(f'Rule added: "{rule.pattern}" -> {account.code} {account.name} '
          f'({rule.tax_code or account.tax_code})')
    return 0


def cmd_rule_list(args):
    rows = [[r.pattern, r.direction, r.account, r.tax_code or '-',
             r.contact or '-', 'review' if r.review else '', r.note[:40]]
            for r in bankrules.all_rules()]
    print(table(['Pattern', 'Dir', 'Acct', 'Tax', 'Contact', 'Flag', 'Note'], rows))
    return 0


def cmd_lodge(args):
    company = config.load()
    kind = args.kind
    if kind == 'bas':
        start, end, label = _period(args)
        pack = lodge_mod.bas_pack(start, end, label,
                                  payg_instalment=args.instalment or 0,
                                  company=company)
    elif kind == 'tpar':
        pack = lodge_mod.tpar_pack(args.fy or fy_ending(today()), company)
    elif kind == 'sgc':
        pack = lodge_mod.sgc_pack(args.end or today(), company)
    else:
        pack = lodge_mod.stp_pack(args.fy or fy_ending(today()), company)

    print(heading(pack.title))
    print(f'  Where  {pack.where}')
    print(f'  Due    {pack.due}')
    print(f'  Entity {company.name}   ABN {company.abn}')

    if pack.fields:
        print('\n  Type these into the form, in this order:\n')
        print(table(['Label', 'Field', 'Enter'],
                    [[f.label, f.description, f.value] for f in pack.fields],
                    align='llr'))
        print('\n  Where each figure comes from:')
        for item in pack.fields:
            if item.source:
                print(f'    {item.label:>4}  {item.source}')
    if pack.rows:
        print()
        print(table(pack.row_headers, pack.rows))
    if pack.notes:
        print('\n  Notes:')
        for note in pack.notes:
            print(f'    - {note}')
    if pack.warnings:
        print('\n  Before you lodge:')
        for warning in pack.warnings:
            print(f'    ! {warning}')
    print('\n  This is a transcription aid. Nothing has been sent to the ATO.')
    print('  Once it is lodged, record it:  python3 -m accounting lodged '
          f'{kind.upper()} "<period>" --date <date> --ref <receipt>')
    return 0


# ------------------------------------------------------------------- calendar

def cmd_calendar(args):
    as_at = args.date or today()
    company = config.load()
    if not company.registered_date:
        print('Set the registration date first: '
              'python3 -m accounting setup --registered YYYY-MM-DD', file=sys.stderr)
        return 2
    overdue = cal.overdue(as_at, company)
    upcoming = cal.upcoming(as_at, args.days, company)
    print(heading(f'Compliance calendar as at {as_at}'))
    if overdue:
        print('\n  OVERDUE')
        print(table(['Due', 'Kind', 'Period', 'Obligation'],
                    [[o.due.isoformat(), o.kind, o.period, o.label] for o in overdue],
                    indent='    '))
        print('    Already lodged by your agent? Record it so it stops showing:')
        print('      python3 -m accounting lodged BAS "Q3 FY2026" --date ... --by ...')
    else:
        print('\n  Nothing overdue.')

    done = [o for o in cal.obligations(company) if o.is_done]
    if done and args.detail:
        print('\n  ALREADY LODGED')
        print(table(['Kind', 'Period', 'Lodged', 'By'],
                    [[o.kind, o.period, o.lodged.lodged_date.isoformat(),
                      o.lodged.lodged_by or '-'] for o in done], indent='    '))
    print(f'\n  NEXT {args.days} DAYS')
    print(table(['Due', 'In', 'Kind', 'Period', 'Obligation'],
                [[o.due.isoformat(), f'{o.days_out(as_at)}d', o.kind, o.period, o.label]
                 for o in upcoming], indent='    '))
    if args.detail:
        for obligation in overdue + upcoming:
            print(f'\n  {obligation.due} {obligation.label} ({obligation.period})')
            print(f'    {obligation.note}')
    return 0


# ---------------------------------------------------------------------- check

def cmd_check(args):
    """One command that answers: is anything wrong with the books right now?"""
    as_at = args.date or today()
    company = config.load()
    problems, notes = [], []

    trial = ledger.trial_balance(as_at)
    debits = money(sum((d for _, d, _ in trial), ZERO))
    credits = money(sum((c for _, _, c in trial), ZERO))
    if debits != credits:
        problems.append(f'Trial balance is out by {fmt(debits - credits)}.')

    if company.registered_date:
        for obligation in cal.overdue(as_at, company):
            problems.append(
                f'{obligation.due}  OVERDUE  {obligation.label} ({obligation.period})')
    else:
        notes.append('No ASIC registration date set, so no compliance calendar.')

    for message in rp.division_7a_warnings(as_at, company):
        problems.append(message)

    for fy, gap in rp.super_shortfalls(as_at, company):
        problems.append(
            f'FY{fy}: wages of {fmt(gap.wages)} were paid but only '
            f'{fmt(gap.recognised)} of superannuation has been recognised. At '
            f'{company.super_rate:.0%} it should be {fmt(gap.expected)}, so '
            f'{fmt(gap.shortfall)} is missing.')

    for obligation in rp.late_super(as_at, company):
        problems.append(
            f'Super of {fmt(obligation.outstanding)} for the {obligation.pay_date} '
            f'pay run was due {obligation.due} and is still unpaid. Late super is '
            'not deductible and has to go on an SGC statement.')

    for contact in contacts_mod.all_contacts():
        if contact.abn and not contact.abn_is_valid:
            problems.append(
                f'{contact.name} ({contact.contact_id}) has ABN {contact.abn} on '
                'file, which fails the ABN checksum. Until a valid one is '
                'quoted, 47% has to be withheld from their payments.')
        elif contact.type == contacts_mod.SUBCONTRACTOR and not contact.abn:
            problems.append(
                f'{contact.name} ({contact.contact_id}) is a subcontractor with no '
                'ABN on file: 47% withholding applies and the TPAR will be short.')

    fy = fy_ending(as_at)
    report = rp.tpar(fy)
    for row in report.rows:
        if row.issues:
            problems.append(
                f'TPAR FY{fy}: {row.contact.name} is missing '
                f'{" and ".join(row.issues)}.')

    aged = rp.aged_receivables(as_at)
    old = [r for r in aged.rows if r.bucket in ('61-90 days', '90+ days')]
    if old:
        problems.append(
            f'{len(old)} invoice(s) worth {fmt(money(sum((r.amount for r in old), ZERO)))} '
            'are more than 60 days overdue.')

    position = rp.cash_position(as_at, company)
    if position.available < ZERO:
        problems.append(
            f'Cash held ({fmt(position.bank)}) is less than what is owed to the ATO '
            f'and super funds ({fmt(position.set_aside)}). Short by '
            f'{fmt(-position.available)}.')
    else:
        notes.append(f'Safe to spend after setting aside tax, GST and super: '
                     f'{fmt(position.available)}.')

    turnover = rp.gst_turnover(as_at)
    if not company.gst_registered and turnover >= 75000:
        problems.append(
            f'Rolling 12-month turnover is {fmt(turnover)}, over the $75,000 GST '
            'registration threshold. Register within 21 days.')
    notes.append(f'Rolling 12-month turnover: {fmt(turnover)}.')

    print(heading(f'Health check as at {as_at}'))
    if problems:
        print(f'\n  {len(problems)} thing(s) need attention:\n')
        for item in problems:
            print(f'  ! {item}')
    else:
        print('\n  Nothing needs attention. Books balance and nothing is overdue.')
    if notes:
        print('\n  For information:')
        for note in notes:
            print(f'  - {note}')
    return 1 if problems else 0


# --------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python3 -m accounting',
        description='Bookkeeping and ATO compliance for an Australian painting '
                    'Pty Ltd.')
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('setup', help='create or update the company profile')
    p.add_argument('--name')
    p.add_argument('--trading-name')
    p.add_argument('--abn')
    p.add_argument('--acn')
    p.add_argument('--state')
    p.add_argument('--address')
    p.add_argument('--registered', help='ASIC registration date, YYYY-MM-DD')
    p.add_argument('--director', dest='directors', action='append',
                   help='director name, repeat for the second director')
    p.add_argument('--no-gst', action='store_true', help='not registered for GST')
    p.add_argument('--gst-basis', choices=['cash', 'accruals'],
                   help='must match the GST accounting method on your activity '
                        'statement')
    p.add_argument('--tax-agent',
                   help='name of your registered BAS or tax agent; enables the '
                        'extended lodgement dates. Pass an empty string to clear.')
    p.set_defaults(func=cmd_setup)

    sub.add_parser('company', help='show the company profile').set_defaults(
        func=cmd_company)

    p = sub.add_parser('accounts', help='list the chart of accounts')
    p.add_argument('search', nargs='?')
    p.set_defaults(func=cmd_accounts)

    contact = sub.add_parser('contact', help='customers, suppliers, subcontractors')
    contact_sub = contact.add_subparsers(dest='action', required=True)
    p = contact_sub.add_parser('add')
    p.add_argument('name')
    p.add_argument('type', choices=contacts_mod.TYPES)
    p.add_argument('--abn')
    p.add_argument('--gst', action='store_true', help='registered for GST')
    p.add_argument('--email')
    p.add_argument('--phone')
    p.add_argument('--address')
    p.add_argument('--notes')
    p.set_defaults(func=cmd_contact_add)
    p = contact_sub.add_parser('list')
    p.add_argument('--type', choices=contacts_mod.TYPES)
    p.set_defaults(func=cmd_contact_list)
    p = contact_sub.add_parser('update')
    p.add_argument('reference')
    p.add_argument('--abn')
    p.add_argument('--gst', dest='gst', action='store_true', default=None)
    p.add_argument('--email')
    p.add_argument('--phone')
    p.add_argument('--address')
    p.add_argument('--notes')
    p.set_defaults(func=cmd_contact_update)

    job = sub.add_parser('job', help='jobs and sites')
    job_sub = job.add_subparsers(dest='action', required=True)
    p = job_sub.add_parser('add')
    p.add_argument('name')
    p.add_argument('--contact')
    p.add_argument('--address')
    p.add_argument('--quoted')
    p.add_argument('--started')
    p.set_defaults(func=cmd_job_add)
    job_sub.add_parser('list').set_defaults(func=cmd_job_list)

    p = sub.add_parser('invoice', help='raise a customer invoice')
    p.add_argument('contact')
    p.add_argument('line', nargs='+',
                   help='account:amount_ex[:description[:tax_code[:job]]]')
    p.add_argument('--date', default=today())
    p.add_argument('--terms', type=int, help='payment terms in days')
    p.add_argument('--job')
    p.add_argument('--memo')
    p.set_defaults(func=cmd_invoice)

    p = sub.add_parser('receipt', help='record money received on an invoice')
    p.add_argument('invoice')
    p.add_argument('amount', nargs='?', help='defaults to the full balance')
    p.add_argument('--date', default=today())
    p.set_defaults(func=cmd_receipt)

    p = sub.add_parser('bill', help='enter a supplier or subcontractor bill')
    p.add_argument('contact')
    p.add_argument('line', nargs='+',
                   help='account:amount_ex[:description[:tax_code[:job]]]')
    p.add_argument('--date', default=today())
    p.add_argument('--terms', type=int)
    p.add_argument('--job')
    p.add_argument('--memo')
    p.set_defaults(func=cmd_bill)

    p = sub.add_parser('pay-bill', help='pay a supplier bill')
    p.add_argument('bill')
    p.add_argument('amount', nargs='?')
    p.add_argument('--date', default=today())
    p.set_defaults(func=cmd_pay_bill)

    p = sub.add_parser('spend', help='pay for something straight from the bank')
    p.add_argument('account')
    p.add_argument('amount', help='GST-inclusive amount actually paid')
    p.add_argument('--date', default=today())
    p.add_argument('--contact')
    p.add_argument('--description')
    p.add_argument('--tax-code')
    p.add_argument('--job')
    p.add_argument('--bank')
    p.set_defaults(func=cmd_spend)

    p = sub.add_parser('receive', help='take money in without an invoice')
    p.add_argument('account')
    p.add_argument('amount', help='GST-inclusive amount received')
    p.add_argument('--date', default=today())
    p.add_argument('--contact')
    p.add_argument('--description')
    p.add_argument('--tax-code')
    p.add_argument('--job')
    p.add_argument('--bank')
    p.set_defaults(func=cmd_receive)

    p = sub.add_parser('wages', help='pay a working director')
    p.add_argument('director')
    p.add_argument('gross')
    p.add_argument('payg', help='PAYG withheld from this pay')
    p.add_argument('--date', default=today())
    p.add_argument('--super', dest='super', help='override the calculated super')
    p.add_argument('--bank')
    p.set_defaults(func=cmd_wages)

    p = sub.add_parser('super', help='remit accrued super to the funds')
    p.add_argument('amount')
    p.add_argument('--date', default=today())
    p.add_argument('--bank')
    p.set_defaults(func=cmd_super)

    p = sub.add_parser('dividend', help='pay a dividend to a director')
    p.add_argument('director')
    p.add_argument('amount')
    p.add_argument('--date', default=today())
    p.add_argument('--unfranked', action='store_true')
    p.add_argument('--bank')
    p.set_defaults(func=cmd_dividend)

    p = sub.add_parser('loan', help='move money to or from a director loan account')
    p.add_argument('director')
    p.add_argument('amount')
    p.add_argument('--repay', action='store_true',
                   help='director putting money back into the company')
    p.add_argument('--date', default=today())
    p.add_argument('--bank')
    p.set_defaults(func=cmd_loan)

    p = sub.add_parser('depreciate', help='write down a fixed asset')
    p.add_argument('account', help='1400 tools or 1420 vehicles')
    p.add_argument('amount')
    p.add_argument('--date', default=today())
    p.set_defaults(func=cmd_depreciate)

    p = sub.add_parser('journal', help='post a manual journal entry')
    p.add_argument('memo')
    p.add_argument('line', nargs='+', help='account:DR|CR:amount[:description]')
    p.add_argument('--date', default=today())
    p.set_defaults(func=cmd_journal)

    p = sub.add_parser('report', help='financial and ATO reports')
    p.add_argument('name', choices=sorted(REPORTS))
    p.add_argument('--period', help='FY2026, 2026Q3, or 2026-01-01:2026-03-31')
    p.add_argument('--from', dest='start')
    p.add_argument('--to', dest='end')
    p.add_argument('--fy', type=int, help='financial year for tpar and tax')
    p.add_argument('--instalment', help='BAS label 5A as notified by the ATO')
    p.add_argument('--basis', choices=['cash', 'accruals'],
                   help='override the company GST accounting method')
    p.add_argument('--pay', action='store_true',
                   help='post the BAS settlement entry as well')
    p.add_argument('--pay-date')
    p.set_defaults(func=cmd_report)

    p = sub.add_parser('calendar', help='ATO and ASIC deadlines')
    p.add_argument('--date', help='treat this as today')
    p.add_argument('--days', type=int, default=120)
    p.add_argument('--detail', action='store_true')
    p.set_defaults(func=cmd_calendar)

    p = sub.add_parser('import-bank', help='read a bank statement into the ledger')
    p.add_argument('file', help='CommBank PDF statement')
    p.add_argument('--post', action='store_true',
                   help='write the ready lines to the ledger')
    p.add_argument('--include-review', action='store_true',
                   help='also post the lines flagged for review')
    p.add_argument('--review', action='store_true',
                   help='show only what needs a decision')
    p.add_argument('--all', action='store_true',
                   help='include lines already imported')
    p.add_argument('--quiet', action='store_true', help='skip the explanations')
    p.set_defaults(func=cmd_import_bank)

    rule = sub.add_parser('rule', help='rules that categorise bank lines')
    rule_sub = rule.add_subparsers(dest='action', required=True)
    p = rule_sub.add_parser('add')
    p.add_argument('pattern', help='text to look for, or re:<regex>')
    p.add_argument('account')
    p.add_argument('--tax-code')
    p.add_argument('--direction', choices=['any', 'debit', 'credit'], default='any')
    p.add_argument('--contact')
    p.add_argument('--note')
    p.set_defaults(func=cmd_rule_add)
    rule_sub.add_parser('list').set_defaults(func=cmd_rule_list)

    p = sub.add_parser('lodge', help='what to type into an ATO form, field by field')
    p.add_argument('kind', choices=sorted(lodge_mod.PACKS))
    p.add_argument('--period', help='FY2026, 2026Q4, or a date range')
    p.add_argument('--from', dest='start')
    p.add_argument('--to', dest='end')
    p.add_argument('--fy', type=int)
    p.add_argument('--instalment', help='BAS label 5A as notified by the ATO')
    p.set_defaults(func=cmd_lodge)

    p = sub.add_parser('lodged', help='record something as lodged with the ATO')
    p.add_argument('kind', help='BAS, TPAR, STP, TAX_RETURN or ASIC')
    p.add_argument('period', help='"Q3 FY2026", "FY2026" or "2027"')
    p.add_argument('--date', help='date it was lodged, defaults to today')
    p.add_argument('--ref', help='ATO document ID or receipt number')
    p.add_argument('--amount', help='amount payable or refundable')
    p.add_argument('--by', help='who lodged it, e.g. your tax agent')
    p.add_argument('--notes')
    p.add_argument('--undo', action='store_true', help='remove the record')
    p.set_defaults(func=cmd_lodged)

    sub.add_parser('lodgements', help='what has been lodged so far').set_defaults(
        func=cmd_lodgements)

    p = sub.add_parser('check', help='everything that needs attention right now')
    p.add_argument('--date', help='treat this as today')
    p.set_defaults(func=cmd_check)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (KeyError, ValueError, LookupError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
