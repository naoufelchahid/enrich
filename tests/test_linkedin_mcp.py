import copy
import json
import sqlite3
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pytest
from pydantic import ValidationError
from scripts.linkedin_mcp import export_task,import_extraction
from scripts.linkedin_contract import Extraction
from scripts.staging_db import init_staging_db,insert_staging_leads

@pytest.fixture
def sample(tmp_path):
    db=tmp_path/'sas.sqlite';init_staging_db(str(db)).close()
    insert_staging_leads(str(db),[{'source_file':'BDD','first_name':'Alice','last_name':'Example',
        'company_name':'Example SA','job_title':'Existing title','raw_email':'alice@example.org'}])
    section={'status':'NOT_OBSERVED','source_url':None,'text':None,'items':[],'limitation':'Not inspected'}
    url='https://www.linkedin.com/in/alice'
    profile={k:copy.deepcopy(section) for k in ('about','experience','education','skills','certifications','languages','services','featured','projects','publications','volunteering','honors','recommendations')}
    profile.update(source_url=url,full_name='Alice Example',headline='CEO',location='Morocco')
    data={'schema_version':'1.0','extraction_id':'test-alice-001','acquisition_method':'linkedin_mcp',
        'purpose':'CONTACT','target':export_task(db,1)['target'],'observed_at':datetime.now(timezone.utc).isoformat(),
        'identity':{'status':'CONFIRMED','basis':['Name matches','Company matches'],'alternative_urls':[]},
        'linkedin_url':url,'profile':profile,'activity':{'status':'COMPLETE','source_url':url+'/recent-activity/all/',
        'sorted_by':'RECENT','exhaustive':False,'posts':[{'kind':'POST','author':'Alice Example','source_url':url+'/recent-activity/all/',
        'permalink':None,'date_displayed':'2d','published_at':None,'date_precision':'RELATIVE','text':'Professional post',
        'text_status':'EXPANDED','original':None}], 'limitation':None},'limitations':[]}
    output=tmp_path/'result.json'
    return db,output,data

def run(sample,**kw):
    db,path,data=sample;path.write_text(json.dumps(data));return import_extraction(db,path,**kw)

def row(db):
    with sqlite3.connect(db) as c:
        c.row_factory=sqlite3.Row
        return dict(c.execute('SELECT * FROM staging_leads').fetchone())

def test_import_proof_isolation_and_idempotency(sample):
    db,_,_=sample;before=row(db)
    assert run(sample)['status']=='IMPORTED'
    after=row(db)
    for k in ('raw_email','clean_email','email_check_status','validated_email','email_validated_at','neverbounce_status','company_name','job_title','crm_person_id','enrichment_status'):
        assert after[k]==before[k]
    assert after['linkedin_url']=='https://www.linkedin.com/in/alice'
    assert len(json.loads(after['recent_posts']))==1
    assert run(sample)['status']=='UNCHANGED'
    import gzip
    packed = db.parent/'backups/migrations'/(db.name+'.before-linkedin-mcp.bak.gz')
    restored = db.parent/'restored.sqlite'
    restored.write_bytes(gzip.decompress(packed.read_bytes()))
    with sqlite3.connect(restored) as c:
        assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        assert c.execute('SELECT linkedin_url FROM staging_leads').fetchone()[0]==before['linkedin_url']

@pytest.mark.parametrize('case',['target','name','url','reference','unknownfield','future','alternatives'])
def test_bad_extraction_never_mutates_db(sample,case):
    db,_,d=sample;before=db.read_bytes()
    if case=='target':d['target']['company_name']='Other'
    if case=='name':d['profile']['full_name']='Other Person'
    if case=='url':d['linkedin_url']='https://www.linkedin.com/company/example'
    if case=='reference':d.update(purpose='REFERENCE',target=None);d['identity']['status']='REFERENCE'
    if case=='unknownfield':d['email_check_status']='VALID'
    if case=='future':d['observed_at']=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
    if case=='alternatives':d['identity']['alternative_urls']=['https://www.linkedin.com/in/other']
    with pytest.raises((ValueError,ValidationError)):run(sample)
    assert db.read_bytes()==before


def test_dry_run_creates_nothing(sample):
    db,_,_=sample;before=db.read_bytes()
    assert run(sample,dry_run=True)['status']=='IMPORTED'
    assert db.read_bytes()==before
    assert not Path(str(db)+'.enrichment.lock').exists()


def test_review_archives_without_attribution(sample):
    db,_,d=sample;before=row(db);d['identity']['status']='NEEDS_REVIEW'
    assert run(sample)['status']=='ARCHIVED_FOR_REVIEW'
    assert row(db)==before


def test_id_reuse_and_older_snapshot_rejected(sample):
    run(sample);db,_,d=sample;before=db.read_bytes()
    d['profile']['headline']='Different'
    with pytest.raises(ValueError,match='ID already'):run(sample)
    d['extraction_id']='older-observation'
    d['observed_at']=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    with pytest.raises(ValueError,match='stale'):run(sample)
    assert db.read_bytes()==before


def test_inaccessible_activity_preserves_old_posts(sample):
    run(sample);db,_,d=sample;posts=row(db)['recent_posts']
    d['extraction_id']='second-observation';d['observed_at']=datetime.now(timezone.utc).isoformat()
    d['activity'].update(status='BLOCKED',posts=[],limitation='Not accessible')
    run(sample)
    assert row(db)['recent_posts']==posts


def test_conflicting_url_rejected(sample):
    db,_,_=sample
    with sqlite3.connect(db) as c:c.execute("UPDATE staging_leads SET linkedin_url='https://www.linkedin.com/in/other'")
    with pytest.raises(ValueError,match='Conflicting'):run(sample)


def test_schema_snapshot_matches_model():
    assert json.loads(Path('schemas/linkedin-extraction-v1.schema.json').read_text())==Extraction.model_json_schema()


def test_pending_tasks_stop_after_review_and_export_does_not_write(sample):
    from scripts.linkedin_mcp import pending_tasks
    db,_,d=sample;before=db.read_bytes()
    assert len(pending_tasks(db))==1
    assert db.read_bytes()==before
    d['identity']['status']='NEEDS_REVIEW'
    run(sample)
    assert pending_tasks(db)==[]


def test_import_legacy_database_preserves_email_columns(tmp_path,sample):
    db,path,d=sample
    legacy=tmp_path/'legacy.sqlite'
    with sqlite3.connect(legacy) as c:
        c.execute('CREATE TABLE staging_leads(id INTEGER PRIMARY KEY,source_file TEXT,first_name TEXT,last_name TEXT,company_name TEXT,linkedin_url TEXT,raw_email TEXT,neverbounce_status TEXT)')
        c.execute("INSERT INTO staging_leads VALUES(1,'BDD','Alice','Example','Example SA',NULL,'alice@example.org','VALID')")
    d['target']=export_task(legacy,1)['target'];path.write_text(json.dumps(d))
    assert import_extraction(legacy,path)['status']=='IMPORTED'
    with sqlite3.connect(legacy) as c:
        assert c.execute('SELECT raw_email,neverbounce_status FROM staging_leads').fetchone()==('alice@example.org','VALID')
        assert 'email_validation_source' not in {r[1] for r in c.execute('PRAGMA table_info(staging_leads)')}


def test_historical_chrome_result_remains_replayable(sample):
    db,_,record=sample
    record['acquisition_method']='codex_chrome'
    assert run(sample)['status']=='IMPORTED'
    assert row(db)['linkedin_data_source']=='codex_chrome'
    assert run(sample)['status']=='UNCHANGED'


def test_suppression_added_during_browser_task_refuses_import(sample):
    db,_,_=sample
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO email_suppressions(email,reason) VALUES('alice@example.org','opt-out')")
    before=db.read_bytes()
    with pytest.raises(ValueError,match='suppressed'):run(sample)
    assert db.read_bytes()==before
