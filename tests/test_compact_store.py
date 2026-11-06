"""Compact state, concurrency and evidence survive process boundaries offline."""
import gzip
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from scripts.compact_store import CompactStore
from scripts.workflow_store import WorkflowStore


def active(tmp_path, *lanes):
    store = CompactStore(tmp_path/'journal.sqlite')
    for lane in lanes or ('qualification',):
        store.set_authority(lane)
    return store


def test_additive_default_does_not_activate_or_break_existing_store(tmp_path):
    path = tmp_path/'journal.sqlite'
    legacy = WorkflowStore(path)
    legacy.put_contact({'journal_contact_id':'admitted', 'clean_email':'alice.tazi@example.org'})
    store = CompactStore(path)
    assert store.authority('qualification') == 'legacy'
    with pytest.raises(RuntimeError, match='COMPACT_AUTHORITY_REQUIRED'):
        store.put_candidate({'first_name':'Alice'}, lane='qualification')
    assert store.contact('admitted') == legacy.contact('admitted')
    assert list(tmp_path.glob('*.sqlite')) == [path]
    store.set_authority('qualification')
    with pytest.raises(RuntimeError, match='AUTHORITY_CHANGED'):
        store.set_authority('qualification')
    with pytest.raises(ValueError, match='INVALID_AUTHORITY'):
        store.set_authority('qualification', 'legacy', expected='compact')


def test_candidate_restart_wait_mapping_and_no_name_merge(tmp_path):
    store = active(tmp_path)
    first = store.put_candidate({'first_name':'Alice','last_name':'Tazi'}, lane='qualification', source_id='source-A', legacy_id=1)
    second = store.put_candidate({'first_name':'Alice','last_name':'Tazi'}, lane='qualification', source_id='source-B', legacy_id=1)
    assert first != second
    store.update_candidate(first, {'email_check_status':'UNKNOWN'}, lane='qualification', status='WAITING', wait_reason='PROVIDER_RECONCILIATION')
    reopened = CompactStore(store.path)
    assert reopened.resolve_legacy('source-A', 1) == first
    assert reopened.candidate(first)['data']['journal_contact_id'] == first
    assert reopened.candidate(first)['wait_reason'] == 'PROVIDER_RECONCILIATION'
    # Replayed import preserves advanced state rather than rolling it back.
    assert reopened.put_candidate({'first_name':'Alice'}, lane='qualification', source_id='source-A', legacy_id=1) == first
    assert reopened.candidate(first)['status'] == 'WAITING'
    assert len(reopened.candidates(status='PENDING')) == 1


def test_identity_mapping_conflicts_are_atomic(tmp_path):
    store = active(tmp_path)
    first = store.put_candidate({}, lane='qualification', candidate_id='one', source_id='source', legacy_id=1)
    other = store.put_candidate({}, lane='qualification', candidate_id='two')
    with pytest.raises(ValueError, match='LEGACY_IDENTITY_CONFLICT'):
        store.bind_legacy('source', 1, other)
    with pytest.raises(ValueError, match='LEGACY_IDENTITY_CONFLICT'):
        store.put_candidate({}, lane='qualification', candidate_id='three', source_id='source', legacy_id=1)
    assert store.candidate('three') is None
    with pytest.raises(ValueError, match='DURABLE_IDENTITY_CONFLICT'):
        store.update_candidate(first, {'journal_contact_id':'two'}, lane='qualification')
    with store.connection() as c:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            c.execute("UPDATE workflow_legacy_ids SET candidate_id='two'")
    assert store.resolve_legacy('source',1) == first


def test_revision_and_reason_changes_are_explicit(tmp_path):
    store = active(tmp_path, 'qualification', 'linkedin')
    identity = store.put_candidate({}, lane='qualification', wait_reason='PROFILE_REQUIRED')
    assert store.update_candidate(identity, {}, lane='qualification')['revision'] == 1
    row = store.update_candidate(identity, {'linkedin_url':'https://www.linkedin.com/in/alice-tazi'}, lane='linkedin', expected_revision=1)
    assert row['wait_reason'] == 'PROFILE_REQUIRED'
    with pytest.raises(RuntimeError, match='CANDIDATE_CHANGED'):
        store.update_candidate(identity, {'raw_email':'stale@example.org'}, lane='qualification', expected_revision=1)
    assert store.update_candidate(identity, {}, lane='qualification', wait_reason=None)['wait_reason'] is None


def test_one_claim_across_connections_and_no_automatic_retry(tmp_path):
    store = active(tmp_path)
    task = store.create_task('qualification','VERIFY', {'email':'alice.tazi@example.org'}, key='verify-1')
    def claim(worker):
        return CompactStore(store.path).claim_task('qualification', worker)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, ['worker-a','worker-b']))
    assert sum(row is not None for row in claimed) == 1
    owner = next(row['worker'] for row in claimed if row)
    with pytest.raises(RuntimeError, match='ANOTHER_WORKER'):
        store.finish_task(task['id'], 'SUBMITTED', worker='intruder')
    store.finish_task(task['id'], 'SUBMITTED', {'remote_id':'provider-1'}, worker=owner)
    resumed = CompactStore(store.path)
    assert resumed.claim_task('qualification','worker-c') is None
    assert resumed.task(task['id'])['result'] == {'remote_id':'provider-1'}
    resumed.finish_task(task['id'], 'UNKNOWN', {'remote_id':'provider-1'})
    assert resumed.claim_task('qualification','worker-c') is None
    assert resumed.create_task('qualification','VERIFY', {'email':'alice.tazi@example.org'}, key='verify-1')['status'] == 'UNKNOWN'
    resumed.finish_task(task['id'], 'SUCCEEDED', {'verdict':'VALID'})
    assert resumed.finish_task(task['id'], 'SUCCEEDED', {'verdict':'VALID'})['status'] == 'SUCCEEDED'
    with pytest.raises(RuntimeError, match='ALREADY_FINISHED'):
        resumed.finish_task(task['id'], 'UNKNOWN')


def test_task_keys_bind_exact_request_and_target(tmp_path):
    store = active(tmp_path)
    one = store.put_candidate({}, lane='qualification')
    two = store.put_candidate({}, lane='qualification')
    task = store.create_task('qualification', 'VERIFY', {'email':'a@example.org'}, candidate_id=one, key='one')
    with pytest.raises(ValueError, match='DIFFERENT_CONTENT'):
        store.create_task('qualification', 'VERIFY', {'email':'a@example.org'}, candidate_id=two, key='one')
    assert store.task(task['id'])['candidate_id'] == one
    assert store.claim_task('qualification','worker',task_id='missing') is None
    store.finish_task(task['id'],'WAITING',wait_reason='ACCESS_REQUIRED')
    assert CompactStore(store.path).task(task['id'])['wait_reason'] == 'ACCESS_REQUIRED'
    assert store.claim_task('qualification','worker') is None


def test_evidence_one_blob_for_multiple_paths_and_candidates(tmp_path):
    store = active(tmp_path)
    one = store.put_candidate({}, lane='qualification')
    two = store.put_candidate({}, lane='qualification')
    raw = b'{ "exact": null, "provider": "response" }\n'
    uri = store.add_evidence(one, raw, path='first.json')
    assert store.add_evidence(one, raw, path='second.json') == uri
    assert store.add_evidence(two, raw) == uri
    assert CompactStore(store.path).get_blob(uri) == raw
    with store.connection() as c:
        assert c.execute('SELECT count(*) FROM workflow_blobs').fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM workflow_evidence_refs').fetchone()[0] == 3
    assert all(ref['uri'] == uri for ref in store.evidence(one))
    with pytest.raises(TypeError, match='EXACT_BYTES'):
        store.put_blob('not exact bytes')


def test_legacy_artifact_uri_and_exclusion_survive(tmp_path):
    store = active(tmp_path)
    raw = b'old exact proof'
    sha = hashlib.sha256(raw).hexdigest()
    with store.connection() as c:
        c.execute('CREATE TABLE workflow_artifacts (path TEXT,sha256 TEXT,content_gzip BLOB,original_bytes INTEGER,saved_at TEXT)')
        c.execute('INSERT INTO workflow_artifacts VALUES (?,?,?,?,?)', ('old/path',sha,gzip.compress(raw),len(raw),'old-date'))
    store.exclude('alice.tazi@example.org','BOUNCE_PERMANENT')
    reopened = CompactStore(store.path)
    assert reopened.get_blob('journal://artifacts/'+sha) == raw
    assert reopened.excluded('alice.tazi@example.org')


def test_corrupt_blob_fails_closed(tmp_path):
    store = active(tmp_path)
    sha = store.put_blob(b'proof')
    with store.connection() as c:
        c.execute('UPDATE workflow_blobs SET content_gzip=? WHERE sha256=?', (gzip.compress(b'other'),sha))
    with pytest.raises(RuntimeError, match='BLOB_VERIFICATION_FAILED'):
        store.get_blob(sha)


def test_existing_approved_draft_is_unchanged(tmp_path):
    from scripts.telegram_approval import create_draft
    store = WorkflowStore(tmp_path/'journal.sqlite')
    identity = store.put_contact({'journal_contact_id':'one','clean_email':'alice.tazi@example.org'}, status='READY_TO_MAIL')
    draft = create_draft(store,identity,'alice.tazi@example.org','Objet exact','Corps exact.', {'signature_mode':'thunderbird_html'})
    with store.connection() as c:
        c.execute("UPDATE workflow_drafts SET status='APPROVED',approved_by='101',approved_at='date' WHERE id=?", (draft['id'],))
        original = dict(c.execute('SELECT * FROM workflow_drafts WHERE id=?',(draft['id'],)).fetchone())
    compact = CompactStore(store.path)
    compact.set_authority('qualification')
    compact.put_candidate({},lane='qualification')
    with compact.connection() as c:
        assert dict(c.execute('SELECT * FROM workflow_drafts WHERE id=?',(draft['id'],)).fetchone()) == original
