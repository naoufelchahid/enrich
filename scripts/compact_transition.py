"""Conservation staging for compact storage; no source mutation or file removal.

The caller supplies a verified writer pause (or an offline rehearsal). Existing
journal history stays in place, untouched. Exact operational source records are
content-addressed; submitted legacy batches remain explicitly unreconciled.
"""
import base64
import gzip
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from scripts.workflow_migration import audit_pair, batches, quote
from scripts.workflow_store import encode, fingerprint, now
from scripts.compact_store import CompactStore

LARGE_FIELDS = {'source_data_json','linkedin_profile_json','linkedin_comments_json','recent_posts'}
TERMINAL = {'FINISHED','SUCCEEDED','NO_MATCH','CANCELLED'}


class _BatchConnection:
    """Nested store APIs share the one migration transaction for this batch."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, parameters=()):
        if statement.strip().upper() == 'BEGIN IMMEDIATE' and self.connection.in_transaction:
            return self.connection.execute('SELECT 1')
        return self.connection.execute(statement,parameters)

    def __getattr__(self, name):
        return getattr(self.connection,name)


class _BatchStore(CompactStore):
    def __init__(self, original):
        self.original, self.path, self._batch = original, original.path, None
        self.transaction_batches = 0

    @contextmanager
    def connection(self):
        if self._batch is not None:
            yield self._batch
        else:
            with self.original.connection() as c:
                yield c

    @contextmanager
    def atomic_batch(self):
        with self.original.connection() as c:
            c.execute('PRAGMA cache_size=-65536')
            c.execute('BEGIN IMMEDIATE')
            self._batch = _BatchConnection(c)
            try:
                yield
            finally:
                self._batch = None
        self.transaction_batches += 1


def _stage_batches(cursor, batch_size, store):
    for rows in batches(cursor,batch_size):
        if isinstance(store,_BatchStore):
            with store.atomic_batch():
                yield rows
        else:
            yield rows


def verify_compact_blobs(store, *, batch_size=128):
    count = 0
    with store.connection() as c:
        for rows in batches(c.execute('SELECT sha256,content_gzip,original_bytes FROM workflow_blobs'),batch_size):
            for digest,compressed,size in rows:
                raw = gzip.decompress(compressed)
                if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
                    raise RuntimeError('COMMITTED_BLOB_VERIFICATION_FAILED')
                count += 1
    return count


def _exact_record(record):
    return {k:({'sqlite_blob_base64':base64.b64encode(v).decode()} if isinstance(v,bytes) else v)
            for k,v in record.items()}


def stage_legacy(store, source_path, *, writes_paused=False, rehearsal=False, batch_size=128, optimized=False):
    if not (writes_paused or rehearsal):
        raise RuntimeError('PLANNED_WRITER_PAUSE_REQUIRED')
    source_path = Path(source_path).resolve(strict=True)
    if rehearsal:
        root = Path(__file__).resolve().parents[1]
        active = Path(os.getenv('WORKFLOW_STAGING_PATH',str(root/'bdd/workflow/staging.sqlite'))).resolve()
        active_journal = Path(os.getenv('WORKFLOW_STATE_PATH') or str(active)+'.journal.sqlite').resolve()
        if source_path in (active,active_journal) or Path(store.path) in (active,active_journal):
            raise RuntimeError('REHEARSAL_REQUIRES_DISTINCT_OFFLINE_COPIES')
    if str(source_path) == store.path:
        raise ValueError('SOURCE_AND_JOURNAL_MUST_DIFFER')
    audit = audit_pair(source_path, store.path, batch_size=batch_size)
    if not audit['exported_copies_verified'] or audit['pending_export']:
        raise RuntimeError('EXACT_JOURNAL_RECONCILIATION_REQUIRED')
    source_id = audit['source_id']
    if optimized:
        store = _BatchStore(store)
    report = {'source_id':source_id,'rehearsal':rehearsal,'candidates':0,'records':0,
              'pending_legacy_jobs':0,'adopted_jobs':0,'missing':0,'divergent':0,'removed_files':[],
              'source_mutated':False,'bascule_effectuee':False}
    with store.lock('compact-transition'):
        with store.connection() as c:
            store._require_authority(c,'qualification')
            store._require_authority(c,'migration')
            c.execute('''CREATE TABLE IF NOT EXISTS workflow_legacy_records (
                source_id TEXT NOT NULL,table_name TEXT NOT NULL,record_key TEXT NOT NULL,
                sha256 TEXT NOT NULL,row_order INTEGER,
                PRIMARY KEY(source_id,table_name,record_key))''')
            if 'row_order' not in {r[1] for r in c.execute('PRAGMA table_info(workflow_legacy_records)')}:
                c.execute('ALTER TABLE workflow_legacy_records ADD COLUMN row_order INTEGER')
        with sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True) as source:
            source.row_factory = sqlite3.Row
            if optimized:
                source.execute('PRAGMA cache_size=-65536')
            source.execute('PRAGMA query_only=ON')
            source.execute('BEGIN')
            report['schema_uri'] = 'journal://artifacts/' + store.put_blob(encode({
                'sqlite_master':[dict(r) for r in source.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')],
                'user_version':source.execute('PRAGMA user_version').fetchone()[0],
                'application_id':source.execute('PRAGMA application_id').fetchone()[0],
                'sqlite_sequence':dict(source.execute('SELECT name,seq FROM sqlite_sequence'))
                    if source.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone() else {}}).encode())
            tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if source.execute('SELECT 1 FROM staging_leads WHERE journal_contact_id IS NULL LIMIT 1').fetchone():
                raise RuntimeError('DURABLE_SOURCE_IDENTITY_REQUIRED')
            for rows in _stage_batches(source.execute('SELECT * FROM staging_leads ORDER BY id'),batch_size,store):
                for row in rows:
                    record = dict(row)
                    identity = record['journal_contact_id']
                    data = {k:v for k,v in record.items() if k not in LARGE_FIELDS}
                    raw = encode(_exact_record(record)).encode()
                    existing_proofs = [r for r in store.evidence(identity) if r['kind']=='legacy_candidate']
                    if existing_proofs and not any(store.get_blob(r['uri']) == raw for r in existing_proofs):
                        raise RuntimeError('SOURCE_CHANGED_SINCE_STAGING')
                    # Source snapshots retain all columns, including bulky historical fields.
                    store.put_candidate(data,lane='qualification',candidate_id=identity,
                        source_id=source_id,legacy_id=record['id'],
                        status='WAITING' if record.get('workflow_wait_reason') else 'PENDING',
                        wait_reason=record.get('workflow_wait_reason'))
                    if record.get('import_key'):
                        store.bind_legacy('legacy-import-key',record['import_key'],identity)
                    uri = store.add_evidence(identity,raw,kind='legacy_candidate')
                    if store.get_blob(uri) != raw:
                        raise RuntimeError('LEGACY_RECORD_VERIFICATION_FAILED')
                    report['candidates'] += 1
            for table in sorted(tables - {'staging_leads','outreach_journal_outbox'}):
                # Do not reproduce the outbox: every event was compared to the
                # existing journal individually before staging began.
                columns = source.execute('PRAGMA table_info(' + quote(table) + ')').fetchall()
                key_columns = [r['name'] for r in sorted(columns,key=lambda r:r['pk']) if r['pk']]
                occurrences = {}
                order = ','.join(quote(k) for k in key_columns) if key_columns else 'rowid'
                row_order = 0
                for rows in _stage_batches(source.execute('SELECT * FROM ' + quote(table)+' ORDER BY '+order),batch_size,store):
                    for row in rows:
                        row_order += 1
                        record = dict(row)
                        raw = encode(_exact_record(record)).encode()
                        digest = store.put_blob(raw)
                        occurrences[digest] = occurrences.get(digest,0)+1
                        key = fingerprint(_exact_record({k:record[k] for k in key_columns})) if key_columns else digest+':'+str(occurrences[digest])
                        with store.connection() as c:
                            prior = c.execute('SELECT sha256 FROM workflow_legacy_records WHERE source_id=? AND table_name=? AND record_key=?',
                                (source_id,table,key)).fetchone()
                            if prior and prior[0] != digest:
                                raise RuntimeError('SOURCE_CHANGED_SINCE_STAGING')
                            c.execute('''INSERT INTO workflow_legacy_records
                                (source_id,table_name,record_key,sha256,row_order) VALUES (?,?,?,?,?)
                                ON CONFLICT(source_id,table_name,record_key) DO UPDATE SET row_order=excluded.row_order''',
                                      (source_id,table,key,digest,row_order))
                        report['records'] += 1
            if 'email_suppressions' in tables:
                for row in source.execute('SELECT * FROM email_suppressions'):
                    if not store.excluded(row['email']):
                        store.exclude(row['email'],row['reason'])
            if 'enrichment_jobs' in tables:
                for rows in _stage_batches(source.execute('SELECT * FROM enrichment_jobs ORDER BY id'),batch_size,store):
                    for row in rows:
                        job = dict(row)
                        targets = [dict(r) for r in source.execute('SELECT * FROM enrichment_targets WHERE job_id=?',(job['id'],))] if 'enrichment_targets' in tables else []
                        candidates = list(dict.fromkeys(store.resolve_legacy(source_id,t['lead_id']) for t in targets))
                        candidates = [c for c in candidates if c]
                        unresolved = job['status'] not in TERMINAL or any(not t.get('applied') for t in targets)
                        if not unresolved:
                            continue
                        try:
                            payload = json.loads(job['payload'])
                            emails = payload.get('emailList')
                            adoptable = (job['provider']=='no2bounce' and job['status']=='SUBMITTED'
                                and isinstance(job.get('remote_id'),str) and bool(job['remote_id'])
                                and len(targets)==len(candidates)==1 and isinstance(emails,list) and len(emails)==1
                                and (store.candidate(candidates[0])['data'].get('raw_email') or '').strip().lower()==emails[0]
                                and not job.get('response'))
                        except (ValueError,TypeError,AttributeError):
                            adoptable = False
                        if adoptable:
                            task = store.create_task('qualification','no2bounce',payload,candidate_id=candidates[0],
                                key='adopted-no2bounce:'+source_id+':'+job['id'])
                            if task['status']=='PENDING':
                                store.finish_task(task['id'],'SUBMITTED',{'remote_id':job['remote_id'],
                                    'legacy_record_uri':'journal://artifacts/'+fingerprint(_exact_record(job))})
                            report['adopted_jobs'] += 1
                            continue
                        task = store.create_task('migration','LEGACY_PROVIDER_BATCH',
                            {'source_id':source_id,'legacy_job_id':job['id'],'provider':job['provider'],
                             'record_sha256':fingerprint(_exact_record(job)),'candidates':candidates},
                            candidate_id=candidates[0] if candidates else None,
                            key='legacy-job:'+source_id+':'+job['id'])
                        state = 'SUBMITTED' if job['status']=='SUBMITTED' else 'UNKNOWN' if job['status'] in ('UNKNOWN','SUBMITTING','RUNNING') else 'WAITING'
                        result = {'legacy_status':job['status'],'remote_id':job.get('remote_id'),
                                  'record_uri':'journal://artifacts/'+fingerprint(_exact_record(job))}
                        if task['status'] == 'PENDING':
                            store.finish_task(task['id'],state,result,wait_reason='LEGACY_PROVIDER_RECONCILIATION_REQUIRED')
                        for identity in candidates:
                            candidate = store.candidate(identity)
                            pending = list(dict.fromkeys(candidate['data'].get('pending_legacy_jobs',[]) + [task['id']]))
                            store.update_candidate(identity,{'pending_legacy_jobs':pending},lane='qualification',
                                status='WAITING',wait_reason='LEGACY_PROVIDER_RECONCILIATION_REQUIRED')
                        report['pending_legacy_jobs'] += 1
            if 'linkedin_browser_queue' in tables:
                for rows in _stage_batches(source.execute('SELECT * FROM linkedin_browser_queue ORDER BY id'),batch_size,store):
                    for row in rows:
                        job = dict(row)
                        identity = store.resolve_legacy(source_id,job['lead_id'])
                        if job['status'] in ('IMPORTED','SUCCEEDED','NO_MATCH') and not identity:
                            continue
                        if identity and job['status'] in ('IMPORTED','SUCCEEDED'):
                            from scripts.compact_linkedin import CompactLinkedIn
                            if CompactLinkedIn(store).confirmed_input(store.candidate(identity)):
                                continue
                        task = store.create_task('migration','LEGACY_LINKEDIN',
                            {'source_id':source_id,'legacy_job_id':job['id'],
                             'record_sha256':fingerprint(_exact_record(job))},candidate_id=identity,
                            key='legacy-linkedin:'+source_id+':'+str(job['id']))
                        result = {'legacy_status':job['status'],
                                  'record_uri':'journal://artifacts/'+fingerprint(_exact_record(job))}
                        if identity and job.get('result_path'):
                            from scripts.workflow_files import read_file
                            try:
                                raw = read_file(store,job['result_path'])
                                result['proof_uri'] = store.add_evidence(identity,raw,kind='legacy_linkedin_result')
                            except (OSError,RuntimeError):
                                result['missing_proof'] = True
                        state = 'UNKNOWN' if job['status'] in ('RUNNING','UNKNOWN') else 'WAITING'
                        if task['status']=='PENDING':
                            store.finish_task(task['id'],state,result,wait_reason='LEGACY_LINKEDIN_RECONCILIATION_REQUIRED')
                        if identity:
                            candidate = store.candidate(identity)
                            pending = list(dict.fromkeys(candidate['data'].get('pending_legacy_jobs',[]) + [task['id']]))
                            store.update_candidate(identity,{'pending_legacy_jobs':pending},lane='qualification',
                                status='WAITING',wait_reason='LEGACY_LINKEDIN_RECONCILIATION_REQUIRED')
                        report['pending_legacy_jobs'] += 1
            # Verify every staged candidate against its exact source record.
            for rows in _stage_batches(source.execute('SELECT * FROM staging_leads ORDER BY id'),batch_size,store):
                for row in rows:
                    record = dict(row)
                    identity = store.resolve_legacy(source_id,record['id'])
                    candidate = store.candidate(identity) if identity else None
                    if not candidate or candidate['id'] != record['journal_contact_id']:
                        report['missing'] += 1
                    elif not any(store.get_blob(r['uri']) == encode(_exact_record(record)).encode()
                                 for r in store.evidence(identity) if r['kind']=='legacy_candidate'):
                        report['divergent'] += 1
        if report['missing'] or report['divergent']:
            raise RuntimeError('COMPACT_CONSERVATION_FAILED')
        report['committed_blobs_verified'] = verify_compact_blobs(store,batch_size=batch_size)
        report['transaction_batches'] = getattr(store,'transaction_batches',0)
        store.event('compact_transition_staged',None,report,key='compact-staged:'+source_id)
    return report
