"""Keep completed run files in the journal, then remove their disk copies."""
import argparse
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

from scripts.workflow_store import WorkflowStore, now


def save_file(store, path):
    path = Path(path)
    raw = path.read_bytes()
    if hasattr(store, 'put_blob'):
        digest = store.put_blob(raw)
        with store.connection() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS workflow_artifact_refs (
                path TEXT NOT NULL,sha256 TEXT NOT NULL,saved_at TEXT NOT NULL,
                PRIMARY KEY(path,sha256))''')
            c.execute('''INSERT INTO workflow_artifact_refs VALUES (?,?,?)
                ON CONFLICT(path,sha256) DO UPDATE SET saved_at=excluded.saved_at''',
                (str(path.resolve()),digest,now()))
        if store.get_blob(digest) != raw:
            raise RuntimeError('ARTIFACT_VERIFICATION_FAILED')
        return digest
    digest = hashlib.sha256(raw).hexdigest()
    compressed = gzip.compress(raw, mtime=0)
    with store.connection() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS workflow_artifacts (
            path TEXT NOT NULL, sha256 TEXT NOT NULL, content_gzip BLOB NOT NULL,
            original_bytes INTEGER NOT NULL, saved_at TEXT NOT NULL,
            PRIMARY KEY(path,sha256))''')
        c.execute('''INSERT INTO workflow_artifacts VALUES (?,?,?,?,?)
            ON CONFLICT(path,sha256) DO UPDATE SET saved_at=excluded.saved_at''',
                  (str(path.resolve()), digest, compressed, len(raw), now()))
    # Verify the committed copy before allowing deletion. Replays are harmless.
    with store.connection() as c:
        saved = c.execute('SELECT content_gzip FROM workflow_artifacts WHERE path=? AND sha256=?',
                          (str(path.resolve()), digest)).fetchone()[0]
    if hashlib.sha256(gzip.decompress(saved)).hexdigest() != digest:
        raise RuntimeError('ARTIFACT_VERIFICATION_FAILED')
    return digest


def confirmed(store, db_path, contact_ids):
    if not contact_ids:
        return False
    compact = db_path is None and hasattr(store, 'candidate')
    if db_path is None and not compact:
        return False
    if not compact:
        with sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True) as source:
            for identity in contact_ids:
                if source.execute('SELECT 1 FROM staging_leads WHERE journal_contact_id=?', (identity,)).fetchone():
                    return False
    with store.connection() as c:
        for identity in contact_ids:
            row = c.execute('SELECT person_id,company_id,admitted_at FROM workflow_contacts WHERE id=?',
                            (identity,)).fetchone()
            if not row or not all(row):
                return False
            if compact:
                candidate = store.candidate(identity)
                if not candidate or candidate['status'] != 'TRANSFERRED':
                    return False
                try:
                    data = candidate['data']
                    control = json.loads(store.get_blob(data['crm_control_uri']))
                    if (control['contact']['journal_contact_id'] != identity
                            or control['person']['id'] != row[0] or control['company']['id'] != row[1]
                            or (control['person'].get('companyId') or (control['person'].get('company') or {}).get('id')) != row[1]):
                        return False
                except (KeyError, TypeError, ValueError, RuntimeError, FileNotFoundError):
                    return False
                if c.execute("""SELECT 1 FROM workflow_tasks WHERE candidate_id=?
                    AND status IN ('PENDING','RUNNING','SUBMITTED','UNKNOWN','NEEDS_REVIEW','WAITING')""", (identity,)).fetchone():
                    return False
            events = c.execute("SELECT payload FROM workflow_events WHERE kind='transfer_verified' AND contact_id=?",
                               (identity,)).fetchall()
            if not any(json.loads(e[0]).get('person_id') == row[0] and
                       json.loads(e[0]).get('company_id') == row[1] for e in events):
                return False
            if c.execute("SELECT 1 FROM workflow_operations WHERE contact_id=? AND status IN ('PENDING','RUNNING','SUBMITTED','SENDING','UNKNOWN','WAITING','NEEDS_REVIEW','RETRY_SCHEDULED')",
                         (identity,)).fetchone():
                return False
    return True


def archived_file(store, path, *, sha256=None):
    """Resolve both compact path references and historical artifact rows."""
    if str(path).startswith('journal://artifacts/') and hasattr(store, 'get_blob'):
        return store.get_blob(str(path))
    absolute = str(Path(path).resolve())
    with store.connection() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = []
        for table in ('workflow_artifact_refs','workflow_artifacts'):
            if table not in tables:
                continue
            query = 'SELECT sha256,saved_at FROM ' + table + ' WHERE path=?'
            values = [absolute]
            if sha256:
                query += ' AND sha256=?'
                values.append(sha256)
            rows.extend((r[0],r[1],table) for r in c.execute(query, values))
        if not rows:
            raise FileNotFoundError(path)
        digest, _, table = max(rows, key=lambda r:r[1])
        if table == 'workflow_artifacts':
            row = c.execute('SELECT content_gzip FROM workflow_artifacts WHERE path=? AND sha256=?', (absolute,digest)).fetchone()
            raw = gzip.decompress(row[0])
        else:
            raw = store.get_blob(digest)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise RuntimeError('ARTIFACT_VERIFICATION_FAILED')
    return raw


def read_file(store, path):
    if str(path).startswith('journal://artifacts/'):
        return archived_file(store, path)
    path = Path(path)
    if path.is_file():
        return path.read_bytes()
    return archived_file(store, path)


def cleanup_completed(store, db_path=None, *, archives_dir, reports_dir, dry_run=False, locked=False):
    if not locked:
        with store.lock():
            return cleanup_completed(store, db_path, archives_dir=archives_dir,
                                     reports_dir=reports_dir, dry_run=dry_run, locked=True)
    removed, waiting = [], []
    # Sources and human exports never become run artifacts, even if a caller
    # accidentally supplies their directory as a cleanup root.
    def protected(path):
        parts = Path(path).resolve().parts
        return 'outputs' in parts or any(parts[i:i+2] == ('bdd','sources') for i in range(len(parts)-1))
    for path in sorted(Path(archives_dir).glob('*.json')):
        if path.is_symlink() or protected(path):
            continue
        if not confirmed(store, db_path, [path.stem]):
            waiting.append(str(path))
            continue
        if not dry_run:
            digest = save_file(store, path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError('FILE_CHANGED_RETAINED')
            store.event('file_cleanup', path.stem, {'path':str(path.resolve()),'sha256':digest})
            path.unlink()
        removed.append(str(path))
    root = Path(reports_dir)
    directories = {p.parent for name in ('run.json','selection.json') for p in root.rglob(name)}
    for directory in sorted(directories,key=lambda p:(-len(p.parts),str(p))):
        if not directory.exists():
            continue
        if not directory.is_dir() or directory.is_symlink():
            continue
        if protected(directory):
            waiting.append(str(directory))
            continue
        manifest = directory / 'selection.json'
        if not manifest.exists():
            manifest = directory / 'run.json'
        if not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text())
            identities = [r['journal_contact_id'] for r in data['contacts']]
        except (OSError, ValueError, KeyError, TypeError):
            waiting.append(str(directory))
            continue
        if not confirmed(store, db_path, identities):
            waiting.append(str(directory))
            continue
        # Only run artifacts: leave unexpected files (databases, secrets, links) for review.
        paths = list(directory.rglob('*'))
        if any(p.is_symlink() or (p.is_file() and p.suffix not in
               {'.json','.jsonl','.md','.csv','.log','.txt','.png','.ndjson'}) for p in paths):
            waiting.append(str(directory))
            continue
        files = [p for p in paths if p.is_file()]
        if not dry_run:
            digests = {str(p.resolve()):save_file(store,p) for p in files}
            if any(hashlib.sha256(p.read_bytes()).hexdigest() != digests[str(p.resolve())] for p in files):
                raise RuntimeError('FILE_CHANGED_RETAINED')
            if db_path is not None:
                with sqlite3.connect(db_path) as c:
                    if c.execute("SELECT 1 FROM sqlite_master WHERE name='linkedin_browser_queue'").fetchone():
                        for original,digest in digests.items():
                            c.execute('UPDATE linkedin_browser_queue SET result_path=? WHERE result_path=?',
                                      ('journal://artifacts/'+digest,original))
            store.event('run_cleanup', None, {'run':directory.name,'contacts':identities,'files':digests})
            # Archive all files before deleting any, including the completion manifest.
            for p in sorted(files, key=lambda p:p == manifest):
                p.unlink()
            for p in sorted((p for p in paths if p.is_dir()), key=lambda p:len(p.parts), reverse=True):
                p.rmdir()
            directory.rmdir()
        removed.append(str(directory))
    return {'dry_run':dry_run,'removed':removed,'waiting':waiting,
            'evidence':'workflow_blobs' if hasattr(store,'put_blob') else 'workflow_artifacts'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db-path', default='bdd/workflow/staging.sqlite')
    parser.add_argument('--journal-path')
    parser.add_argument('--backend', choices=('legacy','compact'), default='legacy')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--restore-path', help='Original absolute artifact path recorded in the journal')
    parser.add_argument('--sha256', help='Restore a specific historical version')
    parser.add_argument('--output', help='Destination of the restored file')
    args = parser.parse_args()
    if args.backend == 'compact':
        if not args.journal_path:
            parser.error('--journal-path is required with --backend compact')
        from scripts.compact_store import CompactStore
        store = CompactStore(args.journal_path)
    else:
        store = WorkflowStore(args.journal_path or str(Path(args.db_path).resolve())+'.journal.sqlite')
    if args.restore_path:
        if not args.output:
            parser.error('--output is required to restore a file')
        try:
            raw = archived_file(store, args.restore_path, sha256=args.sha256)
        except FileNotFoundError:
            parser.error('Artifact not found')
        target = Path(args.output)
        with target.open('xb') as handle:
            handle.write(raw)
        target.chmod(0o600)
        result = {'restored':str(target),'sha256':hashlib.sha256(raw).hexdigest()}
    else:
        root = Path(__file__).resolve().parents[1]
        db_path = None if args.backend == 'compact' else args.db_path
        base = Path(args.journal_path).parent if args.backend == 'compact' else Path(args.db_path).parent
        result = cleanup_completed(store,db_path,archives_dir=base/'archives',
                                   reports_dir=root/'reports',dry_run=args.dry_run)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
