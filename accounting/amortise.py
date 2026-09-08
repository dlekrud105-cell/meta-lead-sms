"""Loan amortisation, so each repayment splits itself.

Only the interest in a finance repayment is deductible. The principal repays
the loan and has already been accounted for in the asset's cost, so getting
the split right every month matters. Working it out from the loan terms beats
reading it off a paper schedule sixty times.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from . import store
from .money import ZERO, money
from .periods import parse_date


@dataclass
class Instalment:
    number: int
    due: date
    payment: Decimal
    interest: Decimal
    principal: Decimal
    balance: Decimal      # what is left owing after this one


@dataclass
class Schedule:
    principal: Decimal
    annual_rate: Decimal
    months: int
    balloon: Decimal
    payment: Decimal
    instalments: list

    @property
    def total_paid(self) -> Decimal:
        return money(sum((i.payment for i in self.instalments), ZERO) + self.balloon)

    @property
    def total_interest(self) -> Decimal:
        return money(sum((i.interest for i in self.instalments), ZERO))

    def at(self, number: int) -> Instalment:
        for instalment in self.instalments:
            if instalment.number == number:
                return instalment
        raise KeyError(f'instalment {number} is not in this schedule')

    def on(self, when) -> Instalment:
        """The instalment falling on or nearest before a date."""
        when = parse_date(when)
        candidates = [i for i in self.instalments if i.due <= when]
        if not candidates:
            return self.instalments[0]
        return candidates[-1]


def _add_month(start: date, months: int) -> date:
    month = start.month - 1 + months
    year = start.year + month // 12
    month = month % 12 + 1
    day = start.day
    while day > 28:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, day)


def monthly_payment(principal, annual_rate, months, balloon=0) -> Decimal:
    """The level repayment that clears the loan down to the balloon."""
    principal = money(principal)
    balloon = money(balloon)
    rate = Decimal(str(annual_rate)) / Decimal('12')
    if rate == 0:
        return money((principal - balloon) / months)
    factor = (Decimal(1) + rate) ** months
    payment = (principal - balloon / factor) * rate / (Decimal(1) - Decimal(1) / factor)
    return money(payment)


def schedule(principal, annual_rate, months, start, balloon=0,
             payment=None) -> Schedule:
    """Build the full repayment schedule.

    `annual_rate` is a decimal fraction: 9.3% is 0.093. The final instalment
    absorbs any rounding so the balance lands exactly on the balloon.
    """
    principal = money(principal)
    balloon = money(balloon)
    months = int(months)
    annual_rate = Decimal(str(annual_rate))
    if annual_rate > 1:
        raise ValueError(
            f'rate {annual_rate} looks like a percentage; pass 0.093 for 9.3%')
    rate = annual_rate / Decimal('12')
    level = money(payment) if payment is not None else monthly_payment(
        principal, annual_rate, months, balloon)

    start = parse_date(start)
    balance = principal
    instalments = []
    for number in range(1, months + 1):
        interest = money(balance * rate)
        due = level
        if number == months:
            # Last one clears whatever is actually left, plus the interest.
            due = money(balance + interest - balloon)
        principal_part = money(due - interest)
        balance = money(balance - principal_part)
        instalments.append(Instalment(number=number, due=_add_month(start, number - 1),
                                      payment=due, interest=interest,
                                      principal=principal_part, balance=balance))
    return Schedule(principal=principal, annual_rate=annual_rate, months=months,
                    balloon=balloon, payment=level, instalments=instalments)


# ------------------------------------------------------------------- storage

SCHEDULES = store.Table('finance_schedules.csv', [
    'account', 'principal', 'annual_rate', 'months', 'balloon', 'payment',
    'first_due', 'description',
])


def save(account, principal, annual_rate, months, start, balloon=0,
         payment=None, description='') -> Schedule:
    built = schedule(principal, annual_rate, months, start, balloon, payment)
    rows = [r for r in SCHEDULES.read() if r['account'] != str(account)]
    rows.append({
        'account': str(account), 'principal': f'{built.principal:.2f}',
        'annual_rate': str(built.annual_rate), 'months': str(built.months),
        'balloon': f'{built.balloon:.2f}', 'payment': f'{built.payment:.2f}',
        'first_due': built.instalments[0].due.isoformat(),
        'description': description,
    })
    SCHEDULES.write_all(rows)
    return built


def load(account):
    row = SCHEDULES.find(account=str(account))
    if row is None:
        return None
    return schedule(row['principal'], row['annual_rate'], row['months'],
                    row['first_due'], row['balloon'], row['payment'])
