import json
import sqlite3
from unittest.mock import patch

import pytest
import requests
import responses

from scripts.direct_enrichment import DirectEnrichment
from scripts.enrichment_clients import FullEnrichClient, HarvestClient
from scripts.staging_db import init_staging_db, insert_staging_leads
from tests.helpers import set_test_linkedin_confirmation

FE = 'https://app.fullenrich.com/api/v2'


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'staging.sqlite')
    init_staging_db(path).close()
    insert_staging_leads(path, [{'source_file': 'test', 'first_name': 'Alice',
                                'last_name': 'Example', 'company_name': 'Example SA'}])
    set_test_linkedin_confirmation(path)
    return path


def worker(**kwargs):
    return DirectEnrichment(fullenrich=FullEnrichClient('test'), harvest=HarvestClient('test'),
                            fullenrich_daily_credits=10, harvest_daily_requests=5, **kwargs)


def lead(db):
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        return dict(c.execute('SELECT * FROM staging_leads LIMIT 1').fetchone())


def submission():
    responses.add(responses.GET, FE + '/account/credits', json={'balance': 100})
    responses.add(responses.POST, FE + '/contact/enrich/bulk', json={'enrichment_id': 'job-1'})


def completion(db, contact_info=None, profile=None):
    with sqlite3.connect(db) as c:
        payload = json.loads(c.execute("SELECT payload FROM enrichment_jobs WHERE provider='fullenrich'").fetchone()[0])
    row = {'custom': payload['data'][0]['custom'], 'input': payload['data'][0],
           'contact_info': contact_info or {}, 'profile': profile or {}}
    responses.add(responses.GET, FE + '/contact/enrich/bulk/job-1',
                  json={'id': 'job-1', 'status': 'FINISHED', 'cost': {'credits': 1}, 'data': [row]})


@responses.activate
def test_async_batch_resume_empty_never_retries(db):
    submission()
    worker().audit_staging_db(db)
    assert lead(db)['contact_discovery_status'] == 'SUBMITTED'
    request = [c.request for c in responses.calls if c.request.method == 'POST'][0]
    body = json.loads(request.body)
    assert body['data'][0]['enrich_fields'] == ['contact.work_emails']
    assert all(isinstance(v, str) for v in body['data'][0]['custom'].values())
    completion(db)
    worker().audit_staging_db(db)
    worker().audit_staging_db(db)
    assert lead(db)['contact_discovery_status'] == 'NO_MATCH'
    assert lead(db)['is_enriched_linkedin'] == 1
    assert len([c for c in responses.calls if c.request.method == 'POST']) == 1


@responses.activate
def test_deliverable_result_has_validation_provenance(db):
    submission()
    worker().audit_staging_db(db)
    completion(db, {'most_probable_work_email': {'email': 'alice@example.org', 'status': 'DELIVERABLE'}})
    worker().audit_staging_db(db)
    row = lead(db)
    assert row['clean_email'] == 'alice@example.org'
    assert row['neverbounce_status'] == 'VALID'
    assert row['email_validation_source'] == 'fullenrich'
    assert row['validated_email'] == 'alice@example.org'


@responses.activate
def test_catchall_never_becomes_valid(db):
    submission()
    worker().audit_staging_db(db)
    completion(db, {'most_probable_work_email': {'email': 'alice@example.org', 'status': 'HIGH_PROBABILITY'}})
    worker().audit_staging_db(db)
    assert lead(db)['neverbounce_status'] != 'VALID'


@responses.activate
def test_timeout_submission_is_unknown_and_reserved(db):
    responses.add(responses.GET, FE + '/account/credits', json={'balance': 100})
    responses.add(responses.POST, FE + '/contact/enrich/bulk', body=requests.Timeout())
    worker().audit_staging_db(db)
    worker().audit_staging_db(db)
    assert lead(db)['contact_discovery_status'] == 'UNKNOWN'
    assert len([c for c in responses.calls if c.request.method == 'POST']) == 1




def test_dry_run_preserves_database(db):
    before = open(db, 'rb').read()
    with patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')):
        worker(dry_run=True).audit_staging_db(db)
    assert open(db, 'rb').read() == before


@responses.activate
def test_missing_key_and_zero_budget_never_call(db, monkeypatch):
    monkeypatch.delenv('FULLENRICH_API_KEY', raising=False)
    monkeypatch.delenv('HARVEST_API_KEY', raising=False)
    DirectEnrichment(fullenrich_daily_credits=0, harvest_daily_requests=0).audit_staging_db(db)
    assert len(responses.calls) == 0
    assert lead(db)['contact_discovery_status'] == 'PENDING'


@responses.activate
def test_duplicate_contacts_in_one_batch_are_charged_once(db):
    insert_staging_leads(db, [{'source_file': 'duplicate', 'first_name': 'Alice',
                             'last_name': 'Example', 'company_name': 'Example SA'}])
    set_test_linkedin_confirmation(db,2)
    submission()
    worker().audit_staging_db(db)
    posted = json.loads(next(c.request.body for c in responses.calls if c.request.method == 'POST'))
    assert len(posted['data']) == 1
    completion(db, {'most_probable_work_email': {'email': 'alice@example.org', 'status': 'DELIVERABLE'}})
    worker().audit_staging_db(db)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT count(*) FROM staging_leads WHERE contact_discovery_status='SUCCEEDED'").fetchone()[0] == 2
        assert c.execute('SELECT reserved FROM enrichment_jobs').fetchone()[0] == 1


@responses.activate
def test_new_duplicate_reuses_persisted_result(db):
    submission()
    worker().audit_staging_db(db)
    completion(db)
    worker().audit_staging_db(db)
    insert_staging_leads(db, [{'source_file': 'duplicate', 'first_name': 'Alice',
                             'last_name': 'Example', 'company_name': 'Example SA'}])
    set_test_linkedin_confirmation(db,2)
    worker().audit_staging_db(db)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT count(*) FROM staging_leads WHERE contact_discovery_status='NO_MATCH'").fetchone()[0] == 2
    assert len([c for c in responses.calls if c.request.method == 'POST']) == 1


@responses.activate
def test_poll_in_progress_and_retrieval_failure_do_not_resubmit(db):
    submission()
    w = worker(poll_seconds=0)
    w.audit_staging_db(db)
    responses.add(responses.GET, FE + '/contact/enrich/bulk/job-1',
                  status=400, json={'code': 'error.enrichment.in_progress'})
    w.audit_staging_db(db)
    assert lead(db)['contact_discovery_status'] == 'SUBMITTED'
    responses.add(responses.GET, FE + '/contact/enrich/bulk/job-1', status=500)
    w.audit_staging_db(db)
    assert lead(db)['contact_discovery_status'] == 'SUBMITTED'
    completion(db)
    w.audit_staging_db(db)
    assert lead(db)['contact_discovery_status'] == 'NO_MATCH'
    assert len([c for c in responses.calls if c.request.method == 'POST']) == 1


@responses.activate
def test_exhausted_budget_does_not_even_check_provider_balance(db):
    w = DirectEnrichment(fullenrich=FullEnrichClient('test'), fullenrich_daily_credits=0.5)
    w.audit_staging_db(db)
    assert not responses.calls


@responses.activate
def test_incomplete_batch_is_review_not_false_no_match(db):
    submission()
    worker().audit_staging_db(db)
    responses.add(responses.GET, FE + '/contact/enrich/bulk/job-1', json={
        'id': 'job-1', 'status': 'FINISHED', 'cost': {'credits': 0}, 'data': []})
    worker().audit_staging_db(db)
    assert lead(db)['contact_discovery_status'] == 'NEEDS_REVIEW'
    assert lead(db)['is_enriched_linkedin'] == 1


@responses.activate
def test_changed_lead_identity_cannot_receive_stale_result(db):
    submission()
    worker().audit_staging_db(db)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE staging_leads SET last_name='SomeoneElse'")
    completion(db, {'most_probable_work_email': {'email': 'alice@example.org', 'status': 'DELIVERABLE'}})
    worker().audit_staging_db(db)
    assert lead(db)['contact_discovery_status'] == 'NEEDS_REVIEW'
    assert not lead(db)['clean_email']


@responses.activate
def test_conflicting_identity_never_imports_email(db):
    submission()
    worker().audit_staging_db(db)
    completion(db, {'most_probable_work_email': {'email': 'bob@example.org', 'status': 'DELIVERABLE'}},
               {'first_name': 'Bob', 'last_name': 'Example'})
    worker().audit_staging_db(db)
    assert lead(db)['contact_discovery_status'] == 'NEEDS_REVIEW'
    assert not lead(db)['clean_email']








def test_concurrent_worker_cannot_submit(db):
    import fcntl
    from pathlib import Path
    with open(str(Path(db).resolve()) + '.enrichment.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')):
            assert worker().audit_staging_db(db) == {'status': 'BUSY'}


@responses.activate
def test_unknown_reservation_survives_day_boundary(db):
    responses.add(responses.GET, FE + '/account/credits', json={'balance': 100})
    responses.add(responses.POST, FE + '/contact/enrich/bulk', body=requests.Timeout())
    worker().audit_staging_db(db)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE enrichment_jobs SET created_at='2020-01-01T00:00:00+00:00'")
    stats = worker().audit_staging_db(db)
    assert stats['fullenrich_credits_committed'] == 1
    assert len([c for c in responses.calls if c.request.method == 'POST']) == 1


@responses.activate
def test_unknown_submission_can_be_reconciled_without_new_post(db):
    responses.add(responses.GET, FE + '/account/credits', json={'balance': 100})
    responses.add(responses.POST, FE + '/contact/enrich/bulk', body=requests.Timeout())
    worker().audit_staging_db(db)
    with sqlite3.connect(db) as c:
        job_id = c.execute('SELECT id FROM enrichment_jobs').fetchone()[0]
    completion(db)
    assert worker().attach_fullenrich_job(db, job_id, 'job-1')['status'] == 'RECONCILED'
    assert lead(db)['contact_discovery_status'] == 'NO_MATCH'
    assert len([c for c in responses.calls if c.request.method == 'POST']) == 1


@responses.activate
def test_new_budget_cannot_be_spent_when_provider_cost_exceeds_reservation(db):
    submission()
    worker().audit_staging_db(db)
    with sqlite3.connect(db) as c:
        payload = json.loads(c.execute('SELECT payload FROM enrichment_jobs').fetchone()[0])
    responses.add(responses.GET, FE + '/contact/enrich/bulk/job-1', json={'id': 'job-1', 'status': 'FINISHED',
        'cost': {'credits': 2}, 'data': [{'custom': payload['data'][0]['custom'], 'contact_info': {}}]})
    worker().audit_staging_db(db)
    insert_staging_leads(db, [{'source_file': 'new', 'first_name': 'Bob', 'last_name': 'Other', 'company_name': 'Acme'}])
    stats = worker().audit_staging_db(db)
    assert stats['jobs_requiring_attention'][0]['error'] == 'COST_EXCEEDED_RESERVATION'
    assert len([c for c in responses.calls if c.request.method == 'POST']) == 1


def test_existing_legacy_validation_is_not_trusted(db):
    from scripts.direct_enrichment import verified
    with sqlite3.connect(db) as c:
        c.execute("UPDATE staging_leads SET raw_email='alice@example.org',clean_email='alice@example.org',neverbounce_status='VALID'")
    assert not verified(lead(db))


def test_http_contract_matches_captured_provider_schemas():
    from pathlib import Path
    from scripts.direct_enrichment import contact_input
    schemas = json.loads((Path(__file__).parent / 'fixtures/direct_api_contracts.json').read_text())
    assert schemas['fullenrich']['servers'][0]['url'] == FE
    contact = contact_input({'first_name': 'Alice', 'last_name': 'Example', 'company_name': 'Example'})
    assert set(contact) <= set(schemas['fullenrich']['contact_input']['properties'])
    assert contact['enrich_fields'][0] in schemas['fullenrich']['contact_input']['properties']['enrich_fields']['items']['enum']
