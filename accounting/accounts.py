"""Chart of accounts for an Australian residential/commercial painting company.

Account numbering follows the convention most AU bookkeepers expect:
  1xxx assets, 2xxx liabilities, 3xxx equity,
  4xxx income, 5xxx cost of sales, 6xxx operating expenses.

`role` marks the handful of accounts the software has to find by meaning
rather than by number (the bank account it pays from, the AR control account,
the GST accounts it clears at BAS time, and so on).
"""
from __future__ import annotations

from dataclasses import dataclass, field

ASSET, LIABILITY, EQUITY, INCOME, COGS, EXPENSE = (
    'ASSET', 'LIABILITY', 'EQUITY', 'INCOME', 'COGS', 'EXPENSE')

DEBIT_TYPES = {ASSET, COGS, EXPENSE}
CREDIT_TYPES = {LIABILITY, EQUITY, INCOME}
PROFIT_TYPES = (INCOME, COGS, EXPENSE)


@dataclass(frozen=True)
class Account:
    code: str
    name: str
    type: str
    tax_code: str = 'NT'
    role: str = ''
    contra: bool = False        # normal balance is opposite to its type
    deductible: bool = True     # False = add back when estimating company tax
    tpar: bool = False          # payments here are reportable on the TPAR
    note: str = ''

    @property
    def normal_side(self) -> str:
        debit_normal = self.type in DEBIT_TYPES
        if self.contra:
            debit_normal = not debit_normal
        return 'DR' if debit_normal else 'CR'

    @property
    def sign(self) -> int:
        """+1 if debits increase this account's own balance, -1 if credits do."""
        return 1 if self.normal_side == 'DR' else -1

    @property
    def type_sign(self) -> int:
        """Sign used in reports: by account *type*, ignoring the contra flag.

        This is what makes contra accounts behave: accumulated depreciation
        lands as a negative asset and dividends paid as negative equity, so
        each one reduces its section instead of inflating it.
        """
        return 1 if self.type in DEBIT_TYPES else -1

    @property
    def is_profit_and_loss(self) -> bool:
        return self.type in PROFIT_TYPES


def _a(*args, **kwargs) -> Account:
    return Account(*args, **kwargs)


CHART: list[Account] = [
    # ------------------------------------------------------------------ assets
    _a('1000', 'Business Bank Account', ASSET, 'NT', role='bank',
       note='Main trading account. Default source of payments.'),
    _a('1010', 'GST & Tax Savings Account', ASSET, 'NT', role='bank',
       note='Park 1/11th of every receipt plus tax and super here.'),
    _a('1020', 'Petty Cash', ASSET, 'NT', role='bank'),
    _a('1100', 'Accounts Receivable', ASSET, 'NT', role='ar',
       note='Control account - only invoices and customer receipts touch it.'),
    _a('1110', 'GST Paid on Purchases', ASSET, 'NT', role='gst_paid',
       note='Input tax credits accrued since the last BAS (BAS label 1B).'),
    _a('1120', 'PAYG Instalments Paid', ASSET, 'NT', role='payg_instalments',
       note='Label 5A payments. Offsets the company tax bill at year end.'),
    _a('1200', 'Prepayments', ASSET, 'NT',
       note='Insurance and licences paid in advance.'),
    _a('1210', 'Deposits Paid on Assets', ASSET, 'NT', role='asset_deposit',
       note='Money down on something not delivered yet. It becomes part of '
            'the asset cost on delivery, not before - nothing is depreciated '
            'and no GST is claimed until you actually hold the thing.'),
    _a('1400', 'Tools & Equipment - at cost', ASSET, 'CAP', role='fixed_asset'),
    _a('1410', 'Tools & Equipment - accumulated depreciation', ASSET, 'NT',
       contra=True),
    _a('1420', 'Motor Vehicles - at cost', ASSET, 'CAP', role='fixed_asset'),
    _a('1430', 'Motor Vehicles - accumulated depreciation', ASSET, 'NT',
       contra=True),

    # ------------------------------------------------------------- liabilities
    _a('2000', 'Accounts Payable', LIABILITY, 'NT', role='ap',
       note='Control account - only supplier bills and payments touch it.'),
    _a('2100', 'GST Collected on Sales', LIABILITY, 'NT', role='gst_collected',
       note='GST charged since the last BAS (BAS label 1A).'),
    _a('2200', 'PAYG Withholding Payable', LIABILITY, 'NT', role='payg_withholding',
       note='Withheld from director wages (W2) and no-ABN supplier payments.'),
    _a('2300', 'Superannuation Payable', LIABILITY, 'NT', role='super_payable',
       note='Must reach the fund by the quarterly due date or it becomes SGC.'),
    _a('2310', 'Wages Payable', LIABILITY, 'NT'),
    _a('2400', 'Income Tax Payable', LIABILITY, 'NT', role='income_tax'),
    _a('2500', 'Business Credit Card', LIABILITY, 'NT', role='bank'),
    _a('2600', 'Director Loan - Director 1', LIABILITY, 'NT', role='director_loan',
       note='Credit = company owes the director. Debit = Division 7A risk.'),
    _a('2610', 'Director Loan - Director 2', LIABILITY, 'NT', role='director_loan',
       note='Credit = company owes the director. Debit = Division 7A risk.'),
    _a('2700', 'Customer Deposits', LIABILITY, 'NT',
       note='Deposits taken before work starts. GST is payable when received.'),
    _a('2800', 'Chattel Mortgage - Motor Vehicle', LIABILITY, 'NT', role='finance',
       note='Under a chattel mortgage the company owns the vehicle outright '
            'and this is the loan secured over it. Repayments split between '
            'principal here and interest at 6900.'),
    _a('2810', 'Equipment Finance', LIABILITY, 'NT', role='finance'),

    # ------------------------------------------------------------------ equity
    _a('3000', 'Share Capital', EQUITY, 'NT'),
    _a('3100', 'Retained Earnings', EQUITY, 'NT', role='retained_earnings'),
    _a('3200', 'Dividends Paid - Director 1', EQUITY, 'NT', contra=True,
       role='dividend'),
    _a('3210', 'Dividends Paid - Director 2', EQUITY, 'NT', contra=True,
       role='dividend'),

    # ------------------------------------------------------------------ income
    _a('4000', 'Painting - Residential', INCOME, 'GST'),
    _a('4010', 'Painting - Commercial', INCOME, 'GST'),
    _a('4020', 'Materials Recharged', INCOME, 'GST'),
    _a('4030', 'Callout & Minor Works', INCOME, 'GST'),
    _a('4100', 'Other Income', INCOME, 'GST'),
    _a('4900', 'Discounts Given', INCOME, 'GST', contra=True),

    # ----------------------------------------------------------- cost of sales
    _a('5000', 'Subcontractor Costs', COGS, 'GST', tpar=True,
       note='Reportable on the TPAR. Never pay without a valid ABN on file.'),
    _a('5100', 'Paint & Materials', COGS, 'GST'),
    _a('5200', 'Equipment & Scaffold Hire', COGS, 'GST'),
    _a('5300', 'Site Consumables', COGS, 'GST',
       note='Drop sheets, tape, sandpaper, brushes, rollers.'),
    _a('5400', 'Waste Removal & Skip Bins', COGS, 'GST'),
    _a('5500', 'Job Travel & Parking', COGS, 'GST'),

    # ---------------------------------------------------------------- expenses
    _a('6000', "Directors' Wages", EXPENSE, 'NT', role='wages',
       note='Feeds BAS label W1. Attracts PAYG withholding and super.'),
    _a('6010', 'Wages & Salaries', EXPENSE, 'NT', role='wages'),
    _a('6020', 'Superannuation Expense', EXPENSE, 'NT', role='super_expense'),
    _a('6030', 'Workers Compensation Insurance', EXPENSE, 'GST',
       note='icare NSW. Required once wages exceed $7,500 a year.'),
    _a('6100', 'Motor Vehicle - Fuel', EXPENSE, 'GST'),
    _a('6110', 'Motor Vehicle - Registration & Insurance', EXPENSE, 'GST',
       note='Split the rego notice: CTP and insurance carry GST, the rego '
            'and inspection components are GST-free.'),
    _a('6120', 'Motor Vehicle - Repairs & Maintenance', EXPENSE, 'GST'),
    _a('6200', 'Tools & Small Equipment', EXPENSE, 'GST',
       note='Items under the instant write-off threshold. Bigger items go to 1400.'),
    _a('6210', 'PPE & Protective Clothing', EXPENSE, 'GST'),
    _a('6300', 'Insurance - Public Liability', EXPENSE, 'GST'),
    _a('6310', 'Licences & Permits', EXPENSE, 'FRE',
       note='NSW Fair Trading painting licence, council permits.'),
    _a('6320', 'Home Building Compensation Insurance', EXPENSE, 'FRE',
       note='HBCF cover, required on residential work over $20,000.'),
    _a('6400', 'Advertising & Marketing', EXPENSE, 'GST',
       note='Meta ads, signage, vehicle wrap, lead generation.'),
    _a('6410', 'Software & Subscriptions', EXPENSE, 'GST'),
    _a('6420', 'Phone & Internet', EXPENSE, 'GST',
       note='Claim the business-use percentage only.'),
    _a('6500', 'Accounting & Bookkeeping', EXPENSE, 'GST'),
    _a('6510', 'Legal Fees', EXPENSE, 'GST'),
    _a('6520', 'ASIC Fees', EXPENSE, 'FRE',
       note='Annual review fee and lodgement fees are GST-free.'),
    _a('6600', 'Bank Fees', EXPENSE, 'INP'),
    _a('6610', 'Merchant & Payment Fees', EXPENSE, 'GST'),
    _a('6700', 'Rent - Storage & Yard', EXPENSE, 'GST'),
    _a('6710', 'Office Supplies & Printing', EXPENSE, 'GST'),
    _a('6800', 'Depreciation', EXPENSE, 'NT', role='depreciation'),
    _a('6900', 'Interest Expense', EXPENSE, 'INP'),
    _a('6950', 'Fines & Penalties', EXPENSE, 'NT', deductible=False,
       note='ATO general interest charge and traffic fines are not deductible.'),
    _a('6960', 'Entertainment', EXPENSE, 'NT', deductible=False,
       note='Client meals and drinks. Not deductible and no GST credit.'),
]

BY_CODE = {a.code: a for a in CHART}


def get(code: str) -> Account:
    key = str(code).strip()
    if key not in BY_CODE:
        raise KeyError(f'unknown account {code!r}')
    return BY_CODE[key]


def by_role(role: str) -> list[Account]:
    return [a for a in CHART if a.role == role]


def first_with_role(role: str) -> Account:
    matches = by_role(role)
    if not matches:
        raise KeyError(f'no account with role {role!r}')
    return matches[0]


def tpar_accounts() -> set:
    return {a.code for a in CHART if a.tpar}


def bank_accounts() -> list[Account]:
    return by_role('bank')
