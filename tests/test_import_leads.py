import sqlite3
from unittest.mock import patch

import pytest

from scripts.import_leads import import_leads
from scripts.models import NormalizedContact


def test_import_preserves_unknown_email_state_and_refuses_existing_db(tmp_path):
    target = tmp_path / 'new.sqlite'
    contacts = [NormalizedContact(company_name='Acme', source_file='BDD1.xlsx',
                                  raw_email='alice@example.org', email='alice@example.org')]
    with patch('scripts.import_leads.parse_all_sources', return_value=contacts):
        assert import_leads(tmp_path, target, dry_run=True)['inserted'] == 0
        assert not target.exists()
        assert import_leads(tmp_path, target)['inserted'] == 1
        before = target.read_bytes()
        with pytest.raises(FileExistsError):
            import_leads(tmp_path, target)
        assert target.read_bytes() == before
    with sqlite3.connect(target) as conn:
        row = conn.execute('SELECT raw_email,clean_email,neverbounce_status,email_validation_source FROM staging_leads').fetchone()
        assert row == ('alice@example.org', None, 'PENDING', None)


def test_empty_or_invalid_import_does_not_create_db(tmp_path):
    target = tmp_path / 'new.sqlite'
    with patch('scripts.import_leads.parse_all_sources', return_value=[]):
        with pytest.raises(ValueError, match='No contacts'):
            import_leads(tmp_path, target)
        with pytest.raises(ValueError, match='positive'):
            import_leads(tmp_path, target, limit=0)
    assert not target.exists()
