"""Minimal operation journal; never stores tender or document identifiers."""

from __future__ import annotations

import fcntl
import hashlib
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


KINDS = frozenset({
    'FULLENRICH', 'NO2BOUNCE', 'CRM_COMPANY_CREATE', 'CRM_COMPANY_PATCH',
    'CRM_PERSON_CREATE', 'CRM_PERSON_PATCH',
})
SUBJECTS = frozenset({'company_domain', 'company_linkedin', 'company_registry',
                      'person_linkedin', 'email'})
FINAL_STATES = frozenset({'SUCCEEDED', 'NO_MATCH', 'REJECTED'})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def operation_id(kind: str, subject_kind: str, subject_id: str) -> str:
    if kind not in KINDS or subject_kind not in SUBJECTS or not subject_id or not subject_id.strip():
        raise ValueError('Invalid operation identity')
    value = '\x00'.join((kind, subject_kind, subject_id.strip().casefold()))
    return hashlib.sha256(value.encode()).hexdigest()


class OperationStore:
    """Keeps only provider/CRM operation state keyed by company, person or email."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            self.path.touch(mode=0o600, exist_ok=True)
        os.chmod(self.path, 0o600)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    subject_kind TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    remote_id TEXT,
                    outcome TEXT,
                    next_attempt_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(kind, subject_kind, subject_id)
                );
                CREATE INDEX IF NOT EXISTS operations_state ON operations(state);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def lock(self):
        self.initialize()
        lock_path = self.path.with_name(self.path.name + '.lock')
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('TENDER_ACQUISITION_BUSY') from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def reserve(self, kind: str, subject_kind: str, subject_id: str) -> dict:
        key = operation_id(kind, subject_kind, subject_id)
        self.initialize()
        stamp = utc_now()
        with self.connection() as db:
            db.execute('''INSERT OR IGNORE INTO operations
                (id,kind,subject_kind,subject_id,state,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)''',
                (key, kind, subject_kind, subject_id.strip(), 'PENDING', stamp, stamp))
            return dict(db.execute('SELECT * FROM operations WHERE id=?', (key,)).fetchone())

    def get(self, kind: str, subject_kind: str, subject_id: str) -> dict | None:
        if not self.path.exists():
            return None
        key = operation_id(kind, subject_kind, subject_id)
        with self.connection() as db:
            row = db.execute('SELECT * FROM operations WHERE id=?', (key,)).fetchone()
            return dict(row) if row else None

    def begin(self, key: str) -> dict:
        """Claim a certain submission. SENDING/UNKNOWN can only be reconciled."""
        stamp = utc_now()
        with self.connection() as db:
            row = db.execute('SELECT * FROM operations WHERE id=?', (key,)).fetchone()
            if row is None:
                raise ValueError('Operation not reserved')
            if row['state'] not in ('PENDING', 'RETRYABLE'):
                raise RuntimeError('OPERATION_RECONCILIATION_REQUIRED')
            if row['attempts'] >= 3:
                raise RuntimeError('OPERATION_RETRY_LIMIT')
            if row['next_attempt_at'] and row['next_attempt_at'] > stamp:
                raise RuntimeError('OPERATION_BACKOFF')
            db.execute('''UPDATE operations SET state='SENDING',attempts=attempts+1,
                next_attempt_at=NULL,updated_at=? WHERE id=?''', (stamp, key))
            return dict(db.execute('SELECT * FROM operations WHERE id=?', (key,)).fetchone())

    def finish(self, key: str, state: str, *, remote_id: str | None = None,
               outcome: str | None = None) -> dict:
        if state not in FINAL_STATES | {'UNKNOWN', 'RETRYABLE'}:
            raise ValueError('Invalid operation state')
        stamp = utc_now()
        with self.connection() as db:
            row = db.execute('SELECT * FROM operations WHERE id=?', (key,)).fetchone()
            if row is None or row['state'] not in ('SENDING', 'UNKNOWN'):
                raise RuntimeError('OPERATION_NOT_IN_FLIGHT')
            if row['state'] == 'UNKNOWN' and state == 'RETRYABLE':
                raise RuntimeError('UNKNOWN_REQUIRES_RECONCILIATION')
            delay = 60 * 2 ** max(0, row['attempts'] - 1)
            next_attempt = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat() if state == 'RETRYABLE' else None
            db.execute('''UPDATE operations SET state=?,remote_id=COALESCE(?,remote_id),
                outcome=?,next_attempt_at=?,updated_at=? WHERE id=?''',
                (state, remote_id, outcome, next_attempt, stamp, key))
            return dict(db.execute('SELECT * FROM operations WHERE id=?', (key,)).fetchone())

    def mark_interrupted(self) -> int:
        """Make crashed submissions explicit without resubmitting them."""
        if not self.path.exists():
            return 0
        with self.connection() as db:
            result = db.execute("UPDATE operations SET state='UNKNOWN',updated_at=? WHERE state='SENDING'", (utc_now(),))
            return result.rowcount

    def summary(self) -> dict:
        if not self.path.exists():
            return {'exists': False, 'counts': {}}
        with self.connection() as db:
            counts = {row['state']: row['n'] for row in db.execute(
                'SELECT state,COUNT(*) AS n FROM operations GROUP BY state')}
        return {'exists': True, 'counts': counts}
