"""Conservation checks use isolated SQLite files and no external clients."""
import gzip
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.workflow_migration import audit_pair, backup_pair, logical_manifest, readonly, restore_pair


@pytest.fixture
def pair(tmp_path):
    source, journal = tmp_path / 'staging.sqlite', tmp_path / 'journal.sqlite'
    with sqlite3.connect(source) as c:
        c.executescript('''
            CREATE TABLE outreach_journal_meta(key TEXT PRIMARY KEY,value TEXT);
            INSERT INTO outreach_journal_meta VALUES('source_id','source-A');
            CREATE TABLE outreach_journal_outbox(id INTEGER PRIMARY KEY,kind TEXT,contact_id TEXT,payload TEXT,recorded_at TEXT,exported INTEGER);
            CREATE TABLE staging_leads(id INTEGER PRIMARY KEY,journal_contact_id TEXT,outreach_status TEXT);
            INSERT INTO staging_leads VALUES(1,'contact-A','WAITING');
            INSERT INTO outreach_journal_outbox VALUES(1,'contact','contact-A','{"a":null}','2026-11-01',1);
            INSERT INTO outreach_journal_outbox VALUES(2,'contact','contact-A','{"a":1}','2026-11-02',1);
            INSERT INTO outreach_journal_outbox VALUES(3,'contact','contact-A','{"a":2}','2026-11-03',0);
            CREATE TABLE enrichment_jobs(id TEXT PRIMARY KEY,status TEXT);
            INSERT INTO enrichment_jobs VALUES('job-A','SUBMITTED');
        ''')
    with sqlite3.connect(journal) as c:
        c.executescript('''
            CREATE TABLE journal_events(source_id TEXT,event_id INTEGER,kind TEXT,contact_id TEXT,payload TEXT,recorded_at TEXT,PRIMARY KEY(source_id,event_id));
            INSERT INTO journal_events VALUES('source-A',1,'contact','contact-A','{"a":null}','2026-11-01');
            INSERT INTO journal_events VALUES('source-A',2,'contact','contact-A','{"a":1}','2026-11-02');
            CREATE TABLE workflow_drafts(id TEXT PRIMARY KEY,status TEXT);
            INSERT INTO workflow_drafts VALUES('draft-A','APPROVED');
            CREATE TABLE workflow_exclusions(email TEXT PRIMARY KEY,reason TEXT);
            INSERT INTO workflow_exclusions VALUES('a@example.test','bounce');
            CREATE TABLE workflow_artifacts(sha256 TEXT PRIMARY KEY,content_gzip BLOB);
        ''')
        c.execute('INSERT INTO workflow_artifacts VALUES(?,?)', ('hash', gzip.compress(b'exact proof\x00')))
    return source, journal


def exact_content(path):
    with readonly(path) as c:
        return logical_manifest(c, batch_size=1)


def test_audit_exact_and_readonly(pair):
    before = [p.read_bytes() for p in pair]
    report = audit_pair(*pair, batch_size=1)
    assert report['exported_copies_verified']
    assert (report['exported'], report['matched'], report['pending_export']) == (2, 2, 1)
    assert report['inventory']['staging']['identities']['staging_leads']['distinct'] == 1
    assert report['inventory']['staging']['states']['staging_leads'] == {'WAITING': 1}
    assert report['inventory']['journal']['states']['workflow_drafts'] == {'APPROVED': 1}
    assert report['inventory']['staging']['unresolved_operations']['enrichment_jobs'] == {'SUBMITTED': 1}
    assert report['inventory']['journal']['tables']['workflow_artifacts'] == 1
    assert report['inventory']['journal']['artifact_references']['workflow_artifacts']['examples'] == [{'sha256': 'hash'}]
    assert report['free_bytes'] > 0
    assert report['potential_payload_bytes'] > 0 and report['recovered_bytes'] == 0
    assert not report['purge_allowed']
    assert [p.read_bytes() for p in pair] == before


@pytest.mark.parametrize('field,value', [('source_id','other'), ('event_id',22), ('kind','changed'), ('contact_id',None), ('payload','{"a": 1}'), ('recorded_at','yesterday')])
def test_each_identity_and_exact_field_must_match(pair, field, value):
    with sqlite3.connect(pair[1]) as c:
        c.execute(f'UPDATE journal_events SET {field}=? WHERE event_id=2', (value,))
    report = audit_pair(*pair, batch_size=1)
    assert not report['exported_copies_verified']
    assert report['matched'] == 1
    assert report['missing'] + report['divergent'] == 1
    assert not report['purge_allowed']


def test_equal_counts_and_marker_do_not_prove_copy(pair):
    with sqlite3.connect(pair[1]) as c:
        c.execute("UPDATE journal_events SET source_id='wrong'")
    report = audit_pair(*pair)
    assert report['missing'] == 2 and not report['exported_copies_verified']


def test_pause_required_and_fresh_backup_and_exact_restore(pair, tmp_path):
    with pytest.raises(RuntimeError, match='WRITER_PAUSE_REQUIRED'):
        backup_pair(*pair, tmp_path / 'backups')
    before = [exact_content(p) for p in pair]
    first = backup_pair(*pair, tmp_path / 'backups', writes_paused=True)
    with sqlite3.connect(pair[0]) as c:
        c.execute("UPDATE staging_leads SET outreach_status='READY'")
    second = backup_pair(*pair, tmp_path / 'backups', writes_paused=True)
    assert first != second
    out = restore_pair(first, tmp_path / 'restored')
    assert out['verified']
    assert [exact_content(out['files'][key]) for key in ('staging', 'journal')] == before
    newer = restore_pair(second, tmp_path / 'restored-new')
    assert exact_content(newer['files']['staging']) == exact_content(pair[0])
    with pytest.raises(FileExistsError):
        restore_pair(first, tmp_path / 'restored')


def test_locked_writer_blocks_snapshot_and_failed_attempt_retries(pair, tmp_path):
    with sqlite3.connect(pair[1]) as writer:
        writer.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            backup_pair(*pair, tmp_path / 'backups', writes_paused=True)
    assert list((tmp_path / 'backups').iterdir()) == []
    marker = backup_pair(*pair, tmp_path / 'backups', writes_paused=True)
    assert marker.exists()


def test_interrupted_backup_is_not_published_and_can_restart(pair, tmp_path, monkeypatch):
    import scripts.workflow_migration as migration
    original = migration._restore_one
    def interrupt(*args, **kwargs):
        raise OSError('disk failure')
    monkeypatch.setattr(migration, '_restore_one', interrupt)
    with pytest.raises(OSError, match='disk failure'):
        backup_pair(*pair, tmp_path / 'backups', writes_paused=True)
    assert list((tmp_path / 'backups').iterdir()) == []
    monkeypatch.setattr(migration, '_restore_one', original)
    assert backup_pair(*pair, tmp_path / 'backups', writes_paused=True).exists()


def test_corrupt_backup_or_manifest_cannot_restore(pair, tmp_path):
    marker = backup_pair(*pair, tmp_path / 'backups', writes_paused=True)
    manifest = json.loads(marker.read_text())
    manifest['files']['journal']['logical']['tables']['journal_events']['count'] = 999
    marker.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match='CONTENT_MISMATCH'):
        restore_pair(marker, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()
    assert not list(tmp_path.glob('.restore-*'))


def test_missing_db_not_created_and_invalid_batch_rejected(pair, tmp_path):
    missing = tmp_path / 'missing.sqlite'
    with pytest.raises(FileNotFoundError):
        audit_pair(missing, pair[1])
    assert not missing.exists()
    with pytest.raises(ValueError, match='batch_size'):
        audit_pair(*pair, batch_size=0)


def test_wal_sources_include_committed_rows_and_sequence(pair, tmp_path):
    with sqlite3.connect(pair[0]) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE identities(id INTEGER PRIMARY KEY AUTOINCREMENT, value BLOB)')
        writer.execute("INSERT INTO identities(value) VALUES(x'001122')")
        writer.commit()
        writer.execute('DELETE FROM identities')
        writer.commit()
        marker = backup_pair(*pair, tmp_path / 'backups', writes_paused=True)
        restored = restore_pair(marker, tmp_path / 'restored')
        assert exact_content(restored['files']['staging']) == exact_content(pair[0])
        with sqlite3.connect(restored['files']['staging']) as c:
            assert c.execute("SELECT seq FROM sqlite_sequence WHERE name='identities'").fetchone() == (1,)


def test_blob_corruption_rejected_and_restore_can_retry(pair, tmp_path):
    marker = backup_pair(*pair, tmp_path / 'backups', writes_paused=True)
    packed = marker.parent / 'journal.sqlite.gz'
    original = packed.read_bytes()
    packed.write_bytes(gzip.compress(b'wrong sqlite data'))
    with pytest.raises(RuntimeError, match='SHA256_MISMATCH'):
        restore_pair(marker, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()
    packed.write_bytes(original)
    assert restore_pair(marker, tmp_path / 'restored')['verified']
