"""Apply captured GET-only reconciliation evidence to explicit OFFLINE copies.

Never connects to a supplier or CRM. Canonical active files (including hardlink
aliases) are refused. Staging commits precede an idempotent outbox export to the
explicit journal path, so an interruption can safely replay. Unknown CRM creates
remain UNKNOWN: absence or an unrelated existing person never proves success.
"""
import argparse
import gzip
import hashlib
import json
import math
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from scripts.outreach_journal import flush_journal
from scripts.workflow_files import save_file
from scripts.workflow_store import encode, now

ROOT = Path(__file__).resolve().parents[1]


def _copies(db_path, journal_path, offline_copy):
    if not offline_copy:
        raise ValueError('OFFLINE_COPY_ACKNOWLEDGEMENT_REQUIRED')
    paths = [Path(db_path).resolve(strict=True),Path(journal_path).resolve(strict=True)]
    active = [ROOT/'bdd/workflow/staging.sqlite',ROOT/'bdd/workflow/staging.sqlite.journal.sqlite']
    active += [Path(os.environ[key]).resolve() for key in ('WORKFLOW_STAGING_PATH','WORKFLOW_STATE_PATH') if os.environ.get(key)]
    for path in paths:
        if any(path==item.resolve() or (item.exists() and os.path.samefile(path,item)) for item in active):
            raise ValueError('ACTIVE_DATABASE_REFUSED')
    if os.path.samefile(*paths):
        raise ValueError('DISTINCT_DATABASE_COPIES_REQUIRED')
    return paths


class _EvidenceStore:
    """Only blob/file APIs, without initializing a workflow or another archive."""
    def __init__(self,path):
        self.path=str(path)

    @contextmanager
    def connection(self):
        c=sqlite3.connect(Path(self.path).as_uri()+'?mode=rw',uri=True,timeout=10)
        c.row_factory=sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def put_blob(self,raw):
        digest=hashlib.sha256(raw).hexdigest()
        with self.connection() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS workflow_blobs (
                sha256 TEXT PRIMARY KEY, content_gzip BLOB NOT NULL,
                original_bytes INTEGER NOT NULL, created_at TEXT NOT NULL)''')
            c.execute('INSERT OR IGNORE INTO workflow_blobs VALUES(?,?,?,?)',
                (digest,gzip.compress(raw,mtime=0),len(raw),now()))
        if self.get_blob(digest)!=raw:
            raise RuntimeError('RECONCILIATION_EVIDENCE_CORRUPT')
        return digest

    def get_blob(self,digest):
        with self.connection() as c:
            row=c.execute('SELECT content_gzip,original_bytes FROM workflow_blobs WHERE sha256=?',(digest,)).fetchone()
        raw=gzip.decompress(row[0])
        if len(raw)!=row[1] or hashlib.sha256(raw).hexdigest()!=digest:
            raise RuntimeError('RECONCILIATION_EVIDENCE_CORRUPT')
        return raw


def _read(path):
    return json.loads(path.read_bytes())


def _event(c,key,kind,contact_id,payload):
    c.execute('INSERT OR IGNORE INTO workflow_events(key,kind,contact_id,payload,created_at) VALUES(?,?,?,?,?)',
        (key,kind,contact_id,encode(payload),now()))


def _validated_no_match(c,local,response):
    observed=local['job']
    row=c.execute('SELECT * FROM enrichment_jobs WHERE id=?',(observed['id'],)).fetchone()
    if row is None:
        raise ValueError('RECONCILIATION_JOB_NOT_FOUND')
    job=dict(row)
    if (job['provider']!='fullenrich' or job['kind']!='contact' or job['remote_id']!=observed['remote_id']
            or job['payload']!=observed['payload'] or job['status'] not in ('SUBMITTED','RECEIVED','FINISHED','NO_MATCH')):
        raise ValueError('RECONCILIATION_JOB_CHANGED')
    if response.get('id')!=job['remote_id'] or response.get('status')!='FINISHED' or not isinstance(response.get('data'),list) or len(response['data'])!=1:
        raise ValueError('FULLENRICH_FINISHED_SINGLE_TARGET_REQUIRED')
    sent=json.loads(job['payload']).get('data')
    targets=[dict(row) for row in c.execute('SELECT * FROM enrichment_targets WHERE job_id=?',(job['id'],))]
    if not isinstance(sent,list) or len(sent)!=1 or len(targets)!=1:
        raise ValueError('FULLENRICH_SINGLE_TARGET_REQUIRED')
    result=response['data'][0]
    if not isinstance(result,dict) or result.get('custom')!=sent[0].get('custom'):
        raise ValueError('FULLENRICH_RESULT_CORRELATION_MISMATCH')
    key=(result.get('custom') or {}).get('request_key')
    if not key or targets[0]['request_key']!=key:
        raise ValueError('FULLENRICH_TARGET_KEY_MISMATCH')
    original_targets=local.get('targets')
    if not isinstance(original_targets,list) or len(original_targets)!=1 or any(targets[0].get(k)!=original_targets[0].get(k) for k in ('job_id','request_key','lead_id','input_json')):
        raise ValueError('FULLENRICH_TARGET_CHANGED')
    info=result.get('contact_info')
    if not isinstance(info,dict):
        raise ValueError('FULLENRICH_CONTACT_RESULT_INVALID')
    most=info.get('most_probable_work_email')
    if most is not None and not isinstance(most,dict):
        raise ValueError('FULLENRICH_CONTACT_RESULT_INVALID')
    if (most or {}).get('email'):
        raise ValueError('BUSINESS_EMAIL_PRESENT_REQUIRES_NORMAL_QUALIFICATION')
    lead=c.execute('SELECT * FROM staging_leads WHERE id=?',(targets[0]['lead_id'],)).fetchone()
    expected=local.get('leads') or []
    if not lead or len(expected)!=1 or lead['journal_contact_id']!=expected[0]['journal_contact_id']:
        raise ValueError('FULLENRICH_CONTACT_IDENTITY_CHANGED')
    for field in ('first_name','last_name','company_name','linkedin_url','raw_email','clean_email','validated_email','email_validated_at'):
        if lead[field] != expected[0].get(field):
            raise ValueError('FULLENRICH_CONTACT_CHANGED')
    if job['status'] in ('FINISHED','NO_MATCH') and job.get('response') and json.loads(job['response'])!=response:
        raise ValueError('FULLENRICH_TERMINAL_RESPONSE_CHANGED')
    return job,targets[0],dict(lead),result


def apply_reconciliation(db_path,journal_path,evidence_dir,*,offline_copy=False):
    """Offline-only apply, returning aggregate counts; never submit or send."""
    db_path,journal_path=_copies(db_path,journal_path,offline_copy)
    evidence=Path(evidence_dir).resolve(strict=True)
    if not evidence.is_dir():
        raise ValueError('EVIDENCE_DIRECTORY_REQUIRED')
    store=_EvidenceStore(journal_path)
    files={}
    for path in sorted(evidence.iterdir()):
        if path.is_file() and not path.is_symlink():
            digest=save_file(store,path)
            files[path.name]='journal://artifacts/'+digest
    if 'run.json' not in files:
        raise ValueError('RECONCILIATION_RUN_MANIFEST_REQUIRED')
    c=sqlite3.connect(db_path.as_uri()+'?mode=rw',uri=True,timeout=10);c.row_factory=sqlite3.Row
    changes=[]
    try:
        for path in sorted(evidence.glob('fullenrich-*-local.json')):
            prefix=path.name.removesuffix('-local.json');response_path=evidence/(prefix+'-result.json')
            if not response_path.exists():
                continue  # Retrieval errors keep the original unresolved state.
            local,response=_read(path),_read(response_path)
            if response is None:
                continue
            attested=False
            for body_path in evidence.glob(prefix+'-http-*.body'):
                metadata=body_path.with_suffix('.json')
                if not metadata.exists():
                    continue
                meta=_read(metadata)
                raw=body_path.read_bytes()
                try:
                    same=_read(body_path)==response
                except (ValueError,UnicodeError):
                    same=False
                if (meta.get('method')=='GET' and meta.get('status_code')==200
                        and meta.get('response_sha256',meta.get('sha256'))==hashlib.sha256(raw).hexdigest() and same):
                    attested=True
                    break
            if not attested:
                raise ValueError('EXACT_HTTP_RESULT_EVIDENCE_REQUIRED')
            with c:
                c.execute('BEGIN IMMEDIATE')
                job,target,lead,result=_validated_no_match(c,local,response)
                cost=(response.get('cost') or {}).get('credits') if isinstance(response.get('cost'),dict) else None
                if isinstance(cost,bool) or not isinstance(cost,(int,float)) or not math.isfinite(cost) or cost<0:
                    cost=job.get('cost')  # Unknown real usage retains the bound.
                if job['status'] not in ('FINISHED','NO_MATCH'):
                    c.execute("UPDATE enrichment_jobs SET status='FINISHED',response=?,cost=?,error=?,next_poll_at=NULL WHERE id=?",
                        (encode(response),cost,'COST_EXCEEDED_RESERVATION' if cost is not None and cost>job['reserved'] else 'NO_MATCH_NO_WORK_EMAIL',job['id']))
                c.execute("UPDATE enrichment_cache SET status='NO_MATCH',result=? WHERE job_id=? AND request_key=? AND (status!='NO_MATCH' OR result IS NOT ?)",
                    (encode(result),job['id'],target['request_key'],encode(result)))
                c.execute('UPDATE enrichment_targets SET applied=1 WHERE job_id=? AND request_key=? AND lead_id=? AND applied!=1',
                    (job['id'],target['request_key'],target['lead_id']))
                c.execute("UPDATE staging_leads SET contact_discovery_status='NO_MATCH',workflow_wait_reason='NO_BUSINESS_EMAIL_FOUND' WHERE id=? AND (contact_discovery_status IS NOT 'NO_MATCH' OR workflow_wait_reason IS NOT 'NO_BUSINESS_EMAIL_FOUND')",(target['lead_id'],))
            # Journal event can be replayed after the source commit on a failure.
            digest=hashlib.sha256(response_path.read_bytes()).hexdigest()
            payload={'job_id':job['id'],'remote_id':job['remote_id'],'outcome':'NO_MATCH',
                'contact_id':lead['journal_contact_id'],'request_proof':files[path.name],
                'response_proof':files[response_path.name],'automatic_resubmission':False}
            with store.connection() as archive:
                _event(archive,'reconciliation:fullenrich:'+job['id']+':'+digest,'provider_no_match_reconciled',lead['journal_contact_id'],payload)
            changes.append({'kind':'fullenrich','job_id':job['id'],'state':'NO_MATCH','contact_id':lead['journal_contact_id']})
    finally:
        c.close()
    unknown_count=0
    for path in sorted(evidence.glob('crm-*-local.json')):
        local=_read(path);observed=local['operation'];prefix='crm-'+observed['id']
        with store.connection() as archive:
            current=archive.execute('SELECT * FROM workflow_operations WHERE id=?',(observed['id'],)).fetchone()
            if (current is None or current['kind']!='CREATE_PERSON' or current['status']!='UNKNOWN'
                    or current['payload']!=observed['payload'] or current['contact_id']!=observed['contact_id']):
                raise ValueError('CRM_UNKNOWN_OPERATION_CHANGED')
            proof={name:uri for name,uri in files.items() if name.startswith(prefix)}
            digest=hashlib.sha256(encode(proof).encode()).hexdigest()
            _event(archive,'reconciliation:crm-unknown:'+current['id']+':'+digest,'crm_unknown_readback',
                current['contact_id'],{'operation_id':current['id'],'state':'UNKNOWN',
                'outcome':'INITIAL_CREATE_NOT_PROVEN','automatic_resubmission':False,'proofs':proof})
        unknown_count+=1
    # Parent-provided explicit path prevents an accidental third journal file.
    exported=flush_journal(str(db_path),archive_path=str(journal_path))
    return {'offline_copy':True,'provider_no_match':len(changes),'crm_unknown_preserved':unknown_count,
            'evidence_files':len(files),'outbox_exported':exported,'changes':changes}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db-path',required=True);parser.add_argument('--journal-path',required=True)
    parser.add_argument('--evidence-dir',required=True);parser.add_argument('--offline-copy',action='store_true')
    args=parser.parse_args(argv)
    print(json.dumps(apply_reconciliation(args.db_path,args.journal_path,args.evidence_dir,offline_copy=args.offline_copy),sort_keys=True))


if __name__=='__main__':
    main()
