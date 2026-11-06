"""Atomic, replay-safe BDD import with source archives and reviewed identity keys."""
import hashlib
import json
import sqlite3
from pathlib import Path

from scripts.email_hygiene import email_identity, name_tokens, is_personal_email, personal_email_replacement
from scripts.etl_normalize import parse_all_sources
from scripts.outreach_journal import flush_journal, journal_path
from scripts.staging_db import init_staging_db, calculate_priority_score


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def identity_key(lead):
    from scripts.enrichment_clients import linkedin_url
    url = linkedin_url(lead.get('linkedin_url'))
    if url and lead.get('linkedin_search_status') == 'CONFIRMED':
        return 'linkedin:' + url
    email = (lead.get('raw_email') or '').strip().lower()
    if email_identity(email, lead.get('first_name'), lead.get('last_name'))['accepted']:
        return 'email:' + email
    return None


def workbook_archive(directory):
    import openpyxl
    import xlrd
    records = []
    for path in sorted(Path(directory).glob('BDD*.*')):
        if path.suffix.lower() not in ('.xls', '.xlsx'):
            continue
        sheets = {}
        if path.suffix.lower() == '.xlsx':
            book = openpyxl.load_workbook(path, read_only=True, data_only=False)
            try:
                for sheet in book:
                    sheets[sheet.title] = list(sheet.values)
            finally:
                book.close()
        else:
            book = xlrd.open_workbook(path)
            for sheet in book.sheets():
                sheets[sheet.name] = [sheet.row_values(i) for i in range(sheet.nrows)]
        records.append({'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'sheets': sheets})
    return records


def import_all(data_dir, db_path, *, archive_path=None, dry_run=False, contacts=None,
               backend='legacy', compact_store=None):
    if backend == 'legacy':
        from scripts.backend_guard import require_legacy
        require_legacy(db_path, lane='qualification', journal_path=archive_path)
    if backend not in ('legacy', 'compact'):
        raise ValueError('UNKNOWN_IMPORT_BACKEND')
    if not Path(data_dir).is_dir():
        raise ValueError('Source directory does not exist')
    rows = [c.model_dump(mode='json') if hasattr(c, 'model_dump') else dict(c)
            for c in (contacts if contacts is not None else parse_all_sources(str(data_dir)))]
    if not rows:
        raise ValueError('No supported source contacts')
    sources = workbook_archive(data_dir) if contacts is None else []
    if backend == 'compact':
        if compact_store is None:
            raise ValueError('COMPACT_STORE_REQUIRED')
        from scripts.compact_qualification import import_compact
        return import_compact(compact_store, rows, sources=sources, dry_run=dry_run)
    batch_id = digest({'rows': rows, 'source_hashes': [s['sha256'] for s in sources]})
    archive_path = archive_path or journal_path(db_path)
    excluded, historical, identities, archived_contacts, imported_rows = set(), {}, {}, {}, []
    if Path(archive_path).exists():
        with sqlite3.connect(Path(archive_path).resolve().as_uri() + '?mode=ro', uri=True) as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'journal_exclusions' in tables:
                excluded = {r[0] for r in c.execute('SELECT email FROM journal_exclusions')}
            if 'workflow_exclusions' in tables:
                excluded.update(r[0] for r in c.execute('SELECT email FROM workflow_exclusions'))
            if 'journal_events' in tables:
                for (payload,) in c.execute("SELECT payload FROM journal_events WHERE kind IN ('contact','contact_removed') ORDER BY rowid"):
                    lead = json.loads(payload)
                    if lead.get('journal_contact_id'):
                        archived_contacts[lead['journal_contact_id']]=lead
                    if lead.get('import_key'):
                        historical[lead['import_key']] = lead
                    if identity_key(lead):
                        identities[identity_key(lead)] = lead
                imported_rows=[json.loads(payload) for (payload,) in c.execute("SELECT payload FROM journal_events WHERE kind='workflow_import_rows' ORDER BY rowid")]
            if 'workflow_contacts' in tables:
                for (payload,) in c.execute('SELECT snapshot FROM workflow_contacts'):
                    lead = json.loads(payload)
                    if lead.get('journal_contact_id'):
                        archived_contacts[lead['journal_contact_id']]=lead
                    if lead.get('import_key'):
                        historical[lead['import_key']] = lead
                    if identity_key(lead):
                        identities[identity_key(lead)] = lead
    existing = []
    if Path(db_path).exists():
        with sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True) as c:
            c.row_factory = sqlite3.Row
            existing = [dict(r) for r in c.execute('SELECT * FROM staging_leads')]
    for lead in existing:
        if lead.get('journal_contact_id'):
            archived_contacts[lead['journal_contact_id']]=lead
        if lead.get('import_key'):
            historical[lead['import_key']] = lead
        if identity_key(lead):
            identities[identity_key(lead)] = lead
    if Path(db_path).exists():
        with sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True) as c:
            c.row_factory=sqlite3.Row
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_import_rows'").fetchone():
                imported_rows.extend(dict(r) for r in c.execute('SELECT * FROM workflow_import_rows'))
    for row in imported_rows:
        if row.get('contact_id') in archived_contacts:
            historical[row['row_key']]=archived_contacts[row['contact_id']]
    summary = {'accepted': 0, 'duplicates': 0, 'excluded': 0, 'transferred': 0, 'ambiguous': 0, 'sources': len(sources)}
    accepted, provenance, links, occurrences = [], [], [], {}
    for lead in rows:
        # Weak identities are replayed by exact source-row content/occurrence;
        # identical names in different sources never become a person merge.
        source_fingerprint=digest(lead)
        occurrence=occurrences.get(source_fingerprint,0)
        occurrences[source_fingerprint]=occurrence+1
        key=digest({'source_row':source_fingerprint,'occurrence':occurrence})
        lead['import_key'] = key
        email = (lead.get('raw_email') or '').strip().lower()
        if email in excluded:
            summary['excluded'] += 1
            continue
        stable_key = identity_key(lead)
        prior = historical.get(key) or identities.get(stable_key)
        if prior:
            if stable_key and (name_tokens(prior.get('first_name')) != name_tokens(lead.get('first_name'))
                               or name_tokens(prior.get('last_name')) != name_tokens(lead.get('last_name'))):
                summary['ambiguous'] += 1
                lead['workflow_wait_reason'] = 'IDENTITY_CONFLICT'
            else:
                if prior.get('workflow_wait_reason')=='IDENTITY_CONFLICT':
                    summary['ambiguous']+=1
                summary['transferred' if prior.get('crm_person_id') else 'duplicates'] += 1
                provenance.append((key, encoded(lead)))
                links.append((key,prior,encoded(lead)))
                continue
        if is_personal_email(email):
            lead.update(personal_email_replacement(lead))
        accepted.append(lead)
        historical[key] = lead
        if stable_key and not lead.get('workflow_wait_reason'):
            identities[stable_key] = lead
        summary['accepted'] += 1
        provenance.append((key, encoded(lead)))
        links.append((key,lead,encoded(lead)))
    if dry_run:
        return {**summary, 'dry_run': True, 'inserted': 0}
    init_staging_db(str(db_path)).close()
    c = sqlite3.connect(db_path, timeout=10)
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('CREATE TABLE IF NOT EXISTS workflow_imports (id TEXT PRIMARY KEY, status TEXT NOT NULL, summary TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS workflow_source_workbooks (sha256 TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS workflow_import_provenance (row_key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(row_key,payload))')
            c.execute('CREATE TABLE IF NOT EXISTS workflow_import_rows (row_key TEXT PRIMARY KEY, contact_id TEXT NOT NULL, source_record TEXT NOT NULL)')
            from scripts.outreach_journal import install_outbox
            install_outbox(c)
            if c.execute('SELECT 1 FROM workflow_imports WHERE id=?', (batch_id,)).fetchone():
                return {**summary, 'inserted': 0, 'status': 'ALREADY_IMPORTED'}
            allowed = {r[1] for r in c.execute('PRAGMA table_info(staging_leads)')}
            for lead in accepted:
                if c.execute('SELECT 1 FROM staging_leads WHERE import_key=?', (lead['import_key'],)).fetchone():
                    continue
                lead.update(clean_email=None, neverbounce_status='PENDING', enrichment_status='PENDING',
                            original_email=lead.get('raw_email'), source_data_json=encoded(lead),
                            priority_score=calculate_priority_score(lead))
                # Operational assertions from a source row are untrusted. Keep
                # them in source_data_json, never import them as provider proof.
                lead.update(email_validation_source=None,email_validated_at=None,validated_email=None,
                            verification_verdict=None,email_check_status='PENDING',email_checked_input=None,
                            contact_discovery_status='PENDING',profile_status='PENDING',posts_status='PENDING',
                            linkedin_data_source=None,linkedin_search_status='PENDING',linkedin_observed_at=None,
                            linkedin_profile_json=None,linkedin_comments_json=None,recent_posts=None,
                            is_enriched_linkedin=0,crm_person_id=None,crm_synced_at=None,outreach_status='PENDING')
                data = {k: v for k, v in lead.items() if k in allowed and k not in ('id', 'journal_contact_id')}
                names = list(data)
                cursor=c.execute('INSERT INTO staging_leads (' + ','.join(names) + ') VALUES (' + ','.join('?' for _ in names) + ')', tuple(data.values()))
                lead['journal_contact_id']=c.execute('SELECT journal_contact_id FROM staging_leads WHERE id=?',(cursor.lastrowid,)).fetchone()[0]
            c.executemany('INSERT OR IGNORE INTO workflow_source_workbooks VALUES (?,?)', [(s['sha256'], encoded(s)) for s in sources])
            c.executemany('INSERT OR IGNORE INTO workflow_import_provenance VALUES (?,?)', provenance)
            for key,owner,payload in links:
                identity=owner.get('journal_contact_id')
                if identity:
                    c.execute('INSERT OR IGNORE INTO workflow_import_rows VALUES (?,?,?)',(key,identity,payload))
            c.execute('INSERT INTO workflow_imports VALUES (?,?,?)', (batch_id, 'COMPLETE', encoded(summary)))
    finally:
        c.close()
    flush_journal(str(db_path))
    return {**summary, 'inserted': len(accepted), 'status': 'COMPLETE'}
