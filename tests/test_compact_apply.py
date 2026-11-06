import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.compact_apply import prepare_candidate, install_candidate, reconstruct_staging, file_sha256
from scripts.compact_cutover import _fixture
from scripts.compact_store import CompactStore
from scripts.outreach_journal import flush_journal
from scripts.workflow_migration import backup_pair, restore_pair, readonly, logical_manifest


def pair(tmp_path, *, reconciled=False):
    source,journal,identities,proof,sha=_fixture(tmp_path)
    # Preserve source high-water marks and row order/type of a table without a PK.
    with sqlite3.connect(source) as c:
        c.execute('CREATE TABLE custom_evidence(label TEXT,payload BLOB)')
        c.executemany('INSERT INTO custom_evidence VALUES(?,?)',[('z',b'\x00\x01'),('a',b'\xff'),('z',b'\x00\x01')])
        c.execute("UPDATE sqlite_sequence SET seq=seq+17 WHERE name='staging_leads'")
    pause=datetime.now(timezone.utc).isoformat()
    manifest=backup_pair(source,journal,tmp_path/'backups',writes_paused=True)
    copies=restore_pair(manifest,tmp_path/'copies')
    copy_source,copy_journal=Path(copies['files']['staging']),Path(copies['files']['journal'])
    evidence=[]
    if reconciled:
        response={'id':'fe-fixture','status':'FINISHED','data':[], 'synthetic':True}
        with sqlite3.connect(copy_source) as c:
            c.execute("UPDATE enrichment_jobs SET status='FINISHED',response=? WHERE id='legacy-fe'",(json.dumps(response),))
            c.execute("UPDATE enrichment_targets SET applied=1 WHERE job_id='legacy-fe'")
            c.execute("UPDATE staging_leads SET contact_discovery_status='NO_MATCH' WHERE id=2")
        flush_journal(copy_source,archive_path=copy_journal)
        proof=tmp_path/'reconciliation.json'
        proof.write_text(json.dumps(response))
        evidence=[proof]
    return source,journal,copy_source,copy_journal,manifest,pause,evidence


def prepare(tmp_path, values, **kwargs):
    source,journal,copy_source,copy_journal,manifest,pause,evidence=values
    return prepare_candidate(copy_source,copy_journal,tmp_path/'candidate.sqlite',
        backup_manifest=manifest,pause_started_at=pause,reconciliation_evidence=evidence,batch_size=2,**kwargs)


def test_verified_candidate_reconstructs_reconciled_copy_and_installs_against_original_active_pair(tmp_path):
    values=pair(tmp_path,reconciled=True)
    source,journal,copy_source,copy_journal,manifest,pause,evidence=values
    initial_source_sha=file_sha256(source)
    plan=prepare(tmp_path,values)
    assert plan['source_roundtrip_exact'] and plan['verified_events']>0
    assert plan['reconciliation_changed_tables']['staging']
    assert plan['reconciliation_proof_uris']
    assert plan['stage']['transaction_batches']>0
    assert file_sha256(source)==initial_source_sha
    # An interrupted installation after atomic replacement retains the SAS.
    def interrupted(phase):
        if phase=='after_replace':
            raise RuntimeError('interrupted after replacement')
    with pytest.raises(RuntimeError,match='after replacement'):
        install_candidate(plan['plan_path'],writes_paused=True,retire_staging=True,fault=interrupted)
    assert source.exists() and file_sha256(journal)==plan['candidate_sha256']
    result=install_candidate(plan['plan_path'],writes_paused=True,retire_staging=True)
    assert result['status']=='INSTALLED' and result['retired_staging'] and not source.exists()
    assert result['provider_calls']==result['env_or_cron_changes']==0
    store=CompactStore(journal)
    assert all(store.authority(lane)=='compact' for lane in ('qualification','linkedin','campaigns','crm'))
    assert any(c['data'].get('contact_discovery_status')=='NO_MATCH' for c in store.candidates())
    recovered=tmp_path/'recovered-from-only-journal.sqlite'
    reconstruct_staging(store,plan['stage']['source_id'],plan['stage']['schema_uri'],recovered)
    with readonly(copy_source) as a,readonly(recovered) as b:
        assert logical_manifest(a)==logical_manifest(b)
    assert install_candidate(plan['plan_path'],writes_paused=True,retire_staging=True)['status']=='ALREADY_INSTALLED'


@pytest.mark.parametrize('which',['staging','journal','candidate'])
def test_changed_active_or_candidate_state_blocks_replacement(tmp_path,which):
    values=pair(tmp_path)
    source,journal,*_=values
    plan=prepare(tmp_path,values)
    if which=='staging':
        with sqlite3.connect(source) as c:
            c.execute("UPDATE staging_leads SET first_name='changed' WHERE id=1")
    elif which=='journal':
        with sqlite3.connect(journal) as c:
            c.execute("INSERT INTO workflow_meta VALUES('new-result','must survive')")
    else:
        with sqlite3.connect(plan['candidate_path']) as c:
            c.execute("INSERT INTO workflow_meta VALUES('tampered','candidate')")
    before=file_sha256(journal)
    with pytest.raises(RuntimeError,match='CHANGED_SINCE'):
        install_candidate(plan['plan_path'],writes_paused=True,retire_staging=True)
    assert source.exists() and file_sha256(journal)==before


def test_preparation_interruptions_leave_actives_unchanged_without_installable_plan(tmp_path):
    values=pair(tmp_path)
    source,journal,*_=values
    before=(file_sha256(source),file_sha256(journal))
    def interrupted(phase,store):
        if phase=='after_stage':
            raise RuntimeError('interrupt preparation')
    with pytest.raises(RuntimeError,match='interrupt preparation'):
        prepare(tmp_path,values,fault=interrupted)
    assert not (tmp_path/'candidate.sqlite.plan.json').exists()
    assert (file_sha256(source),file_sha256(journal))==before


def test_copy_reconciliation_requires_evidence_and_preserves_unknown_crm(tmp_path):
    values=pair(tmp_path,reconciled=True)
    source,journal,copy_source,copy_journal,manifest,pause,evidence=values
    with pytest.raises(RuntimeError,match='RECONCILIATION_EVIDENCE_REQUIRED'):
        prepare_candidate(copy_source,copy_journal,tmp_path/'unproven.sqlite',backup_manifest=manifest,pause_started_at=pause)
    plan=prepare(tmp_path,values)
    store=CompactStore(plan['candidate_path'])
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM workflow_operations WHERE status='UNKNOWN'").fetchone()[0]==2
        assert c.execute("SELECT count(*) FROM workflow_drafts WHERE status='APPROVED'").fetchone()[0]==1
    assert store.excluded('excluded@example.org')
    assert all(store.get_blob(uri) for uri in plan['reconciliation_proof_uris'])


def test_corrupt_blob_refuses_plan_and_active_paths_cannot_be_used_for_preparation(tmp_path):
    values=pair(tmp_path)
    source,journal,copy_source,copy_journal,manifest,pause,evidence=values
    with pytest.raises(RuntimeError,match='DISTINCT_COPIES'):
        prepare_candidate(source,journal,tmp_path/'bad.sqlite',backup_manifest=manifest,pause_started_at=pause)
    def corrupt(phase,store):
        if phase=='after_stage':
            with store.connection() as c:
                c.execute("UPDATE workflow_blobs SET content_gzip=X'00' WHERE sha256=(SELECT min(sha256) FROM workflow_blobs)")
    with pytest.raises((RuntimeError,OSError,EOFError)):
        prepare(tmp_path,values,fault=corrupt)
    assert source.exists() and journal.exists()
    assert not (tmp_path/'candidate.sqlite.plan.json').exists()


def test_writer_lock_prevents_install_and_normal_install_can_keep_sas(tmp_path):
    values=pair(tmp_path)
    source,journal,*_=values
    plan=prepare(tmp_path,values)
    with sqlite3.connect(source) as active_writer:
        active_writer.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError,match='locked'):
            install_candidate(plan['plan_path'],writes_paused=True)
    result=install_candidate(plan['plan_path'],writes_paused=True)
    assert not result['retired_staging'] and source.exists()
    assert file_sha256(journal)==plan['candidate_sha256']


def test_installed_inode_stays_locked_across_atomic_rename_until_receipt(tmp_path):
    values=pair(tmp_path)
    source,journal,*_=values
    plan=prepare(tmp_path,values)
    observed=[]
    def competing_writer(phase):
        if phase=='after_replace':
            with sqlite3.connect(journal,timeout=0) as competitor:
                with pytest.raises(sqlite3.OperationalError,match='locked'):
                    competitor.execute('BEGIN IMMEDIATE')
            observed.append('locked')
    result=install_candidate(plan['plan_path'],writes_paused=True,retire_staging=True,fault=competing_writer)
    assert observed==['locked'] and result['retired_staging']


from tests.test_compact_reconcile import fixture as reconciliation_fixture


def test_real_helper_pipeline_preserves_preexisting_blobs_and_file_refs(tmp_path,reconciliation_fixture):
    from scripts.compact_reconcile import apply_reconciliation
    source,archive,evidence,*_=reconciliation_fixture
    flush_journal(source,archive_path=archive)
    pause=datetime.now(timezone.utc).isoformat()
    manifest=backup_pair(source,archive,tmp_path/'pipeline-backup',writes_paused=True)
    copies=restore_pair(manifest,tmp_path/'pipeline-copies')
    local_source,local_journal=Path(copies['files']['staging']),Path(copies['files']['journal'])
    assert apply_reconciliation(local_source,local_journal,evidence,offline_copy=True)['provider_no_match']==1
    with sqlite3.connect(local_journal) as c:
        original_blobs=c.execute('SELECT * FROM workflow_blobs ORDER BY sha256').fetchall()
        refs=c.execute('SELECT * FROM workflow_artifact_refs ORDER BY path,sha256').fetchall()
    assert original_blobs and refs
    plan=prepare_candidate(local_source,local_journal,tmp_path/'pipeline-candidate.sqlite',
        backup_manifest=manifest,pause_started_at=pause,reconciliation_evidence=list(evidence.iterdir()))
    with sqlite3.connect(plan['candidate_path']) as c:
        for row in original_blobs:
            assert c.execute('SELECT * FROM workflow_blobs WHERE sha256=?',(row[0],)).fetchone()==row
        assert c.execute('SELECT * FROM workflow_artifact_refs ORDER BY path,sha256').fetchall()==refs
    assert plan['preexisting_journal_tables']['workflow_blobs']['append_only']


def test_schema_only_copy_change_requires_explicit_reconciliation_evidence(tmp_path):
    values=pair(tmp_path)
    source,journal,copy_source,copy_journal,manifest,pause,evidence=values
    with sqlite3.connect(copy_source) as c:
        c.execute('CREATE INDEX schema_only_change ON staging_leads(first_name)')
    with pytest.raises(RuntimeError,match='RECONCILIATION_EVIDENCE_REQUIRED'):
        prepare(tmp_path,values)


def test_unreferenced_new_blob_prevents_verified_plan(tmp_path):
    values=pair(tmp_path)
    def orphan(phase,store):
        if phase=='after_stage':
            store.put_blob(b'unreferenced accidental data')
    with pytest.raises(RuntimeError,match='UNREFERENCED_NEW_BLOB'):
        prepare(tmp_path,values,fault=orphan)
