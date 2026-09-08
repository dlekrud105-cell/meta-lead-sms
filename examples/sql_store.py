"""A SQL-backed replacement for accounting.store.Table.

The engine talks to storage through six methods. Implement those with the
same signatures and everything above them - the ledger, every report, the
bank import - works unchanged. This module does exactly that, against SQLite
(so it can be tested without a server) and PostgreSQL (for production).

The one contract that matters: the CSV Table hands out dicts of strings, and
the code above parses them. So this returns strings too, converting from the
typed columns on the way out. Storage gets proper DECIMAL and DATE columns;
the engine keeps seeing exactly what it saw before.

Usage:

    from accounting import store
    from examples.sql_store import bind

    bind(psycopg.connect(DSN), company_id=1)   # patches the module tables

Then use the accounting package normally.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

TEXT, DECIMAL, DATE, BOOL, INT = 'text', 'decimal', 'date', 'bool', 'int'

# Column types per table. Anything not listed is TEXT.
COLUMN_TYPES = {
    'journal_line': {'date': DATE, 'line_no': INT,
                     'debit': DECIMAL, 'credit': DECIMAL},
    'contact': {'abn_quoted': BOOL, 'gst_registered': BOOL},
    'document': {'date': DATE, 'due_date': DATE, 'total_incl': DECIMAL,
                 'gst': DECIMAL, 'withheld': DECIMAL},
    'job': {'quoted_incl': DECIMAL, 'started': DATE, 'completed': DATE},
    'bank_line': {'date': DATE, 'amount': DECIMAL, 'imported_on': DATE},
    'import_rule': {'review': BOOL},
    'lodgement': {'lodged_date': DATE, 'amount': DECIMAL},
    'finance_schedule': {'principal': DECIMAL, 'annual_rate': TEXT,
                         'months': INT, 'balloon': DECIMAL,
                         'payment': DECIMAL, 'first_due': DATE},
    'account': {'contra': BOOL, 'deductible': BOOL, 'tpar': BOOL},
}

TRUTHY = ('1', 'true', 'yes', 'y', 't')


# --------------------------------------------------------------- conversion

def to_db(value, kind):
    """Engine string -> database value."""
    if value is None or value == '':
        return None if kind != TEXT else ''
    if kind == DECIMAL:
        return str(Decimal(str(value)))
    if kind == DATE:
        return str(value)[:10]
    if kind == BOOL:
        return str(value).strip().lower() in TRUTHY
    if kind == INT:
        return int(value)
    return str(value)


def from_db(value, kind):
    """Database value -> the string the engine expects."""
    if value is None:
        return ''
    if kind == DECIMAL:
        return f'{Decimal(str(value)):.2f}'
    if kind == DATE:
        return value.isoformat() if isinstance(value, date) else str(value)[:10]
    if kind == BOOL:
        truthy = value if isinstance(value, bool) else str(value).lower() in TRUTHY
        return 'yes' if truthy else 'no'
    return str(value)


# -------------------------------------------------------------------- table

class SqlTable:
    """Same six methods as accounting.store.Table, backed by SQL."""

    def __init__(self, connection, table, fields, company_id=1,
                 append_only=False, order_by=None):
        self.connection = connection
        self.table = table
        self.fields = list(fields)
        self.company_id = company_id
        self.append_only = append_only
        self.order_by = order_by or 'rowid'
        self.types = COLUMN_TYPES.get(table, {})
        self.placeholder = '?' if isinstance(connection, sqlite3.Connection) else '%s'

    # -- helpers ----------------------------------------------------------
    def _kind(self, field):
        return self.types.get(field, TEXT)

    def _marks(self, count):
        return ', '.join([self.placeholder] * count)

    @contextmanager
    def _cursor(self):
        cursor = self.connection.cursor()
        try:
            yield cursor
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            cursor.close()

    # -- the six methods --------------------------------------------------
    def read(self) -> list:
        columns = ', '.join(self.fields)
        sql = (f'SELECT {columns} FROM {self.table} '
               f'WHERE company_id = {self.placeholder} ORDER BY {self.order_by}')
        with self._cursor() as cursor:
            cursor.execute(sql, (self.company_id,))
            rows = cursor.fetchall()
        return [
            {field: from_db(value, self._kind(field))
             for field, value in zip(self.fields, row)}
            for row in rows
        ]

    def append(self, row: dict) -> None:
        self.append_many([row])

    def append_many(self, rows: list) -> None:
        if not rows:
            return
        for row in rows:
            unknown = set(row) - set(self.fields)
            if unknown:
                raise KeyError(f'{self.table}: unknown column(s) {sorted(unknown)}')
        columns = ['company_id'] + self.fields
        sql = (f'INSERT INTO {self.table} ({", ".join(columns)}) '
               f'VALUES ({self._marks(len(columns))})')
        payload = [
            tuple([self.company_id]
                  + [to_db(row.get(f, ''), self._kind(f)) for f in self.fields])
            for row in rows
        ]
        with self._cursor() as cursor:
            cursor.executemany(sql, payload)

    def write_all(self, rows: list) -> None:
        """Replace this company's rows wholesale.

        Used by the few places that edit a register - updating a contact,
        removing a lodgement. Refused on the journal: history is corrected
        with a reversing entry, never by rewriting it.
        """
        if self.append_only:
            raise RuntimeError(
                f'{self.table} is append-only; post a reversing entry instead '
                'of rewriting history')
        with self._cursor() as cursor:
            cursor.execute(
                f'DELETE FROM {self.table} WHERE company_id = {self.placeholder}',
                (self.company_id,))
        self.append_many(rows)

    def find(self, **criteria):
        for row in self.read():
            if all(str(row.get(k, '')) == str(v) for k, v in criteria.items()):
                return row
        return None

    def next_sequence(self, field: str, prefix: str, width: int = 4) -> str:
        """Next id in the series, allocated atomically.

        The CSV version scans for the high-water mark, which two concurrent
        requests can read at the same time and both use. Here the counter is
        a row, incremented under the row lock the UPDATE takes.
        """
        with self._cursor() as cursor:
            cursor.execute(
                f'INSERT INTO id_sequence (company_id, prefix, next_value, width) '
                f'VALUES ({self._marks(4)}) '
                f'ON CONFLICT (company_id, prefix) DO NOTHING',
                (self.company_id, prefix, 1, width))
            cursor.execute(
                f'UPDATE id_sequence SET next_value = next_value + 1 '
                f'WHERE company_id = {self.placeholder} AND prefix = {self.placeholder}',
                (self.company_id, prefix))
            cursor.execute(
                f'SELECT next_value - 1 FROM id_sequence '
                f'WHERE company_id = {self.placeholder} AND prefix = {self.placeholder}',
                (self.company_id, prefix))
            value = cursor.fetchone()[0]
        return f'{prefix}{value:0{width}d}'


# ------------------------------------------------------------------ company

COMPANY_COLUMNS = [
    'name', 'trading_name', 'abn', 'acn', 'state', 'address', 'registered_date',
    'gst_registered', 'gst_cycle', 'gst_basis', 'uses_tax_agent', 'tax_agent',
    'base_rate_entity', 'reports_tpar', 'default_bank', 'savings_bank',
    'invoice_terms_days', 'rates', 'directors',
]
COMPANY_JSON = ('rates', 'directors')
COMPANY_BOOL = ('gst_registered', 'uses_tax_agent', 'base_rate_entity',
                'reports_tpar')


class SqlCompanyStore:
    """Replaces store.read_company / store.write_company."""

    def __init__(self, connection, company_id=1):
        self.connection = connection
        self.company_id = company_id
        self.placeholder = '?' if isinstance(connection, sqlite3.Connection) else '%s'

    def read(self) -> dict:
        import json
        columns = ', '.join(COMPANY_COLUMNS)
        cursor = self.connection.cursor()
        cursor.execute(
            f'SELECT {columns} FROM company WHERE company_id = {self.placeholder}',
            (self.company_id,))
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            return {}
        config = {}
        for field, value in zip(COMPANY_COLUMNS, row):
            if field in COMPANY_JSON:
                config[field] = json.loads(value) if isinstance(value, str) else (value or {})
            elif field in COMPANY_BOOL:
                config[field] = bool(value) if isinstance(value, (bool, int)) \
                    else str(value).lower() in TRUTHY
            elif field == 'registered_date':
                config[field] = from_db(value, DATE)
            elif field == 'invoice_terms_days':
                config[field] = int(value or 14)
            else:
                config[field] = '' if value is None else str(value)
        return config

    def write(self, config: dict) -> None:
        import json
        values = []
        for field in COMPANY_COLUMNS:
            value = config.get(field)
            if field in COMPANY_JSON:
                values.append(json.dumps(value or ({} if field == 'rates' else [])))
            elif field in COMPANY_BOOL:
                values.append(bool(value))
            elif field == 'registered_date':
                values.append(to_db(value, DATE))
            elif field == 'invoice_terms_days':
                values.append(int(value or 14))
            else:
                values.append('' if value is None else str(value))
        assignments = ', '.join(f'{c} = {self.placeholder}' for c in COMPANY_COLUMNS)
        cursor = self.connection.cursor()
        cursor.execute(
            f'SELECT 1 FROM company WHERE company_id = {self.placeholder}',
            (self.company_id,))
        exists = cursor.fetchone() is not None
        if exists:
            cursor.execute(
                f'UPDATE company SET {assignments} '
                f'WHERE company_id = {self.placeholder}',
                (*values, self.company_id))
        else:
            columns = ', '.join(['company_id'] + COMPANY_COLUMNS)
            cursor.execute(
                f'INSERT INTO company ({columns}) '
                f'VALUES ({", ".join([self.placeholder] * (len(values) + 1))})',
                (self.company_id, *values))
        self.connection.commit()
        cursor.close()


# --------------------------------------------------------------- the binding

TABLE_MAP = {
    # engine attribute -> (sql table, append_only, order by)
    'JOURNAL': ('journal_line', True, 'entry_id, line_no'),
    'CONTACTS': ('contact', False, 'contact_id'),
    'DOCUMENTS': ('document', False, 'doc_id'),
    'JOBS': ('job', False, 'job_id'),
    'LODGEMENTS': ('lodgement', False, 'lodged_date'),
    'IMPORT_RULES': ('import_rule', False, 'sort_order, rule_id'),
    'BANK_LINES': ('bank_line', False, 'date, fingerprint'),
    'ACCOUNTS_EXPORT': ('account', False, 'code'),
}


def bind(connection, company_id=1):
    """Point the accounting package at a SQL database instead of CSV files.

    Call once at startup, before anything imports data. Returns the company
    store so the caller can seed a company row if there isn't one.
    """
    from accounting import amortise, store

    for attribute, (table, append_only, order_by) in TABLE_MAP.items():
        original = getattr(store, attribute)
        setattr(store, attribute, SqlTable(
            connection, table, original.fields, company_id,
            append_only=append_only, order_by=order_by))

    amortise.SCHEDULES = SqlTable(
        connection, 'finance_schedule', amortise.SCHEDULES.fields, company_id,
        order_by='account')

    company = SqlCompanyStore(connection, company_id)
    store.read_company = company.read
    store.write_company = company.write
    return company
