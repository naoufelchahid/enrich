"""Wake the campaign's Codex thread once for authenticated Telegram updates."""
import json
import shutil
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIONABLE = {
    'telegram_approve', 'telegram_reject', 'telegram_rewrite',
    'telegram_rewrite_directive', 'breaker_resume_authorized',
    'telegram_invalid_resume', 'telegram_invalid_action',
}
REJECTED_REASONS = {'UNKNOWN_DIRECTIVE', 'STALE_CALLBACK', 'CONTENT_CHANGED'}


def actionable(event):
    if event['kind'] in ACTIONABLE:
        return True
    if event['kind'] != 'telegram_rejected':
        return False
    return json.loads(event['payload']).get('reason') in REJECTED_REASONS


def wake_prompt(event_ids):
    ids = ', '.join(str(value) for value in event_ids)
    return (
        'Nouveaux événements Telegram authentifiés dans le journal Enrich : '
        f'workflow_events.id = {ids}. Reprends cette conversation et lis leurs '
        'payloads exacts dans bdd/workflow/staging.sqlite.journal.sqlite, puis '
        'l’état courant de la campagne. Traite les instructions de l’opérateur '
        'et réponds-lui via Telegram lorsque nécessaire. Respecte l’approbation '
        'exacte de chaque mail, les exclusions, le coupe-circuit et la cadence '
        'explicitement configurée dans mailing_policy. Sans cadence, conserver '
        'l’attente. Ne réexpédie jamais un mail sans confirmation de son état.'
    )


def dispatch_pending(store, thread_id, *, executable=None, run_command=subprocess.run):
    if not thread_id:
        return {'status': 'AGENT_HOOK_NOT_CONFIGURED'}
    try:
        uuid.UUID(thread_id)
    except ValueError:
        return {'status': 'AGENT_HOOK_INVALID_THREAD'}
    executable = executable or shutil.which('codex')
    if not executable:
        return {'status': 'AGENT_HOOK_CODEX_UNAVAILABLE'}
    try:
        with store.lock('telegram-agent-hook'):
            with store.connection() as c:
                row = c.execute("SELECT value FROM workflow_meta WHERE key='telegram_agent_cursor'").fetchone()
                if not row:
                    return {'status': 'AGENT_HOOK_CURSOR_REQUIRED'}
                cursor = int(row[0])
                events = c.execute("""SELECT id,key,kind,payload FROM workflow_events
                    WHERE id>? AND key LIKE 'telegram-update:%' ORDER BY id LIMIT 20""", (cursor,)).fetchall()
            if not events:
                return {'status': 'NO_TELEGRAM_EVENTS'}
            target = events[-1]['id']
            selected = [row['id'] for row in events if actionable(row)]
            if not selected:
                with store.connection() as c:
                    c.execute("UPDATE workflow_meta SET value=? WHERE key='telegram_agent_cursor' AND value=?",
                              (str(target), str(cursor)))
                return {'status': 'NO_ACTIONABLE_EVENTS', 'cursor': target}
            payload = {'thread_id': thread_id, 'from_event': cursor + 1,
                       'to_event': target, 'event_ids': selected}
            operation = store.operation(None, 'TELEGRAM_AGENT_WAKE', payload,
                                        key='telegram-agent-wake:' + str(target))
            if operation['status'] == 'SUCCEEDED':
                with store.connection() as c:
                    c.execute("UPDATE workflow_meta SET value=? WHERE key='telegram_agent_cursor' AND value=?",
                              (str(target), str(cursor)))
                return {'status': 'ALREADY_QUEUED', 'event_ids': selected}
            if operation['status'] in ('SENDING', 'UNKNOWN', 'NEEDS_REVIEW'):
                return {'status': 'AGENT_WAKE_RECONCILIATION_REQUIRED', 'event_ids': selected}
            store.finish_operation(operation['id'], 'SENDING')
            command = [executable, 'queue', '--thread', thread_id,
                       '--message', wake_prompt(selected), '--resume', '-C', str(ROOT)]
            try:
                result = run_command(command, capture_output=True, text=True, timeout=45)
            except (OSError, subprocess.TimeoutExpired) as error:
                store.finish_operation(operation['id'], 'UNKNOWN', {'error': type(error).__name__})
                return {'status': 'AGENT_WAKE_RECONCILIATION_REQUIRED', 'event_ids': selected,
                        'alert': True}
            output = ((result.stdout or '') + '\n' + (result.stderr or ''))[:1200]
            if result.returncode:
                store.finish_operation(operation['id'], 'UNKNOWN',
                                       {'returncode': result.returncode, 'output': output})
                return {'status': 'AGENT_WAKE_RECONCILIATION_REQUIRED', 'event_ids': selected,
                        'alert': True}
            warning = any(phrase in output.casefold() for phrase in
                          ('failed to resume', 'could not resume', 'resume failed'))
            store.finish_operation(operation['id'], 'SUCCEEDED',
                                   {'event_ids': selected, 'resume_warning': warning, 'output': output})
            with store.connection() as c:
                c.execute("UPDATE workflow_meta SET value=? WHERE key='telegram_agent_cursor' AND value=?",
                          (str(target), str(cursor)))
            return {'status': 'QUEUED_NOT_RESUMED' if warning else 'AGENT_WAKE_QUEUED',
                    'event_ids': selected, 'cursor': target, 'alert': warning}
    except RuntimeError as error:
        if str(error) == 'WORKFLOW_BUSY':
            return {'status': 'AGENT_HOOK_BUSY'}
        raise
