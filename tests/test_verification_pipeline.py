"""Offline policy scenarios. Fake verdicts are NOT No2Bounce wire fixtures."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import requests
import responses

from scripts.direct_enrichment import DirectEnrichment, verified
from scripts.enrichment_clients import ProviderError
from scripts.no2bounce_client import No2BounceClient
from scripts.staging_db import init_staging_db, insert_staging_leads
from tests.helpers import set_test_validation, set_test_linkedin_confirmation


@pytest.fixture
def setup(tmp_path):
    db = str(tmp_path / 'pilot.sqlite')
    init_staging_db(db).close()
    insert_staging_leads(db, [{'source_file': 'test', 'first_name': 'Alice', 'last_name': 'Example',
                              'company_name': 'Acme', 'raw_email': 'alice@example.org'}])
    nb = MagicMock(api_key='test', contract_ready=True)
    nb.submit.side_effect = [f'nb-{n}' for n in range(10)]
    nb.result.return_value = None
    fe = MagicMock(api_key='test')
    fe.credits.return_value = 100
    fe.submit.return_value = 'fe-1'
    fe.result.return_value = None
    hv = MagicMock(api_key='')
    worker = DirectEnrichment(no2bounce=nb, fullenrich=fe, harvest=hv,
                              no2bounce_daily_addresses=3, fullenrich_daily_credits=3,
                              harvest_daily_requests=0, poll_seconds=0)
    return db, worker, nb, fe


def row(db, id=1):
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        return dict(c.execute('SELECT * FROM staging_leads WHERE id=?', (id,)).fetchone())


def finish(nb, verdict='VALID', email='alice@example.org'):
    nb.result.return_value = {'verdicts': {email: verdict}, 'raw': {'synthetic_test_only': True}}


def test_verify_before_discovery_and_deduplicate_email(setup):
    db, w, nb, fe = setup
    insert_staging_leads(db, [{'source_file': 'dup', 'first_name': 'Bob', 'last_name': 'Other',
                              'company_name': 'Acme', 'raw_email': 'ALICE@example.org'}])
    w.audit_staging_db(db)
    assert nb.submit.call_args.args == (['alice@example.org'],)
    assert not fe.submit.called
    assert not verified(row(db))  # Tracking ID is not proof.
    finish(nb)
    w.audit_staging_db(db)
    w.audit_staging_db(db)
    assert verified(row(db)) and verified(row(db, 2))
    assert row(db)['original_email'] == 'alice@example.org'
    nb.submit.assert_called_once()
    fe.submit.assert_not_called()


def test_invalid_triggers_fullenrich_preserving_original(setup):
    db, w, nb, fe = setup
    w.audit_staging_db(db)
    finish(nb, 'INVALID')
    w.audit_staging_db(db)
    fe.submit.assert_not_called()
    assert row(db)['contact_discovery_status'] == 'WAITING_LINKEDIN'
    set_test_linkedin_confirmation(db)
    w.audit_staging_db(db)
    payload = fe.submit.call_args.args[0]
    assert payload['data'][0]['linkedin_url'] == 'https://www.linkedin.com/in/alice-example'
    assert 'company_name' not in payload['data'][0]
    fe.result.return_value = {'id': 'fe-1', 'status': 'FINISHED', 'cost': {'credits': 1},
        'data': [{'custom': payload['data'][0]['custom'], 'contact_info': {
            'most_probable_work_email': {'email': 'new@example.org', 'status': 'DELIVERABLE'}}}]}
    w.audit_staging_db(db)
    w.audit_staging_db(db)
    assert verified(row(db))
    assert row(db)['original_email'] == 'alice@example.org'
    assert row(db)['raw_email'] == 'new@example.org'
    nb.submit.assert_called_once()  # No second immediate verification of FullEnrich.


def test_absent_email_waits_for_linkedin_before_fullenrich(setup):
    db, w, nb, fe = setup
    with sqlite3.connect(db) as c:
        c.execute('UPDATE staging_leads SET raw_email=NULL')
    w.audit_staging_db(db)
    nb.submit.assert_not_called()
    fe.submit.assert_not_called()
    assert row(db)['contact_discovery_status'] == 'WAITING_LINKEDIN'
    set_test_linkedin_confirmation(db)
    w.audit_staging_db(db)
    fe.submit.assert_called_once()


@pytest.mark.parametrize('scenario', ['uncertain', 'timeout', 'malformed'])
def test_uncertainty_never_triggers_discovery_or_resubmission(setup, scenario):
    db, w, nb, fe = setup
    if scenario == 'timeout':
        nb.submit.side_effect = ProviderError('NETWORK_ERROR', uncertain=True)
    w.audit_staging_db(db)
    if scenario == 'uncertain':
        finish(nb, 'WAITING')
    elif scenario == 'malformed':
        nb.result.return_value = {'verdicts': {'wrong@example.org': 'VALID'}}
    for _ in range(3):
        w.audit_staging_db(db)
    assert not verified(row(db))
    nb.submit.assert_called_once()
    fe.submit.assert_not_called()


def test_stale_verdict_reverified_and_fresh_cache_shared(setup):
    db, w, nb, fe = setup
    set_test_validation(db, 1, 'VALID', 'alice@example.org')
    for days, expected in [(89, True), (91, False)]:
        with sqlite3.connect(db) as c:
            c.execute('UPDATE staging_leads SET email_validated_at=?',
                      ((datetime.now(timezone.utc) - timedelta(days=days)).isoformat(),))
        assert verified(row(db)) is expected
    w.audit_staging_db(db)
    nb.submit.assert_called_once()
    fe.submit.assert_not_called()
    finish(nb)
    w.audit_staging_db(db)
    insert_staging_leads(db, [{'source_file': 'duplicate', 'raw_email': 'alice@example.org'}])
    w.audit_staging_db(db)
    assert verified(row(db, 2))
    nb.submit.assert_called_once()


def test_expired_cache_is_not_redated_and_generation_refresh_is_deduplicated(setup):
    db, w, nb, fe = setup
    w.audit_staging_db(db)
    finish(nb)
    w.audit_staging_db(db)
    old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    with sqlite3.connect(db) as c:
        c.execute('UPDATE enrichment_jobs SET created_at=?', (old,))
        c.execute('UPDATE staging_leads SET email_validated_at=?', (old,))
    insert_staging_leads(db, [{'source_file': 'duplicate', 'raw_email': 'alice@example.org'}])
    w.audit_staging_db(db)
    assert nb.submit.call_count == 2
    assert nb.submit.call_args.args == (['alice@example.org'],)
    assert not verified(row(db))
    fe.submit.assert_not_called()


def test_late_verification_for_changed_email_is_not_applied(setup):
    db, w, nb, fe = setup
    w.audit_staging_db(db)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE staging_leads SET raw_email='other@example.org'")
    finish(nb)
    w.audit_staging_db(db)
    assert not verified(row(db))
    assert row(db)['raw_email'] == 'other@example.org'
    fe.submit.assert_not_called()


def test_pilot_limits_survive_midnight_restart_and_forbid_harvest(setup):
    db, w, nb, fe = setup
    for name in ['bob', 'carol']:
        insert_staging_leads(db, [{'source_file': 'test', 'first_name': name, 'last_name': 'Example',
                                  'company_name': 'Acme', 'raw_email': name + '@example.org'}])
    w.pilot = True
    w.audit_staging_db(db)
    nb.result.return_value = {'verdicts': {f'{name}@example.org': 'VALID' for name in ['alice', 'bob', 'carol']}}
    w.audit_staging_db(db)
    old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    with sqlite3.connect(db) as c:
        c.execute('UPDATE enrichment_jobs SET created_at=?', (old,))
        c.execute('UPDATE staging_leads SET email_validated_at=?', (old,))
        c.execute("UPDATE staging_leads SET linkedin_url='https://linkedin.com/in/example'")
    w.pilot = False  # Persisted caps still apply without the flag.
    w.nb_limit = 100
    w.hv_limit = 100
    w.harvest.api_key = 'test'
    w.audit_staging_db(db)
    w.phase = 'profiles'
    w.audit_staging_db(db)
    nb.submit.assert_called_once()
    w.harvest.profile.assert_not_called()
    fe.submit.assert_not_called()


def test_pilot_requires_separate_phase_for_discovery(setup):
    db, w, nb, fe = setup
    w.pilot = True
    w.audit_staging_db(db)
    finish(nb, 'INVALID')
    w.audit_staging_db(db)
    fe.submit.assert_not_called()
    w.phase = 'enrich'
    w.audit_staging_db(db)
    fe.submit.assert_not_called()
    set_test_linkedin_confirmation(db)
    w.audit_staging_db(db)
    fe.submit.assert_called_once()


def test_missing_contract_prevents_paid_call_and_reservation(setup):
    db, w, nb, fe = setup
    w.no2bounce = No2BounceClient('test', result_decoder=None)
    stats = w.audit_staging_db(db)
    assert stats['no2bounce_contract_ready'] is False
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT count(*) FROM enrichment_jobs').fetchone()[0] == 0
    fe.submit.assert_not_called()


@responses.activate
def test_no2bounce_documented_transport_and_pending_response():
    # Decoder deliberately synthetic. No claim about the undocumented row schema.
    client = No2BounceClient('test', result_decoder=lambda body: {'a@example.org': 'VALID'})
    responses.add(responses.POST, client.URL, json={'statusCode': 200, 'data': {'trackingId': 123}})
    assert client.submit(['a@example.org']) == '123'
    req = responses.calls[0].request
    assert req.headers['apitoken'] == 'test'
    assert json.loads(req.body) == {'emailList': ['a@example.org']}
    responses.add(responses.GET, client.URL, json={'statusCode': 200, 'data': {'taskId': 123, 'totalEmails': 1, 'completedEmails': 0}})
    assert client.result('123') is None
    responses.add(responses.GET, client.URL, json={'statusCode': 200, 'data': {'taskId': 123, 'totalEmails': 1, 'completedEmails': 1}})
    with pytest.raises(ProviderError, match='INCOMPLETE'):
        client.result('123')
    assert 'trackingId=123' in responses.calls[-1].request.url


@responses.activate
def test_no2bounce_timeout_redacts_key_and_never_retries():
    client = No2BounceClient('secret', result_decoder=lambda body: {})
    responses.add(responses.POST, client.URL, body=requests.Timeout('apitoken=secret'))
    with pytest.raises(ProviderError) as error:
        client.submit(['a@example.org'])
    assert error.value.uncertain
    assert 'secret' not in str(error.value)
    assert len(responses.calls) == 1


def test_pilot_fullenrich_cap_is_not_a_refundable_credit_budget(setup):
    db, w, nb, fe = setup
    with sqlite3.connect(db) as c:
        c.execute('UPDATE staging_leads SET raw_email=NULL')
    for name in ['Bob', 'Carol']:
        insert_staging_leads(db, [{'source_file': 'test', 'first_name': name, 'last_name': 'Example', 'company_name': 'Acme'}])
    for lead_id,name in [(1,'alice'),(2,'bob'),(3,'carol')]:
        set_test_linkedin_confirmation(db,lead_id,'https://www.linkedin.com/in/'+name+'-example')
    w.pilot = True
    w.phase = 'enrich'
    w.audit_staging_db(db)
    payload = fe.submit.call_args.args[0]
    fe.result.return_value = {'id': 'fe-1', 'status': 'FINISHED', 'cost': {'credits': 0},
                             'data': [{'custom': x['custom']} for x in payload['data']]}
    w.audit_staging_db(db)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE enrichment_jobs SET created_at='2020-01-01T00:00:00+00:00'")
    insert_staging_leads(db, [{'source_file': 'test', 'first_name': 'Dan', 'last_name': 'Example', 'company_name': 'Acme'}])
    set_test_linkedin_confirmation(db,4,'https://www.linkedin.com/in/dan-example')
    w.pilot = False
    w.fe_limit = 100
    w.audit_staging_db(db)
    fe.submit.assert_called_once()


def test_no2bounce_ambiguous_reservation_survives_midnight(setup):
    db, w, nb, fe = setup
    nb.submit.side_effect = ProviderError('TIMEOUT', uncertain=True)
    w.audit_staging_db(db)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE enrichment_jobs SET created_at='2020-01-01T00:00:00+00:00'")
    assert w.audit_staging_db(db)['no2bounce_addresses_committed'] == 1
    nb.submit.assert_called_once()
    fe.submit.assert_not_called()


def test_new_pilot_has_no_crm_queue_or_validation_proof(tmp_path):
    from scripts.prepare_pilot import prepare_pilot
    selection = tmp_path / 'selection.json'
    selection.write_text(json.dumps({'contacts': [{'first_name': 'Alice', 'last_name': 'Example',
        'company_name': 'Acme', 'original_email': 'alice@example.org', 'crm_person_id': 'must-not-copy',
        'neverbounce_status': 'VALID'}]}))
    db = tmp_path / 'pilot.sqlite'
    assert prepare_pilot(selection, db, dry_run=True)['created'] is False
    assert not db.exists()
    prepare_pilot(selection, db)
    with sqlite3.connect(db) as c:
        assert dict(c.execute('SELECT provider,total_items FROM enrichment_limits')) == {'no2bounce': 3, 'fullenrich': 3, 'harvest': 0}
        assert c.execute('SELECT crm_person_id,neverbounce_status,clean_email FROM staging_leads').fetchone() == (None, 'PENDING', None)
    before = db.read_bytes()
    with pytest.raises(FileExistsError):
        prepare_pilot(selection, db)
    assert db.read_bytes() == before


def test_pending_or_uncertain_recheck_cannot_use_older_positive_proof(setup):
    db, w, nb, fe = setup
    set_test_validation(db, 1, 'VALID', 'alice@example.org')
    assert verified(row(db))
    for status in ['SUBMITTED', 'WAITING', 'NEEDS_REVIEW', 'UNKNOWN', 'FAILED']:
        with sqlite3.connect(db) as c:
            c.execute('UPDATE staging_leads SET email_check_status=?', (status,))
        assert not verified(row(db))
