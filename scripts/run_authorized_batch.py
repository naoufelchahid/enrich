"""Continue the manual batch sequentially; never send email."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import json,sqlite3,time,os
from dotenv import load_dotenv
from scripts.workflow import Workflow
from scripts.workflow_batch import ManualBatch

load_dotenv(ROOT/'.env',override=True)
from scripts.backend_guard import require_legacy
require_legacy(ROOT/'bdd/workflow/staging.sqlite')
assert os.getenv('WORKFLOW_STOP_AT_DRAFT')=='1'
workflow=Workflow(ROOT/'bdd/workflow/staging.sqlite',root=ROOT/'bdd/workflow')
batch=ManualBatch(workflow,'manual-20261009-20-01')
selected=set(batch.selected())
for _ in range(20):
    with sqlite3.connect(workflow.db_path) as c:
        pending=[r[0] for r in c.execute("SELECT lead_id FROM linkedin_browser_queue WHERE status='PENDING'") if r[0] in selected]
    if not pending:break
    result=batch.linkedin()
    print(json.dumps({'phase':'LinkedIn','result':result,'remaining_before_tick':len(pending)},ensure_ascii=False),flush=True)
    if result['status'] in ('BUSY_OR_UNRECONCILED','MCP_BUSY','BLOCKED','FAILED'):
        with workflow.store.connection() as c:
            c.execute("UPDATE workflow_batches SET status='REVIEW_REQUIRED' WHERE day=?",(batch.id,))
        batch.report()
        raise SystemExit('LinkedIn paused for operator reconciliation; no retries.')

# Collect/submit paid work only after identity proof; poll known jobs, not POSTs.
for tick in range(60):
    batch.enrich()
    batch.verify()
    with sqlite3.connect(workflow.db_path) as c:
        jobs=c.execute("SELECT DISTINCT j.status FROM enrichment_jobs j JOIN enrichment_targets t ON t.job_id=j.id WHERE t.lead_id IN ("+','.join('?' for _ in selected)+')',list(selected)).fetchall()
    active=any(r[0] in ('SUBMITTED','RECEIVED','RETRY_SCHEDULED') for r in jobs)
    print(json.dumps({'phase':'Providers','tick':tick+1,'active_jobs':active}),flush=True)
    if not active:break
    time.sleep(10)

batch.transfer()
for contact in batch.manifest()['contacts']:
    result=workflow.write_one(submit_to_telegram=False,contact_ids={contact['journal_contact_id']})
    batch.report()
    print(json.dumps({'phase':'Draft','result':result}),flush=True)

report=batch.report()
completed=sum(r['brouillon']!='AUCUN' for r in report['contacts'])
with workflow.store.connection() as c:
    c.execute('UPDATE workflow_batches SET status=? WHERE day=?',
              ('COMPLETE' if completed==len(selected) else 'REVIEW_REQUIRED',batch.id))
report=batch.report()
print(json.dumps({'phase':'Finished','drafts':completed,'selected':len(selected),'sends':report['sends_total'],'report':str(batch.directory/'RESULTATS.md')},ensure_ascii=False),flush=True)
