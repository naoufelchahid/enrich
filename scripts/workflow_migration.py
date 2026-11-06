"""Read-only conservation audit and fresh, verified paired SQLite backups.

This module never purges an outbox or switches an active database. Backups require
an explicit writer-pause acknowledgement and acquire both SQLite write locks
before taking either snapshot. A published manifest is the completion marker;
interrupted preparations can be retried as a new bundle without reusing stale data.
"""
import argparse
import base64
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path


def quote(name):
    return '"' + name.replace('"', '""') + '"'


@contextmanager
def readonly(path):
    path = Path(path).resolve(strict=True)
    c = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)
    try:
        # Connection-local, bounded validation cache; no database setting changes.
        c.execute('PRAGMA cache_size=-65536')
        c.execute('PRAGMA query_only=ON')
        c.execute('BEGIN')
        yield c
    finally:
        c.close()


def tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def columns(c, table):
    return {r[1] for r in c.execute('PRAGMA table_info(' + quote(table) + ')')}


def batches(cursor, batch_size):
    if batch_size < 1:
        raise ValueError('batch_size must be positive')
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            return
        yield rows


def _counts(c, table, field):
    if table not in tables(c) or field not in columns(c, table):
        return {}
    return {str(k): n for k, n in c.execute(
        f'SELECT {quote(field)},count(*) FROM {quote(table)} GROUP BY {quote(field)}')}


def _inventory(c):
    available = tables(c)
    counts = {t: c.execute('SELECT count(*) FROM ' + quote(t)).fetchone()[0]
              for t in sorted(available)}
    result = {'schema_version': c.execute('PRAGMA user_version').fetchone()[0],
              'tables': counts, 'states': {}, 'identities': {}}
    for table, field in [('staging_leads', 'outreach_status'), ('workflow_contacts', 'status'),
                         ('workflow_candidates', 'status'), ('workflow_tasks', 'status'),
                         ('enrichment_jobs', 'status'), ('workflow_operations', 'status'),
                         ('workflow_drafts', 'status'), ('workflow_sends', 'state')]:
        if table in available:
            result['states'][table] = _counts(c, table, field)
    if 'staging_leads' in available:
        result['staging_facets'] = {field: _counts(c, 'staging_leads', field)
            for field in ('enrichment_status', 'email_check_status', 'linkedin_search_status',
                          'outreach_status', 'workflow_wait_reason')
            if field in columns(c, 'staging_leads')}
    for table, field in [('staging_leads', 'journal_contact_id'), ('workflow_contacts', 'id'),
                         ('workflow_candidates', 'id')]:
        if table in available and field in columns(c, table):
            result['identities'][table] = dict(zip(('total', 'distinct', 'missing'), c.execute(
                f'SELECT count(*),count(DISTINCT {quote(field)}),sum({quote(field)} IS NULL) FROM {quote(table)}').fetchone()))
            digest = hashlib.sha256()
            for rows in batches(c.execute(f'SELECT {quote(field)} FROM {quote(table)} ORDER BY {quote(field)}'), 256):
                for row in rows:
                    data = json.dumps(row[0], ensure_ascii=False).encode()
                    digest.update(len(data).to_bytes(8, 'big'))
                    digest.update(data)
            result['identities'][table]['sha256'] = digest.hexdigest()
    # All statuses remain visible; these explicit counts make unresolved work clear.
    result['unresolved_operations'] = {}
    for table in ('enrichment_jobs', 'workflow_operations', 'workflow_sends'):
        field = 'state' if table == 'workflow_sends' else 'status'
        states = _counts(c, table, field)
        result['unresolved_operations'][table] = {k: v for k, v in states.items()
            if any(word in k.upper() for word in ('SUBMIT', 'UNKNOWN', 'UNCERTAIN', 'PENDING', 'INFLIGHT',
                'SENDING', 'NEEDS_REVIEW', 'WAITING', 'RETRY', 'RUNNING'))}
    result['artifact_references'] = {}
    for table in ('workflow_artifacts', 'workflow_candidate_evidence'):
        if table not in available:
            continue
        fields = [name for name in ('path', 'uri', 'sha256', 'original_bytes', 'saved_at')
                  if name in columns(c, table)]
        if fields:
            digest, examples = hashlib.sha256(), []
            order = ','.join(map(quote, fields))
            for rows in batches(c.execute('SELECT ' + order + ' FROM ' + quote(table) + ' ORDER BY ' + order), 256):
                for row in rows:
                    data = json.dumps(list(row), ensure_ascii=False, separators=(',', ':')).encode()
                    digest.update(len(data).to_bytes(8, 'big'))
                    digest.update(data)
                    if len(examples) < 20:
                        examples.append(dict(zip(fields, row)))
            result['artifact_references'][table] = {'count': counts[table], 'sha256': digest.hexdigest(),
                                                    'examples': examples, 'sample_limit': 20}
    return result


def audit_pair(staging, journal, *, batch_size=256):
    """Compare every exported event exactly. Read-only; no purge is performed."""
    staging, journal = Path(staging).resolve(), Path(journal).resolve()
    with readonly(staging) as source, readonly(journal) as archive:
        result = {'format': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                  'paths': {'staging': str(staging), 'journal': str(journal)},
                  'inventory': {'staging': _inventory(source), 'journal': _inventory(archive)},
                  'free_bytes': shutil.disk_usage(staging.parent).free,
                  'cross_database_writer_pause_verified': False,
                  'physical_bytes': {'staging': staging.stat().st_size, 'journal': journal.stat().st_size},
                  'exported': 0, 'matched': 0, 'missing': 0, 'divergent': 0,
                  'mismatch_examples': [], 'potential_payload_bytes': 0, 'recovered_bytes': 0}
        source_id = None
        if 'outreach_journal_meta' in tables(source):
            row = source.execute("SELECT value FROM outreach_journal_meta WHERE key='source_id'").fetchone()
            source_id = row[0] if row else None
        result['source_id'] = source_id
        outbox = 'outreach_journal_outbox' in tables(source)
        has_archive = 'journal_events' in tables(archive)
        if outbox:
            result['pending_export'] = source.execute('SELECT count(*) FROM outreach_journal_outbox WHERE exported=0').fetchone()[0]
            cursor = source.execute('SELECT id,kind,contact_id,payload,recorded_at FROM outreach_journal_outbox WHERE exported=1 ORDER BY id')
            for rows in batches(cursor, batch_size):
                for event_id, kind, contact_id, payload, date in rows:
                    result['exported'] += 1
                    archived = archive.execute('SELECT kind,contact_id,payload,recorded_at FROM journal_events WHERE source_id=? AND event_id=?',
                        (source_id, event_id)).fetchone() if has_archive and source_id else None
                    if archived is None:
                        state = 'missing'
                    elif tuple(archived) != (kind, contact_id, payload, date):
                        state = 'divergent'
                    else:
                        state = 'matched'
                        result['potential_payload_bytes'] += len(payload.encode('utf-8'))
                    result[state] += 1
                    if state != 'matched' and len(result['mismatch_examples']) < 20:
                        result['mismatch_examples'].append({'event_id': event_id, 'reason': state})
        else:
            result['pending_export'] = 0
        result['exported_copies_verified'] = bool(outbox and source_id and has_archive
            and not result['missing'] and not result['divergent'])
        result['purge_allowed'] = False  # Separate migration/pause/conservation gates still required.
        return result


def _cell(value):
    return {'blob': base64.b64encode(value).decode()} if isinstance(value, bytes) else value


def logical_manifest(c, *, batch_size=256):
    """Hash exact typed cells and schema in bounded batches, independent of pages."""
    schema = [tuple(row) for row in c.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
    result = {'schema': schema, 'user_version': c.execute('PRAGMA user_version').fetchone()[0],
              'application_id': c.execute('PRAGMA application_id').fetchone()[0], 'tables': {}}
    for table, in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        info = list(c.execute('PRAGMA table_info(' + quote(table) + ')'))
        pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
        order = ','.join(map(quote, pk)) if pk else 'rowid'
        digest, count = hashlib.sha256(), 0
        for rows in batches(c.execute('SELECT * FROM ' + quote(table) + ' ORDER BY ' + order), batch_size):
            for row in rows:
                data = json.dumps([_cell(v) for v in row], ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
                digest.update(len(data).to_bytes(8, 'big'))
                digest.update(data)
                count += 1
        result['tables'][table] = {'count': count, 'sha256': digest.hexdigest()}
    # Normalize tuples for equality with the JSON manifest restored later.
    return json.loads(json.dumps(result))


def _file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _restore_one(packed, target, expected):
    if target.exists():
        raise FileExistsError(str(target))
    try:
        with gzip.open(packed, 'rb') as src, target.open('xb') as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        target.chmod(0o600)
        if _file_hash(target) != expected['file_sha256']:
            raise RuntimeError('BACKUP_SHA256_MISMATCH')
        with readonly(target) as c:
            if c.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise RuntimeError('BACKUP_INTEGRITY_FAILED')
            if logical_manifest(c) != expected['logical']:
                raise RuntimeError('BACKUP_CONTENT_MISMATCH')
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def backup_pair(staging, journal, directory, *, writes_paused=False):
    """Always create a fresh bundle while holding both databases' write locks."""
    if not writes_paused:
        raise RuntimeError('WRITER_PAUSE_REQUIRED')
    sources = {'staging': Path(staging).resolve(strict=True), 'journal': Path(journal).resolve(strict=True)}
    if sources['staging'] == sources['journal']:
        raise ValueError('Two distinct databases required')
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    bundle = directory / ('migration-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex)
    bundle.mkdir(mode=0o700)
    manifest = {'format': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'files': {}}
    try:
        with ExitStack() as stack:
            # Locks are acquired before either read snapshot. No writer can change
            # either source until both snapshots and their verification complete.
            for path in sorted(sources.values()):
                c = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=0)
                stack.callback(c.close)
                c.execute('BEGIN IMMEDIATE')
            for key, source in sources.items():
                with readonly(source) as original:
                    logical = logical_manifest(original)
                    with tempfile.TemporaryDirectory(dir=bundle) as temporary:
                        raw = Path(temporary) / (key + '.sqlite')
                        with sqlite3.connect(raw) as destination:
                            original.backup(destination)
                        packed = bundle / (key + '.sqlite.gz')
                        with raw.open('rb') as src, gzip.open(packed, 'wb', compresslevel=1) as dst:
                            shutil.copyfileobj(src, dst, length=1024 * 1024)
                        packed.chmod(0o600)
                        item = {'source': str(source), 'archive': packed.name,
                                'file_sha256': _file_hash(raw), 'logical': logical}
                        _restore_one(packed, Path(temporary) / 'verified.sqlite', item)
                        manifest['files'][key] = item
            text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
            marker = bundle / 'manifest.json'
            marker.write_text(text + '\n')
            marker.chmod(0o600)
        return marker
    except BaseException:
        shutil.rmtree(bundle)
        raise


def restore_pair(manifest_path, directory):
    """Restore both files as a new atomic directory; never overwrite an old DB."""
    manifest_path = Path(manifest_path).resolve(strict=True)
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('format') != 1 or set(manifest.get('files', {})) != {'staging', 'journal'}:
        raise ValueError('INVALID_BACKUP_MANIFEST')
    target = Path(directory).resolve()
    if target.exists():
        raise FileExistsError(str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.restore-', dir=target.parent))
    try:
        for key, item in manifest['files'].items():
            archive = item['archive']
            if Path(archive).name != archive:
                raise ValueError('INVALID_ARCHIVE_PATH')
            _restore_one(manifest_path.parent / archive, temporary / (key + '.sqlite'), item)
        if target.exists():
            raise FileExistsError(str(target))
        os.rename(temporary, target)
        return {'directory': str(target), 'verified': True, 'files': {k: str(target / (k + '.sqlite')) for k in manifest['files']}}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('audit', 'backup'):
        p = sub.add_parser(name)
        p.add_argument('--staging', required=True)
        p.add_argument('--journal', required=True)
        if name == 'backup':
            p.add_argument('--directory', required=True)
            p.add_argument('--writes-paused', action='store_true')
    p = sub.add_parser('restore')
    p.add_argument('--manifest', required=True)
    p.add_argument('--directory', required=True)
    args = parser.parse_args()
    if args.action == 'audit':
        result = audit_pair(args.staging, args.journal)
    elif args.action == 'backup':
        result = {'manifest': str(backup_pair(args.staging, args.journal, args.directory, writes_paused=args.writes_paused))}
    else:
        result = restore_pair(args.manifest, args.directory)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
