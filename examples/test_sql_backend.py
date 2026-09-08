"""Run the engine's whole test suite against SQL instead of CSV.

The claim in the handover is that replacing accounting.store with something
that implements the same six methods leaves everything above it working. This
is that claim, executed: the same 148 tests, the same assertions, a SQLite
database underneath instead of CSV files.

    python3 examples/test_sql_backend.py

SQLite is used because it needs no server. The adapter is the same one that
runs on PostgreSQL; only the placeholder style and the DDL differ.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from accounting import accounts as coa, amortise, config, store  # noqa: E402
from examples.sql_store import SqlTable, bind  # noqa: E402

# SQLite mirror of examples/schema.sql. Same columns and the constraints
# SQLite can express; PostgreSQL additionally gets the append-only trigger,
# the deferred balance check and partial indexes.
SCHEMA = """
CREATE TABLE company (
    company_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
    trading_name TEXT DEFAULT '', abn TEXT DEFAULT '', acn TEXT DEFAULT '',
    state TEXT DEFAULT 'NSW', address TEXT DEFAULT '', registered_date TEXT,
    gst_registered INTEGER DEFAULT 1, gst_cycle TEXT DEFAULT 'quarterly',
    gst_basis TEXT DEFAULT 'cash' CHECK (gst_basis IN ('cash','accruals')),
    uses_tax_agent INTEGER DEFAULT 0, tax_agent TEXT DEFAULT '',
    base_rate_entity INTEGER DEFAULT 1, reports_tpar INTEGER DEFAULT 1,
    default_bank TEXT DEFAULT '1000', savings_bank TEXT DEFAULT '1010',
    invoice_terms_days INTEGER DEFAULT 14,
    rates TEXT DEFAULT '{}', directors TEXT DEFAULT '[]'
);
CREATE TABLE account (
    company_id INTEGER NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('ASSET','LIABILITY','EQUITY','INCOME','COGS','EXPENSE')),
    tax_code TEXT DEFAULT 'NT', normal_side TEXT DEFAULT '',
    role TEXT DEFAULT '', contra INTEGER DEFAULT 0,
    deductible INTEGER DEFAULT 1, tpar INTEGER DEFAULT 0, note TEXT DEFAULT '',
    PRIMARY KEY (company_id, code)
);
CREATE TABLE journal_line (
    company_id INTEGER NOT NULL, entry_id TEXT NOT NULL, line_no INTEGER NOT NULL,
    date TEXT NOT NULL, memo TEXT DEFAULT '', source TEXT DEFAULT 'JOURNAL',
    doc_ref TEXT DEFAULT '', account TEXT NOT NULL, description TEXT DEFAULT '',
    debit TEXT NOT NULL DEFAULT '0', credit TEXT NOT NULL DEFAULT '0',
    tax_code TEXT DEFAULT 'NT' CHECK (tax_code IN ('GST','CAP','FRE','INP','NT')),
    contact TEXT DEFAULT '', job TEXT DEFAULT '',
    PRIMARY KEY (company_id, entry_id, line_no),
    FOREIGN KEY (company_id, account) REFERENCES account (company_id, code),
    CHECK ((CAST(debit AS REAL) = 0) <> (CAST(credit AS REAL) = 0))
);
CREATE INDEX journal_by_date ON journal_line (company_id, date);
CREATE INDEX journal_by_account ON journal_line (company_id, account, date);
CREATE INDEX journal_by_doc ON journal_line (company_id, doc_ref);
CREATE TABLE contact (
    company_id INTEGER NOT NULL, contact_id TEXT NOT NULL, name TEXT NOT NULL,
    type TEXT DEFAULT 'other', abn TEXT DEFAULT '', abn_quoted INTEGER DEFAULT 0,
    gst_registered INTEGER DEFAULT 0, email TEXT DEFAULT '', phone TEXT DEFAULT '',
    address TEXT DEFAULT '', notes TEXT DEFAULT '',
    PRIMARY KEY (company_id, contact_id),
    CHECK (abn = '' OR abn GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]')
);
CREATE TABLE job (
    company_id INTEGER NOT NULL, job_id TEXT NOT NULL, name TEXT NOT NULL,
    contact_id TEXT DEFAULT '', address TEXT DEFAULT '', status TEXT DEFAULT 'active',
    quoted_incl TEXT, started TEXT, completed TEXT, notes TEXT DEFAULT '',
    PRIMARY KEY (company_id, job_id)
);
CREATE TABLE document (
    company_id INTEGER NOT NULL, doc_id TEXT NOT NULL, type TEXT NOT NULL,
    date TEXT NOT NULL, due_date TEXT, contact_id TEXT DEFAULT '',
    job_id TEXT DEFAULT '', description TEXT DEFAULT '', total_incl TEXT NOT NULL,
    gst TEXT DEFAULT '0', withheld TEXT DEFAULT '0', entry_id TEXT NOT NULL,
    PRIMARY KEY (company_id, doc_id)
);
CREATE TABLE bank_line (
    company_id INTEGER NOT NULL, fingerprint TEXT NOT NULL, date TEXT NOT NULL,
    description TEXT NOT NULL, amount TEXT NOT NULL, direction TEXT NOT NULL,
    account TEXT NOT NULL, tax_code TEXT DEFAULT 'NT', contact TEXT DEFAULT '',
    entry_id TEXT NOT NULL, imported_on TEXT,
    PRIMARY KEY (company_id, fingerprint)
);
CREATE TABLE import_rule (
    rule_id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL,
    pattern TEXT NOT NULL, direction TEXT DEFAULT 'any', account TEXT NOT NULL,
    tax_code TEXT DEFAULT '', contact TEXT DEFAULT '', review INTEGER DEFAULT 0,
    note TEXT DEFAULT '', sort_order INTEGER DEFAULT 0
);
CREATE TABLE lodgement (
    company_id INTEGER NOT NULL, kind TEXT NOT NULL, period TEXT NOT NULL,
    lodged_date TEXT NOT NULL, reference TEXT DEFAULT '', amount TEXT,
    lodged_by TEXT DEFAULT '', notes TEXT DEFAULT '',
    PRIMARY KEY (company_id, kind, period)
);
CREATE TABLE finance_schedule (
    company_id INTEGER NOT NULL, account TEXT NOT NULL, principal TEXT NOT NULL,
    annual_rate TEXT NOT NULL, months INTEGER NOT NULL, balloon TEXT DEFAULT '0',
    payment TEXT NOT NULL, first_due TEXT NOT NULL, description TEXT DEFAULT '',
    PRIMARY KEY (company_id, account)
);
CREATE TABLE id_sequence (
    company_id INTEGER NOT NULL, prefix TEXT NOT NULL,
    next_value INTEGER NOT NULL DEFAULT 1, width INTEGER NOT NULL DEFAULT 4,
    PRIMARY KEY (company_id, prefix)
);
"""

COMPANY_ID = 1


def open_database() -> sqlite3.Connection:
    connection = sqlite3.connect(':memory:')
    connection.execute('PRAGMA foreign_keys = ON')
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def seed_chart(connection) -> None:
    """The chart of accounts is a code constant; the FK needs it as rows."""
    connection.executemany(
        'INSERT INTO account (company_id, code, name, type, tax_code, '
        'normal_side, role, contra, deductible, tpar, note) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        [(COMPANY_ID, a.code, a.name, a.type, a.tax_code, a.normal_side,
          a.role, int(a.contra), int(a.deductible), int(a.tpar), a.note)
         for a in coa.CHART])
    connection.commit()


def install():
    """Swap the CSV tables for SQL ones and return the original set."""
    import tests.test_accounting as suite

    original = {name: getattr(store, name) for name in
                ('JOURNAL', 'CONTACTS', 'DOCUMENTS', 'JOBS', 'LODGEMENTS',
                 'IMPORT_RULES', 'BANK_LINES', 'ACCOUNTS_EXPORT')}
    original['read_company'] = store.read_company
    original['write_company'] = store.write_company
    original['SCHEDULES'] = amortise.SCHEDULES

    def sql_setup(self):
        connection = open_database()
        seed_chart(connection)
        bind(connection, COMPANY_ID)
        self.connection = connection
        self.company = config.Company(
            name='Test Painters Pty Ltd', registered_date='2026-01-15',
            directors=config.default_directors())
        self.company.save()
        self.addCleanup(connection.close)

    suite.BooksTestCase.setUp = sql_setup
    return suite, original


def main() -> int:
    suite_module, _ = install()
    loader = unittest.TestLoader()
    tests = loader.loadTestsFromModule(suite_module)
    print('Running the accounting test suite against SQLite '
          '(examples/sql_store.py) instead of CSV.\n')
    result = unittest.TextTestRunner(verbosity=1).run(tests)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
