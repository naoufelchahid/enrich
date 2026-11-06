import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from scripts.backend_guard import require_legacy
from scripts.compact_store import CompactStore
from scripts.compact_transition import stage_legacy
from scripts.compact_workflow import ApifyWorkflow, LinkedInWorkflow, CampaignWorkflow, dispatch, preview_compact
from scripts.compact_qualification import CompactQualification
from scripts.direct_enrichment import DirectEnrichment
from scripts.outreach_journal import flush_journal, journal_path
from scripts.staging_db import init_staging_db, insert_staging_leads
from scripts.telegram_approval import create_draft
from scripts.workflow_import import import_all
from tests.test_workflow_refactor import FakeCRM, FakeTelegram, FakeNB


def active(tmp_path):
    store = CompactStore(tmp_path/'journal.sqlite')
    for lane in ('qualification','linkedin','discovery','crm','campaigns','apify','migration'):
        store.set_authority(lane)
    return store


def test_three_services_are_independent_and_missing_adapter_is_explicit(tmp_path):
    store = active(tmp_path)
    with patch('scripts.workflow.ThunderbirdClient',side_effect=AssertionError('mail unavailable')):
        assert ApifyWorkflow(store).run('status')['service']=='apify'
        assert LinkedInWorkflow(store).run('acquire')['reason']=='LINKEDIN_ADAPTER_REQUIRED'
        assert ApifyWorkflow(store).run('acquire')['reason']=='APIFY_ADAPTER_REQUIRED'
    adapter = Mock()
    adapter.tick.return_value={'status':'SUBMITTED'}
    assert ApifyWorkflow(store,acquisition=adapter).run('acquire',task_id='one')['status']=='SUBMITTED'
    adapter.tick.assert_called_once_with('one')
    adapter.run_one.return_value={'status':'WAITING'}
    assert LinkedInWorkflow(store,acquisition=adapter).run('acquire')['status']=='WAITING'
    task = store.create_task('discovery','LINKEDIN_DISCOVER',{},key='discover')
    store.finish_task(task['id'],'WAITING',wait_reason='ACCESS_REQUIRED')
    status = LinkedInWorkflow(store).run('status')
    assert status['tasks']=={'WAITING':1} and status['waiting'][0]['id']=='discover'


def test_preview_is_read_only_and_does_not_need_sas_or_create_missing_journal(tmp_path):
    absent = tmp_path/'missing'/'journal.sqlite'
    assert dispatch(absent,service='apify',action='status',dry_run=True)['external_actions']==0
    assert not absent.parent.exists()
    store = active(tmp_path)
    before = hashlib.sha256(open(store.path,'rb').read()).hexdigest()
    result = preview_compact(store.path,service='campaigns',action='send_next_candidate')
    assert result['external_actions']==0
    assert hashlib.sha256(open(store.path,'rb').read()).hexdigest()==before


def test_main_compact_routes_before_legacy_readiness_or_sas_preview(tmp_path,capsys):
    from scripts.workflow import main
    with patch.object(sys,'argv',['workflow','--backend','compact','--state-path',str(tmp_path/'journal.sqlite'),
        '--db-path',str(tmp_path/'never-open.sqlite'),'--task','status','--dry-run']), \
        patch('scripts.workflow.readiness',side_effect=AssertionError('global readiness')), \
        patch('scripts.workflow.preview',side_effect=AssertionError('legacy preview')):
        main()
    result=json.loads(capsys.readouterr().out)
    assert result['backend']=='compact' and result['external_actions']==0
    assert not list(tmp_path.iterdir())


def legacy_fixture(tmp_path, *, weak=False, submitted=False, queue_state=None):
    source = tmp_path/'sas.sqlite'
    DirectEnrichment()._init(str(source))
    row={'source_file':'fixture.xlsx','first_name':'Alice','last_name':'Tazi','company_name':'Example SA',
         'raw_email':None if weak else 'alice.tazi@example.org'}
    import_all(tmp_path,str(source),contacts=[row])
    with sqlite3.connect(source) as c:
        identity=c.execute('SELECT journal_contact_id FROM staging_leads').fetchone()[0]
        if submitted:
            c.execute('''INSERT INTO enrichment_jobs(id,provider,kind,status,payload,reserved,created_at,remote_id)
                VALUES ('old-job','no2bounce','email','SUBMITTED','{"emailList":["alice.tazi@example.org"]}',1,'2026-11-05','remote-1')''')
            c.execute("INSERT INTO enrichment_targets VALUES('old-job','request',1,'{}',0)")
        else:
            c.execute('''INSERT INTO enrichment_jobs(id,provider,kind,status,payload,reserved,created_at)
                VALUES ('old-job','no2bounce','email','UNKNOWN','{"emailList":["alice.tazi@example.org"]}',1,'2026-11-05')''')
            c.execute("INSERT INTO enrichment_targets VALUES('old-job','request',1,'{}',0)")
        if queue_state:
            c.execute('CREATE TABLE linkedin_browser_queue(id TEXT PRIMARY KEY,lead_id INTEGER,status TEXT,result_path TEXT)')
            c.execute('INSERT INTO linkedin_browser_queue VALUES(?,?,?,NULL)',('linkedin-old',1,queue_state))
        c.execute("INSERT INTO email_suppressions VALUES('excluded@example.org','PERMANENT_BOUNCE','2026-11-05')")
    flush_journal(str(source))
    store=CompactStore(journal_path(str(source)))
    for lane in ('qualification','migration','linkedin','campaigns','crm'):
        store.set_authority(lane)
    return source,store,identity,row


def test_transition_preserves_unknown_approved_copy_exclusions_and_exact_evidence_without_sas(tmp_path):
    source,store,identity,row=legacy_fixture(tmp_path)
    store.put_contact({'journal_contact_id':'admitted','clean_email':'admitted@example.org'},status='READY_TO_MAIL')
    draft=create_draft(store,'admitted','admitted@example.org','Subject','Exact body',{'signature_mode':'thunderbird_html'})
    with store.connection() as c:
        c.execute("UPDATE workflow_drafts SET status='APPROVED',approved_by='101' WHERE id=?",(draft['id'],))
        approved=dict(c.execute('SELECT * FROM workflow_drafts').fetchone())
    source_bytes=source.read_bytes()
    first=stage_legacy(store,source,rehearsal=True,batch_size=1)
    second=stage_legacy(store,source,rehearsal=True,batch_size=1)
    assert first['missing']==first['divergent']==0 and second['candidates']==1
    assert source.read_bytes()==source_bytes
    assert store.excluded('excluded@example.org')
    assert any(r['kind']=='legacy_candidate' for r in store.evidence(identity))
    source.rename(tmp_path/'offline-source.sqlite')
    reopened=CompactStore(store.path)
    nb=Mock()
    qualification=CompactQualification(reopened,no2bounce=nb,hygiene=lambda e:'PASS',cost_guard=lambda **kw:True)
    assert qualification.tick(identity)['reason']=='LEGACY_PROVIDER_RECONCILIATION_REQUIRED'
    nb.submit.assert_not_called()
    with reopened.connection() as c:
        assert dict(c.execute('SELECT * FROM workflow_drafts').fetchone())==approved
        assert c.execute("SELECT status FROM workflow_tasks WHERE kind='LEGACY_PROVIDER_BATCH'").fetchone()[0]=='UNKNOWN'
    assert CampaignWorkflow(reopened).run('status')['drafts']=={'APPROVED':1}
    assert not source.exists()


def test_single_submitted_verification_adopts_remote_and_polls_after_source_disappears(tmp_path):
    source,store,identity,row=legacy_fixture(tmp_path,submitted=True)
    outcome=stage_legacy(store,source,rehearsal=True)
    assert outcome['adopted_jobs']==1 and not store.candidate(identity)['data'].get('pending_legacy_jobs')
    source.rename(tmp_path/'offline.sqlite')
    nb=Mock()
    nb.result.return_value={'verdicts':{'alice.tazi@example.org':'VALID'},'raw':{'exact':'result'}}
    runner=CompactQualification(CompactStore(store.path),no2bounce=nb,hygiene=lambda e:'PASS')
    assert runner.tick(identity)['status']=='QUALIFIED'
    nb.result.assert_called_once_with('remote-1')
    nb.submit.assert_not_called()


@pytest.mark.parametrize('state',['PENDING','RUNNING','FAILED','BLOCKED','IMPORTED','NO_MATCH'])
def test_old_linkedin_queue_is_conserved_and_never_restarted_blindly(tmp_path,state):
    from scripts.compact_linkedin import CompactLinkedIn
    source,store,identity,row=legacy_fixture(tmp_path,queue_state=state)
    stage_legacy(store,source,rehearsal=True)
    source.rename(tmp_path/'offline.sqlite')
    assert CompactLinkedIn(store).enqueue(identity)['reason']=='LEGACY_PROVIDER_RECONCILIATION_REQUIRED'
    with store.connection() as c:
        job=c.execute("SELECT status,result FROM workflow_tasks WHERE kind='LEGACY_LINKEDIN'").fetchone()
    assert job[0]==('UNKNOWN' if state=='RUNNING' else 'WAITING')
    assert json.loads(job[1])['legacy_status']==state


def test_weak_source_reimport_uses_old_import_key_without_name_merge(tmp_path):
    source,store,identity,row=legacy_fixture(tmp_path,weak=True)
    stage_legacy(store,source,rehearsal=True)
    source.rename(tmp_path/'offline.sqlite')
    result=import_all(tmp_path,'absent-sas',backend='compact',compact_store=store,contacts=[row])
    assert result['duplicates']==1 and result['inserted']==0
    assert len(store.candidates())==1
    other={**row,'source_file':'other.xlsx'}
    assert import_all(tmp_path,'absent-sas',backend='compact',compact_store=store,contacts=[other])['inserted']==1
    assert len(store.candidates())==2


def test_rehearsal_rejects_active_paths_and_divergent_archive(tmp_path,monkeypatch):
    source,store,identity,row=legacy_fixture(tmp_path)
    monkeypatch.setenv('WORKFLOW_STAGING_PATH',str(source))
    with pytest.raises(RuntimeError,match='DISTINCT_OFFLINE'):
        stage_legacy(store,source,rehearsal=True)
    monkeypatch.delenv('WORKFLOW_STAGING_PATH')
    with store.connection() as c:
        c.execute("UPDATE journal_events SET payload='divergent' WHERE event_id=(SELECT min(event_id) FROM journal_events)")
    with pytest.raises(RuntimeError,match='EXACT_JOURNAL'):
        stage_legacy(store,source,rehearsal=True)


def test_authority_guard_retires_only_selected_pair_lane_and_preserves_isolated_pilot(tmp_path):
    source=tmp_path/'sas.sqlite'
    init_staging_db(str(source)).close()
    store=CompactStore(journal_path(str(source)))
    store.set_authority('qualification')
    with pytest.raises(RuntimeError,match='LEGACY_WRITER_RETIRED'):
        insert_staging_leads(str(source),[{'source_file':'fixture'}])
    require_legacy(str(source),lane='campaigns')
    pilot=tmp_path/'isolated.sqlite'
    init_staging_db(str(pilot)).close()
    assert pilot.exists()
    with pytest.raises(RuntimeError,match='COMPACT_JOURNAL'):
        init_staging_db(store.path)


def test_compact_reviewed_maintenance_uses_candidates_and_exact_evidence(tmp_path):
    from scripts.compact_maintenance import assign_domain_compact, import_verification_compact
    from scripts.domain_review import timestamp
    store=active(tmp_path)
    identity=store.put_candidate({'first_name':'Alice','last_name':'Tazi','company_name':'Example SA',
        'raw_email':'alice.tazi@example.org'},lane='qualification')
    observed=timestamp()
    cache=tmp_path/'cache.sqlite'
    review={'domain':'example.org','status':'FOUND','observed_at':observed,'protocol':'RDAP',
        'source':'https://rdap.example.org/domain/example.org','registrant_names':['Example SA'],
        'evidence_sha256':'a'*64}
    with sqlite3.connect(cache) as c:
        c.execute('CREATE TABLE domain_review_cache(key TEXT,observed_at TEXT,payload TEXT)')
        c.execute('INSERT INTO domain_review_cache VALUES(?,?,?)',('example.org',observed,json.dumps(review)))
    assert assign_domain_compact(store,[identity],cache,'example.org','Example SA')['status']=='UPDATED'
    assert store.candidate(identity)['data']['company_domain']=='example.org'
    response={'trackingId':'one','overallStatus':'Completed','percent':100,'totalRecord':1}
    raw={'response':json.dumps(response).encode(),
        'csv':b'email,finalScore,finalScoreValue,catchall\nalice.tazi@example.org,100,Deliverable,false\n'}
    manifest={'tracking_id':'one','observed_at':observed}
    for kind,content in raw.items():
        filename=kind+'.txt'
        (tmp_path/filename).write_bytes(content)
        manifest[kind]={'file':filename,'sha256':hashlib.sha256(content).hexdigest()}
    path=tmp_path/'manifest.json'
    path.write_text(json.dumps(manifest))
    assert import_verification_compact(store,identity,path)['verdict']=='VALID'
    data=store.candidate(identity)['data']
    assert data['validated_email']==data['clean_email']=='alice.tazi@example.org'
    before=store.candidate(identity)
    manifest['csv']['sha256']='0'*64
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='HASH_MISMATCH'):
        import_verification_compact(store,identity,path)
    assert store.candidate(identity)==before
    assert not (tmp_path/'staging.sqlite').exists()


def test_custom_journal_authority_also_blocks_legacy_workflow_writer(tmp_path):
    from scripts.workflow import Workflow
    sas=tmp_path/'sas.sqlite'
    init_staging_db(str(sas)).close()
    custom=CompactStore(tmp_path/'custom-journal.sqlite')
    custom.set_authority('qualification')
    workflow=Workflow(sas,state_path=custom.path)
    with pytest.raises(RuntimeError,match='LEGACY_WRITER_RETIRED'):
        workflow.import_sources(tmp_path)
    isolated=tmp_path/'another-pilot.sqlite'
    init_staging_db(str(isolated)).close()
    assert isolated.exists()
