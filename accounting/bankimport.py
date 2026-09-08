"""Turn a parsed bank statement into proposed journal entries.

Nothing posts without being seen first. Every line gets a proposal, lines the
rules are not confident about are held for review, and each imported line is
fingerprinted so re-importing an overlapping statement cannot double up.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

from . import accounts as coa
from . import bankrules
from . import config
from . import contacts as contacts_mod
from . import ledger
from . import store
from . import taxcodes
from . import transactions as tx
from .money import ZERO, money

UNMATCHED = 'unmatched'
READY = 'ready'
REVIEW = 'review'
IMPORTED = 'imported'

SOURCE = tx.BANK


@dataclass
class Proposal:
    line: object              # BankLine
    account: str = ''
    tax_code: str = 'NT'
    contact: str = ''
    status: str = UNMATCHED
    note: str = ''
    entry_id: str = ''

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.line)

    @property
    def amount_ex(self):
        rate = taxcodes.get(self.tax_code).rate
        return money(self.line.amount / (1 + rate))

    @property
    def gst(self):
        return money(self.line.amount - self.amount_ex)


def fingerprint(line) -> str:
    """Stable id for a bank line: date, amount, direction, balance, text.

    The running balance is included so two genuinely identical purchases on the
    same day still get different fingerprints.
    """
    raw = (f'{line.date.isoformat()}|{line.amount}|{line.direction}|'
           f'{line.balance}|{line.description}')
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def already_imported() -> dict:
    return {row['fingerprint']: row for row in store.BANK_LINES.read()}


def propose(statement, company=None) -> list:
    """Work out what each bank line should become."""
    company = company or config.load()
    seen = already_imported()
    proposals = []
    for line in statement.lines:
        proposal = Proposal(line=line)
        existing = seen.get(fingerprint(line))
        if existing:
            proposal.status = IMPORTED
            proposal.account = existing['account']
            proposal.tax_code = existing['tax_code'] or 'NT'
            proposal.entry_id = existing['entry_id']
            proposal.note = 'already imported'
            proposals.append(proposal)
            continue

        rule = bankrules.match(line.description, line.direction)
        if rule is None or not rule.account:
            proposal.status = REVIEW if rule else UNMATCHED
            proposal.note = rule.note if rule else 'no rule matches this line'
            proposals.append(proposal)
            continue

        account = coa.get(rule.account)
        proposal.account = account.code
        proposal.tax_code = (rule.tax_code or account.tax_code).upper()
        proposal.contact = rule.contact
        proposal.note = rule.note
        proposal.status = REVIEW if rule.review else READY
        proposals.append(proposal)
    return proposals


def _bank_account(company) -> str:
    return company.default_bank


def post(proposal: Proposal, company=None, bank=None, override_account=None,
         override_tax_code=None, contact='', job='') -> str:
    """Post one bank line to the ledger and remember that it was imported."""
    company = company or config.load()
    bank_code = bank or _bank_account(company)
    account_code = override_account or proposal.account
    if not account_code:
        raise ValueError('nothing to post: no account chosen for this line')
    account = coa.get(account_code)
    tax_code = (override_tax_code or proposal.tax_code or account.tax_code).upper()
    rate = taxcodes.get(tax_code).rate
    amount = proposal.line.amount
    amount_ex = money(amount / (1 + rate))
    gst = money(amount - amount_ex)

    contact_name = contact or proposal.contact
    contact_id = ''
    if contact_name:
        found = contacts_mod.find(contact_name)
        if found is None:
            # Create the payee rather than dropping it. A payment coded to a
            # TPAR account with no payee on file is a hole in the annual
            # report, so it has to become a contact even if details are thin.
            if account.tpar:
                kind = contacts_mod.SUBCONTRACTOR
            elif proposal.line.direction == 'credit':
                kind = contacts_mod.CUSTOMER
            else:
                kind = contacts_mod.SUPPLIER
            found = contacts_mod.add(contact_name, kind,
                                     notes='created from a bank import')
        contact_id = found.contact_id

    description = proposal.line.description[:120]
    lines = []
    if proposal.line.direction == 'debit':
        lines.append(ledger.Line(account=account.code, debit=amount_ex,
                                 description=description, tax_code=tax_code,
                                 contact=contact_id, job=job))
        if gst != ZERO:
            lines.append(ledger.debit(coa.first_with_role('gst_paid').code, gst,
                                      description='GST on purchases',
                                      contact=contact_id))
        lines.append(ledger.credit(bank_code, amount, description=description,
                                   contact=contact_id, job=job))
    else:
        lines.append(ledger.debit(bank_code, amount, description=description,
                                  contact=contact_id, job=job))
        lines.append(ledger.Line(account=account.code, credit=amount_ex,
                                 description=description, tax_code=tax_code,
                                 contact=contact_id, job=job))
        if gst != ZERO:
            lines.append(ledger.credit(coa.first_with_role('gst_collected').code,
                                       gst, description='GST on sales',
                                       contact=contact_id))

    entry_id = ledger.post(ledger.Entry(
        date=proposal.line.date, memo=description, source=SOURCE,
        doc_ref=proposal.fingerprint, lines=lines))

    store.BANK_LINES.append({
        'fingerprint': proposal.fingerprint,
        'date': proposal.line.date.isoformat(),
        'description': description,
        'amount': f'{amount:.2f}',
        'direction': proposal.line.direction,
        'account': account.code, 'tax_code': tax_code,
        'contact': contact_id, 'entry_id': entry_id,
        'imported_on': date.today().isoformat(),
    })
    proposal.entry_id = entry_id
    proposal.status = IMPORTED
    return entry_id


def summarise(proposals: list) -> dict:
    """Totals by status and by account, for the preview."""
    by_status, by_account = {}, {}
    for proposal in proposals:
        by_status[proposal.status] = by_status.get(proposal.status, 0) + 1
        if proposal.account:
            key = (proposal.account, proposal.line.direction)
            by_account[key] = money(
                by_account.get(key, ZERO) + proposal.line.amount)
    return {'by_status': by_status, 'by_account': by_account}
