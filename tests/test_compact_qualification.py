import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from scripts.compact_store import CompactStore
from scripts.compact_qualification import CompactQualification, email_admissible
from scripts.enrichment_clients import ProviderError
from scripts.workflow_import import import_all

AT = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


class NB:
    def __init__(self, verdict='VALID', error=None):
        self.submissions = []
        self.verdict = verdict
        self.error = error

    def submit(self, emails):
        self.submissions.append(emails)
        if self.error:
            raise self.error
        return 'nb-1'

    def result(self, remote_id):
        assert remote_id == 'nb-1'
        return {'verdicts': {self.submissions[0][0]: self.verdict}, 'raw': {'exact': 'evidence'}}


def setup(tmp_path, **changes):
    store = CompactStore(tmp_path / 'journal.sqlite')
    store.set_authority('qualification')
    row = dict(source_file='fixture.xlsx', first_name='Alice', last_name='Tazi',
               raw_email='alice.tazi@example.org', company_name='Example', **changes)
    import_all(tmp_path, tmp_path / 'nonexistent-sas.sqlite', backend='compact', compact_store=store, contacts=[row])
    return store, store.candidates()[0]['id']


def worker(store, nb=None, **kwargs):
    return CompactQualification(store, no2bounce=nb or NB(), hygiene=lambda email: 'PASS',
                                cost_guard=lambda **kw: {'reserved': True}, clock=lambda: AT, **kwargs)


def test_compact_import_replay_preserves_exact_source_and_never_creates_sas(tmp_path):
    store = CompactStore(tmp_path / 'journal.sqlite')
    store.set_authority('qualification')
    row = dict(source_file='one.xlsx', first_name='Alice', last_name='Tazi', raw_email='alice.tazi@example.org',
               email_check_status='VALID', email_validation_source='no2bounce', validated_email='alice.tazi@example.org',
               email_validated_at=AT.isoformat(), crm_person_id='untrusted')
    sas = tmp_path / 'absent.sqlite'
    assert import_all(tmp_path, sas, backend='compact', compact_store=store, contacts=[row])['inserted'] == 1
    candidate = store.candidates()[0]
    assert candidate['data']['email_check_status'] == 'PENDING'
    assert candidate['data']['clean_email'] is None and candidate['data']['crm_person_id'] is None
    assert json.loads(store.get_blob(store.evidence(candidate['id'])[0]['uri'])) == row
    assert import_all(tmp_path, sas, backend='compact', compact_store=store, contacts=[row])['duplicates'] == 1
    assert not sas.exists()
    assert len(store.candidates()) == 1
    before = store.candidates()
    assert import_all(tmp_path, sas, backend='compact', compact_store=store,
                      contacts=[{**row, 'source_file':'two.xlsx'}], dry_run=True)['inserted'] == 0
    assert store.candidates() == before


def test_homonyms_stay_distinct_and_exclusions_are_respected(tmp_path):
    store, identity = setup(tmp_path)
    row = {'source_file':'other.xlsx','first_name':'Alice','last_name':'Tazi','raw_email':None}
    import_all(tmp_path, 'unused', backend='compact', compact_store=store, contacts=[row, row])
    assert len(store.candidates()) == 3
    store.exclude('excluded@example.org', 'PERMANENT_BOUNCE')
    assert import_all(tmp_path, 'unused', backend='compact', compact_store=store,
                      contacts=[{**row, 'raw_email':'excluded@example.org'}])['excluded'] == 1
    store.exclude('alice.tazi@example.org', 'PERMANENT_BOUNCE')
    nb = NB()
    assert worker(store, nb).tick(identity)['reason'] == 'EXCLUDED'
    assert not nb.submissions


def test_source_address_verified_before_enrichment_and_submitted_job_resumes(tmp_path):
    store, identity = setup(tmp_path)
    nb, fe = NB(), Mock()
    first = worker(store, nb, fullenrich=fe)
    assert first.tick(identity)['status'] == 'SUBMITTED'
    assert store.candidate(identity)['data']['clean_email'] is None
    restarted = worker(CompactStore(store.path), nb, fullenrich=fe)
    assert restarted.tick(identity)['status'] == 'QUALIFIED'
    assert restarted.tick(identity)['status'] == 'QUALIFIED'
    assert nb.submissions == [['alice.tazi@example.org']]
    fe.submit.assert_not_called()
    candidate = store.candidate(identity)
    assert email_admissible(candidate['data'], at=AT)
    assert not email_admissible(candidate['data'], at=AT + timedelta(days=91))
    evidence = store.evidence(identity)
    assert any(json.loads(store.get_blob(r['uri'])).get('raw') == {'exact':'evidence'} for r in evidence)
    assert not (tmp_path/'nonexistent-sas.sqlite').exists()


@pytest.mark.parametrize('verdict', ['CATCHALL', 'WAITING'])
def test_uncertain_verdict_never_admits_or_triggers_paid_enrichment(tmp_path, verdict):
    store, identity = setup(tmp_path)
    nb, fe = NB(verdict), Mock()
    runner = worker(store, nb, fullenrich=fe)
    runner.tick(identity)
    runner.tick(identity)
    assert runner.tick(identity)['reason'] == 'VERIFICATION_' + verdict
    assert not email_admissible(store.candidate(identity)['data'], at=AT)
    assert nb.submissions == [['alice.tazi@example.org']]
    fe.submit.assert_not_called()


def test_ambiguous_submission_keeps_unknown_on_restart_and_never_resubmits(tmp_path):
    store, identity = setup(tmp_path)
    nb = NB(error=ProviderError('NETWORK_ERROR', uncertain=True))
    runner = worker(store, nb)
    assert runner.tick(identity)['reason'] == 'PROVIDER_RECONCILIATION_REQUIRED'
    assert worker(CompactStore(store.path), nb).tick(identity)['reason'] == 'PROVIDER_RECONCILIATION_REQUIRED'
    assert len(nb.submissions) == 1
    with store.connection() as c:
        task_id, state = c.execute('SELECT id,status FROM workflow_tasks').fetchone()
    assert state == 'UNKNOWN'
    runner.reconcile_remote(task_id, 'nb-1')
    assert runner.tick(identity)['status'] == 'QUALIFIED'
    assert len(nb.submissions) == 1


def test_missing_budget_prevents_paid_calls_and_can_resume_once_funded(tmp_path):
    store, identity = setup(tmp_path)
    nb = NB()
    runner = CompactQualification(store, no2bounce=nb, hygiene=lambda email:'PASS')
    assert runner.tick(identity)['reason'] == 'BUDGET_GUARD_REQUIRED'
    assert not nb.submissions
    assert worker(store, nb).tick(identity)['status'] == 'SUBMITTED'


def test_changed_verification_target_is_reviewed_with_exact_evidence(tmp_path):
    store, identity = setup(tmp_path)
    nb = NB()
    runner = worker(store, nb)
    runner.tick(identity)
    store.update_candidate(identity, {'raw_email':'a.tazi@example.org'}, lane='qualification')
    assert runner.tick(identity)['reason'] == 'VERIFICATION_TARGET_CHANGED'
    assert store.candidate(identity)['data']['validated_email'] is None
    assert any(r['kind']=='no2bounce_response' for r in store.evidence(identity))


def test_forbidden_email_is_preserved_only_as_source_and_waits_for_confirmed_profile(tmp_path):
    store = CompactStore(tmp_path / 'journal.sqlite')
    store.set_authority('qualification')
    row = dict(source_file='fixture',first_name='Alice',last_name='Tazi',raw_email='alice.tazi@gmail.com')
    import_all(tmp_path, 'unused', backend='compact', compact_store=store, contacts=[row])
    candidate = store.candidates()[0]
    assert candidate['data']['original_email'] == row['raw_email']
    assert candidate['data']['raw_email'] is None
    nb, fe = NB(), Mock()
    assert worker(store, nb, fullenrich=fe).tick(candidate['id'])['reason'] == 'CONFIRMED_LINKEDIN_REQUIRED'
    assert not nb.submissions
    fe.submit.assert_not_called()


class FE:
    def __init__(self, email='a.tazi@example.org'):
        self.submissions = []
        self.email = email

    def submit(self, payload):
        self.submissions.append(payload)
        return 'fe-1'

    def result(self, remote_id):
        assert remote_id == 'fe-1'
        return {'id':remote_id, 'status':'FINISHED', 'data':[{
            'custom':self.submissions[0]['data'][0]['custom'],
            'profile':{'first_name':'Alice','last_name':'Tazi'},
            'contact_info':{'most_probable_work_email':{'email':self.email,'status':'DELIVERABLE'}}}]}


def confirm_profile(store, identity, tmp_path, monkeypatch):
    from scripts import linkedin_queue
    from scripts.compact_linkedin import CompactLinkedIn
    from tests.test_compact_linkedin import extraction
    monkeypatch.setattr(linkedin_queue, 'ROOT', tmp_path)
    store.set_authority('linkedin')
    queue = CompactLinkedIn(store)
    task = queue.enqueue(identity)
    def result(payload):
        record = extraction(payload)
        record['profile']['company_displayed'] = store.candidate(identity)['data']['company_name']
        return record
    assert queue.run_one(runner=result)['status'] == 'IMPORTED'


def test_invalid_source_then_url_only_enrichment_and_new_email_reverification(tmp_path, monkeypatch):
    store, identity = setup(tmp_path)
    confirm_profile(store, identity, tmp_path, monkeypatch)
    nb, fe = NB('INVALID'), FE()
    runner = worker(store, nb, fullenrich=fe)
    assert runner.tick(identity)['status'] == 'SUBMITTED'
    assert not fe.submissions
    assert runner.tick(identity)['status'] == 'RESULT_APPLIED'
    assert runner.tick(identity)['status'] == 'SUBMITTED'
    sent = fe.submissions[0]['data'][0]
    assert set(sent) == {'linkedin_url','enrich_fields','custom'}
    assert runner.tick(identity)['status'] == 'RESULT_APPLIED'
    candidate = store.candidate(identity)['data']
    assert candidate['raw_email'] == 'a.tazi@example.org'
    assert candidate['clean_email'] is None and candidate['email_validation_source'] is None
    # A new adapter simulates the second provider tracking id and exact input.
    second_nb = NB()
    resumed = worker(CompactStore(store.path), second_nb, fullenrich=fe)
    assert resumed.tick(identity)['status'] == 'SUBMITTED'
    assert second_nb.submissions == [['a.tazi@example.org']]
    assert resumed.tick(identity)['status'] == 'QUALIFIED'
    assert len(fe.submissions) == 1


@pytest.mark.parametrize('change', ['personal', 'target'])
def test_enrichment_personal_result_or_changed_target_stays_out_of_mailing(tmp_path, monkeypatch, change):
    store, identity = setup(tmp_path)
    confirm_profile(store, identity, tmp_path, monkeypatch)
    store.update_candidate(identity, {'raw_email':None}, lane='qualification')
    nb, fe = NB(), FE('alice.tazi@gmail.com' if change=='personal' else 'a.tazi@example.org')
    runner = worker(store, nb, fullenrich=fe)
    assert runner.tick(identity)['status'] == 'SUBMITTED'
    if change == 'target':
        store.update_candidate(identity, {'company_name':'Other employer'}, lane='qualification')
        assert runner.tick(identity)['reason'] == 'FULLENRICH_TARGET_CHANGED'
    else:
        assert runner.tick(identity)['status'] == 'RESULT_APPLIED'
        assert store.candidate(identity)['data']['email_check_reason'] == 'PERSONAL_DOMAIN'
        assert runner.tick(identity)['reason'] == 'NO_BUSINESS_EMAIL_FOUND'
    assert not email_admissible(store.candidate(identity)['data'], at=AT)
    assert not nb.submissions
    assert len(fe.submissions) == 1


def test_restart_repairs_task_commit_after_candidate_application(tmp_path, monkeypatch):
    store, identity = setup(tmp_path)
    nb = NB()
    runner = worker(store, nb)
    submitted = runner.tick(identity)
    original = store.finish_task
    def interrupted(task_id, state, *args, **kwargs):
        if state == 'SUCCEEDED':
            raise RuntimeError('crash after candidate commit')
        return original(task_id, state, *args, **kwargs)
    monkeypatch.setattr(store, 'finish_task', interrupted)
    with pytest.raises(RuntimeError, match='candidate commit'):
        runner.tick(identity)
    assert store.task(submitted['task_id'])['status'] == 'SUBMITTED'
    restarted = worker(CompactStore(store.path), nb)
    assert restarted.tick(identity)['status'] == 'QUALIFIED'
    assert store.task(submitted['task_id'])['status'] == 'SUCCEEDED'
    assert len(nb.submissions) == 1


def test_real_guard_hooks_release_only_certain_failure_and_hold_unknown(tmp_path):
    store, identity = setup(tmp_path)
    guard = Mock()
    nb = NB(error=ProviderError('HTTP_400'))
    runner = CompactQualification(store, no2bounce=nb, cost_guard=guard, hygiene=lambda email:'PASS')
    runner.tick(identity)
    guard.failed.assert_called_once()
    guard.unknown.assert_not_called()
    with store.connection() as c:
        assert c.execute('SELECT status FROM workflow_tasks').fetchone()[0] == 'FAILED'


def test_cross_source_email_and_name_reuse_identity_but_conflicting_name_waits(tmp_path):
    store, identity = setup(tmp_path)
    row = dict(source_file='other.xlsx', first_name='Alice', last_name='Tazi', raw_email='alice.tazi@example.org')
    assert import_all(tmp_path, 'unused', backend='compact', compact_store=store, contacts=[row])['duplicates'] == 1
    assert len(store.candidates()) == 1
    assert len([r for r in store.evidence(identity) if r['kind']=='source_row']) == 2
    conflicting = {**row, 'last_name':'Other'}
    result = import_all(tmp_path, 'unused', backend='compact', compact_store=store, contacts=[conflicting])
    assert result['ambiguous'] == 1
    other = next(c for c in store.candidates() if c['id'] != identity)
    nb = NB()
    assert worker(store, nb).tick(other['id'])['reason'] == 'IDENTITY_CONFLICT'
    assert not nb.submissions


def test_replayed_import_repairs_evidence_after_interrupted_candidate_commit(tmp_path, monkeypatch):
    store = CompactStore(tmp_path/'journal.sqlite')
    store.set_authority('qualification')
    row = dict(source_file='fixture', first_name='Alice', last_name='Tazi', raw_email='alice.tazi@example.org')
    original = store.add_evidence
    monkeypatch.setattr(store, 'add_evidence', Mock(side_effect=RuntimeError('evidence interrupted')))
    with pytest.raises(RuntimeError, match='evidence interrupted'):
        import_all(tmp_path, 'unused', backend='compact', compact_store=store, contacts=[row])
    candidate = store.candidates()[0]
    assert store.evidence(candidate['id']) == []
    monkeypatch.setattr(store, 'add_evidence', original)
    result = import_all(tmp_path, 'unused', backend='compact', compact_store=store, contacts=[row])
    assert result['duplicates'] == 1 and result['inserted'] == 0
    assert len(store.candidates()) == 1
    assert json.loads(store.get_blob(store.evidence(candidate['id'])[0]['uri'])) == row
