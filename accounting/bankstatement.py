"""Parse CommBank business account statements into transactions.

PDF text extraction loses the column layout, so a debit and a credit look
identical once the page is flattened. Direction is recovered from the running
balance instead, and the result is checked against the statement's own
opening balance, closing balance and debit/credit control totals. If those do
not reconcile the parse is rejected rather than quietly importing wrong data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .money import ZERO, money

MONTHS = {m: i for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], start=1)}

_AMOUNT = r'-?[\d,]+\.\d{2}'
# A transaction's last line carries the amount and the balance it left behind.
_TAIL = re.compile(rf'(?P<amount>{_AMOUNT})\s+(?P<balance>{_AMOUNT})\s*(?P<sign>CR|DR)\s*$')
_DATE_START = re.compile(r'^(?P<day>\d{1,2})\s+(?P<month>[A-Z][a-z]{2})\b(?P<rest>.*)$')
_PERIOD = re.compile(
    r'Period\s+(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})\s*-\s*(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})')
_CONTROL = re.compile(
    rf'^(?P<opening>Nil|{_AMOUNT})(?:\s*CR)?\s+(?P<debits>{_AMOUNT})\s+'
    rf'(?P<credits>{_AMOUNT})\s+(?P<closing>{_AMOUNT})\s*CR\s*$')
_ACCOUNT = re.compile(r'Account Number\s+([\d\s]+?)\s*$', re.MULTILINE)
_NOISE = re.compile(r'^(Card xx\d+|Value Date .*|=.*|Statement \d+ Page.*|'
                    r'Date Transaction Debit Credit Balance|Account Number.*|'
                    r'\d{4}\.\d{4}\.\d.*)$')


class StatementError(ValueError):
    pass


@dataclass
class BankLine:
    date: date
    description: str
    amount: Decimal          # always positive
    direction: str           # 'debit' (money out) or 'credit' (money in)
    balance: Decimal
    raw: list = field(default_factory=list)

    @property
    def signed(self) -> Decimal:
        return -self.amount if self.direction == 'debit' else self.amount


@dataclass
class Statement:
    account: str
    start: date
    end: date
    opening: Decimal
    closing: Decimal
    lines: list = field(default_factory=list)
    stated_debits: Decimal = ZERO
    stated_credits: Decimal = ZERO

    @property
    def debits(self) -> Decimal:
        return money(sum((l.amount for l in self.lines if l.direction == 'debit'), ZERO))

    @property
    def credits(self) -> Decimal:
        return money(sum((l.amount for l in self.lines if l.direction == 'credit'), ZERO))

    def reconcile(self) -> list:
        """Every way this parse disagrees with the statement's own figures."""
        problems = []
        computed = money(self.opening - self.debits + self.credits)
        if computed != self.closing:
            problems.append(
                f'balance does not roll forward: opening {self.opening} '
                f'- debits {self.debits} + credits {self.credits} = {computed}, '
                f'but the statement closes at {self.closing}')
        if self.stated_debits and self.debits != self.stated_debits:
            problems.append(f'total debits {self.debits} != stated '
                            f'{self.stated_debits}')
        if self.stated_credits and self.credits != self.stated_credits:
            problems.append(f'total credits {self.credits} != stated '
                            f'{self.stated_credits}')
        return problems


def _num(text: str) -> Decimal:
    return money(text.replace(',', '').strip())


def _clean(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def extract_text(path) -> str:
    try:
        import pypdf
    except ImportError as exc:  # pragma: no cover
        raise StatementError(
            'reading PDF statements needs pypdf: pip install pypdf') from exc
    reader = pypdf.PdfReader(str(path))
    return '\n'.join((page.extract_text() or '') for page in reader.pages)


def parse(text: str) -> Statement:
    """Turn statement text into a reconciled Statement."""
    period = _PERIOD.search(text)
    if not period:
        raise StatementError('could not find the statement period')
    d1, m1, y1, d2, m2, y2 = period.groups()
    start = date(int(y1), MONTHS[m1], int(d1))
    end = date(int(y2), MONTHS[m2], int(d2))

    account_match = _ACCOUNT.search(text)
    account = _clean(account_match.group(1)) if account_match else ''

    opening = closing = None
    stated_debits = stated_credits = ZERO
    lines, pending, previous_balance = [], [], None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        control = _CONTROL.match(stripped)
        if control and previous_balance is not None:
            stated_debits = _num(control.group('debits'))
            stated_credits = _num(control.group('credits'))
            continue

        if 'OPENING BALANCE' in stripped:
            match = re.search(rf'({_AMOUNT})\s*CR', stripped)
            opening = _num(match.group(1)) if match else ZERO
            previous_balance = opening
            pending = []
            continue
        if 'CLOSING BALANCE' in stripped:
            match = re.search(rf'({_AMOUNT})\s*CR', stripped)
            if match:
                closing = _num(match.group(1))
            pending = []
            continue

        if _NOISE.match(stripped):
            # A 'Value Date ...' line can still carry the amount and balance.
            tail = _TAIL.search(stripped)
            if not (tail and stripped.startswith('Value Date')):
                continue

        start_match = _DATE_START.match(stripped)
        if start_match and start_match.group('month') in MONTHS:
            pending = [stripped]
            head = start_match
        else:
            if not pending:
                continue
            pending.append(stripped)
            head = _DATE_START.match(pending[0])

        tail = _TAIL.search(pending[-1])
        if not tail or previous_balance is None:
            continue

        balance = _num(tail.group('balance'))
        amount = _num(tail.group('amount'))
        if amount == ZERO:
            pending = []
            continue

        delta = money(balance - previous_balance)
        if delta == amount:
            direction = 'credit'
        elif delta == -amount:
            direction = 'debit'
        else:
            # The running balance is the only signal; if it disagrees the
            # block was mis-assembled and importing it would corrupt the books.
            raise StatementError(
                f'cannot tell debit from credit for {pending[0][:60]!r}: '
                f'balance moved by {delta} but the amount is {amount}')

        day, month = int(head.group('day')), MONTHS[head.group('month')]
        year = start.year if (month, day) >= (start.month, start.day) else end.year
        if month < start.month and year == start.year:
            year = end.year

        description = _clean(head.group('rest'))
        for extra in pending[1:]:
            description += ' ' + _clean(_TAIL.sub('', extra))
        description = _clean(_TAIL.sub('', description))
        # Drop the bookkeeping furniture CommBank prints inside a transaction.
        description = re.sub(r'\bValue Date \d{2}/\d{2}/\d{4}\b', '', description)
        description = re.sub(r'\bCard xx\d+\b', '', description)
        description = re.sub(r'\bAUD [\d,]+\.\d{2}\b', '', description)
        description = re.sub(r'^\d{4}\s+', '', _clean(description))

        lines.append(BankLine(date=date(year, month, day),
                              description=description or '(no description)',
                              amount=amount, direction=direction,
                              balance=balance, raw=list(pending)))
        previous_balance = balance
        pending = []

    if opening is None or closing is None:
        raise StatementError('could not find the opening and closing balances')

    statement = Statement(account=account, start=start, end=end, opening=opening,
                          closing=closing, lines=lines,
                          stated_debits=stated_debits, stated_credits=stated_credits)
    problems = statement.reconcile()
    if problems:
        raise StatementError('statement does not reconcile: ' + '; '.join(problems))
    return statement


def parse_file(path) -> Statement:
    return parse(extract_text(path))
