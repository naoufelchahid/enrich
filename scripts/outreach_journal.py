"""Durable local audit trail, independent of staging rows and provider calls.

SQLite triggers write an outbox in the SAME transaction as each change. Export
commits to a separate SQLite file before acknowledging the outbox; interruption
between those commits is safe to replay. No identity matching or verdict inference
is performed here.
"""
import argparse
import json
import sqlite3
import uuid
from pathlib import Path

EVIDENCE_TABLES = ('email_suppressions','enrichment_jobs','enrichment_targets',
                  'linkedin_browser_imports','local_email_proofs','workflow_source_workbooks',
                  'workflow_import_provenance','workflow_imports','workflow_import_rows')


def journal_path(db_path):
    return str(Path(db_path).resolve()) + '.journal.sqlite'


def _quote(value):
    return '"' + value.replace('"', '""') + '"'


def _changed(c, table):
    """Compare stored values, including NULL, without serializing a no-op row."""
    columns = [r[1] for r in c.execute(f'PRAGMA table_info({_quote(table)})')]
    return ' OR '.join(f'OLD.{_quote(name)} IS NOT NEW.{_quote(name)}'
                       for name in columns) or '0'


def _snapshot(c, table, alias):
    columns = [r[1] for r in c.execute(f'PRAGMA table_info({_quote(table)})')]
    pairs = [
        "'" + name.replace("'", "''") + "'," + alias + '.' + _quote(name)
        for name in columns]
    # Some SQLite readers cap function calls at 100 arguments. Keep each
    # json_object below that cap regardless of future schema additions.
    chunks = ['json_object(' + ','.join(pairs[i:i + 25]) + ')'
              for i in range(0, len(pairs), 25)]
    if len(chunks) <= 1:
        return chunks[0] if chunks else 'json_object()'
    # Concatenate encoded object interiors instead of json_patch, which would
    # discard null-valued fields. JSON encoding still comes entirely from SQLite.
    interiors = [f'substr({chunk},2,length({chunk})-2)' for chunk in chunks]
    return "json('{' || " + " || ',' || ".join(interiors) + " || '}')"


def install_outbox(c):
    """Additive migration on an already initialized staging connection."""
    c.execute('CREATE TABLE IF NOT EXISTS outreach_journal_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    c.execute('INSERT OR IGNORE INTO outreach_journal_meta VALUES (?,?)', ('source_id', uuid.uuid4().hex))
    c.execute('''CREATE TABLE IF NOT EXISTS outreach_journal_outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
        contact_id TEXT, payload TEXT NOT NULL,
        recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        exported INTEGER NOT NULL DEFAULT 0)''')
    columns = {r[1] for r in c.execute('PRAGMA table_info(staging_leads)')}
    if 'journal_contact_id' not in columns:
        c.execute('ALTER TABLE staging_leads ADD COLUMN journal_contact_id TEXT')
    c.execute('UPDATE staging_leads SET journal_contact_id=lower(hex(randomblob(16))) WHERE journal_contact_id IS NULL')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS staging_journal_identity ON staging_leads(journal_contact_id)')
    # Identity is independent of mutable emails, names and local integer row IDs.
    c.execute('''CREATE TRIGGER IF NOT EXISTS journal_identity_insert AFTER INSERT ON staging_leads
        WHEN NEW.journal_contact_id IS NULL BEGIN
        UPDATE staging_leads SET journal_contact_id=lower(hex(randomblob(16))) WHERE id=NEW.id;
        END''')
    c.execute('''CREATE TRIGGER IF NOT EXISTS journal_identity_immutable BEFORE UPDATE OF journal_contact_id ON staging_leads
        WHEN OLD.journal_contact_id IS NOT NULL AND NEW.journal_contact_id IS NOT OLD.journal_contact_id
        BEGIN SELECT RAISE(ABORT, 'Durable contact identity is immutable'); END''')
    c.execute('DROP TRIGGER IF EXISTS journal_contact_insert')
    c.execute(f'''CREATE TRIGGER journal_contact_insert AFTER INSERT ON staging_leads
        WHEN NEW.journal_contact_id IS NOT NULL BEGIN
        INSERT INTO outreach_journal_outbox(kind,contact_id,payload)
        VALUES('contact',NEW.journal_contact_id,{_snapshot(c, 'staging_leads', 'NEW')}); END''')
    for operation, alias, kind in [('UPDATE', 'NEW', 'contact'), ('DELETE', 'OLD', 'contact_removed')]:
        name = 'journal_contact_' + operation.lower()
        c.execute(f'DROP TRIGGER IF EXISTS {name}')
        condition = f'WHEN {_changed(c, "staging_leads")}' if operation == 'UPDATE' else ''
        c.execute(f'''CREATE TRIGGER {name} AFTER {operation} ON staging_leads {condition} BEGIN
            INSERT INTO outreach_journal_outbox(kind,contact_id,payload)
            VALUES('{kind}',{alias}.journal_contact_id,{_snapshot(c, 'staging_leads', alias)});
            END''')
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # Capture provider requests/responses unchanged, including uncertain outcomes,
    # and the binding of each operation/proof to its durable contact identity.
    for table in EVIDENCE_TABLES:
        if table not in tables:
            continue
        for operation, alias in [('INSERT', 'NEW'), ('UPDATE', 'NEW'), ('DELETE', 'OLD')]:
            name = 'journal_' + table + '_' + operation.lower()
            c.execute(f'DROP TRIGGER IF EXISTS {name}')
            contact = ('(SELECT journal_contact_id FROM staging_leads WHERE id=' + alias + '.lead_id)'
                       if table in ('enrichment_targets', 'linkedin_browser_imports', 'local_email_proofs') else 'NULL')
            condition = f'WHEN {_changed(c, table)}' if operation == 'UPDATE' else ''
            c.execute(f'''CREATE TRIGGER {name} AFTER {operation} ON {_quote(table)} {condition} BEGIN
                INSERT INTO outreach_journal_outbox(kind,contact_id,payload)
                VALUES('{table}',{contact},{_snapshot(c, table, alias)}); END''')
    if not c.execute("SELECT 1 FROM outreach_journal_meta WHERE key='backfilled'").fetchone():
        c.execute(f'''INSERT INTO outreach_journal_outbox(kind,contact_id,payload)
            SELECT 'contact',s.journal_contact_id,{_snapshot(c, 'staging_leads', 's')} FROM staging_leads s''')
        c.execute("INSERT INTO outreach_journal_meta VALUES ('backfilled','1')")
    # Evidence tables may be introduced later. Backfill each once; imported legacy
    # VALID flags remain flags with absent proof, never manufactured verification.
    for table in EVIDENCE_TABLES:
        key = 'backfilled:' + table
        if table in tables and not c.execute('SELECT 1 FROM outreach_journal_meta WHERE key=?', (key,)).fetchone():
            contact = ('(SELECT journal_contact_id FROM staging_leads WHERE id=s.lead_id)'
                       if table in ('enrichment_targets', 'linkedin_browser_imports', 'local_email_proofs') else 'NULL')
            c.execute(f'''INSERT INTO outreach_journal_outbox(kind,contact_id,payload)
                SELECT '{table}',{contact},{_snapshot(c, table, 's')} FROM {_quote(table)} s''')
            c.execute('INSERT INTO outreach_journal_meta VALUES (?,?)', (key, '1'))


def flush_journal(db_path, *, archive_path=None):
    """Replay committed audit events. Missing/legacy DBs are left unchanged."""
    if db_path == ':memory:':
        return 0
    path = Path(db_path).resolve()
    source = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10)
    try:
        if not source.execute("SELECT 1 FROM sqlite_master WHERE name='outreach_journal_outbox'").fetchone():
            return 0
        source_id = source.execute("SELECT value FROM outreach_journal_meta WHERE key='source_id'").fetchone()[0]
        rows = source.execute('SELECT id,kind,contact_id,payload,recorded_at FROM outreach_journal_outbox WHERE exported=0 ORDER BY id').fetchall()
        if not rows:
            return 0
        target = Path(archive_path) if archive_path is not None else Path(journal_path(db_path))
        if not target.exists():
            try:
                target.touch(mode=0o600, exist_ok=False)
            except FileExistsError:
                pass
        archive = sqlite3.connect(target, timeout=10)
        try:
            with archive:
                archive.execute('''CREATE TABLE IF NOT EXISTS journal_events (
                    source_id TEXT NOT NULL, event_id INTEGER NOT NULL,
                    kind TEXT NOT NULL, contact_id TEXT, payload TEXT NOT NULL,
                    recorded_at TEXT NOT NULL, PRIMARY KEY(source_id,event_id))''')
                archive.execute('CREATE INDEX IF NOT EXISTS journal_contact_events ON journal_events(contact_id)')
                archive.execute('''CREATE TABLE IF NOT EXISTS journal_exclusions (
                    email TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(email,reason))''')
                for event_id, kind, contact_id, payload, recorded_at in rows:
                    archive.execute('INSERT OR IGNORE INTO journal_events VALUES (?,?,?,?,?,?)',
                                    (source_id, event_id, kind, contact_id, payload, recorded_at))
                    if kind == 'email_suppressions':
                        exclusion = json.loads(payload)
                        archive.execute('INSERT OR IGNORE INTO journal_exclusions VALUES (?,?,?)',
                                        (exclusion['email'].strip().lower(), exclusion['reason'], exclusion['created_at']))
            # Deliberately AFTER the archive commit. A crash here only replays.
            with source:
                source.executemany('UPDATE outreach_journal_outbox SET exported=1 WHERE id=?', [(r[0],) for r in rows])
        finally:
            archive.close()
        return len(rows)
    finally:
        source.close()


def read_contact(archive_path, contact_id):
    """Read identity, exact evidence and linked operations without the staging DB."""
    with sqlite3.connect(Path(archive_path).resolve().as_uri() + '?mode=ro', uri=True) as c:
        c.row_factory = sqlite3.Row
        events = [dict(r) for r in c.execute('SELECT * FROM journal_events WHERE contact_id=? ORDER BY rowid', (contact_id,))]
        job_ids = {json.loads(e['payload'])['job_id'] for e in events if e['kind'] == 'enrichment_targets'}
        for row in c.execute("SELECT * FROM journal_events WHERE kind='enrichment_jobs' ORDER BY rowid"):
            if json.loads(row['payload'])['id'] in job_ids:
                events.append(dict(row))
        snapshots = [json.loads(e['payload']) for e in events if e['kind'] in ('contact', 'contact_removed')]
        emails = {str(s.get(f) or '').strip().lower() for s in snapshots
                  for f in ('raw_email', 'original_email', 'clean_email', 'validated_email')}
        exclusions = [dict(r) for r in c.execute('SELECT * FROM journal_exclusions') if r['email'] in emails]
        return {'contact_id': contact_id, 'snapshot': snapshots[-1] if snapshots else None,
                'events': events, 'exclusions': exclusions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db-path', help='Export the committed staging outbox')
    parser.add_argument('--journal-path', help='Read an archive independently of staging')
    parser.add_argument('--contact-id')
    args = parser.parse_args()
    if args.journal_path and args.contact_id:
        result = read_contact(args.journal_path, args.contact_id)
    elif args.db_path:
        result = {'exported': flush_journal(args.db_path), 'journal_path': journal_path(args.db_path)}
    else:
        parser.error('Provide --db-path or --journal-path with --contact-id')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
