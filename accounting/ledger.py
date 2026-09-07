"""The double-entry journal: the only place data is stored.

Every report in this package is derived from these lines. Nothing keeps a
running balance on the side, so the books cannot silently disagree with
themselves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import accounts as coa
from . import store
from . import taxcodes
from .money import ZERO, money
from .periods import parse_date


class LedgerError(ValueError):
    """Raised when an entry would break double-entry or referential rules."""


@dataclass
class Line:
    account: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    description: str = ''
    tax_code: str = 'NT'
    contact: str = ''
    job: str = ''

    def __post_init__(self):
        self.account = str(self.account).strip()
        self.debit = money(self.debit)
        self.credit = money(self.credit)
        self.tax_code = (self.tax_code or 'NT').strip().upper()

    @property
    def signed(self) -> Decimal:
        """Debit-positive amount."""
        return self.debit - self.credit

    @property
    def amount(self) -> Decimal:
        return self.debit if self.debit else self.credit


def debit(account, amount, description='', tax_code='NT', contact='', job='') -> Line:
    return Line(account=account, debit=amount, description=description,
                tax_code=tax_code, contact=contact, job=job)


def credit(account, amount, description='', tax_code='NT', contact='', job='') -> Line:
    return Line(account=account, credit=amount, description=description,
                tax_code=tax_code, contact=contact, job=job)


@dataclass
class Entry:
    date: date
    memo: str
    lines: list = field(default_factory=list)
    source: str = 'JOURNAL'
    doc_ref: str = ''
    entry_id: str = ''

    def __post_init__(self):
        self.date = parse_date(self.date)

    @property
    def total_debits(self) -> Decimal:
        return money(sum((l.debit for l in self.lines), ZERO))

    @property
    def total_credits(self) -> Decimal:
        return money(sum((l.credit for l in self.lines), ZERO))

    def validate(self) -> None:
        if not self.lines:
            raise LedgerError('entry has no lines')
        for line in self.lines:
            try:
                account = coa.get(line.account)
            except KeyError as exc:
                raise LedgerError(str(exc)) from None
            if line.debit and line.credit:
                raise LedgerError(
                    f'line on {line.account} has both a debit and a credit')
            if not line.debit and not line.credit:
                raise LedgerError(f'line on {line.account} has a zero amount')
            if line.debit < 0 or line.credit < 0:
                raise LedgerError(
                    f'line on {line.account} is negative; post it to the other side instead')
            try:
                taxcodes.get(line.tax_code)
            except KeyError as exc:
                raise LedgerError(str(exc)) from None
            if line.tax_code != 'NT' and not account.is_profit_and_loss \
                    and account.role != 'fixed_asset':
                raise LedgerError(
                    f'tax code {line.tax_code} on balance sheet account '
                    f'{line.account} - GST is tracked on the income, expense or '
                    'capital line, not on control accounts')
        if self.total_debits != self.total_credits:
            raise LedgerError(
                f'entry does not balance: debits {self.total_debits} '
                f'!= credits {self.total_credits}')


# ---------------------------------------------------------------------- posting

def next_entry_id() -> str:
    return store.JOURNAL.next_sequence('entry_id', 'JE', width=5)


def post(entry: Entry) -> str:
    """Validate and append an entry to the journal. Returns the entry id."""
    entry.validate()
    entry.entry_id = entry.entry_id or next_entry_id()
    rows = []
    for index, line in enumerate(entry.lines, start=1):
        rows.append({
            'entry_id': entry.entry_id,
            'date': entry.date.isoformat(),
            'memo': entry.memo,
            'source': entry.source,
            'doc_ref': entry.doc_ref,
            'line_no': index,
            'account': line.account,
            'description': line.description,
            'debit': f'{line.debit:.2f}',
            'credit': f'{line.credit:.2f}',
            'tax_code': line.tax_code,
            'contact': line.contact,
            'job': line.job,
        })
    store.JOURNAL.append_many(rows)
    return entry.entry_id


# ---------------------------------------------------------------------- reading

@dataclass
class PostedLine:
    entry_id: str
    date: date
    memo: str
    source: str
    doc_ref: str
    account: str
    description: str
    debit: Decimal
    credit: Decimal
    tax_code: str
    contact: str
    job: str

    @property
    def signed(self) -> Decimal:
        return self.debit - self.credit

    @property
    def amount(self) -> Decimal:
        return self.debit if self.debit else self.credit


def all_lines() -> list:
    out = []
    for row in store.JOURNAL.read():
        out.append(PostedLine(
            entry_id=row['entry_id'],
            date=parse_date(row['date']),
            memo=row['memo'],
            source=row['source'],
            doc_ref=row['doc_ref'],
            account=row['account'],
            description=row['description'],
            debit=money(row['debit']),
            credit=money(row['credit']),
            tax_code=row['tax_code'] or 'NT',
            contact=row['contact'],
            job=row['job'],
        ))
    return out


def lines(start=None, end=None, account=None, sources=None,
          exclude_sources=(), contact=None, job=None, doc_ref=None) -> list:
    start = parse_date(start) if start else None
    end = parse_date(end) if end else None
    result = []
    for line in all_lines():
        if start and line.date < start:
            continue
        if end and line.date > end:
            continue
        if account and line.account != str(account):
            continue
        if sources and line.source not in sources:
            continue
        if line.source in exclude_sources:
            continue
        if contact and line.contact != contact:
            continue
        if job and line.job != job:
            continue
        if doc_ref and line.doc_ref != doc_ref:
            continue
        result.append(line)
    return result


def movement(account, start=None, end=None, exclude_sources=()) -> Decimal:
    """Net change in an account over a period, positive in its normal direction."""
    selected = lines(start=start, end=end, account=account,
                     exclude_sources=exclude_sources)
    net = money(sum((l.signed for l in selected), ZERO))
    return money(net * coa.get(account).sign)


def net(account, start=None, end=None, exclude_sources=()) -> Decimal:
    """Net movement signed by account *type*, for reporting.

    Contra accounts come back negative, which is how they should read in a
    profit and loss or balance sheet.
    """
    selected = lines(start=start, end=end, account=account,
                     exclude_sources=exclude_sources)
    total = money(sum((l.signed for l in selected), ZERO))
    return money(total * coa.get(account).type_sign)


def nets(start=None, end=None, exclude_sources=()) -> dict:
    """Type-signed movement for every account touched in the period."""
    totals = {}
    for line in lines(start=start, end=end, exclude_sources=exclude_sources):
        totals[line.account] = totals.get(line.account, ZERO) + line.signed
    return {code: money(total * coa.get(code).type_sign)
            for code, total in totals.items()}


def balance(account, as_at=None, exclude_sources=()) -> Decimal:
    """Balance of an account, positive in its normal direction."""
    return movement(account, start=None, end=as_at, exclude_sources=exclude_sources)


def balances(as_at=None) -> dict:
    """Every account with a non-zero balance, positive in its normal direction."""
    totals = {}
    for line in lines(end=as_at):
        totals[line.account] = totals.get(line.account, ZERO) + line.signed
    return {code: money(total * coa.get(code).sign)
            for code, total in totals.items() if money(total) != ZERO}


def trial_balance(as_at=None) -> list:
    """(account, debit, credit) rows, ordered by account code."""
    totals = {}
    for line in lines(end=as_at):
        totals[line.account] = totals.get(line.account, ZERO) + line.signed
    rows = []
    for code in sorted(totals):
        net = money(totals[code])
        if net == ZERO:
            continue
        rows.append((coa.get(code), net if net > 0 else ZERO,
                     -net if net < 0 else ZERO))
    return rows


def entries(start=None, end=None, sources=None) -> list:
    """Posted lines grouped back into entries, in date then id order."""
    grouped = {}
    for line in lines(start=start, end=end, sources=sources):
        grouped.setdefault(line.entry_id, []).append(line)
    return sorted(grouped.values(), key=lambda ls: (ls[0].date, ls[0].entry_id))
