"""A read-only web dashboard, mounted at /accounting in the Flask app.

It is gated behind ACCOUNTING_TOKEN and never registers without one, because
the app it attaches to is publicly reachable. Nothing here writes to the
books: entries are made from the CLI, where they can be reviewed before they
are posted.

Note on hosting: on Heroku, Railway and similar platforms the filesystem is
ephemeral, so point ACCOUNTING_DATA_DIR at a mounted volume or keep the books
on a machine you control and treat this dashboard as a read-only mirror.
"""
from __future__ import annotations

import hmac
import os
from datetime import date
from html import escape

from flask import Blueprint, Response, request

from . import calendar_au as cal
from . import config
from . import reports as rp
from .money import ZERO, fmt

blueprint = Blueprint('accounting', __name__, url_prefix='/accounting')

STYLE = """
:root { color-scheme: light dark; --bg:#fbfbfa; --fg:#1a1a19; --muted:#6b6b68;
  --line:#e3e3e0; --card:#fff; --bad:#b3261e; --good:#1a7f45; --warn:#8a5a00; }
@media (prefers-color-scheme: dark) { :root { --bg:#17181a; --fg:#ececeb;
  --muted:#9b9b98; --line:#2e3033; --card:#1f2124; --bad:#ff8a80;
  --good:#7ee2a8; --warn:#f0c674; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55
  -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.wrap { max-width:1040px; margin:0 auto; padding:32px 20px 64px; }
h1 { font-size:22px; margin:0 0 4px; }
h2 { font-size:15px; text-transform:uppercase; letter-spacing:.06em;
  color:var(--muted); margin:32px 0 10px; font-weight:600; }
.sub { color:var(--muted); margin:0 0 8px; font-size:13px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:14px 16px; margin-bottom:12px; overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:14px; }
th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line);
  white-space:nowrap; }
th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase;
  letter-spacing:.04em; }
tr:last-child td { border-bottom:none; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
.bad { color:var(--bad); } .good { color:var(--good); } .warn { color:var(--warn); }
.big { font-size:26px; font-variant-numeric:tabular-nums; }
.grid { display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); }
ul.issues { margin:0; padding-left:20px; } ul.issues li { margin:6px 0; }
"""


def _token_ok() -> bool:
    expected = os.environ.get('ACCOUNTING_TOKEN', '')
    if not expected:
        return False
    supplied = (request.headers.get('X-Accounting-Token')
                or request.args.get('token', ''))
    return hmac.compare_digest(supplied, expected)


def _page(title: str, body: str) -> Response:
    html = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(title)}</title><style>{STYLE}</style></head>'
            f'<body><div class="wrap">{body}</div></body></html>')
    return Response(html, mimetype='text/html')


def _table(headers, rows, numeric=()) -> str:
    if not rows:
        return '<p class="sub">Nothing to show.</p>'
    head = ''.join(
        f'<th class="num">{escape(str(h))}</th>' if i in numeric
        else f'<th>{escape(str(h))}</th>' for i, h in enumerate(headers))
    body = ''
    for row in rows:
        cells = ''.join(
            f'<td class="num">{escape(str(c))}</td>' if i in numeric
            else f'<td>{escape(str(c))}</td>' for i, c in enumerate(row))
        body += f'<tr>{cells}</tr>'
    return f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _stat(label: str, value: str, tone: str = '') -> str:
    cls = f' {tone}' if tone else ''
    return (f'<div class="card"><div class="sub">{escape(label)}</div>'
            f'<div class="big{cls}">{escape(value)}</div></div>')


@blueprint.route('/')
def dashboard():
    if not _token_ok():
        return Response('Forbidden', status=403, mimetype='text/plain')

    as_at = request.args.get('as_at') or date.today().isoformat()
    company = config.load()

    cash = rp.cash_position(as_at, company)
    quarter_bas = rp.bas(*_current_quarter(as_at))
    overdue = cal.overdue(as_at, company) if company.registered_date else []
    upcoming = cal.upcoming(as_at, 90, company) if company.registered_date else []
    aged = rp.aged_receivables(as_at)
    loans = rp.division_7a_warnings(as_at, company)
    jobs = rp.job_results()

    parts = [
        f'<h1>{escape(company.name)}</h1>',
        f'<p class="sub">Books as at {escape(as_at)} &middot; read only</p>',
        '<div class="grid">',
        _stat('In the bank', fmt(cash.bank)),
        _stat('Owed to ATO and super funds', fmt(cash.set_aside), 'warn'),
        _stat('Safe to spend', fmt(cash.available),
              'bad' if cash.available < ZERO else 'good'),
        _stat('Owed to you', fmt(aged.total)),
        '</div>',
    ]

    if overdue:
        parts.append('<h2>Overdue</h2><div class="card">')
        parts.append(_table(
            ['Due', 'Kind', 'Period', 'Obligation'],
            [[o.due.isoformat(), o.kind, o.period, o.label] for o in overdue]))
        parts.append('</div>')

    if loans:
        parts.append('<h2>Division 7A</h2><div class="card"><ul class="issues">')
        parts += [f'<li class="bad">{escape(w)}</li>' for w in loans]
        parts.append('</ul></div>')

    parts.append(f'<h2>BAS &mdash; {escape(quarter_bas.label)}</h2><div class="card">')
    parts.append(_table(
        ['Label', 'Description', 'Amount'],
        [['G1', 'Total sales (incl GST)', fmt(quarter_bas.g1)],
         ['G10', 'Capital purchases (incl GST)', fmt(quarter_bas.g10)],
         ['G11', 'Non-capital purchases (incl GST)', fmt(quarter_bas.g11)],
         ['1A', 'GST on sales', fmt(quarter_bas.gst_on_sales)],
         ['1B', 'GST on purchases', fmt(quarter_bas.gst_on_purchases)],
         ['W1', 'Wages', fmt(quarter_bas.w1)],
         ['W2', 'PAYG withheld from wages', fmt(quarter_bas.w2)],
         ['W4', 'Withheld where no ABN quoted', fmt(quarter_bas.w4)],
         ['7', 'Net amount', fmt(quarter_bas.net_amount)]],
        numeric={2}))
    parts.append(f'<p class="sub">Due {quarter_bas.due}</p></div>')

    parts.append('<h2>Coming up</h2><div class="card">')
    parts.append(_table(
        ['Due', 'In', 'Kind', 'Obligation'],
        [[o.due.isoformat(), f'{o.days_out(as_at)}d', o.kind, o.label]
         for o in upcoming]))
    parts.append('</div>')

    parts.append('<h2>Money owed to you</h2><div class="card">')
    parts.append(_table(
        ['Invoice', 'Customer', 'Due', 'Amount', 'Age'],
        [[r.doc_id, r.contact, r.due_date.isoformat(), fmt(r.amount), r.bucket]
         for r in aged.rows], numeric={3}))
    parts.append('</div>')

    parts.append('<h2>Job margins</h2><div class="card">')
    parts.append(_table(
        ['Job', 'Income', 'Cost', 'Margin', 'Margin %'],
        [[j.job.name, fmt(j.income), fmt(j.cost), fmt(j.margin), f'{j.margin_pct}%']
         for j in jobs], numeric={1, 2, 3, 4}))
    parts.append('</div>')

    return _page(f'{company.name} - books', ''.join(parts))


def _current_quarter(as_at):
    from .periods import quarter_of
    quarter = quarter_of(as_at)
    return quarter.start, quarter.end


def register(app) -> bool:
    """Attach the dashboard, but only when a token has been configured."""
    if not os.environ.get('ACCOUNTING_TOKEN'):
        return False
    app.register_blueprint(blueprint)
    return True
