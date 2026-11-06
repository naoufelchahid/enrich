"""Campaign approvals, reviewed replies and inbox processing on the shared journal."""
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.circuit_breaker import CircuitBreaker
from scripts.crm_transfer import transfer_contact, atomic_archive, crm_admissible
from scripts.direct_enrichment import DirectEnrichment, admissible
from scripts.etl_normalize import is_dirigeant_title
from scripts.kerix_client import KerixClient, enrich_company
from scripts.linkedin_queue import enqueue, run_one, codex_runner
from scripts.outreach_journal import journal_path, flush_journal
from scripts.prompt_engine import draft_workflow_email, select_workflow_hook
from scripts.staging_db import init_staging_db
from scripts.structured_agent import run_agent
from scripts.telegram_approval import TelegramClient, create_draft, submit_draft, poll_updates
from scripts.thunderbird_client import ThunderbirdClient
from scripts.twenty_client import TwentyClient
from scripts.workflow_import import import_all
from scripts.workflow_sender import send_approved
from scripts.workflow_store import WorkflowStore, encode, now, fingerprint
from scripts.email_hygiene import is_personal_email
from scripts.email_patterns import formal_salutation

ZONE=ZoneInfo('Africa/Casablanca')
ROOT=Path(__file__).resolve().parents[1]


def readiness(*, probe=False, task=None):
    telegram=TelegramClient()
    status={'telegram':telegram.configured,'twenty':bool(os.getenv('TWENTY_API_KEY')),
            'fullenrich':bool(os.getenv('FULLENRICH_API_KEY')),'no2bounce':bool(os.getenv('NO2BOUNCE_API_KEY')),
            'codex':bool(shutil.which('codex')),
            'linkedin_mcp':bool(shutil.which('codex')),
            'thunderbird':Path(os.getenv('THUNDERBIRD_CONNECTION_FILE',
                '/home/naoufel/Downloads/thunderbird.tmp/thunderbird-mcp/connection.json')).is_file()}
    required={
        'prepare_daily_batch':{'twenty','fullenrich','no2bounce','codex','linkedin_mcp'},
        'enrich':{'twenty','fullenrich','no2bounce','codex','linkedin_mcp'},
        'send_next_candidate':{'telegram','thunderbird','codex'},
        'review_inbox_and_sync':{'telegram','twenty','thunderbird','codex'},
        'draft_only':{'codex'},
    }.get(task)
    if required is not None:
        status={key:value for key,value in status.items() if key in required}
    if probe:
        if status.get('linkedin_mcp'):
            try:
                result=subprocess.run(['codex','mcp','get','linkedin'],capture_output=True,text=True,timeout=10)
                status['linkedin_mcp']=result.returncode==0 and 'enabled: true' in result.stdout
            except (OSError,subprocess.TimeoutExpired):
                status['linkedin_mcp']=False
        if status.get('twenty'):
            try:
                TwentyClient(base_url=os.getenv('TWENTY_BASE_URL','http://localhost:3005')).inspect_workflow_schema()
            except RuntimeError:
                status['twenty']=False
        if status.get('telegram'):
            try:
                telegram.call('getMe',{})
                telegram.call('getChat',{'chat_id':telegram.chat_id})
            except RuntimeError:
                status['telegram']=False
        if status.get('thunderbird'):
            try:
                mail=ThunderbirdClient(connection_file=os.getenv('THUNDERBIRD_CONNECTION_FILE',
                    '/home/naoufel/Downloads/thunderbird.tmp/thunderbird-mcp/connection.json'))
                mail.workflow_folders('inbox')
                status['thunderbird_exact_reply']=mail.supports_exact_reply()
            except RuntimeError:
                status['thunderbird']=False
                status['thunderbird_exact_reply']=False
    return {'ready':all(status.values()),'components':status,
            'missing':[key for key,value in status.items() if not value]}


class Workflow:
    def __init__(self,db_path,*,state_path=None,crm=None,mail=None,telegram=None,breaker=None,
                 enrichment=None,linkedin_runner=codex_runner,agent_runner=run_agent,root=None,clock=None,
                 backend='legacy',qualification=None,cleanup=None):
        self.db_path=str(Path(db_path).resolve())
        self.root=Path(root or Path(self.db_path).parent)
        self.reports_root=(ROOT/'reports' if self.root.resolve()==(ROOT/'bdd/workflow').resolve()
                           else self.root/'reports')
        if backend not in ('legacy','compact'):
            raise ValueError('INVALID_WORKFLOW_BACKEND')
        self.backend=backend
        if backend=='compact':
            from scripts.compact_store import CompactStore
            self.store=CompactStore(state_path or self.db_path)
        else:
            self.store=WorkflowStore(state_path or journal_path(self.db_path))
        self._crm,self._mail,self._telegram,self._enrichment=crm,mail,telegram,enrichment
        self._qualification,self._cleanup=qualification,cleanup
        if self._crm is not None and hasattr(self._crm,'workflow_store'):
            self._crm.workflow_store=self.store
        self.breaker=breaker or CircuitBreaker(str(ROOT/'.circuit_breaker_state.json'))
        self.linkedin_runner=linkedin_runner
        self.agent_runner=agent_runner
        self.clock=clock or (lambda:datetime.now(timezone.utc))

    @property
    def crm(self):
        if self._crm is None:
            self._crm=TwentyClient(base_url=os.getenv('TWENTY_BASE_URL','http://localhost:3005'))
            if hasattr(self._crm,'workflow_store'):
                self._crm.workflow_store=self.store
        return self._crm

    @crm.setter
    def crm(self,value):
        self._crm=value

    @property
    def mail(self):
        if self._mail is None:
            self._mail=ThunderbirdClient(connection_file=os.getenv('THUNDERBIRD_CONNECTION_FILE',
                '/home/naoufel/Downloads/thunderbird.tmp/thunderbird-mcp/connection.json'))
        return self._mail

    @mail.setter
    def mail(self,value):
        self._mail=value

    @property
    def telegram(self):
        if self._telegram is None:
            self._telegram=TelegramClient()
        return self._telegram

    @telegram.setter
    def telegram(self,value):
        self._telegram=value

    @property
    def enrichment(self):
        if self._enrichment is None:
            self._enrichment=DirectEnrichment(workflow=True)
        return self._enrichment

    @enrichment.setter
    def enrichment(self,value):
        self._enrichment=value

    def cleanup_files(self):
        if self._cleanup is not None:
            return self._cleanup(self)
        if self.backend=='compact':
            from scripts.workflow_files import cleanup_completed
            return cleanup_completed(self.store,None,archives_dir=self.root/'archives',
                                     reports_dir=self.reports_root)
        from scripts.workflow_files import cleanup_completed
        from scripts.backend_guard import require_legacy
        require_legacy(self.db_path,journal_path=self.store.path)
        return cleanup_completed(self.store,self.db_path,archives_dir=self.root/'archives',
                                 reports_dir=self.reports_root)

    def import_sources(self,data_dir):
        if self.backend=='compact':
            return import_all(data_dir,self.store.path,backend='compact',compact_store=self.store)
        from scripts.backend_guard import require_legacy
        require_legacy(self.db_path,lane='qualification',journal_path=self.store.path)
        with self.store.lock('import'):
            return import_all(data_dir,self.db_path,archive_path=self.store.path)

    def reserve_count(self):
        with self.store.connection() as c:
            contacts=[dict(r) for r in c.execute("SELECT * FROM workflow_contacts WHERE status='READY_TO_MAIL' AND initial_sent_at IS NULL")]
        if not contacts:
            return 0
        people={r['id']:r for r in self.crm.workflow_list('people')}
        return sum(1 for contact in contacts if contact['person_id'] in people
                   and people[contact['person_id']].get('campaignStatus')=='READY_TO_MAIL')

    def prepare(self):
        return {'status':'COMPACT_ACQUISITION_SERVICE_REQUIRED'}

    def reverify_archived(self):
        if self.backend=='compact':
            from scripts.compact_campaigns import reverify_compact
            return reverify_compact(self)
        from scripts.backend_guard import require_legacy
        require_legacy(self.db_path,lane='qualification',journal_path=self.store.path)
        with self.store.connection() as c:
            contacts=[dict(r) for r in c.execute("SELECT * FROM workflow_contacts WHERE wait_reason='REVERIFY_EMAIL_REQUIRED' AND status IN ('READY_TO_MAIL','SENT','REPLIED')")]
        if not contacts:
            return {'status':'NO_REVERIFICATION'}
        path=str(self.root/'reverification.sqlite')
        init_staging_db(path).close()
        with sqlite3.connect(path) as c:
            fields={r[1] for r in c.execute('PRAGMA table_info(staging_leads)')}
            for contact in contacts:
                lead=json.loads(contact['snapshot'])
                if self.store.excluded(contact['email']):
                    continue
                if c.execute('SELECT 1 FROM staging_leads WHERE journal_contact_id=?',(contact['id'],)).fetchone():
                    continue
                data={k:v for k,v in lead.items() if k in fields and k!='id'}
                data.update(raw_email=contact['email'],clean_email=None,email_validation_source=None,
                            validated_email=None,email_validated_at=None,email_check_status='PENDING',
                            neverbounce_status='PENDING',outreach_status='PENDING')
                c.execute('INSERT INTO staging_leads ('+','.join(data)+') VALUES ('+','.join('?' for _ in data)+') ON CONFLICT(journal_contact_id) DO NOTHING',tuple(data.values()))
        verifier=DirectEnrichment(workflow=True,phase='verify',no2bounce=self.enrichment.no2bounce)
        verifier.audit_staging_db(path,min(100,len(contacts)))
        with sqlite3.connect(path) as c:
            c.row_factory=sqlite3.Row
            results=[dict(r) for r in c.execute('SELECT * FROM staging_leads')]
        for lead in results:
            contact=self.store.contact(lead['journal_contact_id'])
            if not contact or not contact['wait_reason']:
                continue
            if admissible(lead,at=self.clock()):
                snapshot=json.loads(contact['snapshot'])
                for key in ('email_check_status','email_validation_source','email_validated_at','validated_email',
                            'clean_email','verification_verdict','email_checked_input','neverbounce_status'):
                    snapshot[key]=lead.get(key)
                with self.store.connection() as c:
                    c.execute('UPDATE workflow_contacts SET snapshot=?,wait_reason=NULL WHERE id=?',(encode(snapshot),contact['id']))
            elif lead['email_check_status'] in ('INVALID','WAITING','FAILED','UNKNOWN','NEEDS_REVIEW'):
                with self.store.connection() as c:
                    c.execute("UPDATE workflow_contacts SET status='WAITING_EMAIL',wait_reason=? WHERE id=?",(lead['email_check_status'],contact['id']))
        return {'status':'REVERIFICATION_PROCESSED','contacts':len(contacts)}

    def write_one(self, *, submit_to_telegram=True, contact_ids=None):
        if self.backend=='legacy':
            from scripts.backend_guard import require_legacy
            require_legacy(self.db_path,lane='campaigns',journal_path=self.store.path)
        if self.backend=='compact' and self.store.authority('campaigns')!='compact':
            raise RuntimeError('COMPACT_AUTHORITY_REQUIRED:campaigns')
        with self.store.lock('writing'):
            with self.store.connection() as c:
                rewrites=[dict(r) for r in c.execute("SELECT * FROM workflow_drafts WHERE status='REWRITE' AND kind='REPLY' ORDER BY created_at")]
                replies=[dict(r) for r in c.execute("SELECT * FROM workflow_operations WHERE kind='REPLY_NEEDED' AND status='PENDING' ORDER BY created_at")]
                contacts=[dict(r) for r in c.execute("""SELECT * FROM workflow_contacts p WHERE status='READY_TO_MAIL' AND initial_sent_at IS NULL
                    AND NOT EXISTS (SELECT 1 FROM workflow_drafts d WHERE d.contact_id=p.id AND d.kind='INITIAL' AND d.status!='SUPERSEDED') ORDER BY admitted_at,id""")]
            with self.store.connection() as c:
                pilot_contacts=({r[0] for r in c.execute('SELECT contact_id FROM workflow_pilot_assignments')}
                    if c.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_pilot_assignments'").fetchone() else set())
            contacts=[r for r in contacts if r['id'] not in pilot_contacts]
            rewrites=[r for r in rewrites if r['kind']!='INITIAL' or r['contact_id'] not in pilot_contacts]
            if contact_ids is not None:
                rewrites=[r for r in rewrites if r['contact_id'] in contact_ids]
                replies=[r for r in replies if r['contact_id'] in contact_ids]
                contacts=[r for r in contacts if r['id'] in contact_ids]
            if rewrites:
                old=rewrites[0]
                contact=self.store.contact(old['contact_id'])
                kind=old['kind']
                directive=old['rewrite_instruction'] or 'Réécrire avec une formulation différente, en conservant uniquement les faits sourcés.'
                reply_message_id=old['reply_message_id']
                reply=None
                if kind=='REPLY' and reply_message_id:
                    reference=json.loads(reply_message_id)
                    with self.store.connection() as c:
                        original=c.execute("SELECT payload FROM workflow_messages WHERE json_extract(payload,'$.transport_message_id')=? AND json_extract(payload,'$.folder_path')=?",(reference.get('id'),reference.get('folder_path'))).fetchone()
                    reply=json.loads(original[0]) if original else {'subject':reference.get('subject')}
                reply_op=None
            elif replies:
                reply_op=replies[0]
                payload=json.loads(reply_op['payload'])
                contact=self.store.contact(reply_op['contact_id'])
                kind,directive,reply='REPLY',None,payload['message']
                reply_message_id=encode({'id':reply.get('transport_message_id'),'folder_path':reply.get('folder_path'),
                                        'subject':reply.get('subject')})
            else:
                return {'status':'PILOT_PREPARATION_REQUIRED' if contacts else 'NO_DRAFT_NEEDED'}
            if contact and (not contact['email'] or is_personal_email(contact['email'])):
                return {'status':'BUSINESS_EMAIL_REQUIRED'}
            if not contact or self.store.excluded(contact['email']):
                return {'status':'CONTACT_SUPPRESSED'}
            lead=json.loads(contact['snapshot'])
            try:
                formal_salutation(last_name=lead.get('last_name'),civility=lead.get('civility'),
                                  job_title=lead.get('job_title'))
            except ValueError as exc:
                self.store.event('salutation_review_required',contact['id'],{'reason':str(exc)},
                                 key='salutation-review:'+contact['id']+':'+kind)
                return {'status':'SALUTATION_REVIEW_REQUIRED'}
            operation=self.store.operation(contact['id'],'WRITING_WORKER',{'kind':kind,'lead':lead,'directive':directive,'reply':reply,
                                        'previous_draft':rewrites[0]['id'] if rewrites else None})
            if operation['status']=='SUCCEEDED':
                copy=json.loads(operation['result'])
            elif operation['status'] in ('SENDING','UNKNOWN','NEEDS_REVIEW'):
                return {'status':'WRITING_RECONCILIATION_REQUIRED'}
            else:
                self.store.finish_operation(operation['id'],'SENDING')
                try:
                    output_dir=self.reports_root/('writing-'+operation['id'])
                    atomic_archive(output_dir/'run.json',{'contacts':[{'journal_contact_id':contact['id']}]})
                    copy=draft_workflow_email(lead,hook=select_workflow_hook(lead,at=self.clock()),directive=directive,
                                             reply=reply,runner=self.agent_runner,output_dir=output_dir)
                except Exception:
                    self.store.finish_operation(operation['id'],'UNKNOWN')
                    return {'status':'WRITING_RECONCILIATION_REQUIRED'}
                self.store.finish_operation(operation['id'],'SUCCEEDED',copy)
            if kind=='REPLY':
                original_subject=(reply or {}).get('subject')
                if original_subject:
                    copy['subject']=original_subject if original_subject.lower().startswith('re:') else 'Re: '+original_subject
            if rewrites:
                prior_hook=json.loads(old['hook'])
                for key in ('operator_summary',):
                    if key in prior_hook:
                        copy['hook'][key]=prior_hook[key]
            draft=create_draft(self.store,contact['id'],contact['email'],copy['subject'],copy['body'],copy['hook'],
                               kind=kind,reply_message_id=reply_message_id,request_key=operation['id'])
            if reply_op:
                self.store.finish_operation(reply_op['id'],'SUCCEEDED',{'draft_id':draft['id']})
            try:
                self.cleanup_files()
            except (OSError,RuntimeError,sqlite3.Error):
                self.store.event('cleanup_waiting',contact['id'],{'worker':'writing'})
            if self.store.telegram_submission_paused():
                return {'status':'DRAFT_CREATED','draft_id':draft['id'],
                        'submission':{'status':'TELEGRAM_SUBMISSION_PAUSED'},'sends':0}
            if not submit_to_telegram or os.getenv('WORKFLOW_STOP_AT_DRAFT') == '1':
                return {'status':'DRAFT_CREATED','draft_id':draft['id'],
                        'submission':{'status':'STOPPED_AT_DRAFT'},'sends':0}
            return {'status':'DRAFT_CREATED','submission':submit_draft(self.store,self.telegram,draft['id'])}

    def send_tick(self):
        if getattr(self,'backend',None)=='legacy':
            from scripts.backend_guard import require_legacy
            require_legacy(self.db_path,lane='campaigns',journal_path=self.store.path)
        controller=getattr(self,'telegram_controller',None)
        if controller is None and getattr(self,'backend',None)=='compact':
            from scripts.compact_briefs import BriefBudget
            from scripts.compact_telegram import CompactTelegramController
            controller=CompactTelegramController(self.store,BriefBudget(self.store),self.telegram)
            self.telegram_controller=controller
        try:
            updates=poll_updates(self.store,self.telegram,breaker=self.breaker,controller=controller)
        except RuntimeError as error:
            if str(error)!='WORKFLOW_BUSY':
                raise
            return {'send':{'status':'TELEGRAM_POLL_BUSY','sends':0},'telegram_updates':0}
        if os.getenv('WORKFLOW_STOP_AT_DRAFT') == '1':
            return {'send':{'status':'STOPPED_AT_DRAFT','sends':0},
                    'writing':self.write_one(submit_to_telegram=False),'telegram_updates':len(updates)}
        self.reverify_archived()
        # Try the slot before starting a slow writer; approval obtained during
        # this minute only becomes eligible at a later slot.
        sent=send_approved(self.store,self.mail,self.breaker,self.telegram.operator_id,at=self.clock(),clock=self.clock)
        written=self.write_one()
        return {'send':sent,'writing':written,'telegram_updates':len(updates)}

    def review_inbox(self):
        if self.backend=='legacy':
            from scripts.backend_guard import require_legacy
            require_legacy(self.db_path,lane='campaigns',journal_path=self.store.path)
        from scripts.workflow_inbound import scan_inbox
        result=scan_inbox(self.store,self.mail,self.crm,self.breaker,self.telegram,
                          runner=self.agent_runner,output_dir=self.reports_root/'inbound')
        with self.store.connection() as c:
            operations=c.execute("SELECT id,payload FROM workflow_operations WHERE kind='INBOUND_WORKER' AND status='SUCCEEDED'").fetchall()
            for operation in operations:
                directory=self.reports_root/'inbound'/operation['id']
                if not directory.exists():
                    continue
                message_id=json.loads(operation['payload'])['message_id']
                contact=c.execute("SELECT contact_id FROM workflow_messages WHERE id=? AND state='PROCESSED'",(message_id,)).fetchone()
                if contact and contact[0]:
                    atomic_archive(directory/'run.json',{'contacts':[{'journal_contact_id':contact[0]}]})
        try:
            self.cleanup_files()
        except (OSError,RuntimeError,sqlite3.Error):
            self.store.event('cleanup_waiting',None,{'worker':'inbound'})
        return result

    def status(self):
        provider_jobs,provider_usage,staging_count,staging_waiting=[],[],0,{}
        if self.backend=='compact':
            with self.store.connection() as source:
                staging_count=source.execute('SELECT count(*) FROM workflow_candidates').fetchone()[0]
                staging_waiting=dict(source.execute("SELECT coalesce(wait_reason,'QUALIFICATION_PENDING'),count(*) FROM workflow_candidates GROUP BY 1"))
                provider_jobs=[dict(r) for r in source.execute("SELECT id,kind AS provider,status,wait_reason AS error FROM workflow_tasks WHERE status IN ('UNKNOWN','NEEDS_REVIEW','SUBMITTED','WAITING')")]
        elif Path(self.db_path).is_file():
            with sqlite3.connect(Path(self.db_path).as_uri()+'?mode=ro',uri=True) as source:
                source.row_factory=sqlite3.Row
                staging_count=source.execute('SELECT count(*) FROM staging_leads').fetchone()[0]
                staging_waiting=dict(source.execute("SELECT coalesce(workflow_wait_reason,'QUALIFICATION_PENDING'),count(*) FROM staging_leads GROUP BY 1"))
                if source.execute("SELECT 1 FROM sqlite_master WHERE name='enrichment_jobs'").fetchone():
                    provider_jobs=[dict(r) for r in source.execute("SELECT id,provider,status,error FROM enrichment_jobs WHERE status IN ('UNKNOWN','FAILED','NEEDS_REVIEW','RETRY_SCHEDULED')")]
                    provider_usage=[dict(r) for r in source.execute('SELECT provider,count(*) AS jobs,sum(reserved) AS units_committed,sum(cost) AS units_confirmed FROM enrichment_jobs GROUP BY provider')]
        from scripts.mailing_policy import load_policy
        try:
            mailing_policy=load_policy(self.store)
        except ValueError:
            mailing_policy=None
        with self.store.connection() as c:
            return {'mailing_policy':mailing_policy,
                    'send_wait_reason':'STOPPED_AT_DRAFT' if os.getenv('WORKFLOW_STOP_AT_DRAFT')=='1' else
                        'MAILING_POLICY_REQUIRED' if mailing_policy is None else None,
                    'staging_contacts':staging_count,'staging_waiting':staging_waiting,
                    'contacts':dict(c.execute('SELECT status,count(*) FROM workflow_contacts GROUP BY status')),
                    'drafts':dict(c.execute('SELECT status,count(*) FROM workflow_drafts GROUP BY status')),
                    'sends':dict(c.execute('SELECT state,count(*) FROM workflow_sends GROUP BY state')),
                    'operations_requiring_review':[dict(r) for r in c.execute("SELECT id,kind,status FROM workflow_operations WHERE status IN ('UNKNOWN','SENDING','NEEDS_REVIEW','WAITING')")],
                    'provider_jobs_requiring_review':provider_jobs,'provider_usage':provider_usage,
                    'breaker_active':self.breaker.is_tripped()}


def preview(db_path,state_path,task,at):
    count=0
    if Path(db_path).exists():
        with sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True) as c:
            count=c.execute('SELECT count(*) FROM staging_leads').fetchone()[0]
    return {'dry_run':True,'task':task,'staging_contacts':count,'mailing_policy':'EXPLICIT_CONFIGURATION_REQUIRED','configuration':readiness(),'external_actions':0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task',required=True,choices=['import_sources','prepare_daily_batch','send_next_candidate',
        'review_inbox_and_sync','status','readiness','retention','enrich','reconcile','draft_only'])
    parser.add_argument('--db-path',default=os.getenv('WORKFLOW_STAGING_PATH','bdd/workflow/staging.sqlite'))
    parser.add_argument('--state-path',default=os.getenv('WORKFLOW_STATE_PATH'))
    parser.add_argument('--backend',choices=['legacy','compact'],default=os.getenv('WORKFLOW_BACKEND','legacy'))
    parser.add_argument('--data-dir',default='bdd/sources')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--probe',action='store_true')
    parser.add_argument('--send-id')
    parser.add_argument('--local-job-id')
    parser.add_argument('--remote-job-id')
    parser.add_argument('--provider',choices=['fullenrich','no2bounce'])
    parser.add_argument('--service',choices=['apify','linkedin','campaigns'])
    parser.add_argument('--candidate-id')
    parser.add_argument('--task-id')
    args=parser.parse_args()
    if args.backend=='compact':
        from scripts.compact_workflow import dispatch, preview_compact
        service=args.service or ('apify' if args.task=='import_sources' else
            'campaigns' if args.task not in ('prepare_daily_batch','enrich') else None)
        action=args.task
        if args.task=='reconcile' and args.provider:
            action='reconcile_qualification'
            service=args.service or 'linkedin'
        try:
            if not args.state_path:
                raise ValueError('EXPLICIT_COMPACT_JOURNAL_PATH_REQUIRED')
            if service is None:
                raise ValueError('COMPACT_SERVICE_REQUIRED:apify|linkedin')
            if args.task=='readiness':
                result={'backend':'compact','service':service,'ready':False,
                    'scope':'local_journal_only','live_adapters':'injected_by_service',
                    'probe_performed':False,'missing':['SERVICE_ADAPTER_PROBES_REQUIRED']}
            else:
                result=dispatch(args.state_path,service=service,action=action,dry_run=args.dry_run,
                    candidate_id=args.candidate_id,task_id=args.send_id or args.task_id or args.local_job_id,
                    remote_id=args.remote_job_id,data_dir=args.data_dir)
        except (ValueError,RuntimeError) as error:
            result={'status':'WAITING','backend':'compact','service':service,'reason':str(error)}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return
    if args.task=='readiness':
        result=readiness(probe=args.probe and not args.dry_run)
    elif args.dry_run and args.task=='import_sources':
        result=import_all(args.data_dir,args.db_path,archive_path=args.state_path,dry_run=True)
    elif args.dry_run:
        result=preview(args.db_path,args.state_path,args.task,datetime.now(timezone.utc))
    elif args.task in ('prepare_daily_batch','send_next_candidate','review_inbox_and_sync','enrich') and not readiness(task=args.task)['ready']:
        result={'status':'CONFIGURATION_REQUIRED',**readiness(task=args.task)}
    else:
        workflow=Workflow(args.db_path,state_path=args.state_path,backend=args.backend)
        if args.task=='import_sources':
            result=workflow.import_sources(args.data_dir)
        elif args.task in ('prepare_daily_batch','enrich'):
            result=workflow.prepare()
        elif args.task=='send_next_candidate':
            result=workflow.send_tick()
            if os.getenv('TELEGRAM_AGENT_HOOK_ENABLED') == '1':
                from scripts.telegram_agent_hook import dispatch_pending
                wake=dispatch_pending(workflow.store,
                    os.getenv('TELEGRAM_AGENT_THREAD_ID'))
                result['agent_wakeup']=wake
                if wake.get('alert'):
                    try:
                        workflow.telegram.notify('Le réveil de l’agent après Telegram nécessite une vérification. '
                            'Le message a été conservé dans le journal ; aucun second réveil automatique '
                            'n’est tenté pour éviter un doublon.')
                    except RuntimeError:
                        pass
        elif args.task=='draft_only':
            result=workflow.write_one(submit_to_telegram=False)
        elif args.task=='review_inbox_and_sync':
            result=workflow.review_inbox()
        elif args.task=='retention':
            result=workflow.cleanup_files()
        elif args.task=='reconcile':
            if args.send_id:
                from scripts.workflow_sender import reconcile_send
                result=reconcile_send(workflow.store,workflow.mail,args.send_id)
            elif args.provider and args.local_job_id and args.remote_job_id:
                method=workflow.enrichment.attach_fullenrich_job if args.provider=='fullenrich' else workflow.enrichment.attach_no2bounce_job
                result=method(workflow.db_path,args.local_job_id,args.remote_job_id)
            else:
                parser.error('Provide --send-id or --provider with both job IDs')
        else:
            result=workflow.status()
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
