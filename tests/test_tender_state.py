"""Operation-journal invariants for the independent tender workflow."""

import sqlite3

import pytest

from scripts.tender_acquisition.state import OperationStore


def test_store_has_no_tender_or_document_fields(tmp_path):
    store = OperationStore(tmp_path / 'technical.sqlite')
    store.reserve('CRM_COMPANY_CREATE', 'company_domain', 'example.ma')
    with sqlite3.connect(store.path) as db:
        sql = '\n'.join(row[0] for row in db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"))
    for forbidden in ('tender', 'document', 'pdf', 'participation', 'source_url'):
        assert forbidden not in sql.lower()


def test_unknown_submission_cannot_be_repeated(tmp_path):
    store = OperationStore(tmp_path / 'technical.sqlite')
    op = store.reserve('FULLENRICH', 'person_linkedin', 'https://linkedin.com/in/example')
    assert store.begin(op['id'])['state'] == 'SENDING'
    assert store.mark_interrupted() == 1
    assert store.get('FULLENRICH', 'person_linkedin', 'https://linkedin.com/in/example')['state'] == 'UNKNOWN'
    with pytest.raises(RuntimeError, match='RECONCILIATION'):
        store.begin(op['id'])
    assert store.finish(op['id'], 'SUCCEEDED', remote_id='job-1')['state'] == 'SUCCEEDED'


def test_preview_store_read_does_not_create_database(tmp_path):
    path = tmp_path / 'technical.sqlite'
    assert OperationStore(path).summary() == {'exists': False, 'counts': {}}
    assert not path.exists()


def test_operation_identity_is_stable_and_retry_is_bounded(tmp_path):
    store = OperationStore(tmp_path / 'technical.sqlite')
    first = store.reserve('NO2BOUNCE', 'email', 'A@EXAMPLE.MA')
    second = store.reserve('NO2BOUNCE', 'email', 'A@EXAMPLE.MA')
    assert first['id'] == second['id']
    current = store.begin(first['id'])
    assert current['attempts'] == 1
    current = store.finish(first['id'], 'RETRYABLE')
    assert current['state'] == 'RETRYABLE'
    with pytest.raises(RuntimeError, match='BACKOFF'):
        store.begin(first['id'])
