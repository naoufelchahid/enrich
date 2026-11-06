"""Explicit operator batches, separate from scheduled preparation windows.

All provider/CRM operations use the workflow's existing clients and journal.
Batch membership is fixed before the first call. No mail or Telegram submission.
"""
import argparse
import csv
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from scripts.etl_normalize import is_dirigeant_title
from scripts.crm_transfer import transfer_contact, crm_admissible
from scripts.linkedin_queue import enqueue, run_one, codex_runner
from scripts.kerix_client import KerixClient, enrich_company
from scripts.workflow import ROOT, Workflow, readiness
from scripts.workflow_store import encode
from scripts.workflow_files import read_file, cleanup_completed


class ManualBatch:
    def __init__(self, workflow, batch_id):
        if getattr(workflow, 'backend', 'legacy') == 'compact':
            raise RuntimeError('LEGACY_BATCH_RETIRED_USE_COMPACT_SERVICES')
        from scripts.backend_guard import require_legacy
        require_legacy(workflow.db_path,journal_path=workflow.store.path)
        if not re.fullmatch(r'manual-[A-Za-z0-9-]+', batch_id):
            raise ValueError('Invalid manual batch ID')
        self.workflow=workflow
        self.id=batch_id
        self.directory=ROOT/'reports'/batch_id

    def rows(self):
        with sqlite3.connect(self.workflow.db_path) as c:
            c.row_factory=sqlite3.Row
            return {r['id']:dict(r) for r in c.execute('SELECT * FROM staging_leads')}

    def selected(self):
        with self.workflow.store.connection() as c:
            row=c.execute('SELECT * FROM workflow_batches WHERE day=?',(self.id,)).fetchone()
        if not row:
            raise ValueError('Create batch membership first')
        return json.loads(row['candidates'])

    def create(self, size):
        if not 1<=size<=100:
            raise ValueError('Batch size must be 1..100')
        with self.workflow.store.lock('preparation'):
            with self.workflow.store.connection() as c:
                if c.execute('SELECT 1 FROM workflow_batches WHERE day=?',(self.id,)).fetchone():
                    return {'status':'ALREADY_CREATED','selected':len(self.selected())}
                already_selected=set()
                for previous in c.execute("SELECT candidates FROM workflow_batches WHERE day LIKE 'manual-%'"):
                    already_selected.update(json.loads(previous['candidates']))
            leads=[r for r in self.rows().values() if r['outreach_status']=='PENDING'
                   and r['id'] not in already_selected
                   and r.get('workflow_wait_reason') in (None,'REPLACEMENT_EMAIL_REQUIRED')
                   and r.get('contact_discovery_status') not in ('NO_MATCH','UNKNOWN','FAILED','NEEDS_REVIEW')
                   and all(str(r.get(k) or '').strip() for k in ('first_name','last_name','company_name'))]
            def rank(r):
                title=(r.get('job_title') or '').casefold()
                category=0 if r.get('is_dirigeant') or is_dirigeant_title(title) else 1 if any(
                    x in title for x in ('directeur','directrice','manager','responsable','head','director')) else 2
                return category,r['created_at'],r['id']
            leads=sorted(leads,key=rank)[:size]
            if len(leads)!=size:
                return {'status':'NOT_ENOUGH_CANDIDATES','available':len(leads)}
            self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            with self.workflow.store.connection() as c:
                c.execute("INSERT INTO workflow_batches(day,candidates,status,summary) VALUES (?,?,'RUNNING',?)",
                          (self.id,encode([r['id'] for r in leads]),encode({'mode':'MANUAL_DRAFT_ONLY','selected':size})))
                self.workflow.store.event('manual_batch_created',None,{'batch_id':self.id,'lead_ids':[r['id'] for r in leads],
                                          'stop_at_draft':True},connection=c)
            self.save('selection.json',{'batch_id':self.id,'created_at':datetime.now(timezone.utc).isoformat(),
                                       'contacts':leads,'stop_at_draft':True})
        return self.report()

    def save(self,name,data):
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=self.directory/name
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+'\n')
        path.chmod(0o600)

    def manifest(self):
        return json.loads(read_file(self.workflow.store,self.directory/'selection.json'))

    def verify(self):
        selected=self.selected()
        self.workflow.enrichment.lead_ids=set(selected)
        self.workflow.enrichment.phase='verify'
        result=self.workflow.enrichment.audit_staging_db(self.workflow.db_path,len(selected))
        self.save('verification-summary.json',result)
        queued=enqueue(self.workflow.db_path,limit=len(selected),workflow=True,lead_ids=selected)
        self.save('linkedin-queue.json',queued)
        return self.report()

    def linkedin(self):
        with self.workflow.store.connection() as c:
            c.execute("UPDATE workflow_batches SET status='RUNNING' WHERE day=?",(self.id,))
        result=run_one(self.workflow.db_path,daily_limit=None,lead_ids=set(self.selected()),
                       output_dir=self.directory/'preuves/linkedin',runner=codex_runner)
        self.workflow.store.event('manual_batch_linkedin_tick',None,{'batch_id':self.id,'result':result})
        self.report()
        return result

    def enrich(self):
        selected=self.selected()
        self.workflow.enrichment.lead_ids=set(selected)
        self.workflow.enrichment.phase='all'
        result=self.workflow.enrichment.audit_staging_db(self.workflow.db_path,len(selected))
        self.save('enrichment-summary.json',result)
        return self.report()

    def transfer(self):
        results=[]
        rows=self.rows()
        for identity in self.selected():
            row=rows.get(identity)
            if row and crm_admissible(row):
                result=transfer_contact(self.workflow.db_path,identity,self.workflow.store,self.workflow.crm,
                                        archive_dir=Path(self.workflow.root)/'archives')
                results.append({'lead_id':identity,**result})
                if result['status']=='TRANSFERRED':
                    try:enrich_company(self.workflow.store,self.workflow.crm,KerixClient(),result['contact_id'])
                    except (RuntimeError,ValueError):
                        self.workflow.store.event('kerix_waiting',result['contact_id'],{'reason':'ACCESS_OR_CONTRACT_REQUIRED'})
        self.save('crm-result.json',results)
        return self.report()

    def draft(self):
        candidates=self.manifest()['contacts']
        ids={r['journal_contact_id'] for r in candidates}
        result=self.workflow.write_one(submit_to_telegram=False,contact_ids=ids)
        self.report()
        return result

    def report(self):
        manifest=self.manifest()
        self.save('selection.json',manifest)
        rows=self.rows()
        with sqlite3.connect(self.workflow.db_path) as source:
            tables={r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            queue=({r[0]:r[1] for r in source.execute('SELECT lead_id,status FROM linkedin_browser_queue')}
                   if 'linkedin_browser_queue' in tables else {})
            proofs=({r[0]:json.loads(r[1])['identity']['status'] for r in source.execute(
                'SELECT lead_id,payload FROM linkedin_browser_imports ORDER BY imported_at,rowid')}
                    if 'linkedin_browser_imports' in tables else {})
        with self.workflow.store.connection() as c:
            contacts={r['id']:dict(r) for r in c.execute('SELECT * FROM workflow_contacts')}
            drafts={r['contact_id']:dict(r) for r in c.execute('SELECT * FROM workflow_drafts ORDER BY version')}
            sends=c.execute('SELECT count(*) FROM workflow_sends').fetchone()[0]
            batch_status=c.execute('SELECT status FROM workflow_batches WHERE day=?',(self.id,)).fetchone()[0]
        entries=[]
        for original in manifest['contacts']:
            row=rows.get(original['id'])
            contact=contacts.get(original['journal_contact_id']) or {}
            data=row or (json.loads(contact['snapshot']) if contact else original)
            draft=drafts.get(original['journal_contact_id']) or {}
            linkedin=proofs.get(original['id']) or data.get('linkedin_search_status')
            attente=data.get('workflow_wait_reason') or contact.get('wait_reason') or ''
            if not crm_admissible(data) and linkedin in ('BLOCKED','NOT_FOUND','NEEDS_REVIEW'):
                attente='LINKEDIN_'+linkedin
            elif not crm_admissible(data) and queue.get(original['id'])=='FAILED':
                attente='LINKEDIN_WORKER_FAILED'
            entries.append({'id':original['id'],'nom':original['first_name']+' '+original['last_name'],
                'entreprise':original['company_name'],'email':data.get('clean_email') or data.get('raw_email'),
                'verification':data.get('email_check_status'),'linkedin':linkedin,
                'enrichissement':data.get('contact_discovery_status'),
                'crm':contact.get('status') or 'NON_TRANSFERE','brouillon':draft.get('status') or 'AUCUN',
                'attente':attente})
        result={'batch_id':self.id,'batch_status':batch_status,'selected':len(entries),'contacts':entries,'sends_total':sends,'stop_at_draft':True}
        self.save('status.json',result)
        csv_path=self.directory/'contacts.csv'
        with csv_path.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(entries[0]),delimiter=';');writer.writeheader();writer.writerows(entries)
        csv_path.chmod(0o600)
        lines=['# Batch manuel — '+str(len(entries))+' contacts','',
               'Qualification → LinkedIn → enrichissement professionnel → Twenty → brouillon. Envoi bloqué.', '',
               'État : '+batch_status+'.', '',
               '| ID | Contact | Entreprise | Email | LinkedIn | Enrichissement | CRM | Brouillon | Attente |',
               '|---|---|---|---|---|---|---|---|---|']
        for row in entries:
            lines.append('| '+' | '.join(str(row.get(k) or '').replace('|','/').replace('\n',' ') for k in
                                         ('id','nom','entreprise','verification','linkedin','enrichissement','crm','brouillon','attente'))+' |')
        lines+=['','[Liste complète](contacts.csv) · [État structuré](status.json)',
                '',f'Envois enregistrés dans le workflow : {sends}. Aucun appel d’envoi par ce batch.']
        (self.directory/'RESULTATS.md').write_text('\n'.join(lines)+'\n')
        with self.workflow.store.connection() as c:
            c.execute('UPDATE workflow_batches SET summary=? WHERE day=?',(encode(result),self.id))
        result['file_cleanup']=cleanup_completed(self.workflow.store,self.workflow.db_path,
            archives_dir=Path(self.workflow.root)/'archives',reports_dir=ROOT/'reports')
        return result


def main():
    load_dotenv(ROOT/'.env',override=True)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-id',required=True)
    parser.add_argument('--phase',choices=['create','verify','linkedin','enrich','transfer','draft','status'],required=True)
    parser.add_argument('--size',type=int,default=20)
    args=parser.parse_args()
    if os.getenv('WORKFLOW_STOP_AT_DRAFT')!='1':
        raise SystemExit('WORKFLOW_STOP_AT_DRAFT=1 required for manual batches')
    workflow=Workflow(os.getenv('WORKFLOW_STAGING_PATH','bdd/workflow/staging.sqlite'),root=ROOT/'bdd/workflow')
    batch=ManualBatch(workflow,args.batch_id)
    if args.phase=='create':result=batch.create(args.size)
    elif args.phase=='linkedin':result=batch.linkedin()
    else:result=getattr(batch,args.phase if args.phase!='status' else 'report')()
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
