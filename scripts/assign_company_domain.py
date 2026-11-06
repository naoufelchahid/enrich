"""Assign a reviewed domain from the local WHOIS/RDAP cache to matching staging rows.

No network, enrichment, CRM writes or email validation. Explicit invocation is
operator approval of the company/domain association; ambiguous evidence is refused.
"""
import argparse
import fcntl
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from scripts.domain_review import domain_name, recent, timestamp


def company_key(value):
    return ' '.join(re.findall(r'\w+', (value or '').casefold()))


def assign_domain(db_path, cache_path, domain, company, *, dry_run=False):
    from scripts.backend_guard import require_legacy
    require_legacy(db_path)
    domain = domain_name(domain)
    if not company_key(company):
        raise ValueError('Company name is required')
    path, cache = Path(db_path).resolve(), Path(cache_path).resolve()
    if path == cache:
        raise ValueError('Review cache must be separate from staging')
    with sqlite3.connect(cache.as_uri() + '?mode=ro', uri=True) as c:
        stored = c.execute('SELECT observed_at,payload FROM domain_review_cache WHERE key=?', (domain,)).fetchone()
    if not stored:
        raise ValueError('No cached review; review the domain first')
    evidence = json.loads(stored[1])
    if (evidence.get('domain') != domain or evidence.get('status') != 'FOUND'
            or evidence.get('observed_at') != stored[0] or not recent(stored[0], 7)
            or evidence.get('protocol') not in ('WHOIS', 'RDAP')
            or not evidence.get('source') or not re.fullmatch('[0-9a-f]{64}', evidence.get('evidence_sha256', ''))
            or not isinstance(evidence.get('registrant_names'), list)
            or not any(isinstance(n, str) and company_key(n) == company_key(company) for n in evidence['registrant_names'])):
        raise ValueError('Recent matching registrant evidence required; manual review remains necessary')
    source = json.dumps({'method': 'WHOIS_RDAP_OPERATOR_ASSIGNMENT', 'company': company,
                         'domain': domain, 'protocol': evidence['protocol'], 'source': evidence['source'],
                         'observed_at': evidence['observed_at'], 'evidence_sha256': evidence['evidence_sha256']}, sort_keys=True)

    def plan(c):
        columns = {r[1] for r in c.execute('PRAGMA table_info(staging_leads)')}
        if not {'id', 'company_name'} <= columns:
            raise ValueError('Existing staging database required')
        rows = [dict(r) for r in c.execute('SELECT * FROM staging_leads')
                if company_key(r['company_name']) == company_key(company)]
        changes, unchanged, conflicts = [], [], []
        for row in rows:
            existing = row.get('company_domain')
            if existing and existing.strip().lower() != domain:
                conflicts.append(row['id'])
            elif existing and row.get('company_domain_source'):
                unchanged.append(row['id'])
            else:
                changes.append(row)
        if conflicts:
            raise ValueError('Conflicting existing domain; refusing all changes')
        return columns, changes, unchanged

    # A preview does not create a lock, database, backup or schema.
    if dry_run:
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as c:
            c.row_factory = sqlite3.Row
            _, changes, unchanged = plan(c)
        return {'dry_run': True, 'domain': domain, 'company': company,
                'would_update': [r['id'] for r in changes], 'unchanged': unchanged}
    with open(str(path) + '.enrichment.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        c = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            _, changes, unchanged = plan(c)
            if not changes:
                return {'updated': [], 'unchanged': unchanged, 'domain': domain}
            # Preserve the original schema and all data before this assignment.
            from scripts.workflow_backup import ensure_backup
            backup = ensure_backup(c,path,'domain-assignment')
            with c:
                c.execute('BEGIN IMMEDIATE')
                columns, changes, unchanged = plan(c)
                for column in ('company_domain', 'company_domain_source'):
                    if column not in columns:
                        c.execute('ALTER TABLE staging_leads ADD COLUMN ' + column + ' TEXT')
                c.execute('''CREATE TABLE IF NOT EXISTS company_domain_assignments (
                    id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL, company_name TEXT NOT NULL,
                    domain TEXT NOT NULL, previous_domain TEXT, previous_source TEXT,
                    assigned_at TEXT NOT NULL, evidence TEXT NOT NULL)''')
                for row in changes:
                    c.execute('INSERT INTO company_domain_assignments(lead_id,company_name,domain,previous_domain,previous_source,assigned_at,evidence) VALUES(?,?,?,?,?,?,?)',
                              (row['id'], row['company_name'], domain, row.get('company_domain'), row.get('company_domain_source'), timestamp(), json.dumps(evidence, sort_keys=True)))
                    c.execute('UPDATE staging_leads SET company_domain=?,company_domain_source=? WHERE id=?',
                              (domain, source, row['id']))
            return {'updated': [r['id'] for r in changes], 'unchanged': unchanged,
                    'domain': domain, 'company': company, 'backup': str(backup)}
        finally:
            c.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db-path', required=True)
    p.add_argument('--cache', default='bdd/auxiliary/domain-review.sqlite')
    p.add_argument('--domain', required=True)
    p.add_argument('--company', required=True)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    print(json.dumps(assign_domain(a.db_path, a.cache, a.domain, a.company, dry_run=a.dry_run), indent=2))


if __name__ == '__main__':
    main()
