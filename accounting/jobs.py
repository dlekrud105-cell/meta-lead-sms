"""Jobs (quotes/sites) so income and costs can be traced per project.

Tagging invoices and costs with a job code is what makes it possible to see
which quotes actually made money once paint, subbies and travel are counted.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import store

QUOTED, ACTIVE, COMPLETE, LOST = 'quoted', 'active', 'complete', 'lost'
STATUSES = (QUOTED, ACTIVE, COMPLETE, LOST)


@dataclass
class Job:
    job_id: str
    name: str
    contact_id: str = ''
    address: str = ''
    status: str = ACTIVE
    quoted_incl: str = ''
    started: str = ''
    completed: str = ''
    notes: str = ''

    @classmethod
    def from_row(cls, row: dict) -> 'Job':
        return cls(**{k: row.get(k, '') for k in cls.__annotations__})

    def to_row(self) -> dict:
        return {k: getattr(self, k) for k in self.__annotations__}


def all_jobs() -> list:
    return [Job.from_row(row) for row in store.JOBS.read()]


def find(reference) -> Job | None:
    if not reference:
        return None
    wanted = str(reference).strip().lower()
    for job in all_jobs():
        if job.job_id.lower() == wanted or job.name.lower() == wanted:
            return job
    return None


def get(reference) -> Job:
    job = find(reference)
    if job is None:
        raise KeyError(f'unknown job {reference!r} - add it first')
    return job


def add(name, contact_id='', address='', status=ACTIVE, quoted_incl='',
        started='', notes='') -> Job:
    if find(name):
        raise KeyError(f'job {name!r} already exists')
    if status not in STATUSES:
        raise ValueError(f'job status must be one of {", ".join(STATUSES)}')
    job = Job(job_id=store.JOBS.next_sequence('job_id', 'J', width=4), name=name,
              contact_id=contact_id, address=address, status=status,
              quoted_incl=str(quoted_incl), started=started, notes=notes)
    store.JOBS.append(job.to_row())
    return job


def update(reference, **changes) -> Job:
    job = get(reference)
    rows = store.JOBS.read()
    for row in rows:
        if row['job_id'] == job.job_id:
            row.update({k: str(v) for k, v in changes.items()})
            updated = Job.from_row(row)
            break
    else:  # pragma: no cover
        raise KeyError(job.job_id)
    store.JOBS.write_all(rows)
    return updated


def resolve_id(reference) -> str:
    """Return a job id for a reference, or '' when no job was given."""
    return get(reference).job_id if reference else ''
