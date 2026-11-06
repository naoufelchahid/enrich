"""Behavioral regression checks for tickets 02–12, entirely offline."""
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from scripts.circuit_breaker import CircuitBreaker
from scripts.crm_transfer import transfer_contact, crm_admissible
from scripts.direct_enrichment import DirectEnrichment, admissible
from scripts.email_hygiene import email_identity
from scripts.kerix_client import enrich_company
from scripts.prompt_engine import select_workflow_hook
from scripts.telegram_approval import TelegramClient, create_draft, process_update, submit_draft
from scripts.thunderbird_client import ThunderbirdClient
from scripts.workflow import Workflow, preview
from scripts.workflow_import import import_all
from scripts.workflow_inbound import process_message
from scripts.workflow_schedule import merge_crontab, cron_block, purge_archives
from scripts.workflow_sender import send_approved, send_slot, reconcile_send
from scripts.workflow_store import WorkflowStore, encode
from tests.helpers import set_test_linkedin_confirmation

UTC=timezone.utc
ZONE=ZoneInfo('Africa/Casablanca')


class FakeTelegram(TelegramClient):
    def __init__(self):
        super().__init__('test-token','101','202')
        self.sent=[]

    def call(self,method,payload):
        if method=='getUpdates':
            return []
        self.sent.append((method,payload))
        return {'message_id':len(self.sent)}


class FakeCRM:
    def __init__(self):
        self.rows={'people':{},'companies':{},'opportunities':{}}
        self.creations=[]
        self.unknown_once=None
        self.history=[]

    def inspect_workflow_schema(self):
        return {}

    def workflow_fields(self,resource,schema):
        return {'name','emails','companyId','campaignStatus','linkedinLink','jobTitle',
                'domainName','address','activitySector','phones','sourceFile','city'}


    def workflow_list(self,resource):
        return list(self.rows[resource].values())

    def workflow_create(self,resource,payload):
        identity=str(uuid.uuid4())
        self.rows[resource][identity]={'id':identity,**payload}
        self.creations.append((resource,payload))
        if self.unknown_once==resource:
            self.unknown_once=None
            raise RuntimeError('CREATE_RESPONSE_LOST')
        return identity

    def workflow_get(self,resource,identity):
        return self.rows[resource].get(identity)

    def workflow_patch(self,resource,identity,payload):
        self.rows[resource][identity].update(payload)

    def workflow_delete(self,resource,identity):
        del self.rows[resource][identity]

    def workflow_person_history(self,identity):
        return self.history


class FakeNB:
    api_key='test-nb'
    contract_ready=True

    def __init__(self,verdict='VALID'):
        self.calls=[]
        self.verdict=verdict

    def submit(self,emails):
        self.calls.append(emails)
        return 'verification-'+str(len(self.calls))

    def result(self,job_id):
        index=int(job_id.split('-')[-1])-1
        return {'verdicts':{email:self.verdict for email in self.calls[index]},
                'raw':{'exact_provider_verdict':self.verdict}}


class FakeFE:
    api_key='test-fe'

    def __init__(self):
        self.calls=[]

    def credits(self):
        return 10000

    def submit(self,payload):
        self.calls.append(payload)
        return 'discovery-'+str(len(self.calls))

    def result(self,identity):
        payload=self.calls[int(identity.split('-')[-1])-1]
        return {'id':identity,'status':'FINISHED','cost':{'credits':1},
                'data':[{'custom':row['custom'],'contact_info':{'most_probable_work_email':
                    {'email':'alice.tazi@example.org','status':'DELIVERABLE'}}} for row in payload['data']]}


def source(**extra):
    return {'source_file':'BDD1','company_name':'Acme','first_name':'Alice','last_name':'Tazi',
            'raw_email':'alice.tazi@example.org','company_domain':'example.org',
            'company_domain_source':'reviewed source',**extra}


def next_morning():
    date=datetime.now(UTC).astimezone(ZONE).date()+timedelta(days=1)
    while date.weekday()>4:
        date+=timedelta(days=1)
    return datetime.combine(date,datetime.min.time(),ZONE).replace(hour=8).astimezone(UTC)


@pytest.fixture
def state(tmp_path):
    store=WorkflowStore(tmp_path/'state.sqlite')
    lead=source(journal_contact_id='contact-1',clean_email='alice.tazi@example.org',
                civility='Mme',
                email_check_status='VALID',email_validation_source='no2bounce',
                validated_email='alice.tazi@example.org',
                email_validated_at=(datetime.now(UTC)-timedelta(minutes=1)).isoformat(),
                linkedin_url='https://www.linkedin.com/in/alice-tazi',linkedin_search_status='CONFIRMED')
    store.put_contact(lead)
    with store.connection() as c:
        c.execute("UPDATE workflow_contacts SET status='READY_TO_MAIL',person_id='person-1',company_id='company-1',admitted_at=?",(datetime.now(UTC).isoformat(),))
    configure_test_policy(store)
    return store,lead,FakeTelegram(),CircuitBreaker(str(tmp_path/'breaker.json'))


def approve(store,telegram,draft,*,update_id=1,actor='101',action='approve'):
    submit_draft(store,telegram,draft['id'])
    with store.connection() as c:
        row=dict(c.execute('SELECT * FROM workflow_drafts WHERE id=?',(draft['id'],)).fetchone())
    update={'update_id':update_id,'callback_query':{'from':{'id':int(actor)},'data':action+':'+row['callback_token'],
            'message':{'message_id':row['telegram_message_id'],'chat':{'id':202}}}}
    return process_update(store,telegram,update),update


def configure_test_policy(store):
    # An explicit test policy, not a production default.
    policy={'timezone':'Africa/Casablanca','weekdays':[0,1,2,3,4],
            'start_time':'08:00','end_time':'17:45','min_interval_minutes':45}
    with store.connection() as c:
        c.execute("INSERT INTO workflow_meta VALUES('mailing_policy',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (encode(policy),))
    return policy


def sample_draft(store,lead,**options):
    if options.get('kind') == 'REPLY':
        return create_draft(store,lead['journal_contact_id'],lead['clean_email'],'productivité et ia',
                            'Un brouillon exact à approuver.',{'kind':'GENERAL'},**options)
    from scripts.email_patterns import render_pilot_pattern, PILOT_REVISION
    from scripts.telegram_approval import current_signature_revision
    from scripts.workflow_store import fingerprint
    copy=render_pilot_pattern('V2',last_name=lead['last_name'],company_name=lead['company_name'],civility=lead['civility'])
    signature=current_signature_revision()
    copy_hash=fingerprint({'recipient':lead['clean_email'],'subject':copy['subject'],'body':copy['body'],
        'variant_id':'V2','pattern_revision':PILOT_REVISION,'signature_mode':'thunderbird_html','signature_revision':signature})
    dossier={'copy_hash':copy_hash,'subject':copy['subject'],'body':copy['body'],'variant_id':'V2'}
    operation=store.operation(lead['journal_contact_id'],'PILOT_COPY_CRITIQUE',dossier)
    store.finish_operation(operation['id'],'SUCCEEDED',{'copy_hash':copy_hash,'approved':True,'reasons':[]})
    hook={'variant_id':'V2','pattern_revision':PILOT_REVISION,'signature_mode':'thunderbird_html',
          'signature_revision':signature,'critique_hash':copy_hash,'approval_identity_version':2}
    return create_draft(store,lead['journal_contact_id'],lead['clean_email'],copy['subject'],copy['body'],hook,**options)


@pytest.mark.parametrize('email,score,accepted',[
    ('alice.tazi@example.org',100,True),('at azi@example.org',0,False),
    ('a.tazi@example.org',80,True),('al icetazi@example.org',0,False),
    ('alicetazi@example.org',100,True),('alice@example.org',40,True),
    ('tazi@example.org',60,True),('a.t@example.org',40,True),
    ('contact.alice@example.org',0,False),('info@example.org',0,False),
    ('qualite@example.org',0,False),('unknown@example.org',0,False)])
def test_identity_threshold_and_generic_addresses(email,score,accepted):
    result=email_identity(email,'Alice','Tazi')
    assert (result['score'],result['accepted'])==(score,accepted)


def test_import_preserves_all_normalized_fields_replays_and_keeps_homonyms(tmp_path):
    path=tmp_path/'sas.sqlite'
    rows=[source(city='Casablanca',address='Rue 1',activity_sector='Conseil',phone_standard='+212522000000'),
          source(source_file='BDD2',city='Casablanca',address='Rue 1',activity_sector='Conseil',phone_standard='+212522000000'),
          source(company_name='Another company',raw_email=None)]
    previewed=import_all(tmp_path,path,contacts=rows,dry_run=True)
    assert previewed['accepted']==2 and previewed['duplicates']==1 and not path.exists()
    result=import_all(tmp_path,path,contacts=rows)
    assert result['inserted']==2
    assert import_all(tmp_path,path,contacts=rows)['inserted']==0
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*) FROM staging_leads').fetchone()[0]==2
        assert c.execute('SELECT city,address,activity_sector,phone_standard FROM staging_leads WHERE id=1').fetchone()==('Casablanca','Rue 1','Conseil','+212522000000')
        assert c.execute('SELECT clean_email,email_validation_source FROM staging_leads WHERE id=1').fetchone()==(None,None)
        assert c.execute('SELECT count(*) FROM workflow_import_provenance').fetchone()[0]==3


def test_import_refuses_identity_conflict_and_exclusions_from_other_journal(tmp_path):
    store=WorkflowStore(tmp_path/'archive.sqlite')
    store.exclude('alice.tazi@example.org','UNSUBSCRIBE')
    result=import_all(tmp_path,tmp_path/'sas.sqlite',archive_path=store.path,
                      contacts=[source(),source(raw_email='sara.tazi@example.org',first_name='Sara'),
                                source(raw_email='sara.tazi@example.org',first_name='Amina')])
    assert result['excluded']==1 and result['ambiguous']==1
    with sqlite3.connect(tmp_path/'sas.sqlite') as c:
        assert c.execute("SELECT workflow_wait_reason FROM staging_leads WHERE first_name='Amina'").fetchone()[0]=='IDENTITY_CONFLICT'


def test_import_failure_rolls_back_the_batch(tmp_path):
    path=tmp_path/'sas.sqlite'
    rows=[source(),source(first_name=['malformed'],raw_email='other@example.org')]
    with pytest.raises(sqlite3.ProgrammingError):
        import_all(tmp_path,path,contacts=rows)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*) FROM staging_leads').fetchone()[0]==0
        assert not c.execute("SELECT name FROM sqlite_master WHERE name='workflow_imports'").fetchone()


def test_no2bounce_catchall_admission_keeps_distinct_verdict(tmp_path):
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source()])
    nb,fe=FakeNB('CATCHALL'),FakeFE()
    worker=DirectEnrichment(workflow=True,no2bounce=nb,fullenrich=fe,poll_seconds=0)
    worker.audit_staging_db(path,1)
    worker.audit_staging_db(path,1)
    with sqlite3.connect(path) as c:
        c.row_factory=sqlite3.Row
        lead=dict(c.execute('SELECT * FROM staging_leads').fetchone())
    assert admissible(lead)
    assert lead['email_check_status']=='CATCHALL'
    assert lead['neverbounce_status']=='UNKNOWN'
    assert lead['verification_verdict']=='CATCHALL'
    assert len(nb.calls)==1 and fe.calls==[]


def test_fullenrich_is_url_only_and_new_email_requires_no2bounce(tmp_path):
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source(raw_email=None)])
    nb,fe=FakeNB(),FakeFE()
    worker=DirectEnrichment(workflow=True,no2bounce=nb,fullenrich=fe,poll_seconds=0)
    worker.audit_staging_db(path,1)
    assert fe.calls==[]
    set_test_linkedin_confirmation(path,url='https://www.linkedin.com/in/alice-tazi')
    worker.audit_staging_db(path,1)
    assert set(fe.calls[0]['data'][0])=={'linkedin_url','enrich_fields','custom'}
    worker.audit_staging_db(path,1)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT email_validation_source,email_check_status FROM staging_leads').fetchone()==(None,'SUBMITTED')
    worker.audit_staging_db(path,1)
    with sqlite3.connect(path) as c:
        c.row_factory=sqlite3.Row
        lead=dict(c.execute('SELECT * FROM staging_leads').fetchone())
    assert admissible(lead)
    assert len(fe.calls)==1 and len(nb.calls)==1


@pytest.mark.parametrize('actor,action,state_name',[('999','approve','UNAUTHORIZED'),('101','reject','REJECTED'),('101','rewrite','REWRITE'),('101','approve','APPROVED')])
def test_operator_authorization_and_decisions(state,actor,action,state_name):
    store,lead,telegram,_=state
    draft=sample_draft(store,lead)
    result,update=approve(store,telegram,draft,actor=actor,action=action)
    assert result['status']==state_name
    replay=process_update(store,telegram,update)
    assert replay['status'] in ('REPLAY','UNAUTHORIZED')


def test_rewriting_revokes_approval_and_obsolete_callback(state):
    store,lead,telegram,_=state
    old=sample_draft(store,lead)
    _,update=approve(store,telegram,old)
    new=create_draft(store,lead['journal_contact_id'],lead['clean_email'],'nouvel objet','Version différente.',{'kind':'GENERAL'})
    update['update_id']=2
    assert process_update(store,telegram,update)['status']=='STALE_CALLBACK'
    with store.connection() as c:
        assert tuple(c.execute('SELECT status,approved_at FROM workflow_drafts WHERE id=?',(old['id'],)).fetchone())==('SUPERSEDED',None)
        assert c.execute('SELECT status FROM workflow_drafts WHERE id=?',(new['id'],)).fetchone()[0]=='PENDING'


def test_operator_text_directive_revokes_the_exact_version(state):
    store,lead,telegram,_=state
    draft=sample_draft(store,lead)
    approve(store,telegram,draft)
    result=process_update(store,telegram,{'update_id':2,'message':{'from':{'id':101},'chat':{'id':202},
        'reply_to_message':{'message_id':1},'text':'Mettre davantage l’accent sur le gain de temps.'}})
    assert result['status']=='REWRITE'
    with store.connection() as c:
        row=c.execute('SELECT status,approved_at,rewrite_instruction FROM workflow_drafts').fetchone()
        assert row[0:2]==('REWRITE',None) and 'gain de temps' in row[2]


def test_draft_request_replay_never_creates_a_second_version(state):
    store,lead,_,_=state
    first=sample_draft(store,lead,request_key='worker-request-1')
    again=sample_draft(store,lead,request_key='worker-request-1')
    assert first['id']==again['id']


def test_send_requires_approval_and_works_without_staging(state):
    store,lead,telegram,breaker=state
    mail=ThunderbirdClient(dry_run=True)
    at=next_morning()
    draft=sample_draft(store,lead)
    assert send_approved(store,mail,breaker,'101',at=at)['status']=='NO_APPROVED_EMAIL'
    approve(store,telegram,draft)
    assert send_approved(store,mail,breaker,'101',at=at)['status']=='SENT'
    assert len(mail._sent_emails)==1
    import html
    assert mail._sent_emails[0]['body']==html.escape(draft['body']).replace('\n','<br>')
    assert send_approved(store,mail,breaker,'101',at=at)['status']=='SLOT_ALREADY_USED'
    with pytest.raises(ValueError,match='already sent'):
        sample_draft(store,lead)


@pytest.mark.parametrize('change',['exclude','tamper','expire','catchall','breaker'])
def test_late_changes_block_an_approved_email(state,change):
    store,lead,telegram,breaker=state
    draft=sample_draft(store,lead)
    approve(store,telegram,draft)
    if change=='exclude':
        store.exclude(lead['clean_email'],'REFUSAL')
    elif change=='tamper':
        with store.connection() as c:
            c.execute("UPDATE workflow_drafts SET body='Changed body'")
    elif change in ('expire','catchall'):
        if change=='expire':
            lead['email_validated_at']=(next_morning()-timedelta(days=91)).isoformat()
        else:
            lead['email_check_status']='CATCHALL'
        with store.connection() as c:
            c.execute('UPDATE workflow_contacts SET snapshot=?',(encode(lead),))
    else:
        breaker.trip('bounce')
    mail=ThunderbirdClient(dry_run=True)
    assert send_approved(store,mail,breaker,'101',at=next_morning())['status']!='SENT'
    assert not mail._sent_emails


def test_send_unknown_never_retries_blindly_and_can_reconcile(state):
    store,lead,telegram,breaker=state
    draft=sample_draft(store,lead)
    approve(store,telegram,draft)
    mail=ThunderbirdClient(dry_run=True)
    with patch.object(mail,'send_email',side_effect=RuntimeError('response lost')) as transport:
        unknown=send_approved(store,mail,breaker,'101',at=next_morning())
        assert unknown['status']=='SEND_RECONCILIATION_REQUIRED'
        assert send_approved(store,mail,breaker,'101',at=next_morning()+timedelta(minutes=45))['status']=='SEND_RECONCILIATION_REQUIRED'
        assert transport.call_count==1
    with patch.object(mail,'find_sent_exact',return_value=[{'message_id':'sent-1'}]):
        assert reconcile_send(store,mail,unknown['send_id'])['status']=='RECONCILED'


def test_reply_priority_and_configured_global_interval(state):
    store,lead,telegram,breaker=state
    initial=sample_draft(store,lead)
    approve(store,telegram,initial)
    reply=sample_draft(store,lead,kind='REPLY')
    approve(store,telegram,reply,update_id=2)
    mail=ThunderbirdClient(dry_run=True)
    at=next_morning()
    assert send_approved(store,mail,breaker,'101',at=at+timedelta(seconds=30))['status']=='SENT'
    with store.connection() as c:
        assert c.execute('SELECT kind FROM workflow_sends').fetchone()[0]=='REPLY'
    assert send_approved(store,mail,breaker,'101',at=at+timedelta(minutes=45))['status']=='INTERVAL_NOT_ELAPSED'
    assert send_approved(store,mail,breaker,'101',at=at+timedelta(minutes=90))['status']=='SENT'


@pytest.mark.parametrize('day,hour,minute,allowed',[(5,8,0,False),(6,8,0,False),(0,7,59,False),
    (0,8,0,True),(0,8,45,True),(0,9,0,False),(0,17,45,True),(0,18,0,False)])
def test_send_calendar(day,hour,minute,allowed):
    monday=datetime(2026,10,5,hour,minute,tzinfo=ZONE)
    policy={'timezone':'Africa/Casablanca','weekdays':[0,1,2,3,4], 'start_time':'08:00','end_time':'17:45','min_interval_minutes':45}
    assert bool(send_slot(monday+timedelta(days=day),policy)) is allowed


def test_hook_activity_bio_general_preserves_sources_and_ignores_old_posts():
    at=datetime.now(UTC)
    post={'published_at':(at-timedelta(days=1)).isoformat(),'date_precision':'DAY','text_status':'EXPANDED',
          'text':'L’intelligence artificielle améliore la productivité.','permalink':'https://www.linkedin.com/feed/update/urn:li:activity:1'}
    lead=source(recent_posts=[post],linkedin_profile_json={'profile':{'about':{'text':'Conseil en IA et automatisation.',
          'source_url':'https://www.linkedin.com/in/alice-tazi'}}},linkedin_observed_at=at.isoformat())
    assert select_workflow_hook(lead,at=at)['kind']=='ACTIVITY'
    post['published_at']=(at-timedelta(days=16)).isoformat()
    assert select_workflow_hook(lead,at=at)['kind']=='BIO'
    lead['linkedin_profile_json']={}
    assert select_workflow_hook(lead,at=at)=={'kind':'GENERAL','text':'Goulots d’étranglement, croissance commerciale faible ou processus chronophages : choisir un seul axe comme hypothèse.','source':None,'date':None}


def test_preparation_calendar_and_non_destructive_cron_merge():
    old='0 * * * * unrelated-command\n'
    merged=merge_crontab(old,cron_block())
    assert merged.startswith(old)
    assert merge_crontab(merged,cron_block())==merged


def test_mailing_only_schedule_keeps_monitoring_without_acquisition():
    old='0 * * * * unrelated-command\n'+cron_block()+'\n'
    block=cron_block(mailing_only=True)
    merged=merge_crontab(old,block)
    assert 'prepare_daily_batch' not in merged
    assert '--task send_next_candidate' in merged
    assert '--task review_inbox_and_sync' in merged
    assert '--task retention' in merged
    assert merged.startswith('0 * * * * unrelated-command\n')
    assert merge_crontab(merged,block)==merged


def test_calendar_retention_only_deletes_expired_control_json(tmp_path):
    at=datetime(2028,2,29,10,tzinfo=UTC)
    for name,date in [('old','2027-02-27T10:00:00+00:00'),('boundary','2027-02-28T10:00:00+00:00'),('new','2028-02-28T10:00:00+00:00')]:
        (tmp_path/(name+'.json')).write_text(json.dumps({'archived_at':date}))
    (tmp_path/'journal.sqlite').write_text('independent exclusions')
    assert purge_archives(tmp_path,at=at,dry_run=True)['expired_archives']==1
    assert (tmp_path/'old.json').exists()
    purge_archives(tmp_path,at=at)
    assert not (tmp_path/'old.json').exists()
    assert (tmp_path/'boundary.json').exists() and (tmp_path/'journal.sqlite').exists()


def test_preview_has_no_persistent_side_effect(tmp_path):
    assert preview(tmp_path/'absent.sqlite',tmp_path/'state.sqlite','prepare_daily_batch',datetime.now(UTC))['external_actions']==0
    assert list(tmp_path.iterdir())==[]


def test_crm_transfer_with_valid_email_and_no_linkedin_preserves_available_fields(tmp_path):
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source(
        linkedin_url=None, phone_standard='+212522000000',phone_mobile='+212612345678',
        city='Casablanca',address='Rue 1',activity_sector='Conseil',
        job_title='Directrice',civility='Madame')])
    worker=DirectEnrichment(workflow=True,no2bounce=FakeNB(),fullenrich=FakeFE(),poll_seconds=0)
    worker.audit_staging_db(path,1)
    worker.audit_staging_db(path,1)
    store=WorkflowStore(str(tmp_path/'state.sqlite'));crm=FakeCRM()
    result=transfer_contact(path,1,store,crm,archive_dir=tmp_path/'archives')
    assert result['status']=='TRANSFERRED'
    person=crm.rows['people'][result['person_id']]
    company=crm.rows['companies'][result['company_id']]
    assert 'linkedinLink' not in person
    assert person['phones']['primaryPhoneNumber']=='+212612345678'
    assert person['phones']['additionalPhones'][0]['number']=='+212522000000'
    assert person['jobTitle']=='Directrice' and person['city']=='Casablanca'
    assert company['address']=={'addressCity':'Casablanca','addressStreet1':'Rue 1'}
    assert company['activitySector']=='Conseil'


def test_crm_requires_valid_not_catchall_email(tmp_path):
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source(linkedin_url=None)])
    worker=DirectEnrichment(workflow=True,no2bounce=FakeNB('CATCHALL'),fullenrich=FakeFE(),poll_seconds=0)
    worker.audit_staging_db(path,1)
    worker.audit_staging_db(path,1)
    with sqlite3.connect(path) as c:
        c.row_factory=sqlite3.Row
        lead=dict(c.execute('SELECT * FROM staging_leads').fetchone())
    assert admissible(lead) and not crm_admissible(lead)
    crm=FakeCRM()
    assert transfer_contact(path,1,WorkflowStore(str(tmp_path/'state.sqlite')),crm,
                            archive_dir=tmp_path/'archives')['status']=='NOT_ADMISSIBLE'
    assert crm.creations==[]


def test_full_isolated_import_qualification_transfer_approval_send_and_bounce(tmp_path):
    path=str(tmp_path/'sas.sqlite')
    state_path=str(tmp_path/'sas.sqlite.journal.sqlite')
    import_all(tmp_path,path,contacts=[source(civility='Mme')])
    nb,fe=FakeNB(),FakeFE()
    worker=DirectEnrichment(workflow=True,no2bounce=nb,fullenrich=fe,poll_seconds=0)
    worker.audit_staging_db(path,1)
    set_test_linkedin_confirmation(path,url='https://www.linkedin.com/in/alice-tazi')
    worker.audit_staging_db(path,1)
    crm,telegram=FakeCRM(),FakeTelegram()
    store=WorkflowStore(state_path)
    result=transfer_contact(path,1,store,crm,archive_dir=tmp_path/'archives')
    assert result['status']=='TRANSFERRED'
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*) FROM staging_leads').fetchone()[0]==0
    contact=store.contact(result['contact_id'])
    lead=json.loads(contact['snapshot'])
    configure_test_policy(store)
    draft=sample_draft(store,lead)
    approve(store,telegram,draft)
    mail=ThunderbirdClient(dry_run=True)
    breaker=CircuitBreaker(str(tmp_path/'breaker.json'))
    assert send_approved(store,mail,breaker,telegram.operator_id,at=next_morning())['status']=='SENT'
    payload={'from':'mailer-daemon@example.org','body':'Final-Recipient: rfc822; alice.tazi@example.org\nStatus: 5.1.1\nMailbox unknown'}
    def reviewer(instructions,data,schema,**kwargs):
        return {'message_id':data['message_id'],'classification':'BOUNCE_PERMANENT','evidence':'Status: 5.1.1','reply_needed':False}
    outcome=process_message(store,crm,breaker,telegram,'bounce-1',payload,runner=reviewer)
    assert outcome['classification']=='BOUNCE_PERMANENT' and breaker.is_tripped()
    assert contact['person_id'] not in crm.rows['people']
    assert process_message(store,crm,breaker,telegram,'bounce-1',payload,runner=reviewer)['status']=='REPLAY'
    assert import_all(tmp_path,path,contacts=[source()],archive_path=state_path)['excluded']==1
    assert fe.calls==[] and len(nb.calls)==1


def test_personal_source_triggers_business_discovery_and_only_business_verification(tmp_path):
    path = str(tmp_path / 'sas.sqlite')
    import_all(tmp_path, path, contacts=[source(raw_email='alice.tazi@gmail.com')])
    set_test_linkedin_confirmation(path, url='https://www.linkedin.com/in/alice-tazi')
    nb, fe = FakeNB(), FakeFE()
    worker = DirectEnrichment(workflow=True, no2bounce=nb, fullenrich=fe, poll_seconds=0)
    worker.audit_staging_db(path, 1)
    assert nb.calls == [] and len(fe.calls) == 1
    worker.audit_staging_db(path, 1)
    worker.audit_staging_db(path, 1)
    worker.audit_staging_db(path, 1)
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        lead = dict(c.execute('SELECT * FROM staging_leads').fetchone())
    assert lead['raw_email'] == 'alice.tazi@example.org'
    assert admissible(lead) and lead['workflow_wait_reason'] is None
    assert nb.calls == [['alice.tazi@example.org']]


@pytest.mark.parametrize('domain', ['gmail.com','hotmail.fr','yahoo.com','proton.me'])
def test_deliverable_personal_enrichment_result_is_never_accepted(domain):
    lead = source(raw_email='alice.tazi@gmail.com')
    result = DirectEnrichment._apply_contact(lead, {'linkedin_url':'https://www.linkedin.com/in/alice-tazi'},
        {'contact_info':{'most_probable_work_email':{'email':'alice.tazi@'+domain,'status':'DELIVERABLE'}}})
    assert result['contact_discovery_status'] == 'NO_MATCH'
    assert result['clean_email'] is None and result['validated_email'] is None
    assert result['workflow_wait_reason'] == 'NO_BUSINESS_EMAIL_FOUND'
    assert not admissible({**lead, **result})


def test_cached_personal_verdict_cannot_restore_a_validated_email():
    result = DirectEnrichment._apply_verification({'email':'alice.tazi@gmail.com'},
        {'email':'alice.tazi@gmail.com','verdict':'VALID'}, datetime.now(UTC).isoformat())
    assert result['clean_email'] is None and result['email_check_status'] == 'INVALID'


def test_personal_address_cannot_be_drafted_even_for_old_admitted_contact(state):
    store, lead, _, _ = state
    with store.connection() as c:
        c.execute("UPDATE workflow_contacts SET email='alice.tazi@gmail.com'")
    with pytest.raises(ValueError, match='PERSONAL_EMAIL_FORBIDDEN'):
        create_draft(store,lead['journal_contact_id'],'alice.tazi@gmail.com','objet exact','texte',{})


def test_replacement_reuses_verified_crm_entities_and_fills_empty_email(tmp_path):
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source(company_domain=None,company_domain_source=None)])
    worker=DirectEnrichment(workflow=True,no2bounce=FakeNB(),fullenrich=FakeFE(),poll_seconds=0)
    worker.audit_staging_db(path,1)
    set_test_linkedin_confirmation(path,url='https://www.linkedin.com/in/alice-tazi')
    worker.audit_staging_db(path,1)
    store=WorkflowStore(str(tmp_path/'state.sqlite'));crm=FakeCRM()
    first=transfer_contact(path,1,store,crm,archive_dir=tmp_path/'archives')
    assert first['status']=='TRANSFERRED'
    lead=json.loads(store.contact(first['contact_id'])['snapshot'])
    replacement='a.tazi@example.org'
    lead.update(raw_email=replacement,clean_email=replacement,validated_email=replacement)
    with sqlite3.connect(path) as c:
        fields={r[1] for r in c.execute('PRAGMA table_info(staging_leads)')}
        data={k:v for k,v in lead.items() if k in fields}
        c.execute('INSERT INTO staging_leads ('+','.join(data)+') VALUES ('+','.join('?' for _ in data)+')',tuple(data.values()))
    crm.rows['people'][first['person_id']]['emails']={'primaryEmail':'','additionalEmails':['alice.other@example.org']}
    crm.rows['people'][first['person_id']]['campaignStatus']='WAITING_EMAIL'
    with store.connection() as c:
        c.execute("UPDATE workflow_contacts SET status='WAITING_EMAIL',wait_reason='REPLACEMENT_EMAIL_REQUIRED' WHERE id=?",(first['contact_id'],))
    second=transfer_contact(path,1,store,crm,archive_dir=tmp_path/'archives')
    assert second['status']=='TRANSFERRED'
    assert (second['person_id'],second['company_id'])==(first['person_id'],first['company_id'])
    assert len(crm.creations)==2
    assert store.contact(second['contact_id'])['wait_reason'] is None
    assert crm.rows['people'][first['person_id']]['emails']=={'primaryEmail':replacement,'additionalEmails':['alice.other@example.org']}


def test_unknown_crm_creation_reconciles_without_duplicate_person(tmp_path):
    path=str(tmp_path/'sas.sqlite')
    import_all(tmp_path,path,contacts=[source(linkedin_url=None)])
    worker=DirectEnrichment(workflow=True,no2bounce=FakeNB(),fullenrich=FakeFE(),poll_seconds=0)
    worker.audit_staging_db(path,1)
    worker.audit_staging_db(path,1)
    store=WorkflowStore(str(tmp_path/'state.sqlite'))
    crm=FakeCRM()
    crm.unknown_once='people'
    assert transfer_contact(path,1,store,crm,archive_dir=tmp_path/'archives')['status']=='CRM_RECONCILIATION_REQUIRED'
    assert transfer_contact(path,1,store,crm,archive_dir=tmp_path/'archives')['status']=='TRANSFERRED'
    assert len([x for x in crm.creations if x[0]=='people'])==1


@pytest.mark.parametrize('classification',['BOUNCE_TEMPORARY','BOUNCE_UNKNOWN','REFUSAL','UNSUBSCRIBE','POSITIVE','NEUTRAL'])
def test_inbound_classification_consequences_and_real_identity(state,classification):
    store,lead,telegram,breaker=state
    crm=FakeCRM()
    crm.rows['people']['person-1']={'id':'person-1'}
    if classification.startswith('BOUNCE'):
        payload={'from':'mailer-daemon@example.org','body':'Final-Recipient: rfc822; alice.tazi@example.org\nStatus: 4.2.2'}
    else:
        payload={'from':'Alice Tazi <alice.tazi@example.org>','body':'Je souhaite un rendez-vous.' if classification=='POSITIVE' else 'Message exact.'}
    def reviewer(instructions,data,schema,**kwargs):
        return {'message_id':data['message_id'],'classification':classification,'evidence':payload['body'],'reply_needed':classification=='POSITIVE'}
    assert process_message(store,crm,breaker,telegram,'message-1',payload,runner=reviewer)['identity_known']
    assert 'person-1' in crm.rows['people']
    if classification in ('REFUSAL','UNSUBSCRIBE'):
        assert store.excluded(lead['clean_email'])
    if classification in ('BOUNCE_TEMPORARY','BOUNCE_UNKNOWN'):
        assert not store.excluded(lead['clean_email'])
    if classification=='POSITIVE':
        assert len(crm.rows['opportunities'])==1
        with store.connection() as c:
            assert c.execute("SELECT count(*) FROM workflow_operations WHERE kind='REPLY_NEEDED'").fetchone()[0]==1
    assert breaker.is_tripped()==classification.startswith('BOUNCE')


def test_unknown_inbound_sender_cannot_create_fake_opportunity(state):
    store,_,telegram,breaker=state
    crm=FakeCRM()
    def reviewer(instructions,data,schema,**kwargs):
        return {'message_id':data['message_id'],'classification':'POSITIVE','evidence':'RDV','reply_needed':True}
    outcome=process_message(store,crm,breaker,telegram,'unknown-1',{'from':'unknown@example.org','body':'RDV'},runner=reviewer)
    assert not outcome['identity_known'] and crm.creations==[]


def test_kerix_name_alone_does_not_attach_and_unavailability_is_nonblocking(state):
    store,lead,_,_=state
    crm=FakeCRM()
    class Kerix:
        def search(self,name):
            return [{'companyName':'Acme','companyId':'kerix-1','companyDescriptionTextShort':'Conseil'}]
    assert enrich_company(store,crm,Kerix(),lead['journal_contact_id'])['status']=='NEEDS_REVIEW'
    assert store.contact(lead['journal_contact_id'])['status']=='READY_TO_MAIL'


def test_breaker_resume_requires_exact_incident_and_authorized_operator(state):
    store,_,telegram,breaker=state
    breaker.trip('test incident')
    with store.connection() as c:
        c.execute("INSERT INTO workflow_meta VALUES ('active_incident','incident-1')")
    update={'update_id':1,'message':{'from':{'id':999},'chat':{'id':202},'text':'/resume incident-1 examinée'}}
    assert process_update(store,telegram,update,breaker=breaker)['status']=='UNAUTHORIZED'
    assert breaker.is_tripped()
    update['update_id']=2
    update['message']['from']['id']=101
    assert process_update(store,telegram,update,breaker=breaker)['status']=='RESUMED'
    assert not breaker.is_tripped()


@pytest.mark.parametrize('sector',[None,'Donnée CRM fiable'])
def test_kerix_confirmed_domain_only_fills_empty_company_fields(state,sector):
    store,lead,_,_=state
    crm=FakeCRM()
    crm.rows['companies']['company-1']={'id':'company-1','name':'Acme','activitySector':sector}
    class Kerix:
        def search(self,name):
            return [{'companyId':'known-1','companyName':'Acme','website':'https://example.org',
                     'companyDescriptionTextShort':'Conseil en automatisation'}]
    outcome=enrich_company(store,crm,Kerix(),lead['journal_contact_id'])
    assert outcome['status']=='SUCCEEDED'
    assert crm.rows['companies']['company-1']['activitySector']==(sector or 'Conseil en automatisation')
    assert crm.creations==[]
    assert enrich_company(store,crm,Kerix(),lead['journal_contact_id'])['status']=='SUCCEEDED'
