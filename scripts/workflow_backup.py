"""One verified compressed pre-migration SQLite snapshot per database and reason."""
import gzip
import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ensure_backup(connection, db_path, reason):
    source = Path(db_path).resolve()
    directory = ROOT/'backups' if ROOT in source.parents else source.parent/'backups'
    # Reuse the already classified pre-migration backup instead of copying the SAS again.
    existing = list(directory.glob('**/'+source.name+'.before-'+reason+'.bak.gz'))
    if existing:
        return existing[0]
    target = directory/'migrations'/(source.name+'.before-'+reason+'.bak.gz')
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor, name = tempfile.mkstemp(dir=target.parent,suffix='.sqlite.tmp')
    os.close(descriptor)
    temporary = Path(name)
    packed = Path(name+'.gz')
    try:
        with sqlite3.connect(temporary) as destination:
            connection.backup(destination)
            if destination.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise RuntimeError('BACKUP_SQLITE_CHECK_FAILED')
        digest = hashlib.sha256()
        with temporary.open('rb') as src, gzip.open(packed,'wb',compresslevel=1) as dst:
            for chunk in iter(lambda:src.read(2*1024*1024),b''):
                digest.update(chunk)
                dst.write(chunk)
        verify = hashlib.sha256()
        with gzip.open(packed,'rb') as src:
            for chunk in iter(lambda:src.read(2*1024*1024),b''):
                verify.update(chunk)
        if verify.digest() != digest.digest():
            raise RuntimeError('BACKUP_SHA256_CHECK_FAILED')
        packed.chmod(0o600)
        os.replace(packed,target)
        return target
    finally:
        temporary.unlink(missing_ok=True)
        packed.unlink(missing_ok=True)
