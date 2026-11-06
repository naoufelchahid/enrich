import subprocess
from types import SimpleNamespace

from scripts.telegram_agent_hook import dispatch_pending, wake_prompt
from scripts.workflow import Workflow
from scripts.workflow_store import WorkflowStore


THREAD = '01a1a6ca-1cf1-7543-a580-78f298e5f456'


def store_with_cursor(tmp_path):
    store = WorkflowStore(tmp_path / 'journal.sqlite')
    with store.connection() as c:
        c.execute("INSERT INTO workflow_meta(key,value) VALUES('telegram_agent_cursor','0')")
    return store


def test_approval_wakes_thread_once(tmp_path):
    store = store_with_cursor(tmp_path)
    store.event('telegram_approve', 'contact-1', {'draft_id': 'draft-1'}, key='telegram-update:100')
    calls = []

    def queued(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, 'queued and resumed', '')

    result = dispatch_pending(store, THREAD, executable='/usr/bin/codex', run_command=queued)
    assert result['status'] == 'AGENT_WAKE_QUEUED'
    assert calls[0][1:4] == ['queue', '--thread', THREAD]
    assert '--resume' in calls[0]
    assert 'workflow_events.id = 1' in calls[0][calls[0].index('--message') + 1]
    assert dispatch_pending(store, THREAD, executable='/usr/bin/codex', run_command=queued)['status'] == 'NO_TELEGRAM_EVENTS'
    assert len(calls) == 1


def test_operator_text_wakes_but_unauthorized_update_does_not(tmp_path):
    store = store_with_cursor(tmp_path)
    store.event('telegram_rejected', None, {'reason': 'UNAUTHORIZED'}, key='telegram-update:100')
    store.event('telegram_rejected', None, {'reason': 'UNKNOWN_DIRECTIVE', 'text': 'Continue'},
                key='telegram-update:101')
    calls = []

    def queued(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, 'queued and resumed', '')

    result = dispatch_pending(store, THREAD, executable='/usr/bin/codex', run_command=queued)
    assert result['event_ids'] == [2]
    assert len(calls) == 1
    assert len(wake_prompt([2]).encode()) < 1000


def test_uncertain_queue_result_never_requeues_automatically(tmp_path):
    store = store_with_cursor(tmp_path)
    store.event('telegram_approve', 'contact-1', {'draft_id': 'draft-1'}, key='telegram-update:100')
    calls = []

    def failed(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, '', 'queue connection lost')

    assert dispatch_pending(store, THREAD, executable='/usr/bin/codex', run_command=failed)['status'] == 'AGENT_WAKE_RECONCILIATION_REQUIRED'
    assert dispatch_pending(store, THREAD, executable='/usr/bin/codex', run_command=failed)['status'] == 'AGENT_WAKE_RECONCILIATION_REQUIRED'
    assert len(calls) == 1
    with store.connection() as c:
        operation = c.execute("SELECT status FROM workflow_operations WHERE kind='TELEGRAM_AGENT_WAKE'").fetchone()
        assert operation[0] == 'UNKNOWN'
        assert c.execute("SELECT value FROM workflow_meta WHERE key='telegram_agent_cursor'").fetchone()[0] == '0'


def test_overlapping_telegram_poll_waits_for_next_tick(monkeypatch):
    def busy(*args, **kwargs):
        raise RuntimeError('WORKFLOW_BUSY')

    monkeypatch.setattr('scripts.workflow.poll_updates', busy)
    workflow = SimpleNamespace(store=object(), telegram=object(), breaker=object())
    assert Workflow.send_tick(workflow)['send']['status'] == 'TELEGRAM_POLL_BUSY'
