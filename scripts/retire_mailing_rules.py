"""Retire historical mailing policies and pending drafts, preserving exact evidence."""
import argparse
import json
import os
import sqlite3
from pathlib import Path

from scripts.email_patterns import PILOT_REVISION


def retirement_plan(c):
    drafts = []
    for row in c.execute("SELECT * FROM workflow_drafts WHERE kind='INITIAL' "
                         "AND status IN ('PENDING','APPROVED','REWRITE')"):
        hook = json.loads(row['hook'])
        if (hook.get('pattern_revision') != PILOT_REVISION
                or hook.get('variant_id') not in ('V2','V5') or hook.get('immediate_only')):
            drafts.append(dict(row))
    policies = [dict(row) for row in c.execute("SELECT * FROM workflow_meta WHERE key='immediate_campaign'")]
    return {'drafts':drafts,'policies':policies}


def retire(path, *, apply=False):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('EXISTING_JOURNAL_REQUIRED')
    if not apply:
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
            c.row_factory = sqlite3.Row
            plan = retirement_plan(c)
    else:
        if os.getenv('WORKFLOW_STOP_AT_DRAFT') != '1':
            raise ValueError('MAILING_PAUSE_REQUIRED')
        from scripts.compact_store import CompactStore
        store = CompactStore(path)
        with store.lock(), store.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM workflow_sends WHERE state IN ('SENDING','UNKNOWN')").fetchone():
                raise ValueError('SEND_RECONCILIATION_REQUIRED')
            plan = retirement_plan(c)
            if plan['drafts'] or plan['policies']:
                # Exact rows are journaled before retiring approvals or policy.
                store.event('historical_mailing_rules_retired',None,plan,connection=c)
                for draft in plan['drafts']:
                    c.execute("UPDATE workflow_drafts SET status='SUPERSEDED',approved_by=NULL,approved_at=NULL WHERE id=?",
                              (draft['id'],))
                c.execute("DELETE FROM workflow_meta WHERE key='immediate_campaign'")
    return {'applied':apply,'retired_drafts':len(plan['drafts']),
            'retired_policies':len(plan['policies']),'external_actions':0,'sends':0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal-path',required=True)
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    print(json.dumps(retire(args.journal_path,apply=args.apply),ensure_ascii=False))


if __name__ == '__main__':
    main()
