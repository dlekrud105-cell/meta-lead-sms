"""Business transactions, each posted as a balanced journal entry.

Every function here is a thin, auditable wrapper: it works out the GST split,
builds the debits and credits, and posts one entry. Nothing else in the
package writes to the journal.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from . import accounts as coa
from . import config
from . import contacts as contacts_mod
from . import jobs as jobs_mod
from . import ledger
from . import store
from . import taxcodes
from .money import ZERO, money
from .periods import parse_date

# Entry sources. BAS_PAYMENT is excluded when the BAS report measures GST
# movements, otherwise clearing the GST accounts would look like new activity.
INVOICE = 'INVOICE'
RECEIPT = 'RECEIPT'
BILL = 'BILL'
BILL_PAYMENT = 'BILL_PAYMENT'
SPEND = 'SPEND'
RECEIVE = 'RECEIVE'
PAYROLL = 'PAYROLL'
SUPER = 'SUPER'
BAS_PAYMENT = 'BAS_PAYMENT'
DIVIDEND = 'DIVIDEND'
DIRECTOR_LOAN = 'DIRECTOR_LOAN'
DEPRECIATION = 'DEPRECIATION'
MANUAL = 'JOURNAL'

# Payments that put money in a subcontractor's hands, for the TPAR.
PAYMENT_SOURCES = (BILL_PAYMENT, SPEND)

ACCUMULATED_DEPRECIATION = {'1400': '1410', '1420': '1430'}


class TransactionError(ValueError):
    pass


@dataclass
class DocLine:
    """One line of an invoice or a bill, always held GST-exclusive."""
    account: str
    amount_ex: Decimal
    description: str = ''
    tax_code: str = ''
    job: str = ''

    def __post_init__(self):
        self.account = str(self.account).strip()
        self.amount_ex = money(self.amount_ex)
        self.tax_code = (self.tax_code or coa.get(self.account).tax_code).upper()

    @property
    def gst(self) -> Decimal:
        return taxcodes.gst_on(self.amount_ex, self.tax_code)

    @property
    def amount_incl(self) -> Decimal:
        return money(self.amount_ex + self.gst)


def line_from_inclusive(account, amount_incl, description='', tax_code='', job='') -> DocLine:
    """Build a line from a GST-inclusive amount, which is how receipts read."""
    code = (tax_code or coa.get(str(account).strip()).tax_code).upper()
    rate = taxcodes.get(code).rate
    amount_ex = money(money(amount_incl) / (Decimal('1') + rate))
    return DocLine(account=account, amount_ex=amount_ex, description=description,
                   tax_code=code, job=job)


def parse_line(spec: str) -> DocLine:
    """Parse 'account:amount_ex[:description[:tax_code[:job]]]' from the CLI."""
    parts = spec.split(':')
    if len(parts) < 2:
        raise TransactionError(
            f'line {spec!r} needs at least account:amount, '
            'e.g. 4000:1000:Interior repaint:GST')
    account, amount = parts[0], parts[1]
    description = parts[2] if len(parts) > 2 else ''
    tax_code = parts[3] if len(parts) > 3 else ''
    job = parts[4] if len(parts) > 4 else ''
    return DocLine(account=account, amount_ex=amount, description=description,
                   tax_code=tax_code, job=job)


# --------------------------------------------------------------------- helpers

def _bank(company: config.Company, bank=None) -> str:
    code = str(bank or company.default_bank)
    account = coa.get(code)
    if account.role != 'bank':
        raise TransactionError(
            f'{code} {account.name} is not a bank or card account')
    return code


def _role(role: str) -> str:
    return coa.first_with_role(role).code


def _document(doc_id: str) -> dict:
    row = store.DOCUMENTS.find(doc_id=doc_id)
    if row is None:
        raise TransactionError(f'unknown document {doc_id!r}')
    return row


def document_balance(doc_id: str, as_at=None) -> Decimal:
    """Amount still outstanding on an invoice or bill.

    `as_at` gives the balance as it stood on that date, ignoring payments made
    later. Without it you get the position today.
    """
    doc = _document(doc_id)
    control = _role('ar') if doc['type'] == INVOICE else _role('ap')
    selected = ledger.lines(account=control, doc_ref=doc_id, end=as_at)
    net = money(sum((l.signed for l in selected), ZERO))
    return money(net * coa.get(control).sign)


def open_documents(doc_type: str, as_at=None) -> list:
    """Invoices or bills with something still owing."""
    out = []
    for row in store.DOCUMENTS.read():
        if row['type'] != doc_type:
            continue
        if as_at and parse_date(row['date']) > parse_date(as_at):
            continue
        remaining = document_balance(row['doc_id'], as_at)
        if remaining != ZERO:
            out.append((row, remaining))
    return out


def _split_lines(lines: list, gst_is_credit: bool):
    """Turn document lines into journal lines plus the total GST and total incl."""
    journal_lines, total_gst, total_incl = [], ZERO, ZERO
    for line in lines:
        if line.amount_ex == ZERO:
            continue
        journal_lines.append(ledger.Line(
            account=line.account,
            debit=line.amount_ex if not gst_is_credit else ZERO,
            credit=line.amount_ex if gst_is_credit else ZERO,
            description=line.description, tax_code=line.tax_code,
            job=jobs_mod.resolve_id(line.job) if line.job else ''))
        total_gst += line.gst
        total_incl += line.amount_incl
    return journal_lines, money(total_gst), money(total_incl)


# -------------------------------------------------------------------- invoices

def create_invoice(date, contact, lines, due_days=None, job='', memo='',
                   doc_id=None, company=None) -> dict:
    """Raise a customer invoice. Lines are GST-exclusive."""
    company = company or config.load()
    customer = contacts_mod.get(contact)
    lines = [l if isinstance(l, DocLine) else parse_line(l) for l in lines]
    if not lines:
        raise TransactionError('an invoice needs at least one line')
    job_id = jobs_mod.resolve_id(job)
    for line in lines:
        account = coa.get(line.account)
        if account.type != coa.INCOME:
            raise TransactionError(
                f'{line.account} {account.name} is not an income account')
        if not line.job:
            line.job = job_id

    credits, total_gst, total_incl = _split_lines(lines, gst_is_credit=True)
    doc_id = doc_id or store.DOCUMENTS.next_sequence('doc_id', 'INV', width=4)
    date = parse_date(date)
    due = date.toordinal() + int(
        company.invoice_terms_days if due_days is None else due_days)
    from datetime import date as _date
    due_date = _date.fromordinal(due)

    journal_lines = [ledger.debit(_role('ar'), total_incl,
                                  description=customer.name, contact=customer.contact_id,
                                  job=job_id)]
    for line in credits:
        line.contact = customer.contact_id
        journal_lines.append(line)
    if total_gst != ZERO:
        journal_lines.append(ledger.credit(_role('gst_collected'), total_gst,
                                           description='GST on sales',
                                           contact=customer.contact_id))

    entry_id = ledger.post(ledger.Entry(
        date=date, memo=memo or f'Invoice {doc_id} - {customer.name}',
        source=INVOICE, doc_ref=doc_id, lines=journal_lines))

    store.DOCUMENTS.append({
        'doc_id': doc_id, 'type': INVOICE, 'date': date.isoformat(),
        'due_date': due_date.isoformat(), 'contact_id': customer.contact_id,
        'job_id': job_id, 'description': memo,
        'total_incl': f'{total_incl:.2f}', 'gst': f'{total_gst:.2f}',
        'withheld': '0.00', 'entry_id': entry_id,
    })
    return {'doc_id': doc_id, 'entry_id': entry_id, 'total_incl': total_incl,
            'gst': total_gst, 'due_date': due_date}


def record_receipt(date, doc_id, amount=None, bank=None, memo='', company=None) -> dict:
    """Record money received against an invoice."""
    company = company or config.load()
    doc = _document(doc_id)
    if doc['type'] != INVOICE:
        raise TransactionError(f'{doc_id} is not a customer invoice')
    outstanding = document_balance(doc_id)
    amount = money(amount) if amount is not None else outstanding
    if amount <= ZERO:
        raise TransactionError('receipt amount must be positive')
    if amount > outstanding:
        raise TransactionError(
            f'{doc_id} only has {outstanding} outstanding; '
            'record the excess as a separate receipt or a customer deposit')
    bank_code = _bank(company, bank)
    entry_id = ledger.post(ledger.Entry(
        date=date, memo=memo or f'Payment received for {doc_id}',
        source=RECEIPT, doc_ref=doc_id,
        lines=[
            ledger.debit(bank_code, amount, description=f'Receipt {doc_id}',
                         contact=doc['contact_id'], job=doc['job_id']),
            ledger.credit(_role('ar'), amount, description=f'Receipt {doc_id}',
                          contact=doc['contact_id'], job=doc['job_id']),
        ]))
    return {'entry_id': entry_id, 'amount': amount,
            'remaining': money(outstanding - amount)}


# ----------------------------------------------------------------------- bills

def create_bill(date, contact, lines, due_days=None, job='', memo='',
                doc_id=None, withhold=None, company=None) -> dict:
    """Enter a supplier or subcontractor bill. Lines are GST-exclusive."""
    company = company or config.load()
    supplier = contacts_mod.get(contact)
    lines = [l if isinstance(l, DocLine) else parse_line(l) for l in lines]
    if not lines:
        raise TransactionError('a bill needs at least one line')
    job_id = jobs_mod.resolve_id(job)
    for line in lines:
        account = coa.get(line.account)
        if account.type == coa.INCOME:
            raise TransactionError(
                f'{line.account} {account.name} is an income account; '
                'bills post to cost, expense or asset accounts')
        if not line.job:
            line.job = job_id

    debits, total_gst, total_incl = _split_lines(lines, gst_is_credit=False)
    # A supplier who has not quoted an ABN must have 47% withheld and remitted
    # to the ATO. Default it on for subcontractors, who are the usual case.
    if withhold is None:
        withhold = (supplier.type == contacts_mod.SUBCONTRACTOR
                    and supplier.withholding_applies)
    withheld = money(total_incl * company.no_abn_withholding_rate) if withhold else ZERO

    warnings = []
    if supplier.withholding_applies and total_gst != ZERO:
        warnings.append(
            f'{supplier.name} has not quoted an ABN but this bill claims '
            f'{total_gst} of GST. Only a GST-registered supplier can charge GST - '
            'get their ABN, or re-enter the lines with tax code NT.')
    if withhold:
        warnings.append(
            f'Withholding {withheld} ({company.no_abn_withholding_rate:.0%}) because '
            f'{supplier.name} has no ABN on file. Pay it to the ATO with your next BAS '
            'and give them a payment summary.')
    if supplier.type == contacts_mod.SUBCONTRACTOR and not supplier.abn:
        warnings.append(f'{supplier.name} has no ABN recorded - the TPAR needs one.')

    doc_id = doc_id or store.DOCUMENTS.next_sequence('doc_id', 'BILL', width=4)
    date = parse_date(date)
    from datetime import date as _date
    due_date = _date.fromordinal(date.toordinal() + int(
        company.invoice_terms_days if due_days is None else due_days))

    journal_lines = []
    for line in debits:
        line.contact = supplier.contact_id
        journal_lines.append(line)
    if total_gst != ZERO:
        journal_lines.append(ledger.debit(_role('gst_paid'), total_gst,
                                          description='GST on purchases',
                                          contact=supplier.contact_id))
    if withheld != ZERO:
        journal_lines.append(ledger.credit(
            _role('payg_withholding'), withheld,
            description=f'No-ABN withholding {company.no_abn_withholding_rate:.0%}',
            contact=supplier.contact_id))
    journal_lines.append(ledger.credit(
        _role('ap'), money(total_incl - withheld), description=supplier.name,
        contact=supplier.contact_id, job=job_id))

    entry_id = ledger.post(ledger.Entry(
        date=date, memo=memo or f'Bill {doc_id} - {supplier.name}',
        source=BILL, doc_ref=doc_id, lines=journal_lines))

    store.DOCUMENTS.append({
        'doc_id': doc_id, 'type': BILL, 'date': date.isoformat(),
        'due_date': due_date.isoformat(), 'contact_id': supplier.contact_id,
        'job_id': job_id, 'description': memo,
        'total_incl': f'{total_incl:.2f}', 'gst': f'{total_gst:.2f}',
        'withheld': f'{withheld:.2f}', 'entry_id': entry_id,
    })
    return {'doc_id': doc_id, 'entry_id': entry_id, 'total_incl': total_incl,
            'gst': total_gst, 'withheld': withheld, 'due_date': due_date,
            'payable': money(total_incl - withheld), 'warnings': warnings}


def pay_bill(date, doc_id, amount=None, bank=None, memo='', company=None) -> dict:
    """Pay a supplier bill."""
    company = company or config.load()
    doc = _document(doc_id)
    if doc['type'] != BILL:
        raise TransactionError(f'{doc_id} is not a supplier bill')
    outstanding = document_balance(doc_id)
    amount = money(amount) if amount is not None else outstanding
    if amount <= ZERO:
        raise TransactionError('payment amount must be positive')
    if amount > outstanding:
        raise TransactionError(f'{doc_id} only has {outstanding} outstanding')
    bank_code = _bank(company, bank)
    entry_id = ledger.post(ledger.Entry(
        date=date, memo=memo or f'Payment for {doc_id}',
        source=BILL_PAYMENT, doc_ref=doc_id,
        lines=[
            ledger.debit(_role('ap'), amount, description=f'Payment {doc_id}',
                         contact=doc['contact_id'], job=doc['job_id']),
            ledger.credit(bank_code, amount, description=f'Payment {doc_id}',
                          contact=doc['contact_id'], job=doc['job_id']),
        ]))
    return {'entry_id': entry_id, 'amount': amount,
            'remaining': money(outstanding - amount)}


# ------------------------------------------------------- money in and out fast

def spend_money(date, account, amount_incl, contact='', description='',
                tax_code='', job='', bank=None, company=None) -> dict:
    """Pay for something straight from the bank or card, no bill needed."""
    company = company or config.load()
    target = coa.get(account)
    if target.type == coa.INCOME:
        raise TransactionError(f'{account} is an income account; use receive-money')
    bank_code = _bank(company, bank)
    line = line_from_inclusive(account, amount_incl, description, tax_code, job)
    supplier = contacts_mod.find(contact)
    contact_id = supplier.contact_id if supplier else ''
    job_id = jobs_mod.resolve_id(job)

    journal_lines = [ledger.Line(account=line.account, debit=line.amount_ex,
                                 description=description, tax_code=line.tax_code,
                                 contact=contact_id, job=job_id)]
    if line.gst != ZERO:
        journal_lines.append(ledger.debit(_role('gst_paid'), line.gst,
                                          description='GST on purchases',
                                          contact=contact_id))
    journal_lines.append(ledger.credit(bank_code, line.amount_incl,
                                       description=description or target.name,
                                       contact=contact_id, job=job_id))
    entry_id = ledger.post(ledger.Entry(
        date=date, memo=description or f'{target.name} payment',
        source=SPEND, lines=journal_lines))
    return {'entry_id': entry_id, 'amount_incl': line.amount_incl,
            'amount_ex': line.amount_ex, 'gst': line.gst}


def receive_money(date, account, amount_incl, contact='', description='',
                  tax_code='', job='', bank=None, company=None) -> dict:
    """Take money in without raising an invoice (cash job, refund, interest)."""
    company = company or config.load()
    target = coa.get(account)
    if target.type != coa.INCOME:
        raise TransactionError(f'{account} is not an income account')
    bank_code = _bank(company, bank)
    line = line_from_inclusive(account, amount_incl, description, tax_code, job)
    customer = contacts_mod.find(contact)
    contact_id = customer.contact_id if customer else ''
    job_id = jobs_mod.resolve_id(job)

    journal_lines = [ledger.debit(bank_code, line.amount_incl,
                                  description=description or target.name,
                                  contact=contact_id, job=job_id),
                     ledger.Line(account=line.account, credit=line.amount_ex,
                                 description=description, tax_code=line.tax_code,
                                 contact=contact_id, job=job_id)]
    if line.gst != ZERO:
        journal_lines.append(ledger.credit(_role('gst_collected'), line.gst,
                                           description='GST on sales',
                                           contact=contact_id))
    entry_id = ledger.post(ledger.Entry(
        date=date, memo=description or f'{target.name} received',
        source=RECEIVE, lines=journal_lines))
    return {'entry_id': entry_id, 'amount_incl': line.amount_incl,
            'amount_ex': line.amount_ex, 'gst': line.gst}


# --------------------------------------------------------------------- payroll

def pay_wages(date, director, gross, payg_withheld, super_amount=None,
              bank=None, memo='', company=None) -> dict:
    """Pay a working director. Super is accrued, not paid, by this entry.

    Directors who perform work are employees for super purposes, so the
    superannuation guarantee applies to their ordinary time earnings.
    """
    company = company or config.load()
    person = company.director(director)
    gross = money(gross)
    payg_withheld = money(payg_withheld)
    if gross <= ZERO:
        raise TransactionError('gross wage must be positive')
    if payg_withheld < ZERO or payg_withheld > gross:
        raise TransactionError('PAYG withheld must be between zero and the gross')
    if super_amount is None:
        super_amount = money(gross * company.super_rate)
    else:
        super_amount = money(super_amount)
    net = money(gross - payg_withheld)
    bank_code = _bank(company, bank)

    journal_lines = [
        ledger.debit(person.wage_account, gross,
                     description=f'{person.name} gross wage'),
        ledger.credit(_role('payg_withholding'), payg_withheld,
                      description=f'PAYG withheld - {person.name}'),
        ledger.credit(bank_code, net, description=f'Net pay - {person.name}'),
    ]
    if super_amount != ZERO:
        journal_lines += [
            ledger.debit(_role('super_expense'), super_amount,
                         description=f'Super {company.super_rate:.0%} - {person.name}'),
            ledger.credit(_role('super_payable'), super_amount,
                          description=f'Super payable - {person.name}'),
        ]
    entry_id = ledger.post(ledger.Entry(
        date=date, memo=memo or f'Wage - {person.name}',
        source=PAYROLL, lines=journal_lines))
    return {'entry_id': entry_id, 'gross': gross, 'payg': payg_withheld,
            'net': net, 'super': super_amount}


def pay_super(date, amount, bank=None, memo='', company=None) -> dict:
    """Remit accrued super to the funds. Must clear by the quarterly due date."""
    company = company or config.load()
    amount = money(amount)
    if amount <= ZERO:
        raise TransactionError('super payment must be positive')
    bank_code = _bank(company, bank)
    entry_id = ledger.post(ledger.Entry(
        date=date, memo=memo or 'Superannuation paid to funds', source=SUPER,
        lines=[
            ledger.debit(_role('super_payable'), amount, description='Super paid'),
            ledger.credit(bank_code, amount, description='Super paid'),
        ]))
    return {'entry_id': entry_id, 'amount': amount}


# ------------------------------------------------------------------------- ATO

def pay_bas(date, gst_on_sales, gst_on_purchases, payg_withholding=0,
            payg_instalment=0, bank=None, memo='', company=None) -> dict:
    """Settle a BAS: clear the GST accounts and pay (or receive) the net."""
    company = company or config.load()
    gst_on_sales = money(gst_on_sales)
    gst_on_purchases = money(gst_on_purchases)
    payg_withholding = money(payg_withholding)
    payg_instalment = money(payg_instalment)
    net = money(gst_on_sales - gst_on_purchases + payg_withholding + payg_instalment)
    bank_code = _bank(company, bank)

    journal_lines = []
    if gst_on_sales != ZERO:
        journal_lines.append(ledger.debit(_role('gst_collected'), gst_on_sales,
                                          description='1A GST on sales'))
    if gst_on_purchases != ZERO:
        journal_lines.append(ledger.credit(_role('gst_paid'), gst_on_purchases,
                                           description='1B GST on purchases'))
    if payg_withholding != ZERO:
        journal_lines.append(ledger.debit(_role('payg_withholding'), payg_withholding,
                                          description='W2 PAYG withheld'))
    if payg_instalment != ZERO:
        journal_lines.append(ledger.debit(_role('payg_instalments'), payg_instalment,
                                          description='5A PAYG instalment'))
    if net > ZERO:
        journal_lines.append(ledger.credit(bank_code, net, description='BAS payment'))
    elif net < ZERO:
        journal_lines.append(ledger.debit(bank_code, -net, description='BAS refund'))
    if not journal_lines:
        raise TransactionError('nothing to settle on this BAS')

    entry_id = ledger.post(ledger.Entry(
        date=date, memo=memo or 'BAS settlement', source=BAS_PAYMENT,
        lines=journal_lines))
    return {'entry_id': entry_id, 'net': net,
            'direction': 'payable' if net >= ZERO else 'refund'}


# ------------------------------------------------------ director money movements

def pay_dividend(date, director, amount, bank=None, franked=True, memo='',
                 company=None) -> dict:
    """Pay a dividend out of after-tax profits."""
    company = company or config.load()
    person = company.director(director)
    amount = money(amount)
    if amount <= ZERO:
        raise TransactionError('dividend must be positive')
    bank_code = _bank(company, bank)
    label = 'franked' if franked else 'unfranked'
    entry_id = ledger.post(ledger.Entry(
        date=date, memo=memo or f'{label.title()} dividend - {person.name}',
        source=DIVIDEND,
        lines=[
            ledger.debit(person.dividend_account, amount,
                         description=f'{label} dividend'),
            ledger.credit(bank_code, amount, description=f'{label} dividend'),
        ]))
    return {'entry_id': entry_id, 'amount': amount, 'franked': franked}


def director_loan(date, director, amount, direction='to_director', bank=None,
                  memo='', company=None) -> dict:
    """Move money between the company and a director's loan account.

    'to_director' is the Division 7A direction: money leaving the company that
    is neither wages nor a dividend. Anything still owed at year end has to be
    repaid or put under a complying loan agreement before the lodgement day.
    """
    company = company or config.load()
    person = company.director(director)
    amount = money(amount)
    if amount <= ZERO:
        raise TransactionError('loan amount must be positive')
    if direction not in ('to_director', 'from_director'):
        raise TransactionError("direction must be 'to_director' or 'from_director'")
    bank_code = _bank(company, bank)
    if direction == 'to_director':
        lines = [ledger.debit(person.loan_account, amount,
                              description=f'Drawn by {person.name}'),
                 ledger.credit(bank_code, amount, description=f'To {person.name}')]
    else:
        lines = [ledger.debit(bank_code, amount, description=f'From {person.name}'),
                 ledger.credit(person.loan_account, amount,
                               description=f'Funds introduced by {person.name}')]
    entry_id = ledger.post(ledger.Entry(
        date=date, memo=memo or f'Director loan {direction} - {person.name}',
        source=DIRECTOR_LOAN, lines=lines))
    return {'entry_id': entry_id, 'amount': amount, 'direction': direction}


# ------------------------------------------------------------------ adjustments

def record_depreciation(date, asset_account, amount, memo='') -> dict:
    """Write down a fixed asset for the year."""
    asset_account = str(asset_account)
    if asset_account not in ACCUMULATED_DEPRECIATION:
        raise TransactionError(
            f'no accumulated depreciation account for {asset_account}; '
            f'expected one of {", ".join(ACCUMULATED_DEPRECIATION)}')
    amount = money(amount)
    if amount <= ZERO:
        raise TransactionError('depreciation must be positive')
    entry_id = ledger.post(ledger.Entry(
        date=date, memo=memo or f'Depreciation - {coa.get(asset_account).name}',
        source=DEPRECIATION,
        lines=[
            ledger.debit(_role('depreciation'), amount),
            ledger.credit(ACCUMULATED_DEPRECIATION[asset_account], amount),
        ]))
    return {'entry_id': entry_id, 'amount': amount}


def manual_journal(date, memo, lines) -> dict:
    """Post a raw journal entry: 'account:DR:amount' or 'account:CR:amount'."""
    journal_lines = []
    for spec in lines:
        if isinstance(spec, ledger.Line):
            journal_lines.append(spec)
            continue
        parts = spec.split(':')
        if len(parts) < 3:
            raise TransactionError(
                f'journal line {spec!r} must be account:DR|CR:amount[:description]')
        account, side, amount = parts[0], parts[1].upper(), parts[2]
        description = parts[3] if len(parts) > 3 else ''
        if side not in ('DR', 'CR'):
            raise TransactionError(f'side must be DR or CR, got {parts[1]!r}')
        journal_lines.append(
            ledger.debit(account, amount, description) if side == 'DR'
            else ledger.credit(account, amount, description))
    entry_id = ledger.post(ledger.Entry(date=date, memo=memo, source=MANUAL,
                                        lines=journal_lines))
    return {'entry_id': entry_id}
