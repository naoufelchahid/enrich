import json
import sqlite3
from unittest.mock import MagicMock

import pytest
from tests.helpers import set_test_validation

from scripts.direct_enrichment import DirectEnrichment
from scripts.outreach_dispatcher import OutreachDispatcher
from scripts.staging_db import init_staging_db, insert_staging_leads
from scripts.thunderbird_client import ThunderbirdClient


@pytest.fixture
def dispatcher(tmp_path):
    db = str(tmp_path / 'staging.sqlite')
    init_staging_db(db).close()
    insert_staging_leads(db, [{'source_file': 'test', 'first_name': 'Alice', 'last_name': 'Example',
                             'company_name': 'Example', 'raw_email': 'alice@example.org',
                             'job_title': 'DG', 'civility': 'Madame'}])
    set_test_validation(db, 1, 'VALID', 'alice@example.org')
    crm = MagicMock()
    crm.upsert_person.return_value = 'person-1'
    return OutreachDispatcher(db_path=db, queue_file=str(tmp_path / 'queue.json'),
                              circuit_breaker_file=str(tmp_path / 'breaker.json'),
                              twenty_client=crm, mcp_mail=ThunderbirdClient(dry_run=True),
                              enrichment_client=DirectEnrichment(fullenrich_daily_credits=0, harvest_daily_requests=0))


def test_crm_failure_never_counts_or_queues(dispatcher):
    dispatcher.twenty_client.upsert_person.side_effect = RuntimeError('CRM unavailable')
    result = dispatcher.prepare_daily_batch()
    assert result['synced'] == 0
    assert result['queued'] == 0


def test_repeated_prepare_preserves_queue_and_deduplicates(dispatcher):
    dispatcher.prepare_daily_batch()
    dispatcher.prepare_daily_batch()
    assert len(dispatcher.get_pending_queue()) == 1
    dispatcher.twenty_client.upsert_person.assert_called_once()


def test_changed_address_is_not_sent_from_stale_queue(dispatcher):
    dispatcher.prepare_daily_batch()
    with sqlite3.connect(dispatcher.db_path) as c:
        c.execute("UPDATE staging_leads SET raw_email='new@example.org'")
    assert dispatcher.send_next_candidate()['status'] == 'LEGACY_SEND_DISABLED_USE_APPROVED_WORKFLOW'
    assert dispatcher.mcp_mail._sent_emails == []


def test_unknown_mail_submission_is_not_resent(dispatcher):
    dispatcher.prepare_daily_batch()
    dispatcher.mcp_mail.send_email = MagicMock(side_effect=TimeoutError())
    assert dispatcher.send_next_candidate()['status'] == 'LEGACY_SEND_DISABLED_USE_APPROVED_WORKFLOW'
    dispatcher.send_next_candidate()
    dispatcher.prepare_daily_batch()
    dispatcher.mcp_mail.send_email.assert_not_called()
    assert dispatcher.get_pending_queue()


def test_preview_never_modifies_db_or_queue(dispatcher):
    dispatcher.prepare_daily_batch()
    before_db = open(dispatcher.db_path, 'rb').read()
    before_queue = open(dispatcher.queue_file, 'rb').read()
    dispatcher.dry_run = True
    dispatcher.enrichment_client.dry_run = True
    assert dispatcher.prepare_daily_batch()['dry_run']
    assert dispatcher.send_next_candidate()['status'] == 'LEGACY_SEND_DISABLED_USE_APPROVED_WORKFLOW'
    dispatcher.review_inbox_and_sync()
    assert open(dispatcher.db_path, 'rb').read() == before_db
    assert open(dispatcher.queue_file, 'rb').read() == before_queue
    assert not dispatcher.mcp_mail._sent_emails


def test_duplicate_email_is_queued_once(dispatcher):
    insert_staging_leads(dispatcher.db_path, [{'source_file': 'duplicate', 'raw_email': 'alice@example.org', 'company_name': 'Example'}])
    set_test_validation(dispatcher.db_path, 2, 'VALID', 'alice@example.org')
    assert dispatcher.prepare_daily_batch()['queued'] == 1


def test_unsubscribe_blocks_already_queued_contact(dispatcher):
    dispatcher.prepare_daily_batch()
    dispatcher.mcp_mail._mock_inbox.append({'id': 'unsub', 'from': 'Alice <alice@example.org>',
                                           'subject': 'Stop', 'body': 'Ne plus me contacter', 'folder': 'INBOX'})
    assert dispatcher.send_next_candidate()['status'] == 'LEGACY_SEND_DISABLED_USE_APPROVED_WORKFLOW'
    assert not dispatcher.mcp_mail._sent_emails


def test_legacy_schema_migration_backs_up_without_inventing_validation(tmp_path):
    db = str(tmp_path / 'legacy.sqlite')
    init_staging_db(db).close()
    # Model a DB created before validation provenance was introduced.
    with sqlite3.connect(db) as c:
        c.execute('DROP VIEW IF EXISTS vue_contacts')
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'journal_contact_%'").fetchall():
            c.execute('DROP TRIGGER "' + name + '"')
        c.execute('ALTER TABLE staging_leads DROP COLUMN email_validation_source')
    init_staging_db(db).close()
    from pathlib import Path
    backup = Path(db + '.before-direct-enrichment.bak')
    assert backup.exists()
    before = backup.read_bytes()
    init_staging_db(db).close()
    assert backup.read_bytes() == before


def test_inaccessible_inbox_blocks_send_and_preserves_queue(dispatcher):
    dispatcher.prepare_daily_batch()
    before = open(dispatcher.queue_file, 'rb').read()
    dispatcher.mcp_mail.list_inbox_messages = MagicMock(side_effect=RuntimeError('offline'))
    assert dispatcher.send_next_candidate()['status'] == 'LEGACY_SEND_DISABLED_USE_APPROVED_WORKFLOW'
    assert open(dispatcher.queue_file, 'rb').read() == before
    assert not dispatcher.mcp_mail._sent_emails


def test_bounce_exclusion_survives_breaker_reset_and_reimport(dispatcher):
    dispatcher.prepare_daily_batch()
    dispatcher.mcp_mail._mock_inbox.append({'id': 'bounce', 'from': 'MAILER-DAEMON@example.org',
        'subject': 'Delivery Failure', 'body': '550 No such user: alice@example.org', 'folder': 'INBOX'})
    dispatcher.review_inbox_and_sync()
    assert dispatcher.send_next_candidate()['status'] == 'LEGACY_SEND_DISABLED_USE_APPROVED_WORKFLOW'
    dispatcher.circuit_breaker.reset()
    assert dispatcher.send_next_candidate()['status'] == 'LEGACY_SEND_DISABLED_USE_APPROVED_WORKFLOW'
    insert_staging_leads(dispatcher.db_path, [{'source_file': 'reimport', 'raw_email': 'alice@example.org', 'company_name': 'Acme'}])
    set_test_validation(dispatcher.db_path, 2, 'VALID', 'alice@example.org')
    result=dispatcher.prepare_daily_batch()
    assert result['added']==0
    with sqlite3.connect(dispatcher.db_path) as c:
        assert c.execute('SELECT email FROM email_suppressions').fetchone()[0]=='alice@example.org'
    assert not dispatcher.mcp_mail._sent_emails


def test_unsubscribe_persisted_even_when_archive_fails(dispatcher):
    dispatcher.prepare_daily_batch()
    dispatcher.mcp_mail._mock_inbox.append({'id': 'unsub', 'from': 'alice@example.org',
        'subject': 'Stop', 'body': 'Ne plus me contacter', 'folder': 'INBOX'})
    dispatcher.mcp_mail.move_message = MagicMock(side_effect=RuntimeError('archive failed'))
    dispatcher.review_inbox_and_sync()
    with sqlite3.connect(dispatcher.db_path) as c:
        assert c.execute('SELECT email FROM email_suppressions').fetchone()[0] == 'alice@example.org'
    assert dispatcher.circuit_breaker.is_tripped()


def test_expired_validation_blocks_queued_send(dispatcher):
    dispatcher.prepare_daily_batch()
    with sqlite3.connect(dispatcher.db_path) as c:
        c.execute("UPDATE staging_leads SET email_validated_at='2020-01-01T00:00:00+00:00'")
    assert dispatcher.send_next_candidate()['status'] == 'LEGACY_SEND_DISABLED_USE_APPROVED_WORKFLOW'
    assert not dispatcher.mcp_mail._sent_emails


def test_live_thunderbird_errors_do_not_become_empty_inbox(tmp_path):
    from unittest.mock import patch
    client = ThunderbirdClient(dry_run=True)
    client.dry_run = False
    client.port, client.token = 9999, 'test'
    with patch('urllib.request.urlopen', side_effect=OSError('offline')):
        with pytest.raises(RuntimeError, match='request failed'):
            client.list_inbox_messages()
    with patch.object(client, '_call_mcp_tool', return_value={}):
        with pytest.raises(RuntimeError, match='inbox'):
            client.list_inbox_messages()
        with pytest.raises(RuntimeError, match='details'):
            client.get_message_details('1')
