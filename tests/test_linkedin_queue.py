import json
import sqlite3
from pathlib import Path
import pytest
from tests.test_linkedin_mcp import sample
from scripts.linkedin_queue import enqueue,claim,complete,run_one


@pytest.fixture(autouse=True)
def isolate_worker_lock(tmp_path, monkeypatch):
    from scripts import linkedin_queue
    monkeypatch.setattr(linkedin_queue, 'ROOT', tmp_path)


def test_enqueue_claim_replay_and_quota(sample):
    db,path,d=sample
    assert len(enqueue(db)['enqueued'])==1
    assert enqueue(db)['enqueued']==[]
    assert claim(db,daily_limit=0)['status']=='DAILY_LIMIT'
    job=claim(db,daily_limit=1)
    assert job['status']=='RUNNING'
    assert claim(db,daily_limit=3)['status']=='BUSY_OR_UNRECONCILED'
    d['extraction_id']=job['id'];path.write_text(json.dumps(d))
    assert complete(db,job['id'],path)['status']=='IMPORTED'
    assert complete(db,job['id'],path)['import']['status']=='UNCHANGED'
    assert claim(db,daily_limit=1)['status']=='DAILY_LIMIT'


def test_dry_run_no_write_or_launch(sample):
    db,path,d=sample;before=db.read_bytes()
    assert enqueue(db,dry_run=True)['would_enqueue']==[1]
    assert run_one(db,daily_limit=1,dry_run=True)['launches']==0
    assert db.read_bytes()==before


def test_result_bound_to_claim(sample):
    db,path,d=sample;enqueue(db);job=claim(db,daily_limit=1)
    path.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='reserved task'):complete(db,job['id'],path)
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT status FROM linkedin_browser_queue').fetchone()[0]=='RUNNING'
        assert c.execute('SELECT linkedin_url FROM staging_leads').fetchone()[0] is None


def test_mcp_task_rejects_legacy_acquisition_method(sample):
    db,path,d=sample
    enqueue(db)
    job=claim(db,daily_limit=1)
    assert job['task']['provider']=='linkedin_mcp'
    assert 'browser' not in job['task']
    d.update(extraction_id=job['id'],target=job['task']['target'],acquisition_method='codex_chrome')
    path.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='MCP task requires MCP'):
        complete(db,job['id'],path)


def test_failed_runner_is_not_retried(sample,tmp_path):
    db,_,_=sample;enqueue(db);calls=[]
    def broken(task,path):calls.append(task);raise RuntimeError('CODEX_TIMEOUT_REVIEW_REQUIRED')
    assert run_one(db,daily_limit=1,output_dir=tmp_path,runner=broken)['status']=='FAILED'
    assert run_one(db,daily_limit=3,output_dir=tmp_path,runner=broken)['status']=='BUSY_OR_UNRECONCILED'
    assert len(calls)==1


def test_reference_and_stale_target_never_import(sample):
    db,path,d=sample;enqueue(db)
    with sqlite3.connect(db) as c:c.execute("UPDATE staging_leads SET company_name='Changed'")
    assert claim(db,daily_limit=1)['status']=='STALE'


def test_run_complete_path(sample,tmp_path):
    db,path,d=sample;enqueue(db)
    def runner(task,path):
        path.parent.mkdir(parents=True)
        d['extraction_id']=task['extraction_id'];d['target']=task['target']
        path.write_text(json.dumps(d))
    result=run_one(db,daily_limit=1,output_dir=tmp_path,runner=runner)
    assert result['status']=='IMPORTED'
    assert run_one(db,daily_limit=3,output_dir=tmp_path,runner=runner)['status']=='EMPTY'


def test_blocked_browser_archives_and_stops_next_task(sample,tmp_path):
    db,path,d=sample;enqueue(db)
    def runner(task,path):
        path.parent.mkdir(parents=True);d['extraction_id']=task['id'] if 'id' in task else task['extraction_id']
        d['identity']['status']='BLOCKED';d['profile']=None;d['linkedin_url']=None
        d['activity'].update(status='BLOCKED',posts=[])
        path.write_text(json.dumps(d))
    assert run_one(db,daily_limit=1,output_dir=tmp_path,runner=runner)['status']=='BLOCKED'
    assert claim(db,daily_limit=3)['status']=='BUSY_OR_UNRECONCILED'


def test_codex_command_readonly_stdin_and_usage(tmp_path,monkeypatch):
    from scripts import linkedin_queue as q
    import subprocess
    # Use the real instructions/schema paths; subprocess is replaced completely.
    q.ROOT=Path(__file__).resolve().parents[1]
    seen={}
    monkeypatch.setattr(q.shutil,'which',lambda _: '/usr/bin/codex')
    class Process:
        returncode=0
        def __init__(self,args,**kwargs):
            seen.update(args=args,kwargs=kwargs)
            self.output=Path(args[args.index('-o')+1]);self.stream=kwargs['stdout']
        def communicate(self,prompt,timeout):
            seen['prompt']=prompt
            self.output.write_text('{}');self.stream.write('tokens used\n1,234\n');self.stream.flush()
    monkeypatch.setattr(subprocess,'Popen',Process)
    output=tmp_path/'run'/'result.json'
    q.codex_runner({'name':'$(do-not-execute)'},output)
    assert '--no-daemon' in seen['args']
    assert seen['args'][seen['args'].index('--sandbox')+1]=='read-only'
    assert '$(do-not-execute)' not in str(seen['args'])
    assert '$(do-not-execute)' in seen['prompt']
    assert seen['kwargs']['start_new_session'] is True
    assert 'mcp__linkedin__search_people' in seen['prompt']
    assert 'mcp__linkedin__get_person_profile' in seen['prompt']
    assert 'acquisition_method to linkedin_mcp' in seen['prompt']
    assert 'chrome:control-chrome' not in seen['prompt']
    assert json.loads(output.with_suffix('.usage.json').read_text())['tokens_reported']==1234


def test_direct_runner_and_queue_share_the_mcp_lock(tmp_path,monkeypatch):
    from scripts import linkedin_queue as q
    import fcntl
    calls=[]
    monkeypatch.setattr(q,'_codex_runner',lambda *a,**kw:calls.append(True))
    path=q.ROOT/'bdd/linkedin-mcp-worker.lock'
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as held:
        fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError,match='MCP_BUSY'):
            q.codex_runner({},tmp_path/'result.json')
    assert calls==[]
    with q.mcp_lock():
        q.codex_runner({},tmp_path/'result.json')
    assert calls==[True]
