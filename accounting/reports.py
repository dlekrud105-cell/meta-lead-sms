"""Reports derived from the journal: financials, BAS, TPAR and risk checks.

Nothing here stores anything. Every number is recomputed from the ledger, so
a report can never drift away from the underlying entries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import accounts as coa
from . import config
from . import contacts as contacts_mod
from . import jobs as jobs_mod
from . import ledger
from . import store
from . import taxcodes
from . import transactions as tx
from .money import ZERO, money
from .periods import (PAYDAY_SUPER_DAYS, PAYDAY_SUPER_START, fy_ending,
                      fy_range, parse_date, quarter_of)


@dataclass
class Row:
    account: coa.Account
    amount: Decimal


@dataclass
class Section:
    title: str
    rows: list = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return money(sum((r.amount for r in self.rows), ZERO))


def _section(title: str, types, nets: dict) -> Section:
    rows = []
    for account in coa.CHART:
        if account.type in types:
            amount = nets.get(account.code, ZERO)
            if amount != ZERO:
                rows.append(Row(account, amount))
    return Section(title, rows)


# ------------------------------------------------------------- profit and loss

@dataclass
class ProfitAndLoss:
    start: date
    end: date
    label: str
    income: Section
    cost_of_sales: Section
    expenses: Section

    @property
    def gross_profit(self) -> Decimal:
        return money(self.income.total - self.cost_of_sales.total)

    @property
    def net_profit(self) -> Decimal:
        return money(self.gross_profit - self.expenses.total)

    @property
    def non_deductible(self) -> Decimal:
        """Expenses that have to be added back when working out taxable income."""
        rows = [r for s in (self.cost_of_sales, self.expenses) for r in s.rows
                if not r.account.deductible]
        return money(sum((r.amount for r in rows), ZERO))

    @property
    def taxable_income(self) -> Decimal:
        return money(self.net_profit + self.non_deductible)


def profit_and_loss(start, end, label='') -> ProfitAndLoss:
    start, end = parse_date(start), parse_date(end)
    nets = ledger.nets(start=start, end=end)
    return ProfitAndLoss(
        start=start, end=end, label=label or f'{start} to {end}',
        income=_section('Income', {coa.INCOME}, nets),
        cost_of_sales=_section('Cost of sales', {coa.COGS}, nets),
        expenses=_section('Operating expenses', {coa.EXPENSE}, nets),
    )


# --------------------------------------------------------------- balance sheet

@dataclass
class BalanceSheet:
    as_at: date
    assets: Section
    liabilities: Section
    equity: Section
    retained_earnings: Decimal
    current_year_earnings: Decimal

    @property
    def total_equity(self) -> Decimal:
        return money(self.equity.total + self.retained_earnings
                     + self.current_year_earnings)

    @property
    def balances(self) -> bool:
        return self.assets.total == money(self.liabilities.total + self.total_equity)

    @property
    def out_by(self) -> Decimal:
        return money(self.assets.total - self.liabilities.total - self.total_equity)


def balance_sheet(as_at) -> BalanceSheet:
    as_at = parse_date(as_at)
    nets = ledger.nets(end=as_at)
    fy_start, _ = fy_range(fy_ending(as_at))
    day_before = date.fromordinal(fy_start.toordinal() - 1)

    # No closing entries are ever posted, so prior-year profit is folded into
    # retained earnings on the fly and the current year is shown separately.
    prior = profit_and_loss(date(1900, 1, 1), day_before).net_profit
    current = profit_and_loss(fy_start, as_at).net_profit

    equity_section = _section('Equity', {coa.EQUITY}, nets)
    retained_account = coa.first_with_role('retained_earnings').code
    equity_section.rows = [r for r in equity_section.rows
                           if r.account.code != retained_account]

    return BalanceSheet(
        as_at=as_at,
        assets=_section('Assets', {coa.ASSET}, nets),
        liabilities=_section('Liabilities', {coa.LIABILITY}, nets),
        equity=equity_section,
        retained_earnings=money(nets.get(retained_account, ZERO) + prior),
        current_year_earnings=current,
    )


# ------------------------------------------------------------------------- BAS

@dataclass
class Bas:
    label: str
    start: date
    end: date
    due: date
    basis: str                  # 'cash' or 'accruals'
    g1: Decimal                 # total sales including GST
    g3: Decimal                 # GST-free sales
    g10: Decimal                # capital purchases including GST
    g11: Decimal                # non-capital purchases including GST
    gst_on_sales: Decimal       # 1A
    gst_on_purchases: Decimal   # 1B
    w1: Decimal                 # total salary and wages
    w2: Decimal                 # PAYG withheld from wages
    w4: Decimal                 # withheld where no ABN quoted
    payg_instalment: Decimal    # 5A, as notified by the ATO
    deferred_gst_sales: Decimal = ZERO      # GST on invoices not yet paid
    deferred_gst_purchases: Decimal = ZERO  # credits on bills not yet paid
    checks: list = field(default_factory=list)

    @property
    def w5(self) -> Decimal:
        return money(self.w2 + self.w4)

    @property
    def net_gst(self) -> Decimal:
        return money(self.gst_on_sales - self.gst_on_purchases)

    @property
    def net_amount(self) -> Decimal:
        """Label 7: positive means payable to the ATO, negative is a refund."""
        return money(self.net_gst + self.w5 + self.payg_instalment)


@dataclass
class TaxEvent:
    """One taxable amount reaching the BAS: a sale or a purchase, GST-exclusive."""
    kind: str          # 'sale' or 'purchase'
    account: str
    amount_ex: Decimal
    tax_code: str


def _accrual_events(start, end) -> list:
    """Sales and purchases by document date, which is the accruals basis."""
    events = []
    for line in ledger.lines(start=start, end=end,
                             exclude_sources=(tx.BAS_PAYMENT,)):
        account = coa.get(line.account)
        if account.type == coa.INCOME:
            events.append(TaxEvent('sale', line.account,
                                   money(line.credit - line.debit), line.tax_code))
        elif account.type in (coa.COGS, coa.EXPENSE) or account.role == 'fixed_asset':
            events.append(TaxEvent('purchase', line.account,
                                   money(line.debit - line.credit), line.tax_code))
    return events


def _document_composition(doc_id: str, source: str):
    """How a document splits across accounts and tax codes, GST-exclusive.

    Returns (parts, total_incl) where parts is a list of TaxEvent-shaped
    tuples. Used to attribute a part payment back to what was actually bought
    or sold, which is what the cash basis needs.
    """
    kind = 'sale' if source == tx.INVOICE else 'purchase'
    parts, total_incl = [], ZERO
    for line in ledger.lines(doc_ref=doc_id, sources=(source,)):
        account = coa.get(line.account)
        is_income = account.type == coa.INCOME
        is_cost = (account.type in (coa.COGS, coa.EXPENSE)
                   or account.role == 'fixed_asset')
        if not (is_income or is_cost):
            continue
        amount_ex = (money(line.credit - line.debit) if is_income
                     else money(line.debit - line.credit))
        if amount_ex == ZERO:
            continue
        parts.append((account.code, amount_ex, line.tax_code))
        total_incl += money(amount_ex + taxcodes.gst_on(amount_ex, line.tax_code))
    return kind, parts, money(total_incl)


def _cash_events(start, end) -> list:
    """Sales and purchases by when the money moved, which is the cash basis.

    A payment against an invoice or bill is split back across that document's
    own lines in proportion, so a part payment of a mixed-tax-code document
    reports the right amount under each code.
    """
    events = []

    # Money received against invoices, and money paid against bills.
    for payment_source, document_source, control_role in (
            (tx.RECEIPT, tx.INVOICE, 'ar'), (tx.BILL_PAYMENT, tx.BILL, 'ap')):
        control = coa.first_with_role(control_role).code
        for line in ledger.lines(start=start, end=end, sources=(payment_source,),
                                 account=control):
            amount = abs(money(line.debit - line.credit))
            if amount == ZERO or not line.doc_ref:
                continue
            kind, parts, total_incl = _document_composition(line.doc_ref,
                                                            document_source)
            if total_incl <= ZERO:
                continue
            share = amount / total_incl
            for account, amount_ex, tax_code in parts:
                events.append(TaxEvent(kind, account, money(amount_ex * share),
                                       tax_code))

    # Money that moved without a document behind it.
    for line in ledger.lines(start=start, end=end,
                             sources=tx.DIRECT_CASH_SOURCES):
        account = coa.get(line.account)
        if account.type == coa.INCOME:
            events.append(TaxEvent('sale', line.account,
                                   money(line.credit - line.debit), line.tax_code))
        elif account.type in (coa.COGS, coa.EXPENSE) or account.role == 'fixed_asset':
            events.append(TaxEvent('purchase', line.account,
                                   money(line.debit - line.credit), line.tax_code))
    return events


def bas(start, end, label='', payg_instalment=0, basis=None, company=None) -> Bas:
    """Build the activity statement.

    `basis` must match the GST accounting method printed on the activity
    statement. Cash reports GST when the money moves; accruals reports it when
    the invoice is raised.
    """
    company = company or config.load()
    basis = (basis or company.gst_basis).strip().lower()
    if basis not in ('cash', 'accruals'):
        raise ValueError("GST basis must be 'cash' or 'accruals'")
    start, end = parse_date(start), parse_date(end)

    events = _cash_events(start, end) if basis == 'cash' else _accrual_events(start, end)

    g1 = g3 = g10 = g11 = ZERO
    sales_gst = purchase_gst = ZERO
    for event in events:
        code = taxcodes.get(event.tax_code)
        if code.code == 'NT' or event.amount_ex == ZERO:
            continue
        gst = taxcodes.gst_on(event.amount_ex, code.code)
        if event.kind == 'sale':
            sales_gst += gst
            if 'G1' in code.sale_labels:
                g1 += money(event.amount_ex + gst)
            if 'G3' in code.sale_labels:
                g3 += event.amount_ex
        else:
            purchase_gst += gst
            if 'G10' in code.purchase_labels:
                g10 += money(event.amount_ex + gst)
            if 'G11' in code.purchase_labels:
                g11 += money(event.amount_ex + gst)

    checks = []
    deferred_sales = deferred_purchases = ZERO
    if basis == 'accruals':
        # On accruals the GST control accounts are authoritative: they hold
        # what was actually posted. Report those and flag any disagreement.
        ledger_sales_gst = ledger.movement(
            coa.first_with_role('gst_collected').code, start, end,
            exclude_sources=(tx.BAS_PAYMENT,))
        ledger_purchase_gst = ledger.movement(
            coa.first_with_role('gst_paid').code, start, end,
            exclude_sources=(tx.BAS_PAYMENT,))
        if money(sales_gst) != ledger_sales_gst:
            checks.append(
                f'1A is {ledger_sales_gst} in the ledger but the tax codes on '
                f'income lines add to {money(sales_gst)}. Check for a manual '
                'journal that moved GST without a matching sale.')
        if money(purchase_gst) != ledger_purchase_gst:
            checks.append(
                f'1B is {ledger_purchase_gst} in the ledger but the tax codes on '
                f'purchase lines add to {money(purchase_gst)}. Check for a manual '
                'journal that moved GST without a matching purchase.')
        sales_gst, purchase_gst = ledger_sales_gst, ledger_purchase_gst
    else:
        # On cash, GST sitting on unpaid documents is not reportable yet.
        # Showing it separately makes the timing difference visible.
        deferred_sales = _deferred_gst(tx.INVOICE, end)
        deferred_purchases = _deferred_gst(tx.BILL, end)

    w1 = ZERO
    for account in coa.by_role('wages'):
        w1 += ledger.net(account.code, start, end,
                         exclude_sources=(tx.BAS_PAYMENT,))

    withholding_account = coa.first_with_role('payg_withholding').code
    w2 = w4 = ZERO
    for line in ledger.lines(start=start, end=end, account=withholding_account):
        if line.source == tx.PAYROLL:
            w2 += money(line.credit - line.debit)
        elif line.source == tx.BILL:
            w4 += money(line.credit - line.debit)

    quarter = quarter_of(end)
    return Bas(label=label or quarter.label, start=start, end=end,
               due=quarter.due(company.uses_tax_agent), basis=basis,
               g1=money(g1), g3=money(g3), g10=money(g10), g11=money(g11),
               gst_on_sales=money(sales_gst), gst_on_purchases=money(purchase_gst),
               w1=money(w1), w2=money(w2), w4=money(w4),
               payg_instalment=money(payg_instalment),
               deferred_gst_sales=deferred_sales,
               deferred_gst_purchases=deferred_purchases, checks=checks)


def _deferred_gst(doc_type: str, as_at) -> Decimal:
    """GST sitting on documents that have not been paid yet."""
    source = tx.INVOICE if doc_type == tx.INVOICE else tx.BILL
    total = ZERO
    for doc, remaining in tx.open_documents(doc_type, as_at):
        _, _, total_incl = _document_composition(doc['doc_id'], source)
        if total_incl <= ZERO:
            continue
        total += money(money(doc['gst']) * (remaining / total_incl))
    return money(total)


# ------------------------------------------------------------------------ TPAR

@dataclass
class TparRow:
    contact: contacts_mod.Contact
    gross_paid: Decimal      # including GST and any amount withheld
    gst: Decimal
    tax_withheld: Decimal

    @property
    def issues(self) -> list:
        problems = []
        if not self.contact.abn:
            problems.append('no ABN recorded')
        elif not self.contact.abn_is_valid:
            problems.append('ABN fails its checksum')
        if not self.contact.address:
            problems.append('no address recorded')
        return problems


@dataclass
class Tpar:
    fy: int
    start: date
    end: date
    due: date
    rows: list = field(default_factory=list)
    unattributed: Decimal = ZERO   # paid to a TPAR account with no payee on file

    @property
    def total_paid(self) -> Decimal:
        return money(sum((r.gross_paid for r in self.rows), ZERO))

    @property
    def total_gst(self) -> Decimal:
        return money(sum((r.gst for r in self.rows), ZERO))

    @property
    def total_withheld(self) -> Decimal:
        return money(sum((r.tax_withheld for r in self.rows), ZERO))


def tpar(fy: int) -> Tpar:
    """Payments made to subcontractors during a financial year.

    The TPAR is a cash-basis report: it counts what was actually paid in the
    year, not what was invoiced, and the gross figure includes GST and any
    amount withheld.
    """
    start, end = fy_range(fy)
    tpar_accounts = coa.tpar_accounts()
    totals = {}

    orphaned = [ZERO]

    def add(contact_id, gross, gst, withheld):
        if not contact_id:
            # Still counted, so the total cannot silently understate what was
            # paid to subcontractors.
            orphaned[0] += gross
            return
        current = totals.setdefault(contact_id, [ZERO, ZERO, ZERO])
        current[0] += gross
        current[1] += gst
        current[2] += withheld

    # Bills: work out how much of each bill was subcontract labour, then
    # attribute the payments made this year in that proportion.
    for doc in store.DOCUMENTS.read():
        if doc['type'] != tx.BILL:
            continue
        bill_lines = ledger.lines(doc_ref=doc['doc_id'], sources=(tx.BILL,))
        subcontract_ex = money(sum(
            (l.debit - l.credit for l in bill_lines if l.account in tpar_accounts),
            ZERO))
        if subcontract_ex <= ZERO:
            continue
        total_ex = money(sum(
            (l.debit - l.credit for l in bill_lines
             if coa.get(l.account).type in (coa.COGS, coa.EXPENSE)), ZERO))
        if total_ex <= ZERO:
            continue
        share = subcontract_ex / total_ex

        total_incl = money(doc['total_incl'])
        withheld = money(doc['withheld'])
        payable = money(total_incl - withheld)
        if payable <= ZERO:
            continue
        paid = money(sum(
            (l.debit - l.credit for l in
             ledger.lines(start=start, end=end, doc_ref=doc['doc_id'],
                          sources=(tx.BILL_PAYMENT,),
                          account=coa.first_with_role('ap').code)), ZERO))
        if paid <= ZERO:
            continue
        settled = paid / payable
        add(doc['contact_id'],
            money(total_incl * settled * share),
            money(money(doc['gst']) * settled * share),
            money(withheld * settled * share))

    # Money spent directly on subcontract labour without a bill.
    for line in ledger.lines(start=start, end=end,
                             sources=(tx.SPEND, tx.BANK)):
        if line.account not in tpar_accounts:
            continue
        amount_ex = money(line.debit - line.credit)
        gst = taxcodes.gst_on(amount_ex, line.tax_code)
        add(line.contact, money(amount_ex + gst), gst, ZERO)

    rows = []
    for contact_id, (gross, gst, withheld) in totals.items():
        contact = contacts_mod.find(contact_id)
        if contact is None:
            continue
        rows.append(TparRow(contact, money(gross), money(gst), money(withheld)))
    rows.sort(key=lambda r: r.gross_paid, reverse=True)

    return Tpar(fy=fy, start=start, end=end, due=date(fy, 8, 28), rows=rows,
                unattributed=money(orphaned[0]))


# -------------------------------------------------------------- aged balances

BUCKETS = ('Current', '1-30 days', '31-60 days', '61-90 days', '90+ days')


@dataclass
class AgedRow:
    doc_id: str
    contact: str
    date: date
    due_date: date
    amount: Decimal
    bucket: str

    def days_overdue(self, as_at) -> int:
        return max(0, (parse_date(as_at) - self.due_date).days)


@dataclass
class Aged:
    kind: str
    as_at: date
    rows: list = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return money(sum((r.amount for r in self.rows), ZERO))

    def by_bucket(self) -> dict:
        totals = {b: ZERO for b in BUCKETS}
        for row in self.rows:
            totals[row.bucket] += row.amount
        return {b: money(v) for b, v in totals.items()}


def _bucket(days: int) -> str:
    if days <= 0:
        return 'Current'
    if days <= 30:
        return '1-30 days'
    if days <= 60:
        return '31-60 days'
    if days <= 90:
        return '61-90 days'
    return '90+ days'


def _aged(doc_type: str, as_at) -> Aged:
    as_at = parse_date(as_at)
    rows = []
    for doc, remaining in tx.open_documents(doc_type, as_at):
        due = parse_date(doc['due_date']) if doc['due_date'] else parse_date(doc['date'])
        contact = contacts_mod.find(doc['contact_id'])
        rows.append(AgedRow(
            doc_id=doc['doc_id'], contact=contact.name if contact else doc['contact_id'],
            date=parse_date(doc['date']), due_date=due, amount=remaining,
            bucket=_bucket((as_at - due).days)))
    rows.sort(key=lambda r: r.due_date)
    return Aged(kind=doc_type, as_at=as_at, rows=rows)


def aged_receivables(as_at) -> Aged:
    return _aged(tx.INVOICE, as_at)


def aged_payables(as_at) -> Aged:
    return _aged(tx.BILL, as_at)


# ------------------------------------------------------------ job performance

@dataclass
class JobResult:
    job: jobs_mod.Job
    income: Decimal
    cost: Decimal

    @property
    def margin(self) -> Decimal:
        return money(self.income - self.cost)

    @property
    def margin_pct(self) -> Decimal:
        if self.income == ZERO:
            return ZERO
        return money(self.margin / self.income * 100)


def job_results(start=None, end=None) -> list:
    results = []
    for job in jobs_mod.all_jobs():
        income = cost = ZERO
        for line in ledger.lines(start=start, end=end, job=job.job_id):
            account = coa.get(line.account)
            if account.type == coa.INCOME:
                income += money(line.credit - line.debit)
            elif account.type in (coa.COGS, coa.EXPENSE):
                cost += money(line.debit - line.credit)
        if income == ZERO and cost == ZERO:
            continue
        results.append(JobResult(job, money(income), money(cost)))
    results.sort(key=lambda r: r.margin_pct)
    return results


# ------------------------------------------------------------- risk and cash

@dataclass
class LoanPosition:
    director: str
    account: coa.Account
    balance: Decimal          # positive = company owes director
    owed_by_director: Decimal  # positive = Division 7A exposure


def director_loans(as_at, company=None) -> list:
    company = company or config.load()
    as_at = parse_date(as_at)
    positions = []
    for person in company.directors:
        account = coa.get(person.loan_account)
        balance = ledger.balance(account.code, as_at)  # credit-normal, positive = owed to director
        positions.append(LoanPosition(
            director=person.name, account=account, balance=balance,
            owed_by_director=money(-balance) if balance < ZERO else ZERO))
    return positions


def division_7a_warnings(as_at, company=None) -> list:
    """Loans out to a director that turn into deemed dividends if not fixed.

    Two separate risks are checked. A balance outstanding at the end of a
    financial year that has already closed is the urgent one: unless it was
    repaid, or put under a complying loan agreement, before that year's
    lodgement day, it is already a deemed unfranked dividend. A balance in the
    year still running is a warning with time left to act on it.
    """
    as_at = parse_date(as_at)
    current_fy = fy_ending(as_at)
    _, current_fy_end = fy_range(current_fy)
    warnings = []

    for position in director_loans(as_at, company):
        account = position.account

        # Financial years that have already ended on or before as_at.
        for fy in range(fy_ending(_first_activity(account.code) or as_at), current_fy):
            _, fy_end = fy_range(fy)
            if fy_end > as_at:
                continue
            closing = ledger.balance(account.code, fy_end)
            if closing >= ZERO:
                continue
            owed = money(-closing)
            warnings.append(
                f'{position.director} owed the company {owed} on {account.code} at '
                f'{fy_end}, the end of FY{fy}. Unless it was repaid or put under a '
                f'complying Division 7A loan agreement before the FY{fy} lodgement '
                'day, the ATO treats it as an unfranked dividend in that '
                'director\'s hands for FY{fy} - get this reviewed now.'.replace(
                    '{fy}', str(fy)))

        if position.owed_by_director > ZERO:
            warnings.append(
                f'{position.director} currently owes the company '
                f'{position.owed_by_director} on {account.code}. Clear it before '
                f'{current_fy_end} or put it under a complying Division 7A loan '
                'agreement (seven years unsecured, benchmark interest), otherwise '
                'it becomes an unfranked dividend for FY'
                f'{current_fy}. Paying it out as wages or a franked dividend '
                'instead is usually cheaper than letting it be deemed.')

    return warnings


def _first_activity(account_code: str):
    """Date of the earliest entry on an account, or None if it has never moved."""
    dates = [l.date for l in ledger.lines(account=account_code)]
    return min(dates) if dates else None


@dataclass
class SuperObligation:
    pay_date: date
    amount: Decimal
    due: date
    paid: Decimal

    @property
    def outstanding(self) -> Decimal:
        return money(self.amount - self.paid)

    def is_late(self, as_at) -> bool:
        return self.outstanding > ZERO and parse_date(as_at) > self.due


def super_obligations(as_at, company=None) -> list:
    """Each pay run's super and whether it has been paid in time.

    Before 1 July 2026 super was a quarterly obligation, due 28 days after the
    quarter ends. From that date Pay Day Super applies: the money has to reach
    the fund within seven days of the pay day it belongs to. Payments are
    matched to pay runs oldest first.
    """
    as_at = parse_date(as_at)
    accrued, paid_total = [], ZERO
    super_account = coa.first_with_role('super_payable').code

    for line in sorted(ledger.lines(end=as_at, account=super_account),
                       key=lambda l: (l.date, l.entry_id)):
        credited = money(line.credit - line.debit)
        if credited > ZERO:
            accrued.append([line.date, credited])
        elif credited < ZERO:
            paid_total += -credited

    obligations, remaining = [], paid_total
    for pay_date, amount in accrued:
        applied = min(remaining, amount)
        remaining = money(remaining - applied)
        if pay_date >= PAYDAY_SUPER_START:
            due = date.fromordinal(pay_date.toordinal() + PAYDAY_SUPER_DAYS)
        else:
            due = quarter_of(pay_date).super_due
        obligations.append(SuperObligation(pay_date=pay_date, amount=amount,
                                           due=due, paid=money(applied)))
    return obligations


@dataclass
class SuperShortfall:
    wages: Decimal
    expected: Decimal
    recognised: Decimal

    @property
    def shortfall(self) -> Decimal:
        return money(self.expected - self.recognised)


def super_shortfall(start, end, company=None) -> SuperShortfall:
    """Super that should have been accrued on the wages actually paid.

    Wages imported straight from a bank feed carry no super with them, so this
    is what catches a pay run that went out without the guarantee being met.
    """
    company = company or config.load()
    wages = ZERO
    for account in coa.by_role('wages'):
        wages += ledger.net(account.code, start, end)
    recognised = ledger.net(coa.first_with_role('super_expense').code, start, end)
    return SuperShortfall(wages=money(wages),
                          expected=money(wages * company.super_rate),
                          recognised=money(recognised))


def financial_years(as_at, company=None) -> list:
    """Every financial year the books touch, oldest first."""
    company = company or config.load()
    dates = [l.date for l in ledger.lines(end=as_at)]
    if company.registered:
        dates.append(company.registered)
    if not dates:
        return [fy_ending(as_at)]
    return list(range(fy_ending(min(dates)), fy_ending(parse_date(as_at)) + 1))


def super_shortfalls(as_at, company=None) -> list:
    """(fy, SuperShortfall) for every year where super is short."""
    company = company or config.load()
    as_at = parse_date(as_at)
    out = []
    for fy in financial_years(as_at, company):
        fy_start, fy_end = fy_range(fy)
        gap = super_shortfall(fy_start, min(fy_end, as_at), company)
        if gap.shortfall > ZERO:
            out.append((fy, gap))
    return out


def late_super(as_at, company=None) -> list:
    return [o for o in super_obligations(as_at, company) if o.is_late(as_at)]


# The superannuation guarantee charge, for super that missed its deadline.
SGC_INTEREST_RATE = Decimal('0.10')      # nominal interest, 10% a year
SGC_ADMIN_FEE_PER_EMPLOYEE = Decimal('20.00')  # per employee per quarter


@dataclass
class SgcEstimate:
    quarter_label: str
    quarter_start: date
    shortfall: Decimal
    employees: int
    days_of_interest: int
    statement_due: date

    @property
    def nominal_interest(self) -> Decimal:
        return money(self.shortfall * SGC_INTEREST_RATE
                     * Decimal(self.days_of_interest) / Decimal(365))

    @property
    def admin_fee(self) -> Decimal:
        return money(SGC_ADMIN_FEE_PER_EMPLOYEE * self.employees)

    @property
    def total(self) -> Decimal:
        return money(self.shortfall + self.nominal_interest + self.admin_fee)

    @property
    def cost_of_being_late(self, ) -> Decimal:
        """Interest and fee, none of which would have been payable on time."""
        return money(self.nominal_interest + self.admin_fee)


def sgc_estimate(quarter_start, shortfall, employees=1, as_at=None,
                 company=None) -> SgcEstimate:
    """What unpaid super turns into once its deadline passes.

    Super paid late stops being an ordinary deductible contribution and
    becomes the superannuation guarantee charge: the shortfall, plus nominal
    interest running from the START of the quarter, plus an administration fee
    per employee. None of the SGC is deductible, and it has to be reported on
    an SGC statement rather than simply paid to the fund.

    An estimate only - the ATO calculates the charge on salary and wages
    rather than ordinary time earnings, and adds its own penalties for a late
    statement.
    """
    quarter_start = parse_date(quarter_start)
    as_at = parse_date(as_at) if as_at else date.today()
    quarter = quarter_of(quarter_start)
    # The SGC statement is due one month after the contribution deadline.
    statement_due = date.fromordinal(quarter.super_due.toordinal() + 31)
    return SgcEstimate(
        quarter_label=quarter.label, quarter_start=quarter.start,
        shortfall=money(shortfall), employees=max(1, int(employees)),
        days_of_interest=max(0, (as_at - quarter.start).days),
        statement_due=statement_due)


@dataclass
class CashPosition:
    as_at: date
    bank: Decimal
    gst_owing: Decimal
    payg_owing: Decimal
    super_owing: Decimal
    tax_provision: Decimal

    @property
    def set_aside(self) -> Decimal:
        return money(self.gst_owing + self.payg_owing + self.super_owing
                     + self.tax_provision)

    @property
    def available(self) -> Decimal:
        return money(self.bank - self.set_aside)


def cash_position(as_at, company=None) -> CashPosition:
    company = company or config.load()
    as_at = parse_date(as_at)
    bank = money(sum(
        (ledger.balance(a.code, as_at) * (1 if a.type == coa.ASSET else -1)
         for a in coa.bank_accounts()), ZERO))
    gst_owing = money(ledger.balance(coa.first_with_role('gst_collected').code, as_at)
                      - ledger.balance(coa.first_with_role('gst_paid').code, as_at))
    payg_owing = ledger.balance(coa.first_with_role('payg_withholding').code, as_at)
    super_owing = ledger.balance(coa.first_with_role('super_payable').code, as_at)

    fy_start, _ = fy_range(fy_ending(as_at))
    taxable = profit_and_loss(fy_start, as_at).taxable_income
    provision = money(taxable * company.company_tax_rate) if taxable > ZERO else ZERO
    instalments = ledger.balance(coa.first_with_role('payg_instalments').code, as_at)
    provision = money(max(ZERO, provision - instalments))

    return CashPosition(as_at=as_at, bank=bank, gst_owing=gst_owing,
                        payg_owing=payg_owing, super_owing=super_owing,
                        tax_provision=provision)


@dataclass
class TaxEstimate:
    fy: int
    net_profit: Decimal
    non_deductible: Decimal
    taxable_income: Decimal
    rate: Decimal
    tax: Decimal
    instalments_paid: Decimal

    @property
    def payable(self) -> Decimal:
        return money(self.tax - self.instalments_paid)


def tax_estimate(fy: int, as_at=None, company=None) -> TaxEstimate:
    company = company or config.load()
    start, end = fy_range(fy)
    end = min(end, parse_date(as_at)) if as_at else end
    pl = profit_and_loss(start, end)
    rate = company.company_tax_rate
    tax = money(max(ZERO, pl.taxable_income) * rate)
    instalments = ledger.movement(coa.first_with_role('payg_instalments').code,
                                  start, end)
    return TaxEstimate(fy=fy, net_profit=pl.net_profit,
                       non_deductible=pl.non_deductible,
                       taxable_income=pl.taxable_income, rate=rate, tax=tax,
                       instalments_paid=instalments)


# ----------------------------------------------------------------- gst turnover

def gst_turnover(as_at, months=12) -> Decimal:
    """Rolling GST turnover, for checking the $75,000 registration threshold."""
    as_at = parse_date(as_at)
    start_ordinal = as_at.toordinal() - int(months * 30.44)
    start = date.fromordinal(start_ordinal)
    total = ZERO
    for line in ledger.lines(start=start, end=as_at):
        if coa.get(line.account).type == coa.INCOME:
            total += money(line.credit - line.debit)
    return money(total)
