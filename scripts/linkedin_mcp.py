"""Prepare LinkedIn MCP tasks and import validated extraction evidence offline."""
import argparse
import fcntl
import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from scripts.linkedin_contract import Extraction
from scripts.enrichment_clients import linkedin_url


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def target_for(row):
    target = {k: row[k] for k in ('first_name', 'last_name', 'company_name', 'source_file')}
    target['lead_id'] = row['id']
    target['input_sha256'] = digest(target)
    return target


def export_task(db_path, lead_id):
    path = Path(db_path).resolve()
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as c:
        c.row_factory = sqlite3.Row
        row = c.execute('SELECT * FROM staging_leads WHERE id=?', (lead_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown lead')
        row = dict(row)
    if not all(isinstance(row.get(k), str) and row[k].strip() for k in ('first_name', 'last_name', 'company_name')):
        raise ValueError('Contact requires separate first name, last name and company')
    return {'schema_version': '1.0', 'purpose': 'CONTACT', 'target': target_for(row),
            'known_linkedin_url': linkedin_url(row.get('linkedin_url')),
            'max_posts': 3, 'provider': 'linkedin_mcp',
            'instructions': 'docs/LINKEDIN_AGENT_INSTRUCTIONS.md',
            'output_schema': 'schemas/linkedin-extraction-v1.schema.json'}



def pending_tasks(db_path, limit=10, *, include_queued=False):
    """Read-only handoff for the Codex MCP worker."""
    with sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True) as c:
        c.row_factory = sqlite3.Row
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tasks = []
        for r in c.execute('SELECT * FROM staging_leads ORDER BY priority_score DESC,id'):
            row = dict(r)
            if row.get('outreach_status', 'PENDING') != 'PENDING':
                continue
            if not include_queued and 'linkedin_browser_queue' in tables and c.execute('SELECT 1 FROM linkedin_browser_queue WHERE lead_id=?', (row['id'],)).fetchone():
                continue
            if row.get('linkedin_data_source') in ('codex_chrome', 'linkedin_mcp'):
                continue
            if 'linkedin_browser_imports' in tables and c.execute('SELECT 1 FROM linkedin_browser_imports WHERE lead_id=?', (row['id'],)).fetchone():
                continue
            if 'email_suppressions' in tables and c.execute('SELECT 1 FROM email_suppressions WHERE email=?', ((row.get('raw_email') or '').strip().lower(),)).fetchone():
                continue
            if not all(isinstance(row.get(k), str) and row[k].strip() for k in ('first_name','last_name','company_name')):
                continue
            tasks.append({'target': target_for(row), 'known_linkedin_url': linkedin_url(row.get('linkedin_url')),
                          'provider': 'linkedin_mcp', 'max_posts': 3,
                          'instructions': 'docs/LINKEDIN_AGENT_INSTRUCTIONS.md',
                          'output_schema': 'schemas/linkedin-extraction-v1.schema.json'})
            if len(tasks) >= limit:
                break
    return tasks


def name_key(value):
    value = unicodedata.normalize('NFKD', value.casefold())
    return ' '.join(re.findall(r'[^\W_]+', ''.join(c for c in value if not unicodedata.combining(c))))


ADDITIONS = {
    'linkedin_profile_json': 'TEXT',
    'linkedin_comments_json': 'TEXT',
    'linkedin_data_source': 'TEXT',
    'linkedin_observed_at': 'TEXT',
    'profile_status': "TEXT NOT NULL DEFAULT 'PENDING'",
    'posts_status': "TEXT NOT NULL DEFAULT 'PENDING'",
    'linkedin_search_status': "TEXT NOT NULL DEFAULT 'PENDING'",
    'recent_posts': 'TEXT',
    'is_enriched_linkedin': 'BOOLEAN DEFAULT 0',
}


def import_extraction(db_path, json_path, *, dry_run=False):
    from scripts.backend_guard import require_legacy
    require_legacy(db_path, lane='linkedin')
    source = Path(json_path)
    if source.stat().st_size > 2_000_000:
        raise ValueError('Extraction exceeds 2 MB')
    # Model rejects unknown fields, including email/CRM/SQL assignments.
    record = Extraction.model_validate_json(source.read_text())
    if record.purpose != 'CONTACT':
        raise ValueError('Reference profiles must never be imported into a contact')
    data = record.model_dump()
    # Optional schema extensions must preserve hashes of earlier v1 imports.
    if 'comments' not in record.activity.model_fields_set:
        data['activity'].pop('comments',None)
    payload, sha = encode(data), digest(data)
    path = Path(db_path).resolve()

    def plan(c):
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'linkedin_browser_imports' in tables:
            prior = c.execute('SELECT payload_sha256,lead_id FROM linkedin_browser_imports WHERE extraction_id=?', (record.extraction_id,)).fetchone()
            if prior:
                if prior[0] != sha or prior[1] != record.target.lead_id:
                    raise ValueError('Extraction ID already used with different content')
                return None, {'status': 'UNCHANGED', 'lead_id': prior[1]}
        row = c.execute('SELECT * FROM staging_leads WHERE id=?', (record.target.lead_id,)).fetchone()
        if row is None or target_for(dict(row)) != record.target.model_dump():
            raise ValueError('Target changed or belongs to a different contact')
        row = dict(row)
        if row.get('outreach_status', 'PENDING') != 'PENDING':
            raise ValueError('Contact is not pending')
        if 'email_suppressions' in tables and c.execute('SELECT 1 FROM email_suppressions WHERE email=?', ((row.get('raw_email') or '').strip().lower(),)).fetchone():
            raise ValueError('Contact email is suppressed')
        if record.identity.status != 'CONFIRMED':
            # Archive for review, preserve previously attributed profile and posts.
            return {}, {'status': 'ARCHIVED_FOR_REVIEW', 'lead_id': row['id']}
        if name_key(record.profile.full_name) != name_key(row['first_name'] + ' ' + row['last_name']):
            raise ValueError('Observed profile name does not match the target')
        existing = row.get('linkedin_url')
        if existing and linkedin_url(existing) != record.linkedin_url:
            raise ValueError('Conflicting existing LinkedIn URL')
        previous = row.get('linkedin_observed_at')
        if previous and datetime.fromisoformat(previous.replace('Z', '+00:00')) >= datetime.fromisoformat(record.observed_at.replace('Z', '+00:00')):
            raise ValueError('Refusing stale profile snapshot')
        updates = {'linkedin_url': record.linkedin_url,
                   'linkedin_profile_json': encode(data['profile']),
                   'linkedin_data_source': record.acquisition_method,
                   'linkedin_observed_at': record.observed_at,
                   'linkedin_search_status': 'CONFIRMED',
                   'profile_status': 'SUCCEEDED', 'is_enriched_linkedin': 1}
        if row.get('workflow_wait_reason') in ('LINKEDIN_WORKER_REVIEW','LINKEDIN_REVIEW'):
            updates['workflow_wait_reason']=None
        if record.activity.comments:
            updates['linkedin_comments_json']=encode(data['activity']['comments'])
        if record.activity.posts:
            updates['recent_posts'] = encode(data['activity']['posts'])
            updates['posts_status'] = 'SUCCEEDED' if record.activity.status == 'COMPLETE' else 'PARTIAL'
        elif not row.get('recent_posts'):
            updates['posts_status'] = 'NO_VISIBLE_POSTS' if record.activity.status == 'NO_VISIBLE_POSTS' else 'WAITING_LINKEDIN'
        # Missing/inaccessible activity must never erase earlier evidence.
        return updates, {'status': 'IMPORTED', 'lead_id': row['id'], 'fields': list(updates)}

    if dry_run:
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as c:
            c.row_factory = sqlite3.Row
            _, result = plan(c)
        return {**result, 'dry_run': True}
    with open(str(path) + '.enrichment.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with sqlite3.connect(path.as_uri() + '?mode=rw', uri=True) as c:
            c.row_factory = sqlite3.Row
            updates, result = plan(c)
            if updates is None:
                return result
            from scripts.workflow_backup import ensure_backup
            ensure_backup(c,path,'linkedin-mcp')
            c.execute('BEGIN IMMEDIATE')
            updates, result = plan(c)
            if updates is None:
                return result
            columns = {r[1] for r in c.execute('PRAGMA table_info(staging_leads)')}
            for name, declaration in ADDITIONS.items():
                if name not in columns:
                    c.execute(f'ALTER TABLE staging_leads ADD COLUMN {name} {declaration}')
            c.execute('''CREATE TABLE IF NOT EXISTS linkedin_browser_imports (
                extraction_id TEXT PRIMARY KEY, lead_id INTEGER NOT NULL,
                payload_sha256 TEXT NOT NULL, payload TEXT NOT NULL,
                disposition TEXT NOT NULL, imported_at TEXT NOT NULL)''')
            from scripts.outreach_journal import install_outbox
            install_outbox(c)
            c.execute('INSERT INTO linkedin_browser_imports VALUES(?,?,?,?,?,?)',
                (record.extraction_id, record.target.lead_id, sha, payload, result['status'], datetime.now(timezone.utc).isoformat()))
            if updates:
                c.execute('UPDATE staging_leads SET '+','.join(k+'=?' for k in updates)+' WHERE id=?',
                          (*updates.values(), record.target.lead_id))
        from scripts.outreach_journal import flush_journal
        flush_journal(db_path)
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=['export', 'import'])
    p.add_argument('--db-path', required=True)
    p.add_argument('--lead-id', type=int)
    p.add_argument('--json-path')
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    if a.operation == 'export':
        if not a.lead_id:
            p.error('--lead-id is required')
        result = export_task(a.db_path, a.lead_id)
    else:
        if not a.json_path:
            p.error('--json-path is required')
        result = import_extraction(a.db_path, a.json_path, dry_run=a.dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    main()
