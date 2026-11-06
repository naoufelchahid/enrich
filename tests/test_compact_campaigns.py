"""Campaigns use the common journal and existing approval/send gates offline."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from scripts.compact_campaigns import CompactCampaigns
from scripts.compact_store import CompactStore
from scripts.telegram_approval import create_draft, process_update, submit_draft
from scripts.workflow import readiness
from scripts.workflow_sender import send_approved
from tests.test_workflow_refactor import source, FakeTelegram, approve, next_morning, configure_test_policy, sample_draft
from scripts.circuit_breaker import CircuitBreaker


@pytest.fixture
def campaign(tmp_path,monkeypatch):
    monkeypatch.delenv('WORKFLOW_STOP_AT_DRAFT',raising=False)
    monkeypatch.delenv('WORKFLOW_CAMPAIGN_ONLY',raising=False)
    store=CompactStore(tmp_path/'journal.sqlite')
    store.set_authority('campaigns')
    lead=source(journal_contact_id='admitted-1',clean_email='alice.tazi@example.org',civility='Mme',
        email_check_status='VALID',email_validation_source='no2bounce',validated_email='alice.tazi@example.org',
        email_validated_at=datetime.now(timezone.utc).isoformat())
    store.put_contact(lead,status='READY_TO_MAIL')
    with store.connection() as c:
        c.execute("UPDATE workflow_contacts SET person_id='person-1',company_id='company-1',admitted_at=? WHERE id=?",(datetime.now(timezone.utc).isoformat(),lead['journal_contact_id']))
    configure_test_policy(store)
    telegram=FakeTelegram()
    breaker=CircuitBreaker(str(tmp_path/'breaker.json'))
    workflow=CompactCampaigns(store.path,root=tmp_path,telegram=telegram,breaker=breaker,
        cleanup=lambda _: {'removed':[]},agent_runner=lambda *a,**kw:{'pattern_id':'rythme_travail'})
    return store,lead,telegram,breaker,workflow


class FakeMail:
    def __init__(self):
        self.sent=[]
    def send_email(self,**kwargs):
        self.sent.append(kwargs)
        return {'success':True}
    send_approved_email=send_email


def prepare_current(store, contact_id):
    from scripts.compact_copy import CompactCopy
    return CompactCopy(store,critic_runner=lambda instructions,data,schema,**kw:{
        'copy_hash':data['copy_hash'],'approved':True,'reasons':[]}).prepare(contact_id,'V2')


def test_draft_without_sas_acquisition_or_transport_clients(campaign,tmp_path):
    store,lead,telegram,breaker,workflow=campaign
    with patch('scripts.workflow.ThunderbirdClient',side_effect=AssertionError('mail unnecessary')), \
         patch('scripts.workflow.DirectEnrichment',side_effect=AssertionError('acquisition unnecessary')), \
         patch('scripts.workflow.TwentyClient',side_effect=AssertionError('CRM unnecessary')):
        result=prepare_current(store,lead['journal_contact_id'])
        assert result['status']=='DRAFT_CREATED'
    assert telegram.sent==[]
    assert list(tmp_path.glob('*.sqlite'))==[tmp_path/'journal.sqlite']
    assert workflow._mail is None and workflow._enrichment is None and workflow._crm is None
    reopened=CompactCampaigns(store.path,root=tmp_path,telegram=telegram,breaker=breaker)
    with reopened.store.connection() as c:
        row=c.execute('SELECT * FROM workflow_drafts').fetchone()
        assert row['status']=='PENDING'
        hook=json.loads(row['hook'])
        assert hook['approval_identity_version']==2 and len(hook['signature_revision'])==64
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='staging_leads'").fetchone()


def test_silent_draft_does_not_prevent_another_admitted_contact(campaign):
    store,lead,telegram,breaker,workflow=campaign
    first=prepare_current(store,lead['journal_contact_id'])
    other={**lead,'journal_contact_id':'admitted-2','first_name':'Sara','clean_email':'sara.tazi@example.org',
           'raw_email':'sara.tazi@example.org','validated_email':'sara.tazi@example.org'}
    store.put_contact(other,status='READY_TO_MAIL')
    with store.connection() as c:
        c.execute("UPDATE workflow_contacts SET person_id='p2',company_id='c2',admitted_at=? WHERE id='admitted-2'",(datetime.now(timezone.utc).isoformat(),))
    second=prepare_current(store,'admitted-2')
    assert second['status']=='DRAFT_CREATED' and second['draft_id']!=first['draft_id']
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM workflow_drafts WHERE status='PENDING'").fetchone()[0]==2


def test_rewrite_revokes_approval_and_old_callback(campaign):
    store,lead,telegram,breaker,workflow=campaign
    draft=create_draft(store,lead['journal_contact_id'],lead['clean_email'],'Objet exact','Corps exact',{},kind='REPLY')
    approved,old_update=approve(store,telegram,draft)
    assert approved['status']=='APPROVED'
    replacement=create_draft(store,lead['journal_contact_id'],lead['clean_email'],'Nouvel objet','Nouvelle version',{},kind='REPLY')
    old_update['update_id']=999
    assert process_update(store,telegram,old_update)['status']=='STALE_CALLBACK'
    with store.connection() as c:
        old=c.execute('SELECT * FROM workflow_drafts WHERE id=?',(draft['id'],)).fetchone()
        assert old['status']=='SUPERSEDED' and old['approved_by'] is None
    assert replacement['status']=='PENDING'


@pytest.mark.parametrize('field,value', [('signature_mode','thunderbird_html'),('signature_revision','changed'),('pattern_revision','changed')])
def test_hook_change_after_approval_cannot_send(campaign,field,value):
    store,lead,telegram,breaker,workflow=campaign
    draft=create_draft(store,lead['journal_contact_id'],lead['clean_email'],'Objet exact','Corps exact',{},kind='REPLY')
    approve(store,telegram,draft)
    with store.connection() as c:
        hook=json.loads(draft['hook']);hook[field]=value
        c.execute('UPDATE workflow_drafts SET hook=? WHERE id=?',(json.dumps(hook),draft['id']))
    mail=FakeMail()
    assert send_approved(store,mail,breaker,'101',at=next_morning())['status']=='NO_APPROVED_EMAIL'
    assert mail.sent==[]


def test_request_key_binds_signature_identity(campaign):
    store,lead,telegram,breaker,workflow=campaign
    original=create_draft(store,lead['journal_contact_id'],lead['clean_email'],'Objet exact','Corps exact',{},kind='REPLY',request_key='worker-one')
    assert create_draft(store,lead['journal_contact_id'],lead['clean_email'],'Objet exact','Corps exact',{},kind='REPLY',request_key='worker-one')['id']==original['id']
    with pytest.raises(ValueError,match='REQUEST_KEY_REUSED'):
        create_draft(store,lead['journal_contact_id'],lead['clean_email'],'Objet exact','Corps exact',{'pattern_revision':'changed'},kind='REPLY',request_key='worker-one')


def test_expired_validation_and_human_reply_block_last_moment(campaign):
    store,lead,telegram,breaker,workflow=campaign
    draft=sample_draft(store,lead)
    approve(store,telegram,draft)
    with store.connection() as c:
        c.execute('UPDATE workflow_contacts SET human_exchange=1 WHERE id=?',(lead['journal_contact_id'],))
    mail=FakeMail()
    assert send_approved(store,mail,breaker,'101',at=next_morning())['status']=='NO_APPROVED_EMAIL'
    with store.connection() as c:
        stale={**lead,'email_validated_at':(datetime.now(timezone.utc)-timedelta(days=100)).isoformat()}
        c.execute('UPDATE workflow_contacts SET human_exchange=0,snapshot=? WHERE id=?',(json.dumps(stale),lead['journal_contact_id']))
    assert send_approved(store,mail,breaker,'101',at=next_morning())['status']=='NO_APPROVED_EMAIL'
    assert store.contact(lead['journal_contact_id'])['wait_reason']=='REVERIFY_EMAIL_REQUIRED'
    assert mail.sent==[]


def test_approved_recipient_requires_verification_of_that_same_address(campaign):
    store,lead,telegram,breaker,workflow=campaign
    replacement='alice.tazi@other-company.example'
    with store.connection() as c:
        c.execute('UPDATE workflow_contacts SET email=? WHERE id=?',
                  (replacement,lead['journal_contact_id']))
    draft=sample_draft(store,{**lead,'clean_email':replacement})
    approve(store,telegram,draft)
    mail=FakeMail()
    result=send_approved(store,mail,breaker,'101',at=next_morning())
    assert result['status']=='NO_APPROVED_EMAIL' and mail.sent==[]
    assert store.contact(lead['journal_contact_id'])['wait_reason']=='REVERIFY_EMAIL_REQUIRED'


def test_reverify_journal_only_preserves_generation(campaign,tmp_path):
    store,lead,telegram,breaker,workflow=campaign
    store.set_authority('qualification')
    # Existing candidate already completed an older verification.
    store.put_candidate({**lead,'qualification_applied_tasks':['older-check']},lane='qualification',candidate_id=lead['journal_contact_id'],status='QUALIFIED')
    with store.connection() as c:
        c.execute("UPDATE workflow_contacts SET wait_reason='REVERIFY_EMAIL_REQUIRED'")
    seen=[]
    class Verifier:
        def tick(self,identity):
            row=store.candidate(identity)
            seen.append(row['data']['email_validated_at'])
            if len(seen)==1:
                return {'status':'SUBMITTED'}
            store.update_candidate(identity,{'clean_email':lead['clean_email'],'validated_email':lead['clean_email'],
                'email_check_status':'VALID','email_validation_source':'no2bounce',
                'email_validated_at':datetime.now(timezone.utc).isoformat()},lane='qualification')
            return {'status':'QUALIFIED'}
    workflow._qualification=Verifier()
    assert workflow.reverify_archived()['status']=='REVERIFICATION_PROCESSED'
    assert workflow.reverify_archived()['status']=='REVERIFICATION_PROCESSED'
    assert seen==[lead['email_validated_at'],lead['email_validated_at']]
    assert store.contact(lead['journal_contact_id'])['wait_reason'] is None
    assert not (tmp_path/'reverification.sqlite').exists()
    assert store.candidate(lead['journal_contact_id'])['data']['qualification_applied_tasks']==['older-check']


def test_task_readiness_does_not_probe_unneeded_services(monkeypatch):
    monkeypatch.setenv('TWENTY_API_KEY','present')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','present')
    monkeypatch.setenv('TELEGRAM_OPERATOR_ID','101')
    monkeypatch.setenv('TELEGRAM_CHAT_ID','202')
    with patch('scripts.workflow.TwentyClient',side_effect=AssertionError('not needed')), \
         patch('scripts.workflow.TelegramClient.call',side_effect=AssertionError('not needed')), \
         patch('scripts.workflow.ThunderbirdClient',side_effect=AssertionError('not needed')):
        result=readiness(task='draft_only',probe=True)
    assert set(result['components'])=={'codex'}


def test_unknown_send_and_global_reservation_remain_shared(campaign):
    store,lead,telegram,breaker,workflow=campaign
    draft=create_draft(store,lead['journal_contact_id'],lead['clean_email'],'Objet exact','Corps exact',{},kind='REPLY')
    approve(store,telegram,draft)
    class UnknownMail(FakeMail):
        def send_email(self,**kwargs):
            self.sent.append(kwargs)
            raise TimeoutError('unknown')
        send_approved_email=send_email
    mail=UnknownMail()
    assert send_approved(store,mail,breaker,'101',at=next_morning())['status']=='SEND_RECONCILIATION_REQUIRED'
    reopened=CompactStore(store.path)
    assert send_approved(reopened,mail,breaker,'101',at=next_morning()+timedelta(minutes=45))['status']=='SEND_RECONCILIATION_REQUIRED'
    assert len(mail.sent)==1


def test_reverification_after_older_qualification_creates_new_task(campaign):
    from scripts.compact_qualification import CompactQualification
    from tests.test_workflow_refactor import FakeNB
    store,lead,telegram,breaker,workflow=campaign
    store.set_authority('qualification')
    identity=lead['journal_contact_id']
    store.put_candidate(lead,lane='qualification',candidate_id=identity,status='QUALIFIED')
    old=store.create_task('qualification','no2bounce',{'emailList':[lead['clean_email']],'after':None},candidate_id=identity)
    store.finish_task(old['id'],'SUCCEEDED',{'remote_id':'older-provider-job'})
    store.update_candidate(identity,{'qualification_applied_tasks':[old['id']]},lane='qualification')
    with store.connection() as c:
        c.execute("UPDATE workflow_contacts SET wait_reason='REVERIFY_EMAIL_REQUIRED'")
    verifier=FakeNB()
    reservations=[]
    workflow._qualification=CompactQualification(store,no2bounce=verifier,
        cost_guard=lambda **request:reservations.append(request),hygiene=lambda _:'PASS')
    result=workflow.reverify_archived()
    assert result['results'][0]['status']=='SUBMITTED'
    result=workflow.reverify_archived()
    assert result['results'][0]['status']=='QUALIFIED'
    assert store.contact(identity)['wait_reason'] is None
    assert len(verifier.calls)==1 and len(reservations)==1
    assert reservations[0]['payload']['after']==lead['email_validated_at']
    assert reservations[0]['operation_id']!=old['id']
