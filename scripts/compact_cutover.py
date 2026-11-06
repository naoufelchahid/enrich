"""Offline cutover rehearsal and conservative rollback assessment.

There is deliberately no production apply, deletion, cron or environment action.
The rehearsal creates synthetic data inside one temporary directory, restores a
fresh paired backup and exercises compact resumes with fake providers only.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts.compact_store import CompactStore
from scripts.compact_transition import stage_legacy
from scripts.compact_qualification import CompactQualification
from scripts.compact_workflow import ApifyWorkflow, LinkedInWorkflow, CampaignWorkflow, preview_compact
from scripts.direct_enrichment import DirectEnrichment
from scripts.outreach_journal import flush_journal, journal_path
from scripts.telegram_approval import create_draft
from scripts.workflow_files import save_file, read_file
from scripts.workflow_import import import_all
from scripts.workflow_migration import backup_pair, restore_pair, logical_manifest, readonly, audit_pair
from scripts.workflow_store import WorkflowStore, encode


PROTECTED_TABLES = ('journal_events','journal_exclusions','workflow_contacts','workflow_drafts',
    'workflow_operations','workflow_sends','workflow_messages','workflow_artifacts')


def _logical(path):
    with readonly(path) as c:
        return logical_manifest(c)


def rollback_checkpoint(journal_path):
    """Capture all logical state, including schema and blobs; no mutation."""
    manifest = _logical(journal_path)
    return {'format':1,'logical':manifest,
            'sha256':hashlib.sha256(encode(manifest).encode()).hexdigest()}


def assess_rollback(journal_path, checkpoint):
    """No override accepts a stale backup after any unexplained new writes.

This is an assessment, never a restore or overwrite operation. New confirmed or
unknown results must be reconciled and backed up again outside this helper.
"""
    if checkpoint.get('format') != 1 or hashlib.sha256(encode(checkpoint.get('logical')).encode()).hexdigest() != checkpoint.get('sha256'):
        raise ValueError('INVALID_ROLLBACK_CHECKPOINT')
    current = _logical(journal_path)
    prior = checkpoint['logical']
    changed = sorted(t for t in set(current['tables']) | set(prior['tables'])
        if current['tables'].get(t) != prior['tables'].get(t))
    schema_changed = any(current[k] != prior[k] for k in ('schema','user_version','application_id'))
    safe = not changed and not schema_changed
    return {'restore_allowed':safe,'changed_tables':changed,'schema_changed':schema_changed,
            'reason':'NO_NEW_WRITES' if safe else 'NEW_WRITES_REQUIRE_RECONCILIATION_AND_FRESH_BACKUP',
            'mutations':0}


def _bytes(directory):
    return sum(p.stat().st_size for p in directory.rglob('*') if p.is_file() and not p.is_symlink())


def _fixture(directory):
    """Only synthetic names/addresses and a fake submitted-provider id."""
    source = directory/'source.sqlite'
    DirectEnrichment(fullenrich=object(),no2bounce=object(),harvest=object())._init(str(source))
    rows = [dict(source_file='synthetic.xlsx',first_name='Alice',last_name='Tazi',company_name='Fixture A',raw_email='alice.tazi@example.org'),
            dict(source_file='synthetic.xlsx',first_name='Rachid',last_name='Fassi',company_name='Fixture B',raw_email='rachid.fassi@example.org'),
            dict(source_file='synthetic.xlsx',first_name='Hicham',last_name='Bennani',company_name='Fixture C',raw_email=None)]
    import_all(directory,str(source),contacts=rows)
    with sqlite3.connect(source) as c:
        ids = [r[0] for r in c.execute('SELECT journal_contact_id FROM staging_leads ORDER BY id')]
        for identity,provider,payload,remote,lead_id in [
            ('legacy-nb','no2bounce',{'emailList':['alice.tazi@example.org']},'nb-fixture',1),
            ('legacy-fe','fullenrich',{'name':'fixture','data':[{'linkedin_url':'https://www.linkedin.com/in/synthetic-fassi','enrich_fields':['contact.work_emails'],'custom':{'request_key':'fixture'}}]},'fe-fixture',2)]:
            c.execute('''INSERT INTO enrichment_jobs(id,provider,kind,status,payload,reserved,created_at,remote_id)
                VALUES(?,?,?,'SUBMITTED',?,1,?,?)''',(identity,provider,'email' if provider=='no2bounce' else 'contact',encode(payload),datetime.now(timezone.utc).isoformat(),remote))
            c.execute('INSERT INTO enrichment_targets VALUES(?,?,?, ?,0)',(identity,'fixture-'+identity,lead_id,'{}'))
        c.execute('CREATE TABLE linkedin_browser_queue(id TEXT PRIMARY KEY,lead_id INTEGER,status TEXT,result_path TEXT)')
        c.execute("INSERT INTO linkedin_browser_queue VALUES('blocked-linkedin',3,'BLOCKED',NULL)")
        c.execute("INSERT INTO email_suppressions VALUES('excluded@example.org','PERMANENT_BOUNCE','2026-11-05')")
    flush_journal(str(source))
    store = WorkflowStore(journal_path(str(source)))
    store.put_contact({'journal_contact_id':'approved-fixture','first_name':'Synthetic','last_name':'Contact',
        'company_name':'Fixture CRM','clean_email':'synthetic.contact@example.org'},status='READY_TO_MAIL')
    draft = create_draft(store,'approved-fixture','synthetic.contact@example.org','Fixture subject',
        'Exact approved synthetic fixture body.',{'signature_mode':'thunderbird_html'})
    with store.connection() as c:
        c.execute("UPDATE workflow_drafts SET status='APPROVED',approved_by='101',approved_at='2026-11-05' WHERE id=?",(draft['id'],))
    for index in range(2):
        op = store.operation(ids[1],'CREATE_PERSON',{'synthetic_attempt':index})
        store.finish_operation(op['id'],'UNKNOWN')
    proof = directory/'synthetic-proof.json'
    proof.write_bytes(b'{ "exact": null, "synthetic": true }\n')
    proof_sha = save_file(store,proof)
    return source,Path(store.path),ids,proof,proof_sha


class _FakeNo2Bounce:
    def __init__(self):
        self.polls = 0

    def submit(self, *args, **kwargs):
        raise AssertionError('Rehearsal must never resubmit an existing job')

    def result(self, remote_id):
        if remote_id != 'nb-fixture':
            raise AssertionError('Wrong fake provider job')
        self.polls += 1
        return {'verdicts':{'alice.tazi@example.org':'VALID'},'raw':{'synthetic':True,'trackingId':remote_id}}


class _UnavailableAcquisition:
    def run_one(self):
        return {'status':'WAITING','reason':'SYNTHETIC_LINKEDIN_UNAVAILABLE'}


class _RehearsalInterruption(RuntimeError):
    pass


def rehearse(directory):
    """Run only on newly generated fixtures, then remove the temporary databases."""
    base = Path(directory).resolve()
    root = Path(__file__).resolve().parents[1]
    forbidden = root/'bdd'
    if base == forbidden or forbidden in base.parents:
        raise ValueError('REHEARSAL_REQUIRES_SCRATCH_OR_TEMP_DIRECTORY')
    base.mkdir(parents=True,exist_ok=True)
    started = time.monotonic()
    measurements = {}
    phases = {}
    with tempfile.TemporaryDirectory(prefix='compact-cutover-',dir=base) as temporary:
        work = Path(temporary)
        phase_start = time.monotonic()
        source,journal,identities,proof,proof_sha = _fixture(work)
        original_source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        with readonly(journal) as c:
            original_protected = {t:v for t,v in logical_manifest(c)['tables'].items() if t in PROTECTED_TABLES}
        phases['create_fixture'] = round(time.monotonic()-phase_start,4)
        measurements['initial_fixture_bytes'] = _bytes(work)
        phase_start = time.monotonic()
        manifest = backup_pair(source,journal,work/'backups',writes_paused=True)
        restored = restore_pair(manifest,work/'restored')
        restored_source = Path(restored['files']['staging'])
        restored_journal = Path(restored['files']['journal'])
        restored_exactly = _logical(source)==_logical(restored_source) and _logical(journal)==_logical(restored_journal)
        if not restored_exactly:
            raise RuntimeError('REHEARSAL_RESTORE_CONSERVATION_FAILED')
        phases['fresh_backup_and_verified_restore'] = round(time.monotonic()-phase_start,4)
        measurements['after_backup_and_restore_bytes'] = _bytes(work)
        audit = audit_pair(restored_source,restored_journal)
        compact = CompactStore(restored_journal)
        for lane in ('qualification','migration','linkedin','discovery','crm','apify','campaigns'):
            compact.set_authority(lane)
        phase_start = time.monotonic()
        update = compact.update_candidate
        interrupted = False
        def interrupt_after_task_before_candidate(identity, changes, **kwargs):
            nonlocal interrupted
            if changes.get('pending_legacy_jobs') and not interrupted:
                interrupted = True
                raise _RehearsalInterruption('synthetic interruption after durable task commit')
            return update(identity,changes,**kwargs)
        compact.update_candidate = interrupt_after_task_before_candidate
        try:
            stage_legacy(compact,restored_source,rehearsal=True,batch_size=1)
        except _RehearsalInterruption:
            pass
        finally:
            compact.update_candidate = update
        if not interrupted:
            raise RuntimeError('EXPECTED_REHEARSAL_INTERRUPTION_NOT_REACHED')
        compact = CompactStore(restored_journal)
        staged = stage_legacy(compact,restored_source,rehearsal=True,batch_size=1)
        # A second execution demonstrates safe replay of the staged identities/jobs.
        replay = stage_legacy(compact,restored_source,rehearsal=True,batch_size=1)
        phases['stage_and_replay'] = round(time.monotonic()-phase_start,4)
        measurements['after_staging_bytes'] = _bytes(work)
        with readonly(restored_journal) as c:
            current = logical_manifest(c)['tables']
            preserved = all(current.get(t)==v for t,v in original_protected.items())
        if not preserved:
            raise RuntimeError('PROTECTED_JOURNAL_DATA_CHANGED')
        uri = 'journal://artifacts/' + proof_sha
        exact_proof = read_file(compact,uri)==proof.read_bytes()
        if not exact_proof or not compact.excluded('excluded@example.org'):
            raise RuntimeError('PROOF_OR_EXCLUSION_LOST')
        checkpoint = rollback_checkpoint(restored_journal)
        before_resume = assess_rollback(restored_journal,checkpoint)
        offline_source = restored_source.with_name('staging.offline.sqlite')
        restored_source.rename(offline_source)
        fake = _FakeNo2Bounce()
        qualification = CompactQualification(CompactStore(restored_journal),no2bounce=fake)
        resumed = qualification.tick(identities[0])
        blocked_provider = qualification.tick(identities[1])
        if resumed['status'] != 'QUALIFIED' or fake.polls != 1 or blocked_provider.get('reason') != 'LEGACY_PROVIDER_RECONCILIATION_REQUIRED':
            raise RuntimeError('REHEARSAL_PROVIDER_RESUME_FAILED')
        rollback_after_resume = assess_rollback(restored_journal,checkpoint)
        if rollback_after_resume['restore_allowed']:
            raise RuntimeError('STALE_ROLLBACK_WAS_NOT_BLOCKED')
        services = {
            'apify':ApifyWorkflow(compact).run('status'),
            'linkedin':LinkedInWorkflow(compact,acquisition=_UnavailableAcquisition()).run('acquire'),
            'campaigns':CampaignWorkflow(compact).run('status')}
        previews = [preview_compact(restored_journal,service=s,action='status') for s in ('apify','linkedin','campaigns')]
        source_unavailable = not restored_source.exists()
        if hashlib.sha256(source.read_bytes()).hexdigest()!=original_source_hash:
            raise RuntimeError('ORIGINAL_FIXTURE_WAS_MUTATED')
        measurements['after_resume_bytes'] = _bytes(work)
        maximum_observed = max(measurements.values())
        report = {
            'scope':'synthetic_fixture_only','production_apply_implemented':False,'production_activated':False,
            'created_at':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':round(time.monotonic()-started,4),
            'phase_seconds':phases,'measured_bytes':measurements,'maximum_observed_fixture_bytes':maximum_observed,
            'backup_fresh':True,'backup_restore_integrity_and_exact_content':restored_exactly,
            'exported_events':audit['exported'],'matched_events':audit['matched'],
            'migration_missing':staged['missing'],'migration_divergent':staged['divergent'],
            'candidate_count':staged['candidates'],'replay_candidate_count':replay['candidates'],
            'interruption_after_task_before_candidate_recovered':interrupted,
            'original_fixture_source_unchanged':True,'old_sas_unavailable_during_resume':source_unavailable,
            'protected_journal_tables_unchanged':preserved,'exact_proof_restored':exact_proof,
            'exclusion_preserved':True,'submitted_no2bounce_resumed_without_submit':True,
            'legacy_fullenrich_held_for_reconciliation':True,
            'unknown_crm_operations_preserved':services['campaigns']['operations_requiring_review'],
            'independent_services':{'linkedin_waits':services['linkedin']['status']=='WAITING',
                'apify_status_available':services['apify']['service']=='apify',
                'campaign_status_available':services['campaigns']['drafts'].get('APPROVED')==1},
            'offline_previews_external_actions':sum(p['external_actions'] for p in previews),
            'rollback_before_new_writes':before_resume,'rollback_after_new_writes':rollback_after_resume,
            'real_recovered_bytes':0,'emails_sent':0,'real_provider_calls':0,
            'fixture_cleanup_bytes':_bytes(work),'fixture_cleanup_complete':False,
            'storage_cutover_blockers':['PLANNED_WINDOW_REQUIRED','ALL_WRITERS_STOP_REQUIRED',
                'FRESH_PRODUCTION_BACKUP_AND_RESTORE_REQUIRED','LEGACY_FULLENRICH_RECONCILIATION_REQUIRED',
                'UNKNOWN_CRM_OPERATIONS_RECONCILIATION_REQUIRED','FINAL_CONSERVATION_AND_RESUME_CHECKS_REQUIRED'],
            'acquisition_activation_blockers':['LIVE_ADAPTER_CONFIGURATION_REQUIRED','ACQUISITION_BRIEF_BUDGETS_REQUIRED'],
            'email_activation':'EXPLICIT_OPERATOR_RESUME_AND_INDIVIDUAL_APPROVAL_REQUIRED'}
    report['fixture_cleanup_complete'] = not work.exists()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action',required=True)
    run = sub.add_parser('rehearse')
    run.add_argument('--directory',default='.scratch')
    run.add_argument('--output')
    check = sub.add_parser('assess-rollback')
    check.add_argument('--journal',required=True)
    check.add_argument('--checkpoint',required=True)
    args = parser.parse_args()
    result = rehearse(args.directory) if args.action=='rehearse' else assess_rollback(args.journal,json.loads(Path(args.checkpoint).read_text()))
    if args.action=='rehearse' and args.output:
        target = Path(args.output)
        if target.suffix!='.json':
            parser.error('--output must be a JSON report')
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('x') as handle:
            json.dump(result,handle,ensure_ascii=False,indent=2)
            handle.write('\n')
        target.chmod(0o600)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
