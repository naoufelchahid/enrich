"""Prepare and atomically install a verified compact journal under a writer pause.

Preparation mutates only copied databases. Installation checks the active pair
against the original fresh backup, separately from reconciliations on copies.
SAS retirement requires an exact independent reconstruction from the candidate.
No provider, CRM, Telegram, email, cron or environment actions are implemented.
"""
import argparse
import base64
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from scripts.compact_store import CompactStore
from scripts.compact_transition import stage_legacy, verify_compact_blobs
from scripts.workflow_migration import readonly, logical_manifest, quote, batches, audit_pair
from scripts.workflow_store import encode

LANES = ('qualification','migration','linkedin','discovery','crm','apify','campaigns')


def file_sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def _logical(path):
    with readonly(path) as c:
        return logical_manifest(c)


def _atomic_json(path, value):
    path=Path(path)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as handle:
        temporary=Path(handle.name)
        json.dump(value,handle,ensure_ascii=False,indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o600)
    os.replace(temporary,path)


def _verify_backup(manifest_path, pause_started_at):
    path=Path(manifest_path).resolve(strict=True)
    manifest=json.loads(path.read_text())
    started=datetime.fromisoformat(pause_started_at)
    created=datetime.fromisoformat(manifest['created_at'])
    if started.tzinfo is None or created.tzinfo is None or created<started:
        raise RuntimeError('FRESH_BACKUP_AFTER_PAUSE_REQUIRED')
    if manifest.get('format')!=1 or set(manifest.get('files',{}))!={'staging','journal'}:
        raise ValueError('INVALID_BACKUP_MANIFEST')
    for item in manifest['files'].values():
        if Path(item['archive']).name!=item['archive']:
            raise ValueError('INVALID_BACKUP_ARCHIVE_PATH')
        h=hashlib.sha256()
        with gzip.open(path.parent/item['archive'],'rb') as source:
            for chunk in iter(lambda:source.read(1024*1024),b''):
                h.update(chunk)
        if h.hexdigest()!=item['file_sha256']:
            raise RuntimeError('BACKUP_ARCHIVE_CORRUPT')
    return manifest


def _decode_record(raw):
    record=json.loads(raw)
    return {k:base64.b64decode(v['sqlite_blob_base64'])
        if isinstance(v,dict) and set(v)=={'sqlite_blob_base64'} else v for k,v in record.items()}


def _blob(c, digest):
    row=c.execute('SELECT content_gzip,original_bytes FROM workflow_blobs WHERE sha256=?',(digest,)).fetchone()
    if row is None:
        raise FileNotFoundError(digest)
    raw=gzip.decompress(row[0])
    if len(raw)!=row[1] or hashlib.sha256(raw).hexdigest()!=digest:
        raise RuntimeError('BLOB_VERIFICATION_FAILED')
    return raw


def reconstruct_staging(store, source_id, schema_uri, destination, *, batch_size=256):
    """Restore all exact source rows using the compact journal alone.

Triggers/views/indexes are installed after data, so no synthetic audit events
are generated. The exported outbox is rebuilt from its individually verified
journal_events, and AUTOINCREMENT high-water marks are restored explicitly.
"""
    target=Path(destination).resolve()
    if target.exists():
        raise FileExistsError(str(target))
    schema=json.loads(store.get_blob(schema_uri))
    objects=schema['sqlite_master']
    target.parent.mkdir(parents=True,exist_ok=True)
    try:
        with sqlite3.connect(target) as restored:
            restored.execute('PRAGMA cache_size=-65536')
            restored.execute('PRAGMA foreign_keys=OFF')
            for item in objects:
                if item['type']=='table' and item.get('sql') and not item['name'].startswith('sqlite_'):
                    restored.execute(item['sql'])
            def insert(table, record):
                names=list(record)
                restored.execute('INSERT INTO '+quote(table)+' ('+','.join(quote(k) for k in names)+') VALUES ('+
                    ','.join('?' for _ in names)+')',tuple(record[k] for k in names))
            with store.connection() as c:
                c.execute('PRAGMA cache_size=-65536')
                query='''SELECT r.sha256 FROM workflow_evidence_refs r JOIN workflow_legacy_ids m
                    ON m.candidate_id=r.candidate_id WHERE m.source_id=? AND r.kind='legacy_candidate'
                    ORDER BY CAST(m.legacy_id AS INTEGER),r.sha256'''
                for rows in batches(c.execute(query,(source_id,)),batch_size):
                    with restored:
                        for row in rows:
                            insert('staging_leads',_decode_record(_blob(c,row[0])))
                for rows in batches(c.execute('''SELECT table_name,sha256 FROM workflow_legacy_records
                        WHERE source_id=? ORDER BY table_name,row_order,record_key''',(source_id,)),batch_size):
                    with restored:
                        for table,digest in rows:
                            insert(table,_decode_record(_blob(c,digest)))
                for rows in batches(c.execute('''SELECT event_id,kind,contact_id,payload,recorded_at
                        FROM journal_events WHERE source_id=? ORDER BY event_id''',(source_id,)),batch_size):
                    with restored:
                        restored.executemany('''INSERT INTO outreach_journal_outbox
                            (id,kind,contact_id,payload,recorded_at,exported) VALUES(?,?,?,?,?,1)''',rows)
            with restored:
                if restored.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
                    restored.execute('DELETE FROM sqlite_sequence')
                    restored.executemany('INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)',schema.get('sqlite_sequence',{}).items())
                for kind in ('index','view','trigger'):
                    for item in objects:
                        if item['type']==kind and item.get('sql'):
                            restored.execute(item['sql'])
                restored.execute('PRAGMA user_version='+str(int(schema['user_version'])))
                restored.execute('PRAGMA application_id='+str(int(schema['application_id'])))
            if restored.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
                raise RuntimeError('RECONSTRUCTED_STAGING_INTEGRITY_FAILED')
        target.chmod(0o600)
        return str(target)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def _compare_journal_tables(before, after, *, batch_size=256):
    """Compare every pre-existing row; only explicit migration events may append."""
    result={}
    with readonly(before) as old, readonly(after) as new:
        for table, in old.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            info=list(old.execute('PRAGMA table_info('+quote(table)+')'))
            keys=[r[1] for r in sorted(info,key=lambda r:r[5]) if r[5]]
            order=','.join(quote(k) for k in keys) if keys else 'rowid'
            if table=='workflow_blobs':
                count=0
                for rows in batches(old.execute('SELECT * FROM workflow_blobs ORDER BY sha256'),batch_size):
                    for row in rows:
                        current=new.execute('SELECT * FROM workflow_blobs WHERE sha256=?',(row[0],)).fetchone()
                        if current!=row:
                            raise RuntimeError('PREEXISTING_JOURNAL_TABLE_CHANGED:workflow_blobs')
                        count+=1
                result[table]={'preserved':True,'rows':count,'append_only':True}
                continue
            if table=='sqlite_sequence':
                # Migration events allocate new IDs; original other sequences remain exact.
                prior=dict(old.execute('SELECT name,seq FROM sqlite_sequence'))
                current=dict(new.execute('SELECT name,seq FROM sqlite_sequence'))
                if any(current.get(k)!=v for k,v in prior.items() if k!='workflow_events'):
                    raise RuntimeError('JOURNAL_SEQUENCE_CHANGED')
                result[table]={'preserved':True}
                continue
            suffix=''
            parameters=()
            if table=='workflow_events':
                high=old.execute('SELECT coalesce(max(id),0) FROM workflow_events').fetchone()[0]
                suffix=' WHERE id<=?'
                parameters=(high,)
                extra=list(new.execute('SELECT kind,key FROM workflow_events WHERE id>?',(high,)))
                if any((kind,key.split(':',1)[0]) not in (
                    ('compact_transition_staged','compact-staged'),
                    ('compact_copy_reconciliation','compact-copy-reconciliation')) for kind,key in extra):
                    raise RuntimeError('UNEXPLAINED_JOURNAL_APPEND')
            left=old.execute('SELECT * FROM '+quote(table)+' ORDER BY '+order)
            right=new.execute('SELECT * FROM '+quote(table)+suffix+' ORDER BY '+order,parameters)
            count=0
            while True:
                a,b=left.fetchmany(batch_size),right.fetchmany(batch_size)
                if a!=b:
                    raise RuntimeError('PREEXISTING_JOURNAL_TABLE_CHANGED:'+table)
                if not a:
                    break
                count+=len(a)
            result[table]={'preserved':True,'rows':count}
    return result


def _verify_new_blob_references(before, store):
    with readonly(before) as old, store.connection() as c:
        old_has_blobs=bool(old.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_blobs'").fetchone())
        known={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        query='SELECT sha256 FROM workflow_blobs'
        for table in ('workflow_legacy_records','workflow_evidence_refs','workflow_artifact_refs'):
            if table in known:
                query+=' EXCEPT SELECT sha256 FROM '+table
        for digest, in c.execute(query):
            if old_has_blobs and old.execute('SELECT 1 FROM workflow_blobs WHERE sha256=?',(digest,)).fetchone():
                continue
            if not c.execute('SELECT 1 FROM workflow_events WHERE instr(payload,?)>0 LIMIT 1',(digest,)).fetchone():
                raise RuntimeError('UNREFERENCED_NEW_BLOB')


def prepare_candidate(staging_copy, journal_copy, target, *, backup_manifest, pause_started_at,
                      reconciliation_evidence=(), batch_size=256, fault=None):
    """Build from verified/restored (optionally reconciled) copies, never active DBs."""
    source,archive,target=map(lambda p:Path(p).resolve(),(staging_copy,journal_copy,target))
    backup=_verify_backup(backup_manifest,pause_started_at)
    active={key:Path(item['source']).resolve() for key,item in backup['files'].items()}
    if any(p==a or (p.exists() and a.exists() and os.path.samefile(p,a))
           for p in (source,archive,target) for a in active.values()):
        raise RuntimeError('PREPARATION_REQUIRES_DISTINCT_COPIES')
    if len({source,archive,target})!=3 or target.exists():
        raise ValueError('FRESH_DISTINCT_CANDIDATE_PATH_REQUIRED')
    baseline={'staging':_logical(source),'journal':_logical(archive)}
    changed={key:[table for table in set(current['tables'])|set(backup['files'][key]['logical']['tables'])
        if current['tables'].get(table)!=backup['files'][key]['logical']['tables'].get(table)] for key,current in baseline.items()}
    schema_changed={key:any(current.get(field)!=backup['files'][key]['logical'].get(field)
        for field in ('schema','user_version','application_id')) for key,current in baseline.items()}
    if (any(changed.values()) or any(schema_changed.values())) and not reconciliation_evidence:
        raise RuntimeError('RECONCILIATION_EVIDENCE_REQUIRED')
    target.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(target) as c, readonly(archive) as original:
        original.backup(c)
    target.chmod(0o600)
    store=CompactStore(target)
    for lane in LANES:
        store.set_authority(lane)
    if fault:
        fault('after_copy',store)
    stage=stage_legacy(store,source,writes_paused=True,optimized=True,batch_size=batch_size)
    proof_uris=[]
    for proof in reconciliation_evidence:
        raw=Path(proof).read_bytes()
        proof_uris.append('journal://artifacts/'+store.put_blob(raw))
    if proof_uris:
        store.event('compact_copy_reconciliation',None,{'proof_uris':proof_uris,
            'changed_tables':changed,'schema_changed':schema_changed},
            key='compact-copy-reconciliation:'+stage['source_id'])
    if fault:
        fault('after_stage',store)
    preserved=_compare_journal_tables(archive,target,batch_size=batch_size)
    with tempfile.TemporaryDirectory(prefix='roundtrip-',dir=target.parent) as temporary:
        reconstructed=Path(temporary)/'staging.sqlite'
        reconstruct_staging(store,stage['source_id'],stage['schema_uri'],reconstructed,batch_size=batch_size)
        if _logical(reconstructed)!=baseline['staging']:
            raise RuntimeError('STAGING_ROUNDTRIP_CONTENT_MISMATCH')
    audit=audit_pair(source,target,batch_size=batch_size)
    if audit['missing'] or audit['divergent'] or audit['pending_export']:
        raise RuntimeError('FINAL_CANDIDATE_AUDIT_FAILED')
    verify_compact_blobs(store,batch_size=batch_size)
    _verify_new_blob_references(archive,store)
    with store.connection() as c:
        c.execute('PRAGMA cache_size=-65536')
        if c.execute('PRAGMA integrity_check').fetchall()[0][0]!='ok':
            raise RuntimeError('CANDIDATE_INTEGRITY_FAILED')
        c.execute('PRAGMA journal_mode=DELETE')
    if _logical(source)!=baseline['staging'] or _logical(archive)!=baseline['journal']:
        raise RuntimeError('RECONCILED_COPY_CHANGED_DURING_PREPARATION')
    plan={'format':1,'prepared':True,'created_at':datetime.now(timezone.utc).isoformat(),
        'backup_manifest':str(Path(backup_manifest).resolve()),'backup_manifest_sha256':file_sha256(backup_manifest),
        'pause_started_at':pause_started_at,'active_paths':{k:str(v) for k,v in active.items()},
        'expected_active_logical':{k:item['logical'] for k,item in backup['files'].items()},
        'reconciled_copy_logical':baseline,'reconciliation_changed_tables':changed,
        'reconciliation_schema_changes':schema_changed,
        'reconciliation_proof_uris':proof_uris,'candidate_path':str(target),
        'candidate_sha256':file_sha256(target),'candidate_logical':_logical(target),
        'source_roundtrip_exact':True,'preexisting_journal_tables':preserved,'stage':stage,
        'verified_events':audit['matched'],'provider_calls':0,'env_or_cron_changes':0}
    marker=Path(str(target)+'.plan.json')
    _atomic_json(marker,plan)
    return {**plan,'plan_path':str(marker)}


def install_candidate(plan_path, *, writes_paused=False, retire_staging=False, fault=None):
    """Install only against the unchanged original active pair, then optionally retire SAS.

The caller first stops all process/service writers. Actual SQLite locks and exact
logical comparisons supplement that operator pause; no network action occurs.
A crash after replacement leaves the old SAS intact and is safe to reconcile.
"""
    if not writes_paused:
        raise RuntimeError('ALL_WRITERS_MUST_BE_PAUSED')
    marker=Path(plan_path).resolve(strict=True)
    plan=json.loads(marker.read_text())
    if plan.get('format')!=1 or not plan.get('prepared') or not plan.get('source_roundtrip_exact'):
        raise ValueError('VERIFIED_CANDIDATE_PLAN_REQUIRED')
    if file_sha256(plan['backup_manifest'])!=plan['backup_manifest_sha256']:
        raise RuntimeError('BACKUP_MANIFEST_CHANGED')
    _verify_backup(plan['backup_manifest'],plan['pause_started_at'])
    source=Path(plan['active_paths']['staging'])
    journal=Path(plan['active_paths']['journal'])
    candidate=Path(plan['candidate_path'])
    if any(Path(str(p)+'-wal').exists() and Path(str(p)+'-wal').stat().st_size for p in (source,journal,candidate)):
        raise RuntimeError('WAL_MUST_BE_CHECKPOINTED_BEFORE_REPLACEMENT')
    already_installed=journal.exists() and file_sha256(journal)==plan['candidate_sha256']
    if not source.exists() and already_installed:
        return {'status':'ALREADY_INSTALLED','retired_staging':True,'journal':str(journal)}
    if not source.exists():
        raise RuntimeError('ACTIVE_STAGING_MISSING_BEFORE_CHECK')
    with ExitStack() as stack:
        connections={}
        for key,path in sorted((('staging',source),('journal',journal)),key=lambda item:str(item[1])):
            c=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,timeout=0)
            stack.callback(c.close)
            c.execute('PRAGMA cache_size=-65536')
            c.execute('BEGIN EXCLUSIVE')
            connections[key]=c
        if logical_manifest(connections['staging'])!=plan['expected_active_logical']['staging']:
            raise RuntimeError('ACTIVE_STAGING_CHANGED_SINCE_BACKUP')
        if not already_installed:
            if logical_manifest(connections['journal'])!=plan['expected_active_logical']['journal']:
                raise RuntimeError('ACTIVE_JOURNAL_CHANGED_SINCE_BACKUP')
            if not candidate.is_file():
                raise RuntimeError('CANDIDATE_CHANGED_SINCE_VERIFICATION')
            candidate_connection=sqlite3.connect(candidate.as_uri()+'?mode=rw',uri=True,timeout=0)
            stack.callback(candidate_connection.close)
            candidate_connection.execute('BEGIN EXCLUSIVE')
            if file_sha256(candidate)!=plan['candidate_sha256']:
                raise RuntimeError('CANDIDATE_CHANGED_SINCE_VERIFICATION')
            if candidate.stat().st_dev!=journal.parent.stat().st_dev:
                raise RuntimeError('ATOMIC_REPLACEMENT_REQUIRES_SAME_FILESYSTEM')
            if fault:
                fault('before_replace')
            os.replace(candidate,journal)
            directory=os.open(journal.parent,os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            if fault:
                fault('after_replace')
        if file_sha256(journal)!=plan['candidate_sha256']:
            raise RuntimeError('INSTALLED_JOURNAL_MISMATCH')
        if retire_staging:
            if source.stat().st_nlink!=1:
                raise RuntimeError('STAGING_HAS_OTHER_HARD_LINKS')
            recovered=source.stat().st_size
            source.unlink()
            directory=os.open(source.parent,os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        else:
            recovered=0
    receipt={'status':'INSTALLED','journal':str(journal),'retired_staging':retire_staging,
             'removed_staging_file_bytes':recovered,'reconciled_copy_preserved':True,
             'provider_calls':0,'env_or_cron_changes':0}
    _atomic_json(Path(str(marker)+'.receipt.json'),receipt)
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    actions=parser.add_subparsers(dest='action',required=True)
    prepare=actions.add_parser('prepare')
    prepare.add_argument('--staging-copy',required=True)
    prepare.add_argument('--journal-copy',required=True)
    prepare.add_argument('--candidate',required=True)
    prepare.add_argument('--backup-manifest',required=True)
    prepare.add_argument('--pause-started-at',required=True)
    prepare.add_argument('--proof',action='append',default=[])
    install=actions.add_parser('install')
    install.add_argument('--plan',required=True)
    install.add_argument('--writes-paused',action='store_true')
    install.add_argument('--retire-staging',action='store_true')
    args=parser.parse_args()
    result=prepare_candidate(args.staging_copy,args.journal_copy,args.candidate,
        backup_manifest=args.backup_manifest,pause_started_at=args.pause_started_at,reconciliation_evidence=args.proof) if args.action=='prepare' else install_candidate(args.plan,writes_paused=args.writes_paused,retire_staging=args.retire_staging)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
