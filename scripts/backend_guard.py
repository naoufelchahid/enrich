"""Reject legacy SAS writers after an explicit compact authority transition."""
import os
import sqlite3
from pathlib import Path


def require_legacy(db_path=None, *, lane=None, journal_path=None):
    if os.getenv('WORKFLOW_BACKEND', 'legacy') == 'compact':
        raise RuntimeError('LEGACY_COMMAND_DISABLED_USE_COMPACT_SERVICE')
    if db_path is None or str(db_path) == ':memory:':
        return
    path = Path(db_path).resolve()
    candidates = [path, Path(str(path) + '.journal.sqlite')]
    if journal_path:
        candidates.append(Path(journal_path).resolve())
    active = os.getenv('WORKFLOW_STATE_PATH')
    configured = os.getenv('WORKFLOW_STAGING_PATH')
    if active and configured and path == Path(configured).resolve():
        candidates.append(Path(active).resolve())
    for candidate in candidates:
        if not candidate.is_file():
            continue
        with sqlite3.connect(candidate.as_uri() + '?mode=ro', uri=True) as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if candidate == path and 'workflow_candidates' in tables and 'staging_leads' not in tables:
                raise RuntimeError('COMPACT_JOURNAL_IS_NOT_A_STAGING_DATABASE')
            if 'workflow_authorities' in tables:
                query = "SELECT 1 FROM workflow_authorities WHERE backend='compact'"
                args = ()
                if lane:
                    query += ' AND lane=?'
                    args = (lane,)
                if c.execute(query, args).fetchone():
                    raise RuntimeError('LEGACY_WRITER_RETIRED:' + (lane or 'all'))
