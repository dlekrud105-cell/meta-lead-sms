"""Rules that turn a bank line's text into an account and a tax code.

The starter set below is built from this company's own statements. Rules are
matched in order, first match wins, and anything marked `review` is proposed
but held back from posting until a person confirms it - that covers the calls
software should not make on its own, like whether a meal was a site lunch or
entertainment.

Edit data/import_rules.csv to add your own; those are checked before these.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import store

ANY, DEBIT, CREDIT = 'any', 'debit', 'credit'


@dataclass
class Rule:
    pattern: str
    account: str
    tax_code: str = ''
    direction: str = ANY
    contact: str = ''
    review: bool = False
    note: str = ''

    def matches(self, description: str, direction: str) -> bool:
        if self.direction != ANY and self.direction != direction:
            return False
        if self.pattern.startswith('re:'):
            return bool(re.search(self.pattern[3:], description, re.IGNORECASE))
        return self.pattern.lower() in description.lower()


def _r(pattern, account, tax_code='GST', direction=DEBIT, **kwargs) -> Rule:
    return Rule(pattern=pattern, account=account, tax_code=tax_code,
                direction=direction, **kwargs)


# Order matters: the more specific a rule, the earlier it has to appear.
DEFAULT_RULES = [
    # --- money in -----------------------------------------------------------
    _r('ACTIVE BUILDING GROUP', '4010', direction=CREDIT,
       contact='Active Building Group', note='Builder - commercial work'),
    _r('Golden Berg', '4010', direction=CREDIT, contact='Golden Berg Enterprises'),
    _r('KAYANE', '4010', direction=CREDIT, contact='Kayane Pty Ltd'),
    _r('re:Refund Purchase', '5300', direction=CREDIT,
       note='Refund of a purchase - check it lands back on the right account',
       review=True),
    _r('re:(Fast )?Transfer [Ff]rom', '4000', direction=CREDIT, review=True,
       note='Customer payment? Confirm the job and whether it is residential '
            'or commercial. If it is a director putting money in, recode it to '
            'their loan account.'),
    _r('Direct Credit', '4000', direction=CREDIT, review=True),

    # --- paint and trade suppliers -----------------------------------------
    _r('INSPIRATIONS PAINT', '5100', contact='Inspirations Paint'),
    _r('DUMASTER PAINT', '5100', contact='Dumaster Paint Specialists'),
    _r('DULUX', '5100', contact='Dulux'),
    _r('BONG BONG PAINT', '5100', contact='Bong Bong Paint Plus'),
    _r('LS PROTRADE', '5100', contact='LS Protrade Warehouse'),
    _r('LS SYDNEY INDUSTRIAL', '5100', contact='LS Sydney Industrial'),
    _r('NYK HOLDINGS', '5100'),
    _r('BUNNINGS', '5300', contact='Bunnings'),

    # --- marketing ----------------------------------------------------------
    _r('Google ADS', '6400', contact='Google'),
    _r('FACEBK', '6400', contact='Meta'),
    _r('Canva', '6410', contact='Canva'),
    _r('PAYROLLER', '6410', contact='Payroller'),
    _r('Nexprint', '6400', contact='Nexprint', note='Signage and print'),

    # --- insurance and compliance ------------------------------------------
    _r('ICARE NSW', '6030', contact='icare NSW',
       note='NSW workers compensation premium'),
    _r('UPCOVER', '6300', contact='Upcover', note='Business insurance'),
    _r('WOORI ACCOUNTING', '6500', contact='Woori Accounting Services'),
    _r('TAX OFFICE PAYMENT', '', tax_code='NT', review=True,
       note='ATO payment - settle it against the BAS it belongs to with '
            '`report bas --pay`, not as an expense.'),
    _r('SDRO INFRNGMNT', '6950', tax_code='NT', note='Traffic fine - not deductible'),

    # --- vehicle and travel -------------------------------------------------
    _r('METRO PETROLEUM', '6100'),
    _r('7-ELEVEN', '6100'),
    _r('SPEEDWAY', '6100'),
    _r('Reddy Express', '6100'),
    _r('DIDI MOBILITY', '5500'),
    _r('UBER *TRIP', '5500'),

    # --- clothing and equipment --------------------------------------------
    _r('re:Pistol Clothing|uniform', '6210', note='Uniforms'),
    _r('OFFICEWORKS', '6710', review=True,
       note='Consumables go to 6710; anything that lasts belongs on 1400.'),
    _r('JB HI FI', '6200', review=True,
       note='Likely capital - if it lasts more than a year put it on 1400 '
            'with tax code CAP and depreciate it.'),
    _r('APPLE ', '6200', review=True, note='Capital? See 1400/CAP.'),
    _r('KMART', '6710', review=True),

    # --- people -------------------------------------------------------------
    _r('re:Transfer To .*(Chungyeon|Doyeob|Doyeop)', '6000', tax_code='NT',
       review=True,
       note='Director payment. If it went through STP as a wage it belongs on '
            '6000 and needs PAYG withheld and 12% super. If not, it is a '
            'Division 7A loan - recode it to 2600/2610.'),
    _r('re:Transfer To J HAN', '5000', review=True,
       contact='J Han',
       note='Described as a wage but paid in round GST-inclusive amounts, '
            'which reads like a subcontractor. If they invoice with an ABN it '
            'is 5000 and TPAR-reportable. If they are an employee it is 6010 '
            'and needs STP, PAYG and super.'),

    # --- food: the call software should not make on its own -----------------
    _r('re:UBER \\*EATS|MCDONALDS|KFC|Dominos|CHICKEN|Cafe|Coffee|EATERY|'
       r'KITCHEN|BUTCHER|SASHIMI|POCHA|MARKET|FOOD|Bake|Brew|CREMA|Shuk|'
       r'TEHWARU|Irea|SSAL|JINJJA|Kmall|MANSUN|SMKOREA|MISO|Golden Lotus|'
       r'WANTED|THE FRESH|SHINARA|Redstone|ENZE|Little Me|LOCAL ENFIELD|'
       r'TANAYA|PAJUOK|Jeoung Don|MTRAN|ZLR\\*|SMP\\*|Smelly|August Coffee',
       '6960', tax_code='NT', review=True,
       note='Meals. Entertainment is not deductible and has no GST credit. '
            'Genuine on-site refreshments for workers can go to 6210 or 5300 - '
            'you have to decide which this was.'),
]


def user_rules() -> list:
    rules = []
    for row in store.IMPORT_RULES.read():
        rules.append(Rule(
            pattern=row['pattern'], account=row['account'],
            tax_code=row.get('tax_code', ''),
            direction=row.get('direction') or ANY,
            contact=row.get('contact', ''),
            review=str(row.get('review', '')).lower() in ('1', 'true', 'yes', 'y'),
            note=row.get('note', '')))
    return rules


def all_rules() -> list:
    """User rules first so they can override any of the built-in ones."""
    return user_rules() + DEFAULT_RULES


def match(description: str, direction: str):
    for rule in all_rules():
        if rule.matches(description, direction):
            return rule
    return None


def add(pattern, account, tax_code='', direction=ANY, contact='', note='') -> Rule:
    rule = Rule(pattern=pattern, account=account, tax_code=tax_code,
                direction=direction, contact=contact, note=note)
    store.IMPORT_RULES.append({
        'pattern': rule.pattern, 'direction': rule.direction,
        'account': rule.account, 'tax_code': rule.tax_code,
        'contact': rule.contact, 'review': 'no', 'note': rule.note})
    return rule
