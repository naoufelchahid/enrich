import json
from unittest.mock import patch

import pytest

from scripts.compact_cutover import rehearse, rollback_checkpoint, assess_rollback
from scripts.compact_store import CompactStore


def test_offline_rehearsal_restores_interrupts_resumes_and_conserves_without_external_calls(tmp_path):
    with patch('requests.request',side_effect=AssertionError('real provider forbidden')), \
         patch('scripts.workflow.ThunderbirdClient',side_effect=AssertionError('mail forbidden')), \
         patch('scripts.workflow.TwentyClient',side_effect=AssertionError('CRM forbidden')):
        report=rehearse(tmp_path)
    assert report['scope']=='synthetic_fixture_only'
    assert report['backup_fresh'] and report['backup_restore_integrity_and_exact_content']
    assert report['interruption_after_task_before_candidate_recovered']
    assert report['migration_missing']==report['migration_divergent']==0
    assert report['candidate_count']==report['replay_candidate_count']==3
    assert report['matched_events']==report['exported_events'] and report['matched_events']>0
    assert report['protected_journal_tables_unchanged'] and report['exact_proof_restored']
    assert report['old_sas_unavailable_during_resume'] and report['original_fixture_source_unchanged']
    assert report['submitted_no2bounce_resumed_without_submit']
    assert report['legacy_fullenrich_held_for_reconciliation'] and report['exclusion_preserved']
    assert len(report['unknown_crm_operations_preserved'])==2
    assert all(o['kind']=='CREATE_PERSON' and o['status']=='UNKNOWN' for o in report['unknown_crm_operations_preserved'])
    assert all(report['independent_services'].values())
    assert report['rollback_before_new_writes']['restore_allowed']
    assert not report['rollback_after_new_writes']['restore_allowed']
    assert report['emails_sent']==report['real_provider_calls']==report['real_recovered_bytes']==0
    assert not report['production_apply_implemented'] and not report['production_activated']
    assert 'ACQUISITION_BRIEF_BUDGETS_REQUIRED' not in report['storage_cutover_blockers']
    assert 'ACQUISITION_BRIEF_BUDGETS_REQUIRED' in report['acquisition_activation_blockers']
    assert report['fixture_cleanup_complete'] and not list(tmp_path.iterdir())
    assert report['maximum_observed_fixture_bytes']>=report['measured_bytes']['initial_fixture_bytes']>0


def test_restore_failure_aborts_and_cleans_only_generated_fixtures(tmp_path):
    human=tmp_path/'human-export.txt'
    human.write_bytes(b'keep exact human export')
    with patch('scripts.compact_cutover.restore_pair',side_effect=RuntimeError('restore failed')):
        with pytest.raises(RuntimeError,match='restore failed'):
            rehearse(tmp_path)
    assert list(tmp_path.iterdir())==[human]
    assert human.read_bytes()==b'keep exact human export'


def test_rollback_assessment_is_read_only_and_refuses_new_rows_even_if_unrelated(tmp_path):
    store=CompactStore(tmp_path/'journal.sqlite')
    checkpoint=rollback_checkpoint(store.path)
    assert assess_rollback(store.path,checkpoint)['restore_allowed']
    with store.connection() as c:
        c.execute("INSERT INTO workflow_meta VALUES('new_confirmed_result','must survive')")
    result=assess_rollback(store.path,checkpoint)
    assert not result['restore_allowed'] and result['changed_tables']==['workflow_meta']
    assert result['mutations']==0
    with store.connection() as c:
        assert c.execute("SELECT value FROM workflow_meta WHERE key='new_confirmed_result'").fetchone()[0]=='must survive'
    assert assess_rollback(store.path,checkpoint)==result


def test_rollback_detects_schema_changes_and_rejects_tampered_checkpoint(tmp_path):
    store=CompactStore(tmp_path/'journal.sqlite')
    checkpoint=rollback_checkpoint(store.path)
    with store.connection() as c:
        c.execute('CREATE INDEX additional_state_index ON workflow_tasks(kind)')
    assert assess_rollback(store.path,checkpoint)['schema_changed']
    tampered=json.loads(json.dumps(checkpoint))
    tampered['logical']['application_id']=123
    with pytest.raises(ValueError,match='INVALID_ROLLBACK_CHECKPOINT'):
        assess_rollback(store.path,tampered)


def test_rollback_preserves_autoincrement_high_water_mark_after_row_removal(tmp_path):
    store=CompactStore(tmp_path/'journal.sqlite')
    # Allocate before the checkpoint to ensure sqlite_sequence already exists.
    store.event('before',None,{})
    checkpoint=rollback_checkpoint(store.path)
    with store.connection() as c:
        cursor=c.execute("INSERT INTO workflow_events(key,kind,payload,created_at) VALUES('temporary','temporary','{}','fixture')")
        c.execute('DELETE FROM workflow_events WHERE id=?',(cursor.lastrowid,))
    result=assess_rollback(store.path,checkpoint)
    assert not result['restore_allowed'] and 'sqlite_sequence' in result['changed_tables']


def test_rehearsal_cli_has_no_apply_action_and_rejects_active_storage_directory(tmp_path):
    from scripts.compact_cutover import main
    from pathlib import Path
    import sys
    root=Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError,match='SCRATCH_OR_TEMP'):
        rehearse(root/'bdd/workflow')
    with patch.object(sys,'argv',['compact_cutover','apply']):
        with pytest.raises(SystemExit) as error:
            main()
    assert error.value.code==2
