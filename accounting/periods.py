"""Australian financial year and BAS quarter arithmetic.

The AU financial year runs 1 July - 30 June and is named after the year it
ends in: FY2026 = 2025-07-01 .. 2026-06-30.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# BAS/super quarters, keyed by the quarter's ordinal within the financial year.
# (start month, start day, end month, end day, due month, due day, due year offset)
# Due dates are the standard self-lodgement dates; a registered BAS/tax agent
# usually gets around four extra weeks on the BAS ones.
_QUARTERS = {
    1: ('Q1', 7, 9, 10, 28, 0),   # Jul-Sep, due 28 Oct same calendar year
    2: ('Q2', 10, 12, 2, 28, 1),  # Oct-Dec, due 28 Feb next calendar year
    3: ('Q3', 1, 3, 4, 28, 0),    # Jan-Mar, due 28 Apr same calendar year
    4: ('Q4', 4, 6, 7, 28, 0),    # Apr-Jun, due 28 Jul same calendar year
}


def parse_date(value) -> date:
    """Accept a date or an ISO 'YYYY-MM-DD' string."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def end_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def fy_ending(d) -> int:
    """Financial year label (the year it ends) containing `d`."""
    d = parse_date(d)
    return d.year + 1 if d.month >= 7 else d.year


def fy_range(fy: int) -> tuple[date, date]:
    """Start and end dates of financial year `fy` (named by ending year)."""
    return date(fy - 1, 7, 1), date(fy, 6, 30)


@dataclass(frozen=True)
class Quarter:
    fy: int          # financial year it belongs to (ending year)
    number: int      # 1-4 within that FY
    start: date
    end: date
    bas_due: date
    super_due: date

    @property
    def label(self) -> str:
        return f'{_QUARTERS[self.number][0]} FY{self.fy}'

    def contains(self, d) -> bool:
        return self.start <= parse_date(d) <= self.end


def quarter(fy: int, number: int) -> Quarter:
    _, start_month, end_month, due_month, due_day, due_offset = _QUARTERS[number]
    # Q1/Q2 fall in the earlier calendar year, Q3/Q4 in the later one.
    start_year = fy - 1 if start_month >= 7 else fy
    end_year = fy - 1 if end_month >= 7 else fy
    start = date(start_year, start_month, 1)
    end = end_of_month(end_year, end_month)
    due = date(end_year + due_offset, due_month, due_day)
    # Super guarantee and BAS share the same 28-day-after-quarter-end deadline,
    # but super must be *received by the fund* by then, not merely lodged.
    return Quarter(fy=fy, number=number, start=start, end=end,
                   bas_due=due, super_due=due)


def quarter_of(d) -> Quarter:
    """The BAS quarter containing `d`."""
    d = parse_date(d)
    fy = fy_ending(d)
    for number in (1, 2, 3, 4):
        q = quarter(fy, number)
        if q.contains(d):
            return q
    raise ValueError(f'no quarter contains {d}')


def quarters_in_fy(fy: int) -> list[Quarter]:
    return [quarter(fy, n) for n in (1, 2, 3, 4)]


def resolve_period(period: str) -> tuple[date, date, str]:
    """Turn a period string into (start, end, label).

    Accepts 'FY2026', '2026Q3', 'Q3FY2026', '2026-01-01:2026-03-31'.
    """
    if ':' in period:
        start, end = period.split(':', 1)
        return parse_date(start), parse_date(end), f'{start} to {end}'
    p = period.upper().replace(' ', '')
    if p.startswith('FY'):
        fy = int(p[2:])
        start, end = fy_range(fy)
        return start, end, f'FY{fy}'
    if 'Q' in p:
        # '2026Q3' or 'Q3FY2026'
        if p.startswith('Q'):
            number = int(p[1])
            fy = int(p.split('FY')[1])
        else:
            year_part, number_part = p.split('Q')
            fy, number = int(year_part), int(number_part)
        q = quarter(fy, number)
        return q.start, q.end, q.label
    raise ValueError(f'unrecognised period: {period!r}')
