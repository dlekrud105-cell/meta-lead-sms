"""Customers, suppliers and subcontractors.

The ABN fields matter beyond admin: a supplier who does not quote an ABN must
have 47% withheld from their payment, and a subcontractor's ABN and GST status
drive what goes on the TPAR.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import abn as abn_mod
from . import store

CUSTOMER, SUPPLIER, SUBCONTRACTOR, DIRECTOR, ATO, OTHER = (
    'customer', 'supplier', 'subcontractor', 'director', 'ato', 'other')
TYPES = (CUSTOMER, SUPPLIER, SUBCONTRACTOR, DIRECTOR, ATO, OTHER)


def _truthy(value) -> bool:
    return str(value).strip().lower() in ('1', 'true', 'yes', 'y')


@dataclass
class Contact:
    contact_id: str
    name: str
    type: str = OTHER
    abn: str = ''
    abn_quoted: bool = False
    gst_registered: bool = False
    email: str = ''
    phone: str = ''
    address: str = ''
    notes: str = ''

    @classmethod
    def from_row(cls, row: dict) -> 'Contact':
        return cls(
            contact_id=row['contact_id'], name=row['name'],
            type=row.get('type') or OTHER, abn=row.get('abn', ''),
            abn_quoted=_truthy(row.get('abn_quoted')),
            gst_registered=_truthy(row.get('gst_registered')),
            email=row.get('email', ''), phone=row.get('phone', ''),
            address=row.get('address', ''), notes=row.get('notes', ''),
        )

    def to_row(self) -> dict:
        return {
            'contact_id': self.contact_id, 'name': self.name, 'type': self.type,
            'abn': self.abn, 'abn_quoted': 'yes' if self.abn_quoted else 'no',
            'gst_registered': 'yes' if self.gst_registered else 'no',
            'email': self.email, 'phone': self.phone, 'address': self.address,
            'notes': self.notes,
        }

    @property
    def abn_is_valid(self) -> bool:
        return abn_mod.is_valid_abn(self.abn)

    @property
    def abn_formatted(self) -> str:
        return abn_mod.format_abn(self.abn)

    @property
    def withholding_applies(self) -> bool:
        """47% is withheld unless a valid ABN has been quoted.

        An ABN that fails its checksum has not been validly quoted, so it is
        treated the same as no ABN at all.
        """
        return not (self.abn_quoted and self.abn_is_valid)


def all_contacts() -> list:
    return [Contact.from_row(row) for row in store.CONTACTS.read()]


def find(reference) -> Contact | None:
    if not reference:
        return None
    wanted = str(reference).strip().lower()
    for contact in all_contacts():
        if contact.contact_id.lower() == wanted or contact.name.lower() == wanted:
            return contact
    return None


def get(reference) -> Contact:
    contact = find(reference)
    if contact is None:
        raise KeyError(f'unknown contact {reference!r} - add it first')
    return contact


def add(name, type=OTHER, abn='', gst_registered=False, email='', phone='',
        address='', notes='', abn_quoted=None) -> Contact:
    existing = find(name)
    if existing:
        raise KeyError(f'contact {name!r} already exists as {existing.contact_id}')
    if type not in TYPES:
        raise ValueError(f'contact type must be one of {", ".join(TYPES)}')
    abn = abn_mod.normalise(abn)
    if abn:
        problem = abn_mod.check_abn(abn)
        if problem:
            raise ValueError(problem)
    contact = Contact(
        contact_id=store.CONTACTS.next_sequence('contact_id', 'C', width=4),
        name=name, type=type, abn=abn,
        abn_quoted=bool(abn) if abn_quoted is None else abn_quoted,
        gst_registered=gst_registered, email=email, phone=phone,
        address=address, notes=notes)
    store.CONTACTS.append(contact.to_row())
    return contact


def ensure(name, **kwargs) -> Contact:
    """Fetch a contact by id or name, creating it if it does not exist yet."""
    existing = find(name)
    return existing if existing else add(name, **kwargs)


def update(reference, **changes) -> Contact:
    contact = get(reference)
    rows = store.CONTACTS.read()
    for row in rows:
        if row['contact_id'] == contact.contact_id:
            for key, value in changes.items():
                if key in ('abn_quoted', 'gst_registered'):
                    value = 'yes' if value else 'no'
                if key == 'abn' and value:
                    value = abn_mod.normalise(value)
                    problem = abn_mod.check_abn(value)
                    if problem:
                        raise ValueError(problem)
                row[key] = value
            updated = Contact.from_row(row)
            break
    else:  # pragma: no cover - get() already guarantees a match
        raise KeyError(contact.contact_id)
    store.CONTACTS.write_all(rows)
    return updated
