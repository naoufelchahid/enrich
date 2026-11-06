"""Import a reviewed local No2Bounce proof into an isolated pilot, without network."""
import argparse
import csv
import fcntl
import hashlib
import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from scripts.direct_enrichment import DirectEnrichment, encoded, now
from scripts.email_hygiene import fresh, valid_email


def digest(data):
    return hashlib.sha256(data).hexdigest()


def import_proof(db_path, manifest_path, *, dry_run=False):
    from scripts.backend_guard import require_legacy
    require_legacy(db_path)
    manifest = json.loads(Path(manifest_path).read_text())
    raw = {}
    for kind in ('response', 'csv'):
        item = manifest[kind]
        data = (Path(manifest_path).parent / item['file']).read_bytes()
        if digest(data) != item['sha256']:
            raise ValueError('Evidence hash mismatch')
        raw[kind] = data
    body = json.loads(raw['response'])
    observed = manifest['observed_at']
    if datetime.fromisoformat(observed).tzinfo is None or not fresh(observed):
        raise ValueError('Evidence must have a recent, explicit timezone')
    if (body.get('trackingId') != manifest['tracking_id'] or body.get('overallStatus') != 'Completed'
            or type(body.get('percent')) is not int or body['percent'] != 100):
        raise ValueError('Wrong job or incomplete result')
    reader = csv.DictReader(io.StringIO(raw['csv'].decode('utf-8-sig')), strict=True)
    if reader.fieldnames != ['email', 'finalScore', 'finalScoreValue', 'catchall']:
        raise ValueError('Unexpected CSV columns')
    rows = list(reader)
    if type(body.get('totalRecord')) is not int or len(rows) != body['totalRecord']:
        raise ValueError('Wrong record count')
    unique = {}
    for row in rows:
        if set(row) != set(reader.fieldnames) or any(v is None for v in row.values()):
            raise ValueError('Malformed CSV row')
        email = row['email'].strip().lower()
        if not valid_email(email):
            raise ValueError('Invalid address in evidence')
        normalized = dict(row, email=email)
        if email in unique and unique[email] != normalized:
            raise ValueError('Conflicting duplicate evidence')
        unique[email] = normalized
    proof_id = digest(encoded(manifest).encode())
    path = Path(db_path).resolve()
    # No migration, implicit DB creation or external service is involved.
    with open(str(path) + '.enrichment.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        c = sqlite3.connect(path.as_uri() + ('?mode=ro' if dry_run else '?mode=rw'), uri=True)
        c.row_factory = sqlite3.Row
        try:
            c.execute('BEGIN' if dry_run else 'BEGIN IMMEDIATE')
            caps = dict(c.execute('SELECT provider,total_items FROM enrichment_limits'))
            if caps != {'no2bounce': 3, 'fullenrich': 3, 'harvest': 0}:
                raise ValueError('Reviewed pilot limits required')
            leads = [dict(r) for r in c.execute('SELECT * FROM staging_leads ORDER BY id')]
            if not 1 <= len(leads) <= 3:
                raise ValueError('Only isolated pilot supported')
            exists = c.execute("SELECT 1 FROM sqlite_master WHERE name='local_email_proofs' AND type='table'").fetchone()
            pending, summary = [], []
            for lead in leads:
                email = (lead['raw_email'] or '').strip().lower()
                if email not in unique or lead['outreach_status'] != 'PENDING' or lead['crm_person_id']:
                    raise ValueError('Pilot address absent or outreach already started')
                if c.execute('SELECT 1 FROM email_suppressions WHERE email=?', (email,)).fetchone():
                    raise ValueError('Suppressed address')
                prior = c.execute('SELECT * FROM local_email_proofs WHERE lead_id=?', (lead['id'],)).fetchone() if exists else None
                if prior:
                    if prior['proof_id'] != proof_id or prior['email'] != email:
                        raise ValueError('Different proof or changed address requires review')
                    summary.append({'id': lead['id'], 'status': lead['email_check_status'], 'already_imported': True})
                    continue
                if lead['email_check_status'] != 'PENDING' or lead['email_validated_at']:
                    raise ValueError('Existing verification must not be overwritten')
                row = unique[email]
                verdict = {('Deliverable', 'false'): 'VALID', ('UnDeliverable', 'false'): 'INVALID'}.get(
                    (row['finalScoreValue'], row['catchall']), 'WAITING')
                updates = DirectEnrichment._apply_verification({'email': email}, {'email': email, 'verdict': verdict}, observed)
                updates['email_check_reason'] = 'IMPORTED_EXISTING_PROOF:' + row['finalScoreValue']
                pending.append((lead['id'], email, row, updates))
                summary.append({'id': lead['id'], 'status': verdict, 'already_imported': False})
            if not dry_run and pending:
                c.execute('''CREATE TABLE IF NOT EXISTS local_email_proofs (
                    lead_id INTEGER PRIMARY KEY, email TEXT NOT NULL, proof_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL, imported_at TEXT NOT NULL,
                    provenance TEXT NOT NULL, result TEXT NOT NULL)''')
                from scripts.outreach_journal import install_outbox
                install_outbox(c)
                for lead_id, email, row, updates in pending:
                    c.execute('INSERT INTO local_email_proofs VALUES(?,?,?,?,?,?,?)',
                              (lead_id, email, proof_id, observed, now(), encoded(manifest), encoded(row)))
                    c.execute('UPDATE staging_leads SET ' + ','.join(k + '=?' for k in updates) + ' WHERE id=?',
                              [*updates.values(), lead_id])
                c.commit()
                from scripts.outreach_journal import flush_journal
                flush_journal(db_path)
            else:
                c.rollback()
            return {'dry_run': dry_run, 'contacts': summary, 'new_paid_calls': 0,
                    'observed_at': observed, 'proof_id': proof_id}
        finally:
            c.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db-path', required=True)
    p.add_argument('--manifest', required=True)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    print(json.dumps(import_proof(a.db_path, a.manifest, dry_run=a.dry_run), indent=2))


if __name__ == '__main__':
    main()
