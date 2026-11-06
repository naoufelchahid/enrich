import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.prepare_pilot import prepare_pilot
from scripts.import_verification_proof import import_proof
from scripts.direct_enrichment import verified


@pytest.fixture
def pilot(tmp_path):
    contacts = [{'first_name': str(i), 'last_name': 'Example', 'company_name': 'Acme',
                 'original_email': f'person{i}@example.org'} for i in range(3)]
    selection = tmp_path / 'selection.json'; selection.write_text(json.dumps({'contacts': contacts}))
    db = tmp_path / 'pilot.sqlite'; prepare_pilot(selection, db)
    csv = tmp_path / 'proof.csv'
    csv.write_text('email,finalScore,finalScoreValue,catchall\nperson0@example.org,99,Deliverable/AcceptAll,true\nperson0@example.org,99,Deliverable/AcceptAll,true\nperson1@example.org,0,UnDeliverable/AcceptAll,true\nperson2@example.org,99,Deliverable,false\n')
    response = tmp_path / 'proof.json'
    response.write_text(json.dumps({'trackingId': 'job', 'overallStatus': 'Completed', 'percent': 100, 'totalRecord': 4}))
    m = {'tracking_id': 'job', 'observed_at': datetime.now(timezone.utc).isoformat()}
    for k, p in [('response', response), ('csv', csv)]:
        m[k] = {'file': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    manifest = tmp_path / 'manifest.json'; manifest.write_text(json.dumps(m))
    return db, manifest


def test_import_atomic_provenance_and_repeat(pilot):
    db, manifest = pilot
    before = db.read_bytes()
    assert [r['status'] for r in import_proof(db, manifest, dry_run=True)['contacts']] == ['WAITING', 'WAITING', 'VALID']
    assert db.read_bytes() == before
    result = import_proof(db, manifest)
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute('SELECT * FROM staging_leads ORDER BY id')]
        assert [verified(r) for r in rows] == [False, False, True]
        assert all(r['raw_email'] == r['original_email'] for r in rows)
        assert all(r['email_validated_at'] == result['observed_at'] for r in rows)
        assert c.execute('SELECT count(*) FROM local_email_proofs').fetchone()[0] == 3
        assert c.execute('SELECT count(*) FROM enrichment_jobs').fetchone()[0] == 0
    after = db.read_bytes()
    assert all(r['already_imported'] for r in import_proof(db, manifest)['contacts'])
    assert db.read_bytes() == after


@pytest.mark.parametrize('failure', ['hash', 'conflict', 'missing', 'expired', 'suppressed', 'nonpilot'])
def test_invalid_proof_leaves_entire_database_unchanged(pilot, failure):
    db, manifest = pilot
    m = json.loads(manifest.read_text())
    if failure in ('hash', 'conflict', 'missing'):
        p = manifest.parent / m['csv']['file']
        s = p.read_text()
        if failure == 'missing': s = s.replace('person2@example.org', 'other@example.org')
        else: s = s.replace('person0@example.org,99,Deliverable/AcceptAll,true', 'person0@example.org,0,UnDeliverable,false', 1)
        p.write_text(s)
        if failure != 'hash': m['csv']['sha256'] = hashlib.sha256(p.read_bytes()).hexdigest()
    if failure == 'expired': m['observed_at'] = '2000-01-01T00:00:00+00:00'
    manifest.write_text(json.dumps(m))
    with sqlite3.connect(db) as c:
        if failure == 'nonpilot': c.execute('DELETE FROM enrichment_limits')
        if failure == 'suppressed': c.execute("INSERT INTO email_suppressions(email,reason) VALUES('person2@example.org','unsubscribe')")
    before = db.read_bytes()
    with pytest.raises(ValueError): import_proof(db, manifest)
    assert db.read_bytes() == before
