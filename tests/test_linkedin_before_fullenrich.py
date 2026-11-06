"""No email spend until the personal URL has been confirmed and imported."""
import json
import sqlite3
from unittest.mock import Mock
import pytest
from scripts.direct_enrichment import DirectEnrichment,contact_input,contact_key,database,encoded,now
from scripts.staging_db import init_staging_db,insert_staging_leads
from tests.helpers import set_test_linkedin_confirmation,set_test_validation

@pytest.fixture
def setup(tmp_path):
    p=str(tmp_path/'sas.sqlite');init_staging_db(p).close()
    insert_staging_leads(p,[{'source_file':'test','first_name':'Alice','last_name':'Example','company_name':'Acme'}])
    fe=Mock(api_key='test');fe.credits.return_value=50;fe.submit.return_value='new-job';fe.result.return_value=None
    w=DirectEnrichment(fullenrich=fe,phase='enrich',fullenrich_daily_credits=3,no2bounce_daily_addresses=0)
    return p,w,fe


def row(p):
    with database(p) as c:return dict(c.execute('SELECT * FROM staging_leads').fetchone())

@pytest.mark.parametrize('fake_proof',[False,True])
def test_bare_or_forged_url_does_not_spend(setup,fake_proof):
    p,w,fe=setup
    with sqlite3.connect(p) as c:
        c.execute("UPDATE staging_leads SET linkedin_url='https://www.linkedin.com/in/alice-example'")
        if fake_proof:c.execute("UPDATE staging_leads SET linkedin_data_source='codex_chrome',linkedin_search_status='CONFIRMED',linkedin_observed_at=?",(now(),))
    for _ in range(2):stats=w.audit_staging_db(p)
    assert row(p)['contact_discovery_status']=='WAITING_LINKEDIN'
    fe.credits.assert_not_called();fe.submit.assert_not_called()
    assert stats['workflow_order']==['verify_existing_email','linkedin_confirmed_url','fullenrich_work_email']


def test_confirmation_releases_waiter_with_url_only(setup):
    p,w,fe=setup
    w.audit_staging_db(p)
    assert row(p)['contact_discovery_status']=='WAITING_LINKEDIN'
    set_test_linkedin_confirmation(p)
    # Optional domain metadata is irrelevant to a URL-only lookup.
    with sqlite3.connect(p) as c:c.execute("UPDATE staging_leads SET company_domain='not-a-domain',company_domain_source=NULL")
    w.audit_staging_db(p)
    data=fe.submit.call_args.args[0]['data'][0]
    assert set(data)=={'linkedin_url','enrich_fields','custom'}
    assert data['linkedin_url']=='https://www.linkedin.com/in/alice-example'
    assert data['enrich_fields']==['contact.work_emails']
    w.audit_staging_db(p)
    fe.submit.assert_called_once()

@pytest.mark.parametrize('verdict',['VALID','WAITING'])
def test_confirmed_url_does_not_override_email_policy(setup,verdict):
    p,w,fe=setup;set_test_linkedin_confirmation(p)
    with sqlite3.connect(p) as c:
        c.execute("UPDATE staging_leads SET raw_email='alice@example.org',email_checked_input='alice@example.org',email_check_status=?",(verdict,))
    if verdict=='VALID':set_test_validation(p,1,'VALID','alice@example.org')
    w.audit_staging_db(p)
    fe.credits.assert_not_called();fe.submit.assert_not_called()


def test_changed_identity_invalidates_browser_proof_before_spend(setup):
    p,w,fe=setup;set_test_linkedin_confirmation(p)
    with sqlite3.connect(p) as c:c.execute("UPDATE staging_leads SET last_name='Other'")
    w.audit_staging_db(p)
    assert row(p)['contact_discovery_status']=='WAITING_LINKEDIN'
    fe.submit.assert_not_called()


def test_changed_url_cannot_receive_pending_email(setup):
    p,w,fe=setup;set_test_linkedin_confirmation(p);w.audit_staging_db(p)
    data=fe.submit.call_args.args[0]['data'][0]
    with sqlite3.connect(p) as c:c.execute("UPDATE staging_leads SET linkedin_url='https://www.linkedin.com/in/other'")
    fe.result.return_value={'id':'new-job','status':'FINISHED','cost':{'credits':1},'data':[{'custom':data['custom'],
        'contact_info':{'most_probable_work_email':{'email':'new@example.org','status':'DELIVERABLE'}}}]}
    w.audit_staging_db(p)
    assert row(p)['contact_discovery_status']=='NEEDS_REVIEW'
    assert row(p)['raw_email'] is None
    fe.submit.assert_called_once()


def test_historical_name_job_is_collected_without_resubmission(setup):
    p,w,fe=setup;w._init(p)
    lead=row(p);sent=contact_input(lead);key=contact_key(sent)
    job,payload=w._reserve(p,'fullenrich','contact',{'data':[{**sent,'custom':{'request_key':key}}]},
        {key:[(lead,sent)]},1,3)
    with database(p) as c:
        c.execute("UPDATE enrichment_jobs SET status='SUBMITTED',remote_id='old-job' WHERE id=?",(job,))
        c.execute("UPDATE staging_leads SET contact_discovery_status='SUBMITTED'")
    fe.result.return_value={'id':'old-job','status':'FINISHED','cost':{'credits':1},'data':[{
        'custom':{'request_key':key},'contact_info':{'most_probable_work_email':{'email':'found@example.org','status':'DELIVERABLE'}}}]}
    w.audit_staging_db(p)
    assert row(p)['raw_email']=='found@example.org'
    assert row(p)['email_check_status']=='VALID'
    fe.submit.assert_not_called()


@pytest.mark.parametrize('identity',[
    {'first_name':'Alice','last_name':'Example','company_name':'Acme'},
    {'linkedin_url':'https://www.linkedin.com/in/alice','domain':'acme.org'}])
def test_client_refuses_name_domain_submissions_before_network(identity):
    from scripts.enrichment_clients import FullEnrichClient
    with pytest.raises(ValueError,match='LinkedIn'):
        FullEnrichClient('test').submit({'name':'batch','data':[{**identity,'enrich_fields':['contact.work_emails']}]})
