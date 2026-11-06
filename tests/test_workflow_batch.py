import sqlite3

from scripts.workflow_batch import ManualBatch
from scripts.workflow import Workflow
from scripts.workflow_import import import_all
from tests.test_workflow_refactor import FakeCRM, FakeTelegram, FakeNB, FakeFE, source, state
from scripts.direct_enrichment import DirectEnrichment
from scripts.thunderbird_client import ThunderbirdClient


def test_manual_batch_freezes_exact_membership_and_never_sends(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.workflow_batch.ROOT',tmp_path)
    monkeypatch.setenv('WORKFLOW_STOP_AT_DRAFT','1')
    path=tmp_path/'sas.sqlite'
    import_all(tmp_path,path,contacts=[source(first_name=f'Alice{i}',raw_email=None,is_dirigeant=True) for i in range(25)])
    nb=FakeNB();telegram=FakeTelegram();mail=ThunderbirdClient(dry_run=True)
    workflow=Workflow(path,root=tmp_path,crm=FakeCRM(),telegram=telegram,mail=mail,
                      enrichment=DirectEnrichment(workflow=True,no2bounce=nb,fullenrich=FakeFE(),poll_seconds=0))
    batch=ManualBatch(workflow,'manual-test-20')
    created=batch.create(20)
    assert created['selected']==20 and batch.selected()==list(range(1,21))
    assert batch.create(20)['status']=='ALREADY_CREATED'
    assert ManualBatch(workflow,'manual-test-next').create(20)=={
        'status':'NOT_ENOUGH_CANDIDATES','available':5}
    batch.verify()
    assert nb.calls==[] and telegram.sent==[] and not mail._sent_emails
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*) FROM linkedin_browser_queue').fetchone()[0]==20
    calls=[]
    def fake_run_one(db_path, **kwargs):
        calls.append((db_path,kwargs))
        return {'status':'EMPTY'}
    monkeypatch.setattr('scripts.workflow_batch.run_one',fake_run_one)
    assert batch.linkedin()['status']=='EMPTY'
    assert calls[0][0]==str(path)
    assert calls[0][1]['lead_ids']==set(range(1,21))
    assert batch.draft()['status']=='NO_DRAFT_NEEDED'
    assert (batch.directory/'RESULTATS.md').exists()


def test_batch_drafting_does_not_write_unrelated_ready_contact(state, tmp_path, monkeypatch):
    store,lead,telegram,breaker=state
    monkeypatch.setenv('WORKFLOW_STOP_AT_DRAFT','1')
    def forbidden_writer(*args,**kwargs):
        raise AssertionError('Unrelated contact must not reach writer')
    workflow=Workflow(tmp_path/'sas.sqlite',state_path=store.path,root=tmp_path,
                      crm=FakeCRM(),telegram=telegram,mail=ThunderbirdClient(dry_run=True),
                      breaker=breaker,agent_runner=forbidden_writer)
    assert workflow.write_one(submit_to_telegram=False,contact_ids=set())['status']=='NO_DRAFT_NEEDED'
    with store.connection() as c:
        assert c.execute('SELECT count(*) FROM workflow_drafts').fetchone()[0]==0
    assert telegram.sent==[]
