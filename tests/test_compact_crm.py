import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from scripts.compact_crm import transfer_compact
from scripts.compact_store import CompactStore
from scripts.workflow_files import cleanup_completed, confirmed, read_file, save_file
from tests.test_workflow_refactor import FakeCRM

AT = datetime(2026,11,5,12,tzinfo=timezone.utc)


def setup(tmp_path):
    store = CompactStore(tmp_path/'journal.sqlite')
    store.set_authority('qualification')
    store.set_authority('crm')
    identity = store.put_candidate({'first_name':'Alice','last_name':'Tazi','company_name':'Example SA',
        'raw_email':'alice.tazi@example.org','clean_email':'alice.tazi@example.org',
        'validated_email':'alice.tazi@example.org','email_check_status':'VALID',
        'email_validation_source':'no2bounce','email_validated_at':AT.isoformat(),
        'company_domain':'example.org','company_domain_source':'operator',
        'phone_mobile':'0612345678','phone_standard':'0522345678','job_title':'CEO',
        'city':'Casablanca','address':'Test address','activity_sector':'Software',
        'linkedin_url':'https://www.linkedin.com/in/unconfirmed'}, lane='qualification', status='QUALIFIED')
    return store, identity, FakeCRM()


def transfer(store, identity, crm, **kwargs):
    return transfer_compact(store,identity,crm,clock=lambda:AT,**kwargs)


def run_files(tmp_path, identity, *, folder='reports/run'):
    directory = tmp_path/folder
    directory.mkdir(parents=True)
    (directory/'run.json').write_text(json.dumps({'contacts':[{'journal_contact_id':identity}]}))
    evidence = directory/'exact.json'
    evidence.write_bytes(b'{ "exact": null }\n')
    return directory, evidence


def clean(store, tmp_path, **kwargs):
    return cleanup_completed(store,None,archives_dir=tmp_path/'archives',reports_dir=tmp_path/'reports',**kwargs)


def test_transfer_compact_readback_fields_and_no_sas(tmp_path):
    store, identity, crm = setup(tmp_path)
    result = transfer(store,identity,crm)
    assert result['status'] == 'TRANSFERRED'
    row = store.contact(identity)
    person = crm.rows['people'][row['person_id']]
    company = crm.rows['companies'][row['company_id']]
    assert row['status'] == 'READY_TO_MAIL'
    assert person['companyId'] == company['id']
    assert person['phones']['primaryPhoneNumber'] == '0612345678'
    assert person['phones']['additionalPhones'][0]['number'] == '0522345678'
    assert 'linkedinLink' not in person
    assert company['address']['addressCity'] == 'Casablanca'
    assert company['activitySector'] == 'Software'
    control = json.loads(store.get_blob(result['proof_uri']))
    assert control['person'] == person and control['company'] == company
    assert confirmed(store,None,[identity])
    assert transfer(store,identity,crm)['status'] == 'TRANSFERRED'
    assert len(crm.creations) == 2
    with store.connection() as c:
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='staging_leads'").fetchone() is None
    assert list(tmp_path.glob('*.sqlite')) == [tmp_path/'journal.sqlite']


@pytest.mark.parametrize('change', [dict(email_check_status='CATCHALL'),dict(email_check_status='WAITING'),
    dict(email_validated_at=(AT-timedelta(days=91)).isoformat()),dict(validated_email='other@example.org')])
def test_admission_gate_blocks_nonmatching_or_stale_verdict(tmp_path,change):
    store, identity, crm = setup(tmp_path)
    store.update_candidate(identity,change,lane='qualification')
    assert transfer(store,identity,crm)['status'] == 'NOT_ADMISSIBLE'
    assert not crm.creations


@pytest.mark.parametrize('resource',['companies','people'])
def test_lost_create_response_reconciles_without_duplicate(tmp_path,resource):
    store, identity, crm = setup(tmp_path)
    crm.unknown_once = resource
    assert transfer(store,identity,crm)['status'] == 'CRM_RECONCILIATION_REQUIRED'
    assert clean(store,tmp_path)['removed'] == []
    assert transfer(CompactStore(store.path),identity,crm)['status'] == 'TRANSFERRED'
    assert [r for r,p in crm.creations].count(resource) == 1
    assert len(crm.creations) == 2


def test_person_company_readback_mismatch_is_not_admitted(tmp_path):
    store, identity, crm = setup(tmp_path)
    original = crm.workflow_get
    def wrong(resource, key):
        row = original(resource,key)
        if resource == 'people':
            return {**row,'companyId':'wrong-company'}
        return row
    crm.workflow_get = wrong
    assert transfer(store,identity,crm)['status'] == 'PERSON_COMPANY_REVIEW'
    assert store.contact(identity) is None
    assert not confirmed(store,None,[identity])


def test_interrupted_proof_commit_replays_without_second_crm_create(tmp_path,monkeypatch):
    store, identity, crm = setup(tmp_path)
    original = store.add_evidence
    monkeypatch.setattr(store,'add_evidence',Mock(side_effect=RuntimeError('disk interrupted')))
    assert transfer(store,identity,crm)['status'] == 'CRM_ACCESS_OR_CONTRACT_REQUIRED'
    assert store.contact(identity) is None
    monkeypatch.setattr(store,'add_evidence',original)
    assert transfer(CompactStore(store.path),identity,crm)['status'] == 'TRANSFERRED'
    assert len(crm.creations) == 2


def test_cleanup_archives_exact_bytes_once_and_preserves_exclusions(tmp_path):
    store, identity, crm = setup(tmp_path)
    assert transfer(store,identity,crm)['status'] == 'TRANSFERRED'
    directory, evidence = run_files(tmp_path,identity)
    original = evidence.read_bytes()
    other = tmp_path/'other.json'
    other.write_bytes(original)
    digest = save_file(store,other)
    store.exclude('other@example.org','PERMANENT_BOUNCE')
    assert str(directory) in clean(store,tmp_path)['removed']
    assert not directory.exists()
    assert read_file(CompactStore(store.path),evidence) == original
    assert read_file(store,'journal://artifacts/'+digest) == original
    assert store.excluded('other@example.org')
    with store.connection() as c:
        assert c.execute('SELECT count(*) FROM workflow_blobs WHERE sha256=?',(digest,)).fetchone()[0] == 1
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_artifacts'").fetchone() is None


@pytest.mark.parametrize('state',['PENDING','RUNNING','SUBMITTED','UNKNOWN','WAITING','NEEDS_REVIEW'])
def test_pending_or_uncertain_task_keeps_every_file(tmp_path,state):
    store, identity, crm = setup(tmp_path)
    assert transfer(store,identity,crm)['status'] == 'TRANSFERRED'
    task = store.create_task('qualification','VERIFY',{},candidate_id=identity)
    if state == 'RUNNING':
        store.claim_task('qualification','worker',task_id=task['id'])
    elif state != 'PENDING':
        store.finish_task(task['id'],state)
    directory, evidence = run_files(tmp_path,identity)
    assert str(directory) in clean(store,tmp_path)['waiting']
    assert evidence.exists()


def test_untransferred_candidate_corrupt_proof_and_unknown_operation_keep_files(tmp_path):
    store, identity, crm = setup(tmp_path)
    directory, evidence = run_files(tmp_path,identity)
    assert clean(store,tmp_path)['removed'] == []
    assert transfer(store,identity,crm)['status'] == 'TRANSFERRED'
    operation = store.operation(identity,'OTHER',{})
    store.finish_operation(operation['id'],'UNKNOWN')
    assert clean(store,tmp_path)['removed'] == []
    store.finish_operation(operation['id'],'SUCCEEDED')
    uri = store.candidate(identity)['data']['crm_control_uri']
    with store.connection() as c:
        c.execute('UPDATE workflow_blobs SET content_gzip=? WHERE sha256=?',(gzip.compress(b'corrupt'),uri.rsplit('/',1)[1]))
    assert clean(store,tmp_path)['removed'] == []
    assert evidence.exists()


def test_legacy_artifact_references_still_resolve_and_source_exports_are_protected(tmp_path):
    store, identity, crm = setup(tmp_path)
    assert transfer(store,identity,crm)['status'] == 'TRANSFERRED'
    raw = b'old exact bytes'
    digest = hashlib.sha256(raw).hexdigest()
    path = tmp_path/'legacy.json'
    with store.connection() as c:
        c.execute('CREATE TABLE workflow_artifacts(path TEXT,sha256 TEXT,content_gzip BLOB,original_bytes INTEGER,saved_at TEXT)')
        c.execute('INSERT INTO workflow_artifacts VALUES(?,?,?,?,?)',(str(path),digest,gzip.compress(raw),len(raw),'old'))
    assert read_file(store,path) == raw
    assert read_file(store,'journal://artifacts/'+digest) == raw
    for folder in ('outputs/human-export','bdd/sources/original'):
        directory, evidence = run_files(tmp_path,identity,folder=folder)
        outcome = cleanup_completed(store,None,archives_dir=tmp_path/'archives',reports_dir=directory.parent)
        assert outcome['removed'] == [] and evidence.exists()


def test_unknown_patch_reconciles_by_readback_and_never_reapplies_mutation(tmp_path):
    store, identity, crm = setup(tmp_path)
    crm.rows['people']['existing-person'] = {
        'id':'existing-person','name':{'firstName':'Alice','lastName':'Tazi'},
        'emails':{'primaryEmail':'alice.tazi@example.org'},'campaignStatus':'WAITING_EMAIL'}
    original = crm.workflow_patch
    calls = []
    def lost(resource, identity, payload):
        calls.append(payload)
        original(resource,identity,payload)
        raise RuntimeError('patch response lost')
    crm.workflow_patch = lost
    assert transfer(store,identity,crm)['status'] == 'CRM_RECONCILIATION_REQUIRED'
    assert transfer(CompactStore(store.path),identity,crm)['status'] == 'TRANSFERRED'
    assert len(calls) == 1
    assert len(crm.creations) == 1


@pytest.mark.parametrize('state',['WAITING','SUBMITTED','RUNNING'])
def test_incomplete_workflow_operation_also_protects_files(tmp_path,state):
    store, identity, crm = setup(tmp_path)
    assert transfer(store,identity,crm)['status'] == 'TRANSFERRED'
    directory, evidence = run_files(tmp_path,identity)
    operation = store.operation(identity,'INCOMPLETE',{})
    store.finish_operation(operation['id'],state)
    assert str(directory) in clean(store,tmp_path)['waiting']
    assert evidence.exists()
