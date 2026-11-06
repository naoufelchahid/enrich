import gzip
import hashlib
import json
import sqlite3

from scripts.workflow_files import cleanup_completed, read_file
from scripts.workflow_store import WorkflowStore


def setup_run(tmp_path, *, transferred=True):
    db = tmp_path/'sas.sqlite'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE staging_leads(journal_contact_id TEXT)')
        c.execute('CREATE TABLE linkedin_browser_queue(result_path TEXT,lead_id INTEGER,status TEXT)')
        if not transferred:
            c.execute("INSERT INTO staging_leads VALUES('contact-1')")
    store = WorkflowStore(tmp_path/'journal.sqlite')
    with store.connection() as c:
        c.execute("INSERT INTO workflow_contacts(id,snapshot,person_id,company_id,status,admitted_at) VALUES('contact-1','{}','person','company','READY_TO_MAIL','2026-10-12')")
    store.event('transfer_verified','contact-1',{'person_id':'person','company_id':'company'})
    directory = tmp_path/'reports/manual-test'
    directory.mkdir(parents=True)
    manifest = directory/'selection.json'
    manifest.write_text(json.dumps({'contacts':[{'journal_contact_id':'contact-1'}]}))
    result = directory/'result.json'
    result.write_bytes(b'{"status":"CONFIRMED"}')
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO linkedin_browser_queue VALUES(?,1,'IMPORTED')",(str(result),))
    return db,store,directory,result


def clean(db,store,tmp_path,**kwargs):
    return cleanup_completed(store,db,archives_dir=tmp_path/'archives',reports_dir=tmp_path/'reports',**kwargs)


def test_completed_run_removed_and_exact_proof_readable_from_journal(tmp_path):
    db,store,directory,result = setup_run(tmp_path)
    raw = result.read_bytes()
    outcome = clean(db,store,tmp_path)
    assert str(directory) in outcome['removed'] and not directory.exists()
    assert read_file(store,result) == raw
    with store.connection() as c:
        row = c.execute('SELECT sha256,content_gzip FROM workflow_artifacts WHERE path=?',(str(result),)).fetchone()
    assert gzip.decompress(row[1]) == raw
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT result_path FROM linkedin_browser_queue').fetchone()[0] == 'journal://artifacts/'+hashlib.sha256(raw).hexdigest()
    assert clean(db,store,tmp_path)['removed'] == []


def test_sas_contact_and_unknown_operation_keep_files(tmp_path):
    db,store,directory,result = setup_run(tmp_path,transferred=False)
    assert clean(db,store,tmp_path)['removed'] == [] and result.exists()
    with sqlite3.connect(db) as c:
        c.execute('DELETE FROM staging_leads')
    op = store.operation('contact-1','CREATE_PERSON',{})
    store.finish_operation(op['id'],'UNKNOWN')
    assert clean(db,store,tmp_path)['removed'] == [] and result.exists()


def test_dry_run_and_unexpected_files_never_delete(tmp_path):
    db,store,directory,result = setup_run(tmp_path)
    assert clean(db,store,tmp_path,dry_run=True)['removed'] == [str(directory)]
    assert result.exists()
    (directory/'.env').write_text('private')
    assert clean(db,store,tmp_path)['removed'] == [] and result.exists()


def test_failed_evidence_commit_keeps_all_run_files(tmp_path,monkeypatch):
    db,store,directory,result = setup_run(tmp_path)
    def fail(*args):
        raise RuntimeError('storage unavailable')
    monkeypatch.setattr('scripts.workflow_files.save_file',fail)
    import pytest
    with pytest.raises(RuntimeError):
        clean(db,store,tmp_path)
    assert result.exists() and (directory/'selection.json').exists()


def test_completed_batch_can_report_and_draft_after_its_files_were_removed(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from scripts.workflow_batch import ManualBatch
    monkeypatch.setattr('scripts.workflow_batch.ROOT',tmp_path)
    db,store,directory,result = setup_run(tmp_path)
    selection = {'contacts':[{'id':1,'journal_contact_id':'contact-1',
                             'first_name':'Alice','last_name':'Tazi','company_name':'Example'}]}
    (directory/'selection.json').write_text(json.dumps(selection))
    with store.connection() as c:
        c.execute("INSERT INTO workflow_batches VALUES('manual-test','[1]','COMPLETE','{}')")
    calls = []
    def writer(**kwargs):
        calls.append(kwargs)
        return {'status':'DRAFT_ONLY'}
    workflow = SimpleNamespace(store=store,db_path=db,root=tmp_path,write_one=writer)
    batch = ManualBatch(workflow,'manual-test')
    assert batch.report()['selected'] == 1
    assert not directory.exists()
    assert batch.report()['selected'] == 1
    assert batch.draft()['status'] == 'DRAFT_ONLY'
    assert calls[0]['contact_ids'] == {'contact-1'}
    assert calls[0]['submit_to_telegram'] is False
    assert not directory.exists()


def test_completed_worker_can_be_removed_inside_an_unfinished_batch(tmp_path):
    db,store,directory,result = setup_run(tmp_path)
    (directory/'selection.json').write_text(json.dumps({'contacts':[{'journal_contact_id':'pending'}]}))
    worker = directory/'preuves/linkedin/extraction-1'
    worker.mkdir(parents=True)
    (worker/'run.json').write_text(json.dumps({'contacts':[{'journal_contact_id':'contact-1'}]}))
    proof = worker/'result.json'
    proof.write_bytes(b'{"proof":true}')
    outcome = clean(db,store,tmp_path)
    assert not worker.exists() and directory.exists()
    assert str(directory) in outcome['waiting']
    assert read_file(store,proof) == b'{"proof":true}'
