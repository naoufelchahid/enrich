import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from scripts.circuit_breaker import CircuitBreaker
from scripts.direct_enrichment import DirectEnrichment
from scripts.enrichment_clients import ProviderError
from scripts.prompt_engine import draft_workflow_email
from scripts.telegram_approval import process_update
from scripts.twenty_client import TwentyClient
from scripts.workflow import Workflow
from scripts.workflow_import import import_all
from scripts.workflow_schedule import install_cron
from scripts.workflow_sender import send_approved
from scripts.workflow_store import WorkflowStore, encode
from scripts.thunderbird_client import ThunderbirdClient
from tests.test_workflow_refactor import FakeCRM, FakeTelegram, FakeNB, FakeFE, source, next_morning, sample_draft, approve, state


def test_homonyms_without_identity_are_not_merged_across_sources_or_occurrences(tmp_path):
    rows=[source(source_file='BDD1',raw_email=None),source(source_file='BDD2',raw_email=None),
          source(source_file='BDD2',raw_email=None)]
    path=str(tmp_path/'sas.sqlite')
    assert import_all(tmp_path,path,contacts=rows)['inserted']==3
    assert import_all(tmp_path,path,contacts=rows)['inserted']==0
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(DISTINCT journal_contact_id) FROM staging_leads').fetchone()[0]==3


def test_shared_source_email_does_not_collapse_changed_person_identity(tmp_path):
    rows=[source(),source(first_name='Amina')]
    result=import_all(tmp_path,str(tmp_path/'sas.sqlite'),contacts=rows)
    assert result['accepted']==2 and result['ambiguous']==1


def test_paid_unknown_request_never_resubmits(tmp_path):
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source()])
    nb=FakeNB()
    nb.submit=MagicMock(side_effect=ProviderError('NETWORK_ERROR',uncertain=True))
    worker=DirectEnrichment(workflow=True,no2bounce=nb,fullenrich=FakeFE(),poll_seconds=0)
    worker.audit_staging_db(path,1)
    worker.audit_staging_db(path,1)
    assert nb.submit.call_count==1
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*),status FROM enrichment_jobs').fetchone()==(1,'UNKNOWN')


def test_definitely_rejected_post_retries_three_times_with_one_job(tmp_path):
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source()])
    nb=FakeNB()
    nb.submit=MagicMock(side_effect=ProviderError('HTTP_429',uncertain=False))
    worker=DirectEnrichment(workflow=True,no2bounce=nb,fullenrich=FakeFE(),poll_seconds=0)
    for _ in range(5):
        worker.audit_staging_db(path,1)
        with sqlite3.connect(path) as c:
            c.execute("UPDATE enrichment_jobs SET next_poll_at='2000-01-01T00:00:00+00:00'")
    assert nb.submit.call_count==3
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*),status,retry_attempts FROM enrichment_jobs').fetchone()==(1,'FAILED',3)


def test_provider_result_read_failures_stop_after_three_attempts(tmp_path):
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source()])
    nb=FakeNB()
    nb.result=MagicMock(side_effect=ProviderError('NETWORK_ERROR'))
    worker=DirectEnrichment(workflow=True,no2bounce=nb,fullenrich=FakeFE(),poll_seconds=0)
    worker.audit_staging_db(path,1)
    for _ in range(5):
        with sqlite3.connect(path) as c:
            c.execute("UPDATE enrichment_jobs SET next_poll_at='2000-01-01T00:00:00+00:00'")
        worker.audit_staging_db(path,1)
    assert nb.result.call_count==3 and len(nb.calls)==1


def test_crm_read_retry_budget_persists_between_client_instances(tmp_path):
    store=WorkflowStore(tmp_path/'state.sqlite')
    with patch('scripts.twenty_client.requests.request',side_effect=requests.ConnectionError) as transport:
        for index in range(5):
            client=TwentyClient(api_key='test-key')
            client.workflow_store=store
            with pytest.raises(RuntimeError):
                client.workflow_request('GET','/rest/open-api/core')
            with store.connection() as c:
                c.execute("UPDATE workflow_operations SET next_attempt_at='2000-01-01T00:00:00+00:00'")
        assert transport.call_count==3


def test_no2bounce_expired_proof_gets_a_new_verification_key(tmp_path):
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source()])
    nb=FakeNB()
    worker=DirectEnrichment(workflow=True,no2bounce=nb,fullenrich=FakeFE(),poll_seconds=0)
    worker.audit_staging_db(path,1)
    worker.audit_staging_db(path,1)
    expired=(datetime.now(timezone.utc)-timedelta(days=91)).isoformat()
    with sqlite3.connect(path) as c:
        c.execute('UPDATE staging_leads SET email_validated_at=?',(expired,))
        c.execute('UPDATE enrichment_jobs SET created_at=?',(expired,))
    worker.audit_staging_db(path,1)
    assert len(nb.calls)==2


def test_no_fullenrich_rediscovery_after_confirmed_no_match(tmp_path):
    from tests.helpers import set_test_linkedin_confirmation
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source(raw_email=None)])
    set_test_linkedin_confirmation(path,url='https://www.linkedin.com/in/alice-tazi')
    fe=FakeFE()
    def no_match(identity):
        return {'id':identity,'status':'FINISHED','cost':{'credits':0},
                'data':[{'custom':fe.calls[0]['data'][0]['custom']}]}
    fe.result=no_match
    worker=DirectEnrichment(workflow=True,no2bounce=FakeNB(),fullenrich=fe,poll_seconds=0)
    for _ in range(5):
        worker.audit_staging_db(path,1)
    assert len(fe.calls)==1
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT contact_discovery_status FROM staging_leads').fetchone()[0]=='NO_MATCH'


def test_telegram_lost_submission_can_be_reconciled_by_exact_callback_message(state):
    store,lead,telegram,_=state
    draft=sample_draft(store,lead)
    from scripts.telegram_approval import submit_draft
    with patch.object(telegram,'call',side_effect=RuntimeError('lost response')):
        assert submit_draft(store,telegram,draft['id'])['status']=='TELEGRAM_RECONCILIATION_REQUIRED'
    from scripts.telegram_approval import approval_text
    text=approval_text(draft)
    update={'update_id':1,'callback_query':{'from':{'id':101},'data':'approve:'+draft['callback_token'],
            'message':{'message_id':9,'text':text,'chat':{'id':202}}}}
    assert process_update(store,telegram,update)['status']=='APPROVED'


def test_concurrent_senders_only_submit_once(state):
    store,lead,telegram,breaker=state
    draft=sample_draft(store,lead)
    approve(store,telegram,draft)
    entered,release=threading.Event(),threading.Event()
    mail=ThunderbirdClient(dry_run=True)
    def transport(**kwargs):
        entered.set()
        assert release.wait(3)
        return {'success':True}
    def send():
        try:
            return send_approved(store,mail,breaker,'101',at=next_morning())
        except RuntimeError as error:
            return {'status':str(error)}
    with patch.object(mail,'send_email',side_effect=transport) as mock:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(send)
            assert entered.wait(3)
            second=pool.submit(send)
            second_result=second.result(timeout=3)
            release.set()
            first_result=first.result(timeout=3)
        assert mock.call_count==1
        assert {first_result['status'],second_result['status']}=={'SENT','WORKFLOW_BUSY'}










def test_cron_installation_does_not_write_when_configuration_missing():
    command=MagicMock(returncode=0,stdout='0 * * * * existing-command\n')
    with patch('scripts.workflow_schedule.subprocess.run',return_value=command) as run:
        result=install_cron(apply=True,ready={'ready':False,'missing':['telegram','twenty']})
        assert result['status']=='ACTIVATION_BLOCKED'
        assert run.call_count==1


def test_scheduling_uses_transport_confirmation_time_for_global_spacing(state):
    store,lead,telegram,breaker=state
    initial=sample_draft(store,lead)
    approve(store,telegram,initial)
    reply=sample_draft(store,lead,kind='REPLY')
    approve(store,telegram,reply,update_id=2)
    at=next_morning()
    mail=ThunderbirdClient(dry_run=True)
    assert send_approved(store,mail,breaker,'101',at=at,clock=lambda:at+timedelta(minutes=1))['status']=='SENT'
    assert send_approved(store,mail,breaker,'101',at=at+timedelta(minutes=45))['status']=='INTERVAL_NOT_ELAPSED'


def test_no_new_provider_submission_near_end_of_preparation_window(tmp_path):
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source()])
    nb,fe=FakeNB(),FakeFE()
    worker=DirectEnrichment(workflow=True,no2bounce=nb,fullenrich=fe)
    at=datetime.now(timezone.utc)
    worker.clock=lambda:at
    worker.deadline=at+timedelta(seconds=20)
    worker.audit_staging_db(path,1)
    assert nb.calls==[] and fe.calls==[]


def test_recent_comment_is_a_sourced_activity_hook():
    from scripts.prompt_engine import select_workflow_hook
    at=datetime.now(timezone.utc)
    comment={'author':'Alice Tazi','text':'L’IA et la productivité sont liées.',
             'source_url':'https://www.linkedin.com/in/alice-tazi/recent-activity/comments/',
             'published_at':(at-timedelta(days=2)).isoformat(),'date_precision':'DAY','text_status':'EXPANDED'}
    hook=select_workflow_hook(source(linkedin_comments_json=json.dumps([comment])),at=at)
    assert hook['kind']=='ACTIVITY' and hook['source']==comment['source_url'] and hook['date']==comment['published_at']
    assert hook['activity_type']=='COMMENT'


def test_draft_request_key_cannot_reuse_a_different_content(state):
    store,lead,_,_=state
    sample_draft(store,lead,request_key='request-1')
    from scripts.telegram_approval import create_draft
    with pytest.raises(ValueError,match='DIFFERENT_CONTENT'):
        create_draft(store,lead['journal_contact_id'],lead['clean_email'],'objet changé','corps modifié',{},request_key='request-1')


def test_older_linkedin_import_without_optional_comments_replays_unchanged(tmp_path):
    from tests.helpers import set_test_linkedin_confirmation
    from scripts.linkedin_mcp import import_extraction
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source()])
    set_test_linkedin_confirmation(path,url='https://www.linkedin.com/in/alice-tazi')
    with sqlite3.connect(path) as c:
        payload=c.execute('SELECT payload FROM linkedin_browser_imports').fetchone()[0]
    assert 'comments' not in json.loads(payload)['activity']
    artifact=tmp_path/'old-extraction.json'
    artifact.write_text(payload)
    assert import_extraction(path,artifact)['status']=='UNCHANGED'


def test_observed_comments_are_imported_and_available_to_the_writer(tmp_path):
    from tests.helpers import set_test_linkedin_confirmation
    from scripts.linkedin_mcp import import_extraction
    from scripts.prompt_engine import select_workflow_hook
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source()])
    set_test_linkedin_confirmation(path,url='https://www.linkedin.com/in/alice-tazi')
    with sqlite3.connect(path) as c:
        record=json.loads(c.execute('SELECT payload FROM linkedin_browser_imports').fetchone()[0])
    import uuid
    record['extraction_id']=uuid.uuid4().hex
    record['observed_at']=(datetime.now(timezone.utc)+timedelta(milliseconds=100)).isoformat()
    record['activity']['status']='COMPLETE'
    record['activity']['comments']=[{'author':'Alice Tazi','text':'L’IA améliore la productivité.',
        'source_url':'https://www.linkedin.com/in/alice-tazi/recent-activity/comments/',
        'published_at':(datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),
        'date_precision':'DAY','date_displayed':'2 jours','text_status':'EXPANDED'}]
    record['activity']['source_url']=record['activity']['comments'][0]['source_url']
    artifact=tmp_path/'comment-extraction.json'
    artifact.write_text(json.dumps(record))
    assert import_extraction(path,artifact)['status']=='IMPORTED'
    with sqlite3.connect(path) as c:
        c.row_factory=sqlite3.Row
        lead=dict(c.execute('SELECT * FROM staging_leads').fetchone())
    assert select_workflow_hook(lead)['kind']=='ACTIVITY'


def test_import_does_not_trust_verification_or_profile_assertions_in_source_rows(tmp_path):
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source(email_check_status='VALID',email_validation_source='no2bounce',
        email_validated_at=datetime.now(timezone.utc).isoformat(),validated_email='alice.tazi@example.org',
        linkedin_data_source='codex_chrome',linkedin_search_status='CONFIRMED',
        recent_posts='invented source activity',crm_person_id='untrusted-source-id')])
    with sqlite3.connect(path) as c:
        row=c.execute('SELECT email_check_status,email_validation_source,linkedin_data_source,recent_posts,crm_person_id,source_data_json FROM staging_leads').fetchone()
        assert row[:5]==('PENDING',None,None,None,None)
        assert json.loads(row[5])['email_validation_source']=='no2bounce'


def test_daily_preparation_requires_an_independent_acquisition_service(tmp_path):
    workflow=Workflow(tmp_path/'sas.sqlite',state_path=tmp_path/'state.sqlite')
    with patch('scripts.workflow.DirectEnrichment',side_effect=AssertionError('no provider')):
        assert workflow.prepare()=={'status':'COMPACT_ACQUISITION_SERVICE_REQUIRED'}
    assert not (tmp_path/'sas.sqlite').exists()


def test_ordinary_writer_does_not_select_an_initial_pattern(state,tmp_path):
    store,lead,telegram,breaker=state
    workflow=Workflow(tmp_path/'sas.sqlite',state_path=store.path,telegram=telegram,
                      agent_runner=lambda *a,**kw:pytest.fail('initial selection retired'))
    assert workflow.write_one()['status']=='PILOT_PREPARATION_REQUIRED'
    assert telegram.sent==[]
