"""Three independent compact workflows, explicitly selected and never activated here.

python -m scripts.compact_workflow --service apify|linkedin|campaigns
    --journal-path PATH --action status

Live acquisition adapters must be supplied with an account-verified contract and
cost guard. Their absence is a waiting state, never a legacy SAS fallback.
"""
import argparse
import json
import os
import sqlite3
from pathlib import Path

from scripts.compact_store import CompactStore


class AcquisitionWorkflow:
    def __init__(self, store, *, service, acquisition=None, qualification=None, crm=None,
                 linkedin_runner=None, clock=None):
        self.store, self.service = store, service
        self.acquisition, self.qualification, self.crm = acquisition, qualification, crm
        self.linkedin_runner, self.clock = linkedin_runner, clock

    def status(self):
        lanes = ('linkedin','discovery') if self.service == 'linkedin' else ('apify',)
        with self.store.connection() as c:
            return {'service':self.service,'backend':'compact',
                'authority':self.store.authority(self.service),
                'candidates':dict(c.execute('SELECT status,count(*) FROM workflow_candidates GROUP BY status')),
                'tasks':dict(c.execute('SELECT status,count(*) FROM workflow_tasks WHERE lane IN ('+','.join('?' for _ in lanes)+') GROUP BY status',lanes)),
                'waiting':[dict(r) for r in c.execute('SELECT id,status,wait_reason FROM workflow_tasks WHERE lane IN ('+','.join('?' for _ in lanes)+') AND wait_reason IS NOT NULL',lanes)],
                'acquisition_configured':self.acquisition is not None}

    def run(self, action, *, candidate_id=None, task_id=None, data_dir='bdd/sources',
            remote_id=None, **kwargs):
        if action == 'status':
            return self.status()
        if action == 'import_sources':
            from scripts.workflow_import import import_all
            return import_all(data_dir,self.store.path,backend='compact',compact_store=self.store)
        if action == 'assign_domain':
            if not candidate_id or not all(isinstance(kwargs.get(k),str) and kwargs[k].strip()
                    for k in ('cache_path','domain','company')):
                raise ValueError('EXPLICIT_REVIEWED_DOMAIN_FIELDS_REQUIRED')
            from scripts.compact_maintenance import assign_domain_compact
            return assign_domain_compact(self.store,[candidate_id],kwargs['cache_path'],
                kwargs['domain'],kwargs['company'])
        if action == 'import_proof':
            if not candidate_id or not kwargs.get('manifest_path'):
                raise ValueError('EXPLICIT_CANDIDATE_AND_MANIFEST_REQUIRED')
            from scripts.compact_maintenance import import_verification_compact
            return import_verification_compact(self.store,candidate_id,kwargs['manifest_path'])
        if action in ('qualify','enrich','prepare_daily_batch'):
            if candidate_id is None:
                return {'status':'WAITING','reason':'EXPLICIT_CANDIDATE_OR_BRIEF_REQUIRED'}
            if self.qualification is None:
                from scripts.compact_qualification import CompactQualification
                self.qualification = CompactQualification(self.store,clock=self.clock)
            return self.qualification.tick(candidate_id)
        if action == 'transfer':
            if not candidate_id:
                return {'status':'WAITING','reason':'CANDIDATE_REQUIRED'}
            if self.crm is None:
                return {'status':'WAITING','reason':'CRM_SERVICE_REQUIRED'}
            from scripts.compact_crm import transfer_compact
            return transfer_compact(self.store,candidate_id,self.crm,clock=self.clock)
        if action in ('confirm_enqueue','confirm_one'):
            if self.service != 'linkedin':
                raise ValueError('ACTION_BELONGS_TO_LINKEDIN')
            from scripts.compact_linkedin import CompactLinkedIn
            queue = CompactLinkedIn(self.store)
            if action == 'confirm_enqueue':
                return queue.enqueue(candidate_id)
            if self.linkedin_runner is None:
                return {'status':'WAITING','reason':'LINKEDIN_MCP_RUNNER_REQUIRED'}
            return queue.run_one(runner=self.linkedin_runner)
        if action == 'reconcile_qualification':
            if self.qualification is None:
                from scripts.compact_qualification import CompactQualification
                self.qualification = CompactQualification(self.store,clock=self.clock)
            return self.qualification.reconcile_remote(task_id,remote_id)
        if self.acquisition is None:
            return {'status':'WAITING','reason':self.service.upper() + '_ADAPTER_REQUIRED'}
        if action == 'acquire':
            return self.acquisition.tick(task_id) if self.service == 'apify' else self.acquisition.run_one()
        if action == 'reconcile':
            if self.service == 'apify':
                return self.acquisition.reconcile(task_id,run_id=remote_id)
            return self.acquisition.reconcile(task_id)
        if action == 'enqueue':
            return self.acquisition.enqueue(**kwargs) if self.service == 'apify' else self.acquisition.enqueue_company(**kwargs)
        raise ValueError('UNKNOWN_ACQUISITION_ACTION')


class ApifyWorkflow(AcquisitionWorkflow):
    def __init__(self, store, **kwargs):
        super().__init__(store,service='apify',**kwargs)


class LinkedInWorkflow(AcquisitionWorkflow):
    def __init__(self, store, **kwargs):
        super().__init__(store,service='linkedin',**kwargs)


class CampaignWorkflow:
    def __init__(self, store, **kwargs):
        from scripts.compact_campaigns import CompactCampaigns
        self.store = store
        self.workflow = CompactCampaigns(store.path,**kwargs)

    def run(self, action, **kwargs):
        actions = {'status':self.workflow.status, 'draft_only':lambda:self.workflow.write_one(submit_to_telegram=False),
            'send_next_candidate':self.workflow.send_tick,'review_inbox_and_sync':self.workflow.review_inbox,
            'reverify':self.workflow.reverify_archived,'retention':self.workflow.cleanup_files}
        if action == 'reconcile':
            from scripts.workflow_sender import reconcile_send
            return reconcile_send(self.store,self.workflow.mail,kwargs['task_id'])
        if action not in actions:
            raise ValueError('UNKNOWN_CAMPAIGN_ACTION')
        return actions[action]()


def preview_compact(path, *, service, action):
    """Read-only preview: no client, directory, schema, lock, state or SAS writes."""
    path = Path(path).resolve()
    result = {'dry_run':True,'backend':'compact','service':service,'action':action,
              'external_actions':0,'candidates':{},'tasks':{},'contacts':{},'drafts':{}}
    if not path.exists():
        return result
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name,table in [('candidates','workflow_candidates'),('tasks','workflow_tasks'),
                           ('contacts','workflow_contacts'),('drafts','workflow_drafts')]:
            if table in tables:
                result[name] = dict(c.execute('SELECT status,count(*) FROM ' + table + ' GROUP BY status'))
    return result


def dispatch(journal_path, *, service, action, dry_run=False, dependencies=None, **kwargs):
    if service not in ('apify','linkedin','campaigns'):
        raise ValueError('COMPACT_SERVICE_REQUIRED:apify|linkedin|campaigns')
    if not journal_path:
        raise ValueError('EXPLICIT_COMPACT_JOURNAL_PATH_REQUIRED')
    if dry_run:
        return preview_compact(journal_path,service=service,action=action)
    store = CompactStore(journal_path)
    classes = {'apify':ApifyWorkflow,'linkedin':LinkedInWorkflow,'campaigns':CampaignWorkflow}
    workflow = classes[service](store,**(dependencies or {}))
    return workflow.run(action,**kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service',required=True,choices=('apify','linkedin','campaigns'))
    parser.add_argument('--journal-path',default=os.getenv('WORKFLOW_STATE_PATH'))
    parser.add_argument('--action',required=True,choices=('status','import_sources','qualify','transfer',
        'confirm_enqueue','confirm_one','acquire','reconcile','reconcile_qualification','draft_only',
        'send_next_candidate','review_inbox_and_sync','reverify','retention','assign_domain','import_proof'))
    parser.add_argument('--candidate-id')
    parser.add_argument('--task-id')
    parser.add_argument('--remote-id')
    parser.add_argument('--data-dir',default='bdd/sources')
    parser.add_argument('--cache-path',default='bdd/auxiliary/domain-review.sqlite')
    parser.add_argument('--domain')
    parser.add_argument('--company')
    parser.add_argument('--manifest-path')
    parser.add_argument('--dry-run',action='store_true')
    args = parser.parse_args(argv)
    try:
        result = dispatch(args.journal_path,service=args.service,action=args.action,dry_run=args.dry_run,
            candidate_id=args.candidate_id,task_id=args.task_id,remote_id=args.remote_id,data_dir=args.data_dir,
            cache_path=args.cache_path,domain=args.domain,company=args.company,manifest_path=args.manifest_path)
    except (ValueError,RuntimeError) as exc:
        result = {'status':'WAITING','reason':str(exc),'backend':'compact','service':args.service}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
