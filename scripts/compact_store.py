"""Additive compact storage in the existing workflow journal.

No backend is activated by construction. Each writer must explicitly select its
lane with set_authority; compare-and-swap prevents two competing transitions.
Candidate data is the latest working dossier, not another CRM history. Large
exact evidence belongs in blobs and is referenced by URI. Legacy identifiers
are scoped by source_id and never inferred from names or email addresses.
"""
import gzip
import hashlib
import json
import uuid

from scripts.workflow_store import WorkflowStore, encode, fingerprint, now

_UNSET = object()
TASK_STATES = frozenset({'PENDING', 'RUNNING', 'SUBMITTED', 'UNKNOWN', 'WAITING',
                         'SUCCEEDED', 'FAILED', 'NO_MATCH', 'NEEDS_REVIEW', 'CANCELLED'})


class CompactStore(WorkflowStore):
    """Common storage contract; existing WorkflowStore interfaces stay intact.

put_candidate returns the durable id; candidate/candidates return dictionaries
whose `data` is decoded JSON. A candidate's lane records its origin. Other
explicitly activated lanes may update it with optimistic revision checks.
claim_task only claims PENDING tasks: RUNNING/SUBMITTED/UNKNOWN never expire.
finish_task is the orchestrator's explicit reconciliation interface.
"""

    def __init__(self, path):
        super().__init__(path)
        with self.connection() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS workflow_authorities (
                    lane TEXT PRIMARY KEY, backend TEXT NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_candidates (
                    id TEXT PRIMARY KEY, lane TEXT NOT NULL, data TEXT NOT NULL,
                    status TEXT NOT NULL, wait_reason TEXT, revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS workflow_candidate_state ON workflow_candidates(lane,status);
                CREATE TABLE IF NOT EXISTS workflow_legacy_ids (
                    source_id TEXT NOT NULL, legacy_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
                    PRIMARY KEY(source_id,legacy_id));
                CREATE TRIGGER IF NOT EXISTS workflow_legacy_identity_immutable
                    BEFORE UPDATE ON workflow_legacy_ids
                    WHEN NEW.source_id IS NOT OLD.source_id OR NEW.legacy_id IS NOT OLD.legacy_id
                        OR NEW.candidate_id IS NOT OLD.candidate_id
                    BEGIN SELECT RAISE(ABORT, 'Legacy identity mapping is immutable'); END;
                CREATE TABLE IF NOT EXISTS workflow_tasks (
                    id TEXT PRIMARY KEY, lane TEXT NOT NULL, kind TEXT NOT NULL,
                    candidate_id TEXT, payload TEXT NOT NULL, status TEXT NOT NULL,
                    result TEXT, worker TEXT, wait_reason TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS workflow_task_state ON workflow_tasks(lane,status,created_at);
                CREATE TABLE IF NOT EXISTS workflow_blobs (
                    sha256 TEXT PRIMARY KEY, content_gzip BLOB NOT NULL,
                    original_bytes INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_evidence_refs (
                    candidate_id TEXT NOT NULL, sha256 TEXT NOT NULL,
                    path TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(candidate_id,sha256,path,kind));
            ''')

    def authority(self, lane, *, connection=None):
        if connection is None:
            with self.connection() as c:
                return self.authority(lane, connection=c)
        row = connection.execute('SELECT backend FROM workflow_authorities WHERE lane=?', (lane,)).fetchone()
        return row[0] if row else 'legacy'

    def set_authority(self, lane, backend='compact', *, expected='legacy'):
        """Explicit transition under the shared lane lock; never automatic.

The caller must stop/reconcile the previous writer before this local switch.
Moving back to legacy requires external reconciliation, not this API.
"""
        if not lane or backend != 'compact' or expected not in ('legacy', 'compact'):
            raise ValueError('INVALID_AUTHORITY_TRANSITION')
        with self.lock('authority-' + hashlib.sha256(lane.encode()).hexdigest()):
            with self.connection() as c:
                c.execute('BEGIN IMMEDIATE')
                current = self.authority(lane, connection=c)
                if current != expected:
                    raise RuntimeError('AUTHORITY_CHANGED')
                c.execute('''INSERT INTO workflow_authorities VALUES (?, ?, 1)
                    ON CONFLICT(lane) DO UPDATE SET backend=excluded.backend,
                    revision=workflow_authorities.revision+1''', (lane, backend))
        return backend

    def _require_authority(self, c, lane):
        if self.authority(lane, connection=c) != 'compact':
            raise RuntimeError('COMPACT_AUTHORITY_REQUIRED:' + lane)

    @staticmethod
    def _candidate(row):
        if row is None:
            return None
        value = dict(row)
        value['data'] = json.loads(value['data'])
        return value

    def candidate(self, candidate_id):
        with self.connection() as c:
            return self._candidate(c.execute('SELECT * FROM workflow_candidates WHERE id=?', (candidate_id,)).fetchone())

    def candidates(self, *, lane=None, status=None):
        clauses, args = [], []
        for name, value in [('lane', lane), ('status', status)]:
            if value is not None:
                clauses.append(name + '=?')
                args.append(value)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.connection() as c:
            return [self._candidate(r) for r in c.execute('SELECT * FROM workflow_candidates' + where + ' ORDER BY created_at,id', args)]

    def _bind_legacy(self, c, source_id, legacy_id, candidate_id):
        if not source_id or legacy_id is None:
            raise ValueError('SOURCE_AND_LEGACY_ID_REQUIRED')
        legacy_id = str(legacy_id)
        previous = c.execute('SELECT candidate_id FROM workflow_legacy_ids WHERE source_id=? AND legacy_id=?', (source_id, legacy_id)).fetchone()
        if previous and previous[0] != candidate_id:
            raise ValueError('LEGACY_IDENTITY_CONFLICT')
        c.execute('INSERT OR IGNORE INTO workflow_legacy_ids VALUES (?,?,?)', (source_id, legacy_id, candidate_id))

    def bind_legacy(self, source_id, legacy_id, candidate_id):
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM workflow_candidates WHERE id=?', (candidate_id,)).fetchone():
                raise ValueError('CANDIDATE_NOT_FOUND')
            self._bind_legacy(c, source_id, legacy_id, candidate_id)
        return candidate_id

    def resolve_legacy(self, source_id, legacy_id):
        with self.connection() as c:
            row = c.execute('SELECT candidate_id FROM workflow_legacy_ids WHERE source_id=? AND legacy_id=?', (source_id, str(legacy_id))).fetchone()
            return row[0] if row else None

    def put_candidate(self, data, *, lane, candidate_id=None, source_id=None, legacy_id=None,
                      status='PENDING', wait_reason=None):
        """Create once; retries return the same identity without reverting state.

Use update_candidate for an intentional dossier change. Caller-supplied durable
identity and an existing source mapping must agree.
"""
        if not isinstance(data, dict):
            raise ValueError('CANDIDATE_DATA_REQUIRED')
        if (source_id is None) != (legacy_id is None):
            raise ValueError('SOURCE_AND_LEGACY_ID_REQUIRED')
        explicit = candidate_id or data.get('journal_contact_id')
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            self._require_authority(c, lane)
            prior = c.execute('SELECT candidate_id FROM workflow_legacy_ids WHERE source_id=? AND legacy_id=?', (source_id, str(legacy_id))).fetchone() if source_id is not None else None
            if prior and explicit and prior[0] != explicit:
                raise ValueError('LEGACY_IDENTITY_CONFLICT')
            identity = explicit or (prior[0] if prior else uuid.uuid4().hex)
            if data.get('journal_contact_id') and data['journal_contact_id'] != identity:
                raise ValueError('DURABLE_IDENTITY_CONFLICT')
            dossier = {**data, 'journal_contact_id': identity}
            c.execute('''INSERT OR IGNORE INTO workflow_candidates
                VALUES (?,?,?,?,?,1,?,?)''', (identity, lane, encode(dossier), status, wait_reason, now(), now()))
            if source_id is not None:
                self._bind_legacy(c, source_id, legacy_id, identity)
        return identity

    def update_candidate(self, candidate_id, changes, *, lane, status=None,
                         wait_reason=_UNSET, expected_revision=None):
        if not isinstance(changes, dict):
            raise ValueError('CANDIDATE_DATA_REQUIRED')
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            self._require_authority(c, lane)
            row = c.execute('SELECT * FROM workflow_candidates WHERE id=?', (candidate_id,)).fetchone()
            if row is None:
                raise ValueError('CANDIDATE_NOT_FOUND')
            if expected_revision is not None and row['revision'] != expected_revision:
                raise RuntimeError('CANDIDATE_CHANGED')
            if 'journal_contact_id' in changes and changes['journal_contact_id'] != candidate_id:
                raise ValueError('DURABLE_IDENTITY_CONFLICT')
            dossier = {**json.loads(row['data']), **changes}
            updated_status = row['status'] if status is None else status
            reason = row['wait_reason'] if wait_reason is _UNSET else wait_reason
            if (encode(dossier), updated_status, reason) != (row['data'], row['status'], row['wait_reason']):
                c.execute('''UPDATE workflow_candidates SET data=?,status=?,wait_reason=?,
                    revision=revision+1,updated_at=? WHERE id=?''',
                    (encode(dossier), updated_status, reason, now(), candidate_id))
            return self._candidate(c.execute('SELECT * FROM workflow_candidates WHERE id=?', (candidate_id,)).fetchone())

    @staticmethod
    def _task(row):
        if row is None:
            return None
        value = dict(row)
        value['payload'] = json.loads(value['payload'])
        value['result'] = json.loads(value['result']) if value['result'] is not None else None
        return value

    def create_task(self, lane, kind, payload, *, candidate_id=None, key=None):
        identity = key or fingerprint({'lane': lane, 'kind': kind, 'candidate': candidate_id, 'payload': payload})
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            self._require_authority(c, lane)
            if candidate_id and not c.execute('SELECT 1 FROM workflow_candidates WHERE id=?', (candidate_id,)).fetchone():
                raise ValueError('CANDIDATE_NOT_FOUND')
            previous = c.execute('SELECT * FROM workflow_tasks WHERE id=?', (identity,)).fetchone()
            if previous and (previous['lane'], previous['kind'], previous['candidate_id'], previous['payload']) != (lane, kind, candidate_id, encode(payload)):
                raise ValueError('TASK_KEY_REUSED_WITH_DIFFERENT_CONTENT')
            c.execute('''INSERT OR IGNORE INTO workflow_tasks
                VALUES (?,?,?,?,?,'PENDING',NULL,NULL,NULL,?,?)''',
                (identity, lane, kind, candidate_id, encode(payload), now(), now()))
            return self._task(c.execute('SELECT * FROM workflow_tasks WHERE id=?', (identity,)).fetchone())

    def task(self, task_id):
        with self.connection() as c:
            return self._task(c.execute('SELECT * FROM workflow_tasks WHERE id=?', (task_id,)).fetchone())

    def claim_task(self, lane, worker, task_id=None):
        if not worker:
            raise ValueError('WORKER_REQUIRED')
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            self._require_authority(c, lane)
            query = "SELECT * FROM workflow_tasks WHERE lane=? AND status='PENDING'"
            args = [lane]
            if task_id is not None:
                query += ' AND id=?'
                args.append(task_id)
            row = c.execute(query + ' ORDER BY created_at,id LIMIT 1', args).fetchone()
            if row is None:
                return None
            c.execute("UPDATE workflow_tasks SET status='RUNNING',worker=?,updated_at=? WHERE id=?", (worker, now(), row['id']))
            return self._task(c.execute('SELECT * FROM workflow_tasks WHERE id=?', (row['id'],)).fetchone())

    def finish_task(self, task_id, state, result=None, *, worker=None, wait_reason=None):
        if state not in TASK_STATES or state in ('PENDING', 'RUNNING'):
            raise ValueError('INVALID_TASK_FINISH_STATE')
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM workflow_tasks WHERE id=?', (task_id,)).fetchone()
            if row is None:
                raise ValueError('TASK_NOT_FOUND')
            self._require_authority(c, row['lane'])
            if worker is not None and row['worker'] != worker:
                raise RuntimeError('TASK_OWNED_BY_ANOTHER_WORKER')
            if row['status'] in ('SUCCEEDED', 'NO_MATCH', 'CANCELLED'):
                if row['status'] != state or row['result'] != (encode(result) if result is not None else None):
                    raise RuntimeError('TASK_ALREADY_FINISHED')
                return self._task(row)
            c.execute('UPDATE workflow_tasks SET status=?,result=?,wait_reason=?,updated_at=? WHERE id=?',
                      (state, encode(result) if result is not None else None, wait_reason, now(), task_id))
            return self._task(c.execute('SELECT * FROM workflow_tasks WHERE id=?', (task_id,)).fetchone())

    def put_blob(self, content):
        if not isinstance(content, bytes):
            raise TypeError('EXACT_BYTES_REQUIRED')
        digest = hashlib.sha256(content).hexdigest()
        with self.connection() as c:
            c.execute('INSERT OR IGNORE INTO workflow_blobs VALUES (?,?,?,?)',
                      (digest, gzip.compress(content, mtime=0), len(content), now()))
        # Verify the committed blob, including when a duplicate already existed.
        if self.get_blob(digest) != content:
            raise RuntimeError('BLOB_VERIFICATION_FAILED')
        return digest

    def get_blob(self, digest):
        digest = digest.removeprefix('journal://artifacts/')
        with self.connection() as c:
            row = c.execute('SELECT content_gzip,original_bytes FROM workflow_blobs WHERE sha256=?', (digest,)).fetchone()
            if row is None:
                # Legacy artifact paths and URIs remain resolvable during transition.
                exists = c.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_artifacts'").fetchone()
                row = c.execute('SELECT content_gzip,original_bytes FROM workflow_artifacts WHERE sha256=? LIMIT 1', (digest,)).fetchone() if exists else None
        if row is None:
            raise FileNotFoundError(digest)
        try:
            content = gzip.decompress(row[0])
        except (OSError, EOFError) as exc:
            raise RuntimeError('BLOB_VERIFICATION_FAILED') from exc
        if len(content) != row[1] or hashlib.sha256(content).hexdigest() != digest:
            raise RuntimeError('BLOB_VERIFICATION_FAILED')
        return content

    def add_evidence(self, candidate_id, content, *, path=None, kind='evidence'):
        if not self.candidate(candidate_id):
            raise ValueError('CANDIDATE_NOT_FOUND')
        digest = self.put_blob(content)
        with self.connection() as c:
            c.execute('INSERT OR IGNORE INTO workflow_evidence_refs VALUES (?,?,?,?,?)',
                      (candidate_id, digest, str(path) if path is not None else '', kind, now()))
        return 'journal://artifacts/' + digest

    def evidence(self, candidate_id):
        with self.connection() as c:
            return [{**dict(r), 'uri': 'journal://artifacts/' + r['sha256']}
                    for r in c.execute('SELECT * FROM workflow_evidence_refs WHERE candidate_id=? ORDER BY created_at,sha256,path', (candidate_id,))]
