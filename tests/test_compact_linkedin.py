"""Named-person confirmation uses a shared MCP lock and compact state only."""
import copy
import fcntl
import json
from datetime import datetime, timezone

import pytest

from scripts.compact_linkedin import CompactLinkedIn
from scripts.compact_store import CompactStore
from scripts import linkedin_queue


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(linkedin_queue, 'ROOT', tmp_path)
    store = CompactStore(tmp_path/'journal.sqlite')
    store.set_authority('qualification')
    store.set_authority('linkedin')
    identity = store.put_candidate({'first_name':'Alice','last_name':'Tazi',
        'company_name':'Example SA','source_file':'synthetic fixture',
        'raw_email':'alice.tazi@example.org'}, lane='qualification')
    return store, identity, CompactLinkedIn(store)


def extraction(task, state='CONFIRMED'):
    section = {'status':'NOT_OBSERVED','source_url':None,'text':None,'items':[], 'limitation':'Synthetic test'}
    url = 'https://www.linkedin.com/in/alice-tazi'
    profile = {key:copy.deepcopy(section) for key in ('about','experience','education','skills','certifications','languages','services','featured','projects','publications','volunteering','honors','recommendations')}
    profile.update(source_url=url,full_name='Alice Tazi',headline='CEO',location='Morocco',company_displayed='Example SA')
    return {'schema_version':'1.0','extraction_id':task['extraction_id'],'acquisition_method':'linkedin_mcp',
        'purpose':'CONTACT','target':task['target'],'observed_at':datetime.now(timezone.utc).isoformat(),
        'identity':{'status':state,'basis':['Name observed','Company observed'],'alternative_urls':[]},
        'linkedin_url':url if state == 'CONFIRMED' else None,
        'profile':profile if state == 'CONFIRMED' else None,
        'activity':{'status':'NOT_OBSERVED','source_url':None,'sorted_by':'UNKNOWN','exhaustive':False,'posts':[],'limitation':'Synthetic fixture'},
        'limitations':['Synthetic fixture, no real LinkedIn calls']}


def test_confirmation_replay_and_restart_without_staging(setup, tmp_path):
    store, identity, queue = setup
    task = queue.enqueue(identity)
    assert queue.enqueue(identity)['id'] == task['id']
    calls=[]
    outputs=[]
    def runner(payload):
        calls.append(payload)
        outputs.append(json.dumps(extraction(payload), indent=2).encode())
        return outputs[-1]
    result = queue.run_one(runner=runner)
    assert result['status'] == 'IMPORTED'
    reopened = CompactLinkedIn(CompactStore(store.path))
    assert reopened.enqueue(identity)['status'] == 'ALREADY_CONFIRMED'
    assert reopened.run_one(runner=runner)['status'] == 'EMPTY'
    assert len(calls) == 1
    candidate = store.candidate(identity)
    assert reopened.confirmed_input(candidate) == {'linkedin_url':'https://www.linkedin.com/in/alice-tazi','enrich_fields':['contact.work_emails']}
    assert store.get_blob(result['proof_uri']) == outputs[0]
    assert 'linkedin_profile_json' not in candidate['data']
    assert not (tmp_path/'journal.sqlite.journal.sqlite').exists()
    with store.connection() as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='staging_leads'").fetchone()


@pytest.mark.parametrize('state,expected,reason',[
    ('NOT_FOUND','NO_MATCH','LINKEDIN_NOT_FOUND'),
    ('NEEDS_REVIEW','NEEDS_REVIEW','LINKEDIN_REVIEW'),
    ('BLOCKED','WAITING','LINKEDIN_ACCESS_REQUIRED')])
def test_absent_ambiguous_and_access_are_distinct(setup,state,expected,reason):
    store, identity, queue = setup
    task = queue.enqueue(identity)
    assert queue.run_one(runner=lambda p:extraction(p,state))['status'] == state
    assert store.task(task['id'])['status'] == expected
    assert store.candidate(identity)['wait_reason'] == reason
    assert store.candidate(identity)['data'].get('linkedin_url') is None
    assert len(store.evidence(identity)) == 1
    assert queue.run_one(runner=lambda _:pytest.fail('must not retry'))['status'] == 'EMPTY'


def test_global_lock_prevents_second_queue_claim(setup,tmp_path):
    store, identity, queue = setup
    task = queue.enqueue(identity)
    path=tmp_path/'bdd/linkedin-mcp-worker.lock'
    path.parent.mkdir(parents=True)
    with path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert queue.run_one(runner=lambda _:pytest.fail('busy'))['status'] == 'MCP_BUSY'
    assert store.task(task['id'])['status'] == 'PENDING'
    assert queue.run_one(runner=lambda p:extraction(p))['status'] == 'IMPORTED'


def test_unknown_outcome_is_never_relaunched(setup):
    store, identity, queue = setup
    task = queue.enqueue(identity)
    calls=[]
    def broken(payload):
        calls.append(payload)
        raise TimeoutError('not logged')
    assert queue.run_one(runner=broken)['status'] == 'UNKNOWN'
    assert CompactLinkedIn(CompactStore(store.path)).run_one(runner=broken)['status'] == 'EMPTY'
    assert queue.enqueue(identity)['status'] == 'UNKNOWN'
    assert len(calls) == 1
    assert queue.reconcile(task['id'])['status'] == 'RECONCILIATION_REQUIRED'


def test_changed_target_not_spent_and_foreign_result_rejected(setup):
    store, identity, queue = setup
    task = queue.enqueue(identity)
    store.update_candidate(identity, {'company_name':'Changed'}, lane='qualification')
    assert queue.run_one(runner=lambda _:pytest.fail('stale'))['status'] == 'STALE'
    assert store.task(task['id'])['status'] == 'NEEDS_REVIEW'
    foreign = extraction(task['payload'])
    foreign['extraction_id']='some-other-task'
    with pytest.raises(ValueError,match='RESERVED_TASK'):
        queue.complete(task['id'],foreign)
    assert not store.evidence(identity)


def test_confirmed_name_and_professional_link_required(setup):
    store, identity, queue = setup
    task = queue.enqueue(identity)
    def runner(payload):
        record=extraction(payload)
        record['profile']['company_displayed']='Unrelated company'
        return record
    assert queue.run_one(runner=runner)['status'] == 'NEEDS_REVIEW'
    assert store.candidate(identity)['data'].get('linkedin_url') is None
    assert queue.confirmed_input(store.candidate(identity)) is None


def test_failed_result_keeps_exact_proof_without_attribution(setup):
    store, identity, queue = setup
    task = queue.enqueue(identity)
    def runner(payload):
        record = extraction(payload)
        record['profile']['full_name']='Someone Else'
        return record
    assert queue.run_one(runner=runner)['status'] == 'NEEDS_REVIEW'
    assert store.candidate(identity)['data'].get('linkedin_url') is None
    assert len(store.evidence(identity)) == 1


def test_interruption_after_proof_resumes_without_runner(setup,monkeypatch):
    store, identity, queue = setup
    task = queue.enqueue(identity)
    store.claim_task('linkedin','fixture-worker')
    original = store.update_candidate
    def interrupted(*args,**kwargs):
        raise RuntimeError('interrupted after proof saved')
    monkeypatch.setattr(store,'update_candidate',interrupted)
    record=extraction(task['payload'])
    with pytest.raises(RuntimeError,match='interrupted'):
        queue.complete(task['id'],record)
    assert store.task(task['id'])['status']=='SUBMITTED'
    reopened = CompactLinkedIn(CompactStore(store.path))
    assert reopened.reconcile(task['id'])['status']=='IMPORTED'
    assert reopened.reconcile(task['id'])['status']=='UNCHANGED'
    assert len(store.evidence(identity))==1


def test_exclusion_and_missing_name_require_review_without_search(setup):
    store, identity, queue = setup
    store.exclude('alice.tazi@example.org','UNSUBSCRIBE')
    assert queue.enqueue(identity)['status'] == 'EXCLUDED'
    other=store.put_candidate({'company_name':'Unnamed'},lane='qualification')
    with pytest.raises(ValueError,match='NAMED_PERSON_REQUIRED'):
        queue.enqueue(other)


def test_profile_binding_rechecked_before_paid_enrichment(setup):
    store, identity, queue = setup
    queue.enqueue(identity)
    assert queue.run_one(runner=lambda p:extraction(p))['status']=='IMPORTED'
    store.update_candidate(identity,{'company_name':'Now elsewhere'},lane='qualification')
    assert queue.confirmed_input(store.candidate(identity)) is None
