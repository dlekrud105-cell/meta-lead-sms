"""CSV-backed storage.

Plain CSV on purpose: the books stay readable, diffable and committable to
git, and they can be opened in Excel or handed to a tax agent without an
export step. Set ACCOUNTING_DATA_DIR to keep the books outside the repo.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    path = Path(os.environ.get('ACCOUNTING_DATA_DIR') or (_PACKAGE_ROOT / 'data'))
    path.mkdir(parents=True, exist_ok=True)
    return path


class Table:
    """A CSV file with a fixed header."""

    def __init__(self, filename: str, fields: list[str]):
        self.filename = filename
        self.fields = fields

    @property
    def path(self) -> Path:
        return data_dir() / self.filename

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> list[dict]:
        if not self.exists():
            return []
        with self.path.open(newline='', encoding='utf-8') as fh:
            return [dict(row) for row in csv.DictReader(fh)]

    def _normalise(self, row: dict) -> dict:
        unknown = set(row) - set(self.fields)
        if unknown:
            raise KeyError(f'{self.filename}: unknown column(s) {sorted(unknown)}')
        return {f: '' if row.get(f) is None else str(row.get(f, '')) for f in self.fields}

    def append_many(self, rows: list[dict]) -> None:
        if not rows:
            return
        new_file = not self.exists()
        with self.path.open('a', newline='', encoding='utf-8') as fh:
            writer = csv.DictWriter(fh, fieldnames=self.fields)
            if new_file:
                writer.writeheader()
            for row in rows:
                writer.writerow(self._normalise(row))

    def append(self, row: dict) -> None:
        self.append_many([row])

    def write_all(self, rows: list[dict]) -> None:
        with self.path.open('w', newline='', encoding='utf-8') as fh:
            writer = csv.DictWriter(fh, fieldnames=self.fields)
            writer.writeheader()
            for row in rows:
                writer.writerow(self._normalise(row))

    def find(self, **criteria):
        for row in self.read():
            if all(str(row.get(k, '')) == str(v) for k, v in criteria.items()):
                return row
        return None

    def next_sequence(self, field: str, prefix: str, width: int = 4) -> str:
        """Next id like 'INV0007', scanning existing rows for the high-water mark."""
        highest = 0
        for row in self.read():
            value = str(row.get(field, ''))
            if value.startswith(prefix):
                tail = value[len(prefix):]
                if tail.isdigit():
                    highest = max(highest, int(tail))
        return f'{prefix}{highest + 1:0{width}d}'


JOURNAL = Table('journal.csv', [
    'entry_id', 'date', 'memo', 'source', 'doc_ref', 'line_no', 'account',
    'description', 'debit', 'credit', 'tax_code', 'contact', 'job',
])

CONTACTS = Table('contacts.csv', [
    'contact_id', 'name', 'type', 'abn', 'abn_quoted', 'gst_registered',
    'email', 'phone', 'address', 'notes',
])

DOCUMENTS = Table('documents.csv', [
    'doc_id', 'type', 'date', 'due_date', 'contact_id', 'job_id',
    'description', 'total_incl', 'gst', 'withheld', 'entry_id',
])

JOBS = Table('jobs.csv', [
    'job_id', 'name', 'contact_id', 'address', 'status', 'quoted_incl',
    'started', 'completed', 'notes',
])

LODGEMENTS = Table('lodgements.csv', [
    'kind', 'period', 'lodged_date', 'reference', 'amount', 'lodged_by', 'notes',
])

ACCOUNTS_EXPORT = Table('accounts.csv', [
    'code', 'name', 'type', 'tax_code', 'normal_side', 'tpar', 'deductible', 'note',
])

TABLES = [JOURNAL, CONTACTS, DOCUMENTS, JOBS, LODGEMENTS, ACCOUNTS_EXPORT]


def company_file() -> Path:
    return data_dir() / 'company.json'


def read_company() -> dict:
    path = company_file()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def write_company(config: dict) -> None:
    company_file().write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
