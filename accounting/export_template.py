"""Export the books as rows for a bookkeeping spreadsheet.

Built for the template a tax agent hands out: one row per bank transaction,
income and expense separated, GST split out. Pure Python - it produces rows,
and something else writes the file, so the core package still needs nothing
installed.

Movements that are neither income nor expense - a director putting money in,
a BAS payment settling a GST liability - are marked OTHER rather than forced
into one of the two. Calling a loan repayment an expense understates the
profit, and a bookkeeping sheet that quietly does that is worse than one that
admits the row does not fit.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from . import accounts as coa
from . import contacts as contacts_mod
from . import store
from . import taxcodes
from .money import ZERO, money
from .periods import parse_date

INCOME, EXPENSE, OTHER = '수입', '비용', '기타'

# Bilingual category labels, matching the template's own header style.
CATEGORY_LABELS = {
    '4000': '주거 페인팅 (Painting - Residential)',
    '4010': '상업 페인팅 (Painting - Commercial)',
    '4020': '자재 재청구 (Materials Recharged)',
    '4030': '출장·소규모 작업 (Callout & Minor Works)',
    '4100': '기타 수입 (Other Income)',
    '5000': '하청비 (Subcontractor Costs)',
    '5100': '페인트·자재 (Paint & Materials)',
    '5200': '장비·비계 임대 (Equipment & Scaffold Hire)',
    '5300': '현장 소모품 (Site Consumables)',
    '5400': '폐기물 처리 (Waste Removal)',
    '5500': '현장 이동·주차 (Job Travel & Parking)',
    '6000': '디렉터 급여 (Directors\' Wages)',
    '6010': '급여 (Wages & Salaries)',
    '6020': '연금 (Superannuation)',
    '6030': '산재보험 (Workers Compensation)',
    '6100': '차량 연료 (Motor Vehicle - Fuel)',
    '6110': '차량 등록·보험 (Vehicle Registration & Insurance)',
    '6120': '차량 정비 (Vehicle Repairs)',
    '6200': '공구·소형장비 (Tools & Small Equipment)',
    '6210': '작업복·보호구 (PPE & Protective Clothing)',
    '6300': '배상책임보험 (Public Liability Insurance)',
    '6310': '면허·인허가 (Licences & Permits)',
    '6320': '주택보증보험 (Home Building Compensation)',
    '6400': '광고·마케팅 (Advertising & Marketing)',
    '6410': '소프트웨어·구독 (Software & Subscriptions)',
    '6420': '통신비 (Phone & Internet)',
    '6500': '회계·기장료 (Accounting & Bookkeeping)',
    '6510': '법무비 (Legal Fees)',
    '6520': 'ASIC 수수료 (ASIC Fees)',
    '6600': '은행 수수료 (Bank Fees)',
    '6610': '카드 수수료 (Merchant Fees)',
    '6700': '창고 임차료 (Rent - Storage & Yard)',
    '6710': '사무용품·인쇄 (Office Supplies & Printing)',
    '6800': '감가상각 (Depreciation)',
    '6900': '이자비용 (Interest Expense)',
    '6950': '벌금·과태료 (Fines & Penalties) — 손금불산입',
    '6960': '접대비 (Entertainment) — 손금불산입',
    '1420': '차량 (Motor Vehicle)',
    '1400': '공구·장비 자산 (Tools & Equipment)',
    '2100': 'ATO 납부 - GST (BAS Payment)',
    '2200': 'ATO 납부 - 원천징수 (PAYG Withheld)',
    '2300': '연금 납부 (Superannuation Paid)',
    '2600': '디렉터 대여금 (Director Loan)',
    '2610': '디렉터 대여금 (Director Loan)',
    '2800': '차량 할부금 (Chattel Mortgage)',
    '3000': '자본금 (Share Capital)',
}

# How the money moved, read off the wording CommBank uses.
PAYMENT_METHODS = (
    ('direct debit', '자동이체 (Direct Debit)'),
    ('direct credit', '계좌입금 (Direct Credit)'),
    ('fast transfer', '계좌이체 (Bank Transfer)'),
    ('transfer to', '계좌이체 (Bank Transfer)'),
    ('transfer from', '계좌이체 (Bank Transfer)'),
    ('bpay', 'BPAY'),
    ('refund', '환불 (Refund)'),
)
DEFAULT_METHOD = '카드 (Card)'


@dataclass
class TemplateRow:
    date: str
    kind: str              # 수입 / 비용 / 기타
    description: str
    amount: Decimal        # as it hit the bank, GST inclusive
    gst_included: str      # Y / N
    gst: Decimal
    net: Decimal
    method: str
    category: str
    account: str


def category_for(code: str) -> str:
    account = coa.get(code)
    return CATEGORY_LABELS.get(code, f'{account.name} ({code})')


def kind_for(code: str) -> str:
    account = coa.get(code)
    if account.type == coa.INCOME:
        return INCOME
    if account.type in (coa.COGS, coa.EXPENSE):
        return EXPENSE
    return OTHER


def method_for(description: str) -> str:
    lowered = description.lower()
    for marker, label in PAYMENT_METHODS:
        if marker in lowered:
            return label
    return DEFAULT_METHOD


def bookkeeping_rows(start=None, end=None) -> list:
    """One row per imported bank transaction, oldest first."""
    start = parse_date(start) if start else None
    end = parse_date(end) if end else None
    rows = []
    for raw in store.BANK_LINES.read():
        when = parse_date(raw['date'])
        if (start and when < start) or (end and when > end):
            continue
        amount = money(raw['amount'])
        code = raw['account']
        tax_code = raw['tax_code'] or 'NT'
        has_gst = taxcodes.get(tax_code).rate > 0
        gst = money(amount / Decimal('11')) if has_gst else ZERO
        contact = contacts_mod.find(raw['contact'])
        description = raw['description']
        if contact and contact.name.lower() not in description.lower():
            description = f'{description} — {contact.name}'
        rows.append(TemplateRow(
            date=when.isoformat(), kind=kind_for(code), description=description,
            amount=amount, gst_included='Y' if has_gst else 'N', gst=gst,
            net=money(amount - gst), method=method_for(raw['description']),
            category=category_for(code), account=code))
    rows.sort(key=lambda r: (r.date, r.description))
    return rows


def totals(rows: list) -> dict:
    """Cross-check figures, so the spreadsheet can be verified against them."""
    def summed(kind, field):
        return money(sum((getattr(r, field) for r in rows if r.kind == kind), ZERO))
    return {
        'income': summed(INCOME, 'amount'),
        'expenses': summed(EXPENSE, 'amount'),
        'other': summed(OTHER, 'amount'),
        'gst': money(sum((r.gst for r in rows), ZERO)),
        'count': len(rows),
    }
