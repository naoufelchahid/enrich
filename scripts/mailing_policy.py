"""Current mailing rules: exact V2/V5 copy and an explicitly configured cadence."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from scripts.email_patterns import PILOT_REVISION, validate_pilot_copy
from scripts.workflow_store import fingerprint


def validate_policy(policy):
    required = {'min_interval_minutes', 'timezone', 'weekdays', 'start_time', 'end_time'}
    if not isinstance(policy, dict) or set(policy) != required:
        raise ValueError('MAILING_POLICY_REQUIRED')
    interval = policy['min_interval_minutes']
    days = policy['weekdays']
    if type(interval) is not int or not 1 <= interval <= 1440:
        raise ValueError('MAILING_INTERVAL_REQUIRED')
    if (not isinstance(days, list) or not days
            or any(type(day) is not int or not 0 <= day <= 6 for day in days)
            or len(days) != len(set(days))):
        raise ValueError('MAILING_WEEKDAYS_REQUIRED')
    try:
        ZoneInfo(policy['timezone'])
        start = datetime.strptime(policy['start_time'], '%H:%M').time()
        end = datetime.strptime(policy['end_time'], '%H:%M').time()
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise ValueError('MAILING_WINDOW_REQUIRED') from None
    if (start > end or start.strftime('%H:%M') != policy['start_time']
            or end.strftime('%H:%M') != policy['end_time']):
        raise ValueError('MAILING_WINDOW_REQUIRED')
    return policy


def load_policy(store):
    with store.connection() as c:
        row = c.execute("SELECT value FROM workflow_meta WHERE key='mailing_policy'").fetchone()
    try:
        return validate_policy(json.loads(row[0]) if row else None)
    except (ValueError, TypeError):
        raise ValueError('MAILING_POLICY_REQUIRED') from None


def send_slot(at, policy):
    policy = validate_policy(policy)
    if at.tzinfo is None:
        raise ValueError('Aware clock required')
    local = at.astimezone(ZoneInfo(policy['timezone']))
    start = datetime.strptime(policy['start_time'], '%H:%M').time()
    end = datetime.strptime(policy['end_time'], '%H:%M').time()
    minute = local.hour * 60 + local.minute
    elapsed = minute - start.hour * 60 - start.minute
    if (local.weekday() not in policy['weekdays'] or elapsed < 0
            or minute > end.hour * 60 + end.minute
            or elapsed % policy['min_interval_minutes']):
        return None
    return local.replace(second=0, microsecond=0).isoformat()


def initial_copy_error(store, draft, connection=None):
    """Historical drafts stay readable, but cannot be approved or transported."""
    if draft['kind'] != 'INITIAL':
        return None
    hook = json.loads(draft['hook']) if isinstance(draft['hook'], str) else draft['hook']
    if (hook.get('immediate_only') or hook.get('pattern_revision') != PILOT_REVISION
            or hook.get('variant_id') not in ('V2', 'V5')):
        return 'RETIRED_MAILING_RULES'
    contact = store.contact(draft['contact_id'])
    if not contact:
        return 'CONTACT_NOT_FOUND'
    lead = json.loads(contact['snapshot'])
    valid, _ = validate_pilot_copy(hook['variant_id'], draft['subject'], draft['body'],
        last_name=lead.get('last_name'),
        company_name=lead.get('company_display_name') or lead.get('company_name'),
        civility=lead.get('civility'), revision=hook.get('pattern_revision'))
    if not valid or hook.get('signature_mode') != 'thunderbird_html':
        return 'PILOT_COPY_INVALID'
    expected_hash = fingerprint({'recipient':draft['recipient'], 'subject':draft['subject'],
        'body':draft['body'], 'variant_id':hook['variant_id'], 'pattern_revision':PILOT_REVISION,
        'signature_mode':hook['signature_mode'], 'signature_revision':hook.get('signature_revision')})
    if hook.get('critique_hash') != expected_hash:
        return 'INDEPENDENT_COPY_REVIEW_REQUIRED'
    def reviewed(c):
        for row in c.execute("SELECT payload,result FROM workflow_operations WHERE contact_id=? "
                             "AND kind='PILOT_COPY_CRITIQUE' AND status='SUCCEEDED'", (draft['contact_id'],)):
            dossier, result = json.loads(row['payload']), json.loads(row['result'])
            if (hook.get('critique_hash') and result.get('approved') is True
                    and result.get('copy_hash') == hook['critique_hash'] == dossier.get('copy_hash')
                    and dossier.get('subject') == draft['subject'] and dossier.get('body') == draft['body']
                    and dossier.get('variant_id') == hook['variant_id']):
                return True
        return False
    if connection is not None:
        accepted = reviewed(connection)
    else:
        with store.connection() as c:
            accepted = reviewed(c)
    return None if accepted else 'INDEPENDENT_COPY_REVIEW_REQUIRED'


def main():
    import argparse
    import sqlite3
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal-path', required=True)
    parser.add_argument('--policy-json', help='Explicit reviewed cadence; no defaults')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    path = Path(args.journal_path).resolve()
    if not path.is_file():
        parser.error('Existing journal required')
    if args.policy_json is None:
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as c:
            row = c.execute("SELECT value FROM workflow_meta WHERE key='mailing_policy'").fetchone()
        result = {'policy':json.loads(row[0]) if row else None, 'external_actions':0}
    else:
        policy = validate_policy(json.loads(args.policy_json))
        result = {'policy':policy, 'applied':False, 'external_actions':0}
        if args.apply:
            from scripts.compact_store import CompactStore
            from scripts.workflow_store import encode
            store = CompactStore(path)
            with store.lock(), store.connection() as c:
                c.execute('BEGIN IMMEDIATE')
                old = c.execute("SELECT value FROM workflow_meta WHERE key='mailing_policy'").fetchone()
                store.event('mailing_policy_configured',None,
                    {'previous':old[0] if old else None,'policy':policy},connection=c)
                c.execute("INSERT INTO workflow_meta VALUES('mailing_policy',?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (encode(policy),))
            result['applied'] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
