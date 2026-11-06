"""Captured GET evidence is applied exclusively to isolated restored copies."""
import copy
import gzip
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.compact_reconcile import apply_reconciliation
from scripts.outreach_journal import install_outbox, flush_journal
from scripts.workflow_store import WorkflowStore, encode


@pytest.fixture
def fixture(tmp_path):
    source=tmp_path/'restored'/'staging.sqlite';source.parent.mkdir()
    archive=source.parent/'journal.sqlite'
    evidence=tmp_path/'evidence';evidence.mkdir()
    lead={'id':1,'journal_contact_id':'contact-1','first_name':'Alice','last_name':'Tazi','company_name':'Example',
        'linkedin_url':'https://www.linkedin.com/in/alice-tazi','raw_email':None,'clean_email':None,'validated_email':None,
        'email_validated_at':None,'contact_discovery_status':'SUBMITTED','workflow_wait_reason':None}
    payload={'data':[{'linkedin_url':lead['linkedin_url'],'enrich_fields':['contact.work_emails'],'custom':{'request_key':'request-1'}}]}
    result={'id':'remote-1','status':'FINISHED','data':[{'custom':{'request_key':'request-1'},'contact_info':{'most_probable_work_email':None},'profile':{}}],'cost':{'credits':0}}
    target={'job_id':'job-1','request_key':'request-1','lead_id':1,'input_json':encode(payload['data'][0]),'applied':0}
    with sqlite3.connect(source) as c:
        c.execute('CREATE TABLE staging_leads('+','.join('"'+key+'" '+('INTEGER PRIMARY KEY' if key=='id' else 'TEXT') for key in lead)+')')
        c.execute('INSERT INTO staging_leads VALUES('+','.join('?' for _ in lead)+')',tuple(lead.values()))
        c.executescript('''CREATE TABLE enrichment_jobs(id TEXT PRIMARY KEY,provider TEXT,kind TEXT,status TEXT,remote_id TEXT,payload TEXT,response TEXT,reserved REAL,cost REAL,error TEXT,next_poll_at TEXT);
            CREATE TABLE enrichment_targets(job_id TEXT,request_key TEXT,lead_id INTEGER,input_json TEXT,applied INTEGER,PRIMARY KEY(job_id,request_key,lead_id));
            CREATE TABLE enrichment_cache(provider TEXT,kind TEXT,request_key TEXT,job_id TEXT,status TEXT,result TEXT,PRIMARY KEY(provider,kind,request_key));''')
        c.execute("INSERT INTO enrichment_jobs VALUES('job-1','fullenrich','contact','SUBMITTED','remote-1',?,NULL,2,NULL,NULL,NULL)",(encode(payload),))
        c.execute('INSERT INTO enrichment_targets VALUES(?,?,?,?,?)',tuple(target.values()))
        c.execute("INSERT INTO enrichment_cache VALUES('fullenrich','contact','request-1','job-1','SUBMITTED',NULL)")
        install_outbox(c)
    store=WorkflowStore(archive)
    operation=store.operation('contact-1','CREATE_PERSON',{'name':{'firstName':'Alice','lastName':'Tazi'},'emails':{'primaryEmail':'alice.tazi@example.org'},'companyId':'company-1'},key='unknown-create')
    store.finish_operation(operation['id'],'UNKNOWN')
    with store.connection() as c:
        operation=dict(c.execute('SELECT * FROM workflow_operations WHERE id=?',(operation['id'],)).fetchone())
    with sqlite3.connect(source) as c:
        c.row_factory=sqlite3.Row;job=dict(c.execute("SELECT * FROM enrichment_jobs WHERE id='job-1'").fetchone())
    def save(name,value,raw=False):
        path=evidence/name;path.write_bytes(value if raw else encode(value).encode());return path
    save('run.json',{'contacts':[{'journal_contact_id':'contact-1'}]})
    save('fullenrich-job-1-local.json',{'job':job,'targets':[target],'leads':[lead]})
    save('fullenrich-job-1-result.json',result)
    raw=encode(result).encode();save('fullenrich-job-1-http-1.body',raw,True)
    save('fullenrich-job-1-http-1.json',{'method':'GET','status_code':200,'response_sha256':hashlib.sha256(raw).hexdigest()})
    save('crm-unknown-create-local.json',{'operation':operation})
    save('crm-unknown-create-decision.json',{'state':'WAITING_UNPROVEN','matched_person_ids':[]})
    return source,archive,evidence,lead,result


def apply(fixture):
    return apply_reconciliation(*fixture[:3],offline_copy=True)


def test_local_no_match_and_unknown_evidence_are_idempotent_without_network(fixture):
    source,archive,evidence,lead,result=fixture
    with patch('requests.request',side_effect=AssertionError('no supplier or CRM request')):
        report=apply(fixture)
        assert report['provider_no_match']==1 and report['crm_unknown_preserved']==1
        with sqlite3.connect(source) as c:
            assert c.execute('SELECT status,error FROM enrichment_jobs').fetchone()==('FINISHED','NO_MATCH_NO_WORK_EMAIL')
            assert c.execute('SELECT status FROM enrichment_cache').fetchone()==('NO_MATCH',)
            assert c.execute('SELECT applied FROM enrichment_targets').fetchone()==(1,)
            assert c.execute('SELECT contact_discovery_status,workflow_wait_reason FROM staging_leads').fetchone()==('NO_MATCH','NO_BUSINESS_EMAIL_FOUND')
        with sqlite3.connect(archive) as c:
            assert c.execute("SELECT status FROM workflow_operations WHERE id='unknown-create'").fetchone()==('UNKNOWN',)
            count=c.execute('SELECT count(*) FROM journal_events').fetchone()[0]
            assert c.execute("SELECT count(*) FROM workflow_events WHERE kind='crm_unknown_readback'").fetchone()[0]==1
            uri=json.loads(c.execute("SELECT payload FROM workflow_events WHERE kind='provider_no_match_reconciled'").fetchone()[0])['response_proof']
            raw=c.execute('SELECT content_gzip FROM workflow_blobs WHERE sha256=?',(uri.rsplit('/',1)[-1],)).fetchone()[0]
            assert json.loads(gzip.decompress(raw))==result
        again=apply(fixture)
        assert again['outbox_exported']==0
        with sqlite3.connect(archive) as c:
            assert c.execute('SELECT count(*) FROM journal_events').fetchone()[0]==count
        assert not Path(str(source)+'.journal.sqlite').exists()


def test_explicit_copy_ack_and_active_file_aliases_required(fixture,monkeypatch):
    import scripts.compact_reconcile as module
    source,archive,*_=fixture
    with pytest.raises(ValueError,match='OFFLINE_COPY'):
        apply_reconciliation(*fixture[:3])
    monkeypatch.setenv('WORKFLOW_STAGING_PATH',str(source))
    with pytest.raises(ValueError,match='ACTIVE_DATABASE_REFUSED'):
        apply(fixture)
    monkeypatch.delenv('WORKFLOW_STAGING_PATH')
    hardlink=source.parent/'active-alias.sqlite';os.link(source,hardlink)
    monkeypatch.setenv('WORKFLOW_STAGING_PATH',str(hardlink))
    with pytest.raises(ValueError,match='ACTIVE_DATABASE_REFUSED'):
        apply(fixture)


@pytest.mark.parametrize('mutation',['custom','email','http','identity'])
def test_mismatched_or_email_result_requires_review(fixture,mutation):
    source,archive,evidence,lead,result=fixture
    if mutation=='identity':
        with sqlite3.connect(source) as c:c.execute("UPDATE staging_leads SET last_name='Changed'")
    elif mutation=='http':
        (evidence/'fullenrich-job-1-http-1.body').write_bytes(b'{}')
    else:
        modified=copy.deepcopy(result)
        if mutation=='custom':modified['data'][0]['custom']['request_key']='other'
        else:modified['data'][0]['contact_info']['most_probable_work_email']={'email':'alice.tazi@example.org'}
        raw=encode(modified).encode()
        (evidence/'fullenrich-job-1-result.json').write_bytes(raw)
        (evidence/'fullenrich-job-1-http-1.body').write_bytes(raw)
        (evidence/'fullenrich-job-1-http-1.json').write_text(encode({'method':'GET','status_code':200,'response_sha256':hashlib.sha256(raw).hexdigest()}))
    with pytest.raises(ValueError):apply(fixture)
    with sqlite3.connect(source) as c:
        assert c.execute('SELECT status FROM enrichment_jobs').fetchone()==('SUBMITTED',)


def test_interruption_after_source_commit_replays_to_explicit_archive(fixture,monkeypatch):
    import scripts.compact_reconcile as module
    original=module.flush_journal
    monkeypatch.setattr(module,'flush_journal',lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('exit before outbox export')))
    with pytest.raises(RuntimeError,match='exit before'):apply(fixture)
    monkeypatch.setattr(module,'flush_journal',original)
    assert apply(fixture)['provider_no_match']==1
    with sqlite3.connect(fixture[1]) as c:
        assert c.execute("SELECT count(*) FROM workflow_events WHERE kind='provider_no_match_reconciled'").fetchone()[0]==1
    assert not Path(str(fixture[0])+'.journal.sqlite').exists()
