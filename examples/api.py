"""Reference HTTP service for the settlement tab.

The handlers below are plain functions that take primitives and return
JSON-ready dicts, so they can be tested without a web server and wired into
whatever framework the app already uses. FastAPI wiring is at the bottom as
one worked example; swap it for Django, Flask or Starlette without touching
anything above it.

Two things this file is really about:

  Decimal never becomes a float. jsonable() renders money as a string. A
  float that arrives back as 0.1 + 0.2 is a trial balance that will not
  balance, and the failure surfaces a quarter later inside a BAS.

  Engine exceptions carry messages written for people. ApiError passes them
  through rather than replacing them with "Bad Request", because the engine
  already knows how to say "Kim Painting has not quoted an ABN".
"""
from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

from accounting import bankimport, bankrules, bankstatement, calendar_au as cal
from accounting import config, contacts as contacts_mod, jobs as jobs_mod
from accounting import ledger, lodge, lodgements, reports as rp
from accounting import transactions as tx
from accounting.periods import fy_ending, resolve_period


class ApiError(Exception):
    """An engine error on its way to an HTTP response."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def guard(function, *args, **kwargs):
    """Run an engine call, turning its exceptions into ApiError."""
    try:
        return function(*args, **kwargs)
    except (tx.TransactionError, ledger.LedgerError, ValueError) as exc:
        raise ApiError(str(exc), 422) from exc
    except (KeyError, LookupError) as exc:
        raise ApiError(str(exc).strip("'\""), 404) from exc


# ------------------------------------------------------------ serialisation

def jsonable(value):
    """Render engine objects as JSON-safe values.

    Decimal becomes a string on purpose. Every consumer of this API - the
    settlement tab included - should format from the string, never parse it
    into a float.
    """
    if isinstance(value, Decimal):
        return f'{value:.2f}'
    if isinstance(value, date):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out = {f.name: jsonable(getattr(value, f.name))
               for f in dataclasses.fields(value)}
        # dataclass properties carry the derived figures (net_amount, margin,
        # total) that the UI actually shows, so include them too.
        for name in dir(type(value)):
            if name.startswith('_'):
                continue
            attribute = getattr(type(value), name, None)
            if isinstance(attribute, property):
                try:
                    out[name] = jsonable(getattr(value, name))
                except Exception:
                    pass
        return out
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def _today() -> str:
    return date.today().isoformat()


# ------------------------------------------------------------- the handlers

def get_dashboard(as_at=None):
    """Everything the settlement tab shows above the fold."""
    as_at = as_at or _today()
    company = config.load()
    cash = rp.cash_position(as_at, company)
    receivables = rp.aged_receivables(as_at)

    attention = []
    for obligation in cal.overdue(as_at, company):
        attention.append({
            'severity': 'high', 'kind': obligation.kind,
            'title': f'{obligation.label} ({obligation.period})',
            'detail': f'Due {obligation.due}', 'due': obligation.due.isoformat(),
        })
    for message in rp.division_7a_warnings(as_at, company):
        attention.append({'severity': 'high', 'kind': 'DIV7A',
                          'title': 'Division 7A exposure', 'detail': message})
    for fy, gap in rp.super_shortfalls(as_at, company):
        attention.append({
            'severity': 'high', 'kind': 'SUPER',
            'title': f'FY{fy} superannuation shortfall',
            'detail': f'{gap.shortfall} missing on wages of {gap.wages}'})
    for contact in contacts_mod.all_contacts():
        if contact.abn and not contact.abn_is_valid:
            attention.append({
                'severity': 'medium', 'kind': 'ABN',
                'title': f'{contact.name}: ABN fails its checksum',
                'detail': '47% must be withheld until a valid ABN is quoted'})
        elif contact.type == contacts_mod.SUBCONTRACTOR and not contact.abn:
            attention.append({
                'severity': 'medium', 'kind': 'ABN',
                'title': f'{contact.name} has no ABN on file',
                'detail': 'The TPAR will be incomplete without it'})
    if cash.available < Decimal('0'):
        attention.append({
            'severity': 'high', 'kind': 'CASH',
            'title': 'Holding less than what is owed to the ATO',
            'detail': f'Short by {-cash.available}'})

    return {
        'as_at': as_at,
        'company': {'name': company.name, 'abn': company.abn,
                    'gst_basis': company.gst_basis},
        'cash': jsonable(cash),
        'owed_to_you': jsonable(receivables.total),
        'attention': attention,
        'upcoming': [jsonable_obligation(o, as_at)
                     for o in cal.upcoming(as_at, 90, company)],
    }


def jsonable_obligation(obligation, as_at):
    return {'kind': obligation.kind, 'label': obligation.label,
            'period': obligation.period, 'due': obligation.due.isoformat(),
            'days_out': obligation.days_out(as_at),
            'status': obligation.status(as_at), 'note': obligation.note}


def get_report(name, period=None, start=None, end=None, fy=None):
    """One entry point for every report the tab renders."""
    end = end or _today()
    if period:
        start, end, _ = resolve_period(period)
        start, end = start.isoformat(), end.isoformat()
    handlers = {
        'pl': lambda: rp.profit_and_loss(start or f'{fy_ending(end) - 1}-07-01', end),
        'balance-sheet': lambda: rp.balance_sheet(end),
        'trial-balance': lambda: [
            {'code': a.code, 'name': a.name, 'debit': jsonable(d),
             'credit': jsonable(c)} for a, d, c in ledger.trial_balance(end)],
        'bas': lambda: rp.bas(start or f'{fy_ending(end) - 1}-07-01', end),
        'tpar': lambda: rp.tpar(fy or fy_ending(end)),
        'cashflow': lambda: rp.cashflow(start or f'{fy_ending(end) - 1}-07-01', end),
        'receivables': lambda: rp.aged_receivables(end),
        'payables': lambda: rp.aged_payables(end),
        'jobs': lambda: rp.job_results(start, end),
        'cash': lambda: rp.cash_position(end),
        'tax': lambda: rp.tax_estimate(fy or fy_ending(end)),
        'loans': lambda: rp.director_loans(end),
        'super': lambda: rp.super_obligations(end),
    }
    if name not in handlers:
        raise ApiError(f'unknown report {name!r}; try: '
                       f'{", ".join(sorted(handlers))}', 404)
    return jsonable(guard(handlers[name]))


def get_lodgement_pack(kind, period=None, start=None, end=None, fy=None):
    """The figures to type into an ATO form, field by field."""
    end = end or _today()
    if period:
        start, end, _ = resolve_period(period)
        start, end = start.isoformat(), end.isoformat()
    builders = {
        'bas': lambda: lodge.bas_pack(start, end),
        'tpar': lambda: lodge.tpar_pack(fy or fy_ending(end)),
        'sgc': lambda: lodge.sgc_pack(end),
        'stp': lambda: lodge.stp_pack(fy or fy_ending(end)),
    }
    if kind not in builders:
        raise ApiError(f'unknown lodgement {kind!r}', 404)
    return jsonable(guard(builders[kind]))


# ------------------------------------------------------- bank statements

def preview_statement(file_path):
    """Parse an uploaded statement and propose a coding for every line.

    Nothing is written. The response is what the review queue renders; the
    client sends back the fingerprints it wants posted.
    """
    try:
        statement = bankstatement.parse_file(file_path)
    except bankstatement.StatementError as exc:
        # The parser refuses a statement it cannot reconcile rather than
        # importing plausible-looking wrong numbers, so this is a real 422.
        raise ApiError(str(exc), 422) from exc

    company = config.load()
    proposals = bankimport.propose(statement, company)
    return {
        'statement': {
            'account': statement.account,
            'start': statement.start.isoformat(),
            'end': statement.end.isoformat(),
            'opening': jsonable(statement.opening),
            'closing': jsonable(statement.closing),
            'debits': jsonable(statement.debits),
            'credits': jsonable(statement.credits),
            'reconciled': True,
        },
        'lines': [{
            'fingerprint': p.fingerprint,
            'date': p.line.date.isoformat(),
            'description': p.line.description,
            'amount': jsonable(p.line.amount),
            'direction': p.line.direction,
            'account': p.account,
            'tax_code': p.tax_code,
            'status': p.status,
            'note': p.note,
        } for p in proposals],
        'summary': {k: v for k, v in
                    bankimport.summarise(proposals)['by_status'].items()},
    }


def commit_statement(file_path, decisions):
    """Post the lines the user accepted.

    `decisions` maps fingerprint to either True (take the proposal as it
    stands) or {'account': ..., 'tax_code': ..., 'contact': ..., 'job': ...}.
    Anything not named is left alone. Re-posting an already-imported line is
    a no-op, because the fingerprint is what stops a double import.
    """
    statement = bankstatement.parse_file(file_path)
    company = config.load()
    posted, skipped = [], []
    for proposal in bankimport.propose(statement, company):
        decision = decisions.get(proposal.fingerprint)
        if not decision or proposal.status == bankimport.IMPORTED:
            skipped.append(proposal.fingerprint)
            continue
        overrides = decision if isinstance(decision, dict) else {}
        entry_id = guard(
            bankimport.post, proposal, company,
            override_account=overrides.get('account'),
            override_tax_code=overrides.get('tax_code'),
            contact=overrides.get('contact', ''),
            job=overrides.get('job', ''))
        posted.append({'fingerprint': proposal.fingerprint, 'entry_id': entry_id})
    return {'posted': posted, 'skipped': skipped, 'count': len(posted)}


def add_rule(pattern, account, tax_code='', direction='any', contact='', note=''):
    """Teach the importer a merchant so it stops asking."""
    rule = guard(bankrules.add, pattern, account, tax_code=tax_code,
                 direction=direction, contact=contact, note=note)
    return jsonable(rule)


# ------------------------------------------------------------- write paths

def create_invoice(payload):
    """lines: [{account, amount_ex, description, tax_code, job}, ...]"""
    lines = [tx.DocLine(account=l['account'], amount_ex=l['amount_ex'],
                        description=l.get('description', ''),
                        tax_code=l.get('tax_code', ''), job=l.get('job', ''))
             for l in payload['lines']]
    result = guard(tx.create_invoice, payload['date'], payload['contact'], lines,
                   due_days=payload.get('due_days'), job=payload.get('job', ''),
                   memo=payload.get('memo', ''))
    return jsonable(result)


def record_receipt(payload):
    return jsonable(guard(tx.record_receipt, payload['date'], payload['doc_id'],
                          payload.get('amount')))


def spend_money(payload):
    return jsonable(guard(
        tx.spend_money, payload['date'], payload['account'],
        payload['amount_incl'], contact=payload.get('contact', ''),
        description=payload.get('description', ''),
        tax_code=payload.get('tax_code', ''), job=payload.get('job', ''),
        bank=payload.get('bank')))


def pay_wages(payload):
    return jsonable(guard(
        tx.pay_wages, payload['date'], payload['director'], payload['gross'],
        payload['payg_withheld'], super_amount=payload.get('super_amount')))


def record_lodged(payload):
    """Mark something as filed so the calendar stops reporting it."""
    item = guard(lodgements.record, payload['kind'], payload['period'],
                 payload['lodged_date'], reference=payload.get('reference', ''),
                 amount=payload.get('amount', ''),
                 lodged_by=payload.get('lodged_by', ''),
                 notes=payload.get('notes', ''))
    return jsonable(item)


def add_contact(payload):
    """The ABN checksum is validated here, so a typo never reaches the TPAR."""
    contact = guard(contacts_mod.add, payload['name'], payload.get('type', 'other'),
                    abn=payload.get('abn', ''),
                    gst_registered=payload.get('gst_registered', False),
                    email=payload.get('email', ''), phone=payload.get('phone', ''),
                    address=payload.get('address', ''),
                    notes=payload.get('notes', ''))
    return jsonable(contact)


def update_contact(reference, changes):
    return jsonable(guard(contacts_mod.update, reference, **changes))


def add_job(payload):
    return jsonable(guard(jobs_mod.add, payload['name'],
                          contact_id=payload.get('contact_id', ''),
                          address=payload.get('address', ''),
                          quoted_incl=payload.get('quoted_incl', '')))


# --------------------------------------------------------- FastAPI wiring
# One worked example. Everything above is framework-free.

def build_app(require_director):
    """`require_director` is your app's dependency that authorises the caller.

    The settlement tab shows the company's full financial position, so it
    belongs behind a director-level check rather than any logged-in user.
    """
    from fastapi import Depends, FastAPI, HTTPException, UploadFile
    from fastapi.responses import JSONResponse

    app = FastAPI(title='Accounting')

    @app.exception_handler(ApiError)
    async def on_api_error(request, exc):
        # The engine's own wording, not a generic status phrase.
        return JSONResponse({'error': str(exc)}, status_code=exc.status)

    @app.get('/accounting/dashboard')
    def dashboard(as_at: str = None, _=Depends(require_director)):
        return get_dashboard(as_at)

    @app.get('/accounting/reports/{name}')
    def report(name: str, period: str = None, start: str = None,
               end: str = None, fy: int = None, _=Depends(require_director)):
        return get_report(name, period, start, end, fy)

    @app.get('/accounting/lodge/{kind}')
    def lodgement_pack(kind: str, period: str = None, start: str = None,
                       end: str = None, fy: int = None,
                       _=Depends(require_director)):
        return get_lodgement_pack(kind, period, start, end, fy)

    @app.get('/accounting/calendar')
    def calendar(as_at: str = None, days: int = 120,
                 _=Depends(require_director)):
        as_at = as_at or _today()
        company = config.load()
        return {
            'overdue': [jsonable_obligation(o, as_at)
                        for o in cal.overdue(as_at, company)],
            'upcoming': [jsonable_obligation(o, as_at)
                         for o in cal.upcoming(as_at, days, company)],
        }

    @app.post('/accounting/statements/preview')
    async def statement_preview(file: UploadFile,
                                _=Depends(require_director)):
        path = await _save_upload(file)
        try:
            return preview_statement(path)
        finally:
            _discard(path)   # bank statements do not linger on disk

    @app.post('/accounting/statements/commit')
    async def statement_commit(file: UploadFile, decisions: dict,
                               _=Depends(require_director)):
        path = await _save_upload(file)
        try:
            return commit_statement(path, decisions)
        finally:
            _discard(path)

    @app.post('/accounting/invoices')
    def invoice(payload: dict, _=Depends(require_director)):
        return create_invoice(payload)

    @app.post('/accounting/receipts')
    def receipt(payload: dict, _=Depends(require_director)):
        return record_receipt(payload)

    @app.post('/accounting/spend')
    def spend(payload: dict, _=Depends(require_director)):
        return spend_money(payload)

    @app.post('/accounting/wages')
    def wages(payload: dict, _=Depends(require_director)):
        return pay_wages(payload)

    @app.post('/accounting/lodgements')
    def lodged(payload: dict, _=Depends(require_director)):
        return record_lodged(payload)

    @app.post('/accounting/contacts')
    def contact(payload: dict, _=Depends(require_director)):
        return add_contact(payload)

    @app.post('/accounting/rules')
    def rule(payload: dict, _=Depends(require_director)):
        return add_rule(payload['pattern'], payload['account'],
                        tax_code=payload.get('tax_code', ''),
                        direction=payload.get('direction', 'any'),
                        contact=payload.get('contact', ''),
                        note=payload.get('note', ''))

    return app


async def _save_upload(file) -> str:
    import tempfile
    suffix = '.pdf' if (file.filename or '').lower().endswith('.pdf') else ''
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handle.write(await file.read())
    handle.close()
    return handle.name


def _discard(path) -> None:
    import os
    try:
        os.unlink(path)
    except OSError:
        pass
