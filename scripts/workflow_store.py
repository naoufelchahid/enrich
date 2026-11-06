"""Durable workflow state alongside the independent outreach journal."""
import fcntl
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class WorkflowStore:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        if not Path(self.path).exists():
            try:
                Path(self.path).touch(mode=0o600, exist_ok=False)
            except FileExistsError:
                pass
        with self.connection() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS workflow_contacts (
                    id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, person_id TEXT, company_id TEXT,
                    status TEXT NOT NULL, email TEXT, admitted_at TEXT,
                    initial_sent_at TEXT, human_exchange INTEGER NOT NULL DEFAULT 0,
                    opportunity_id TEXT, wait_reason TEXT);
                CREATE TABLE IF NOT EXISTS workflow_operations (
                    id TEXT PRIMARY KEY, contact_id TEXT, kind TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, result TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_drafts (
                    id TEXT PRIMARY KEY, contact_id TEXT NOT NULL, kind TEXT NOT NULL,
                    version INTEGER NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL,
                    body TEXT NOT NULL, content_hash TEXT NOT NULL, hook TEXT NOT NULL,
                    status TEXT NOT NULL, callback_token TEXT UNIQUE NOT NULL,
                    telegram_message_id INTEGER, approved_by TEXT, approved_at TEXT,
                    created_at TEXT NOT NULL, reply_message_id TEXT, rewrite_instruction TEXT, request_key TEXT,
                    UNIQUE(contact_id,kind,version));
                CREATE TABLE IF NOT EXISTS workflow_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL,
                    kind TEXT NOT NULL, contact_id TEXT, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_sends (
                    id TEXT PRIMARY KEY, draft_id TEXT UNIQUE NOT NULL, contact_id TEXT NOT NULL,
                    kind TEXT NOT NULL, slot TEXT UNIQUE NOT NULL, state TEXT NOT NULL,
                    submitted_at TEXT NOT NULL, confirmed_at TEXT, receipt TEXT);
                CREATE TABLE IF NOT EXISTS workflow_exclusions (
                    email TEXT PRIMARY KEY, reason TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_messages (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, contact_id TEXT,
                    payload TEXT NOT NULL, classification TEXT, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_batches (
                    day TEXT PRIMARY KEY, candidates TEXT NOT NULL, status TEXT NOT NULL, summary TEXT);
            ''')
            columns = {r[1] for r in c.execute('PRAGMA table_info(workflow_drafts)')}
            if 'rewrite_instruction' not in columns:
                c.execute('ALTER TABLE workflow_drafts ADD COLUMN rewrite_instruction TEXT')
            if 'request_key' not in columns:
                c.execute('ALTER TABLE workflow_drafts ADD COLUMN request_key TEXT')
            c.execute('CREATE UNIQUE INDEX IF NOT EXISTS workflow_draft_request ON workflow_drafts(request_key)')

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    @contextmanager
    def lock(self, lane='global'):
        # All mutations that can affect send eligibility share the global lock.
        with open(self.path + '.' + lane + '.lock', 'a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('WORKFLOW_BUSY') from None
            yield

    def event(self, kind, contact_id, payload, *, key=None, connection=None):
        values = (key or uuid.uuid4().hex, kind, contact_id, encode(payload), now())
        if connection is not None:
            connection.execute('INSERT OR IGNORE INTO workflow_events(key,kind,contact_id,payload,created_at) VALUES(?,?,?,?,?)', values)
        else:
            with self.connection() as c:
                c.execute('INSERT OR IGNORE INTO workflow_events(key,kind,contact_id,payload,created_at) VALUES(?,?,?,?,?)', values)

    def exclude(self, email, reason, *, connection=None):
        values = (email.strip().lower(), reason, now())
        if connection is not None:
            connection.execute('INSERT OR IGNORE INTO workflow_exclusions VALUES (?,?,?)', values)
            return
        with self.lock():
            with self.connection() as c:
                self.exclude(email, reason, connection=c)
                self.event('exclusion', None, {'email': values[0], 'reason': reason}, connection=c)

    def excluded(self, email, connection=None):
        def check(c):
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return (bool(c.execute('SELECT 1 FROM workflow_exclusions WHERE email=?', (email.strip().lower(),)).fetchone())
                    or ('journal_exclusions' in tables and bool(c.execute('SELECT 1 FROM journal_exclusions WHERE email=?', (email.strip().lower(),)).fetchone())))
        if connection is not None:
            return check(connection)
        with self.connection() as c:
            return check(c)

    def contact(self, contact_id):
        with self.connection() as c:
            row = c.execute('SELECT * FROM workflow_contacts WHERE id=?', (contact_id,)).fetchone()
            return dict(row) if row else None

    def telegram_submission_paused(self, connection=None):
        def check(c):
            row = c.execute("SELECT value FROM workflow_meta WHERE key='telegram_submission_paused'").fetchone()
            return bool(row and row[0])
        if connection is not None:
            return check(connection)
        with self.connection() as c:
            return check(c)

    def put_contact(self, lead, status='QUALIFIED'):
        identity = lead['journal_contact_id']
        with self.connection() as c:
            c.execute('''INSERT INTO workflow_contacts(id,snapshot,status,email) VALUES (?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET snapshot=excluded.snapshot,email=excluded.email''',
                (identity, encode(lead), status, lead.get('clean_email')))
        return identity

    def operation(self, contact_id, kind, payload, *, key=None):
        key = key or fingerprint({'contact': contact_id, 'kind': kind, 'payload': payload})
        with self.connection() as c:
            c.execute('INSERT OR IGNORE INTO workflow_operations VALUES (?,?,?,?,?,NULL,0,NULL,?,?)',
                      (key, contact_id, kind, 'PENDING', encode(payload), now(), now()))
            return dict(c.execute('SELECT * FROM workflow_operations WHERE id=?', (key,)).fetchone())

    def finish_operation(self, op_id, state, result=None):
        with self.connection() as c:
            c.execute('UPDATE workflow_operations SET status=?,result=?,updated_at=? WHERE id=?',
                      (state, encode(result) if result is not None else None, now(), op_id))
            self.event('operation', None, {'id': op_id, 'state': state, 'result': result}, connection=c)
