import json
import sqlite3
from unittest.mock import patch

import pytest

from scripts.direct_enrichment import DirectEnrichment
from scripts.outreach_journal import flush_journal, install_outbox, journal_path, read_contact
from scripts.staging_db import init_staging_db, insert_staging_leads, mark_lead_synced


def test_wide_contact_schema_opens_and_journals_with_legacy_function_limit(tmp_path):
    path = tmp_path / 'wide.sqlite'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE staging_leads (id INTEGER PRIMARY KEY, ' +
                  ','.join(f'field_{i} TEXT' for i in range(75)) + ')')
        c.setlimit(sqlite3.SQLITE_LIMIT_FUNCTION_ARG, 100)
        install_outbox(c)
        values = {f'field_{i}': None if i % 3 == 0 else f'value {i}: "quoted" {{braces}}' for i in range(75)}
        c.execute('INSERT INTO staging_leads (' + ','.join(values) + ') VALUES (' +
                  ','.join('?' for _ in values) + ')', tuple(values.values()))
    with sqlite3.connect(path) as c:
        c.setlimit(sqlite3.SQLITE_LIMIT_FUNCTION_ARG, 100)
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert c.execute('SELECT count(*) FROM staging_leads').fetchone()[0] == 1
        c.execute('UPDATE staging_leads SET field_74=field_74, field_0=NULL WHERE id=1')
        c.execute("UPDATE staging_leads SET field_74='changed' WHERE id=1")
        c.execute('DELETE FROM staging_leads WHERE id=1')
        snapshots = [(kind, json.loads(payload)) for kind, payload in
                     c.execute('SELECT kind,payload FROM outreach_journal_outbox ORDER BY id')]
    assert [kind for kind, _ in snapshots] == ['contact', 'contact', 'contact_removed']
    assert snapshots[0][1]['journal_contact_id']
    assert {k: snapshots[0][1][k] for k in values} == values
    for _, snapshot in snapshots[1:]:
        assert {k: snapshot[k] for k in values} == {**values, 'field_74': 'changed'}


def test_additive_migration_preserves_legacy_without_inventing_proof(tmp_path):
    path = str(tmp_path / 'legacy.sqlite')
    with sqlite3.connect(path) as c:
        c.execute('''CREATE TABLE staging_leads (id INTEGER PRIMARY KEY, source_file TEXT,
            raw_email TEXT, neverbounce_status TEXT, crm_person_id TEXT)''')
        c.execute("INSERT INTO staging_leads VALUES (7,'BDD1.xlsx','alice@example.org','VALID','crm-7')")
    init_staging_db(path).close()
    with sqlite3.connect(path) as c:
        identity = c.execute('SELECT journal_contact_id FROM staging_leads').fetchone()[0]
    init_staging_db(path).close()
    record = read_contact(journal_path(path), identity)
    assert record['snapshot']['raw_email'] == 'alice@example.org'
    assert record['snapshot']['neverbounce_status'] == 'VALID'
    assert record['snapshot']['crm_person_id'] == 'crm-7'
    assert record['snapshot']['email_validation_source'] is None
    assert record['snapshot']['email_validated_at'] is None
    assert len(record['events']) == 1
    assert (tmp_path / 'legacy.sqlite.before-direct-enrichment.bak').exists()


def test_identity_proof_and_exclusions_survive_removal_of_staging_database(tmp_path):
    path = str(tmp_path / 'sas.sqlite')
    init_staging_db(path).close()
    insert_staging_leads(path, [{'source_file': 'BDD1.xlsx', 'first_name': 'Alice',
                                'last_name': 'Tazi', 'raw_email': 'alice@example.org'}])
    with sqlite3.connect(path) as c:
        identity = c.execute('SELECT journal_contact_id FROM staging_leads').fetchone()[0]
        c.execute('''UPDATE staging_leads SET email_check_status='WAITING',
            email_checked_input='alice@example.org', email_check_reason='AcceptAll',
            email_validation_source='no2bounce', email_validated_at='2026-10-07T09:00:00+00:00'
            WHERE id=1''')
    mark_lead_synced(path, 1, 'twenty-1')
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO email_suppressions VALUES ('alice@example.org','BOUNCE','2026-10-07T10:00:00Z')")
        c.execute('DELETE FROM staging_leads WHERE id=1')
    assert flush_journal(path) == 2
    assert flush_journal(path) == 0
    (tmp_path / 'sas.sqlite').unlink()
    record = read_contact(journal_path(path), identity)
    assert record['snapshot']['email_check_status'] == 'WAITING'
    assert record['snapshot']['email_check_reason'] == 'AcceptAll'
    assert record['snapshot']['email_checked_input'] == 'alice@example.org'
    assert record['snapshot']['email_validation_source'] == 'no2bounce'
    assert record['snapshot']['email_validated_at'] == '2026-10-07T09:00:00+00:00'
    assert record['snapshot']['crm_person_id'] == 'twenty-1'
    assert record['events'][-1]['kind'] == 'contact_removed'
    assert record['exclusions'] == [{'email': 'alice@example.org', 'reason': 'BOUNCE',
                                     'created_at': '2026-10-07T10:00:00Z'}]


def test_committed_change_is_recovered_and_archive_commit_is_safe_to_replay(tmp_path):
    path = str(tmp_path / 'sas.sqlite')
    init_staging_db(path).close()
    insert_staging_leads(path, [{'source_file': 'BDD1.xlsx', 'raw_email': 'a@example.org'}])
    with sqlite3.connect(path) as c:
        identity = c.execute('SELECT journal_contact_id FROM staging_leads').fetchone()[0]
        c.execute("UPDATE staging_leads SET outreach_status='SEND_UNKNOWN' WHERE id=1")
    # Simulate a restart after a committed staging operation, before export.
    init_staging_db(path).close()
    before = read_contact(journal_path(path), identity)
    assert before['snapshot']['outreach_status'] == 'SEND_UNKNOWN'
    # Simulate crash AFTER the archive commit and BEFORE outbox acknowledgement.
    with sqlite3.connect(path) as c:
        c.execute('UPDATE outreach_journal_outbox SET exported=0')
    assert flush_journal(path) > 0
    assert read_contact(journal_path(path), identity) == before
    with sqlite3.connect(path) as c:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            c.execute("UPDATE staging_leads SET journal_contact_id='another' WHERE id=1")


def test_rollback_does_not_archive_an_uncommitted_verdict(tmp_path):
    path = str(tmp_path / 'sas.sqlite')
    init_staging_db(path).close()
    insert_staging_leads(path, [{'source_file': 'BDD1.xlsx'}])
    with sqlite3.connect(path) as c:
        identity = c.execute('SELECT journal_contact_id FROM staging_leads').fetchone()[0]
        c.execute("UPDATE staging_leads SET email_check_status='VALID' WHERE id=1")
        c.rollback()
    assert flush_journal(path) == 0
    assert read_contact(journal_path(path), identity)['snapshot']['email_check_status'] == 'PENDING'


def test_provider_operation_and_exact_response_survive_contact_deletion(tmp_path):
    path = str(tmp_path / 'sas.sqlite')
    DirectEnrichment()._init(path)
    insert_staging_leads(path, [{'source_file': 'BDD1.xlsx', 'raw_email': 'a@example.org'}])
    response = json.dumps({'finalScoreValue': 'AcceptAll', 'catchall': 'true'})
    with sqlite3.connect(path) as c:
        identity = c.execute('SELECT journal_contact_id FROM staging_leads').fetchone()[0]
        c.execute('''INSERT INTO enrichment_jobs(id,provider,kind,status,payload,reserved,created_at)
            VALUES ('job-1','no2bounce','verification','SUBMITTING','{"emailList":["a@example.org"]}',1,'2026-10-07')''')
        c.execute("INSERT INTO enrichment_targets VALUES ('job-1','request-1',1,'{}',0)")
        c.execute("UPDATE enrichment_jobs SET status='FINISHED',response=?,remote_id='remote-1' WHERE id='job-1'", (response,))
        c.execute('DELETE FROM staging_leads WHERE id=1')
    init_staging_db(path).close()
    record = read_contact(journal_path(path), identity)
    operations = [json.loads(e['payload']) for e in record['events'] if e['kind'] == 'enrichment_jobs']
    assert len(operations) == 2
    assert operations[0]['status'] == 'SUBMITTING'
    assert operations[-1]['response'] == response
    assert operations[-1]['remote_id'] == 'remote-1'
    assert operations[-1]['payload'] == '{"emailList":["a@example.org"]}'


def test_archive_failure_keeps_pending_outbox_recoverable(tmp_path):
    path = str(tmp_path / 'sas.sqlite')
    init_staging_db(path).close()
    insert_staging_leads(path, [{'source_file': 'BDD1.xlsx'}])
    with sqlite3.connect(path) as c:
        c.execute("UPDATE staging_leads SET outreach_status='SENDING' WHERE id=1")
    connect = sqlite3.connect

    def unavailable(database, *args, **kwargs):
        if str(database) == journal_path(path):
            raise sqlite3.OperationalError('archive unavailable')
        return connect(database, *args, **kwargs)

    with patch('scripts.outreach_journal.sqlite3.connect', side_effect=unavailable):
        with pytest.raises(sqlite3.OperationalError, match='unavailable'):
            flush_journal(path)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*) FROM outreach_journal_outbox WHERE exported=0').fetchone()[0] == 1
    assert flush_journal(path) == 1


def test_homonyms_get_distinct_durable_identities(tmp_path):
    path = str(tmp_path / 'sas.sqlite')
    init_staging_db(path).close()
    insert_staging_leads(path, [{'source_file': 'BDD1.xlsx', 'first_name': 'Ali', 'last_name': 'Tazi'},
                                {'source_file': 'BDD2.xlsx', 'first_name': 'Ali', 'last_name': 'Tazi'}])
    with sqlite3.connect(path) as c:
        identities = [r[0] for r in c.execute('SELECT journal_contact_id FROM staging_leads')]
    assert len(set(identities)) == 2
    assert all(read_contact(journal_path(path), identity)['snapshot'] for identity in identities)


def test_repeated_enrichment_initialization_does_not_rewrite_stable_contacts(tmp_path):
    path = str(tmp_path / 'stable.sqlite')
    worker = DirectEnrichment()
    worker._init(path)
    insert_staging_leads(path, [{'source_file': 'fixture', 'first_name': name}
                                for name in ('Missing', 'Legacy', 'Confirmed')])
    with sqlite3.connect(path) as c:
        c.execute('UPDATE staging_leads SET is_enriched_linkedin=NULL WHERE id=1')
        c.execute('UPDATE staging_leads SET is_enriched_linkedin=1 WHERE id=2')
        c.execute("""UPDATE staging_leads SET is_enriched_linkedin=1,
            linkedin_url='https://www.linkedin.com/in/confirmed' WHERE id=3""")
    worker._init(path)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT is_enriched_linkedin FROM staging_leads ORDER BY id').fetchall() == [(0,), (0,), (1,)]
        stable_count = c.execute('SELECT count(*) FROM outreach_journal_outbox').fetchone()[0]
    worker._init(path)
    worker._init(path)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*) FROM outreach_journal_outbox').fetchone()[0] == stable_count
    assert flush_journal(path) == 0


def test_update_only_captures_real_changes_including_null_transitions(tmp_path):
    path = str(tmp_path / 'changes.sqlite')
    init_staging_db(path).close()
    insert_staging_leads(path, [{'source_file': 'fixture', 'first_name': 'Alice'}])
    with sqlite3.connect(path) as c:
        identity = c.execute('SELECT journal_contact_id FROM staging_leads').fetchone()[0]
        before = c.execute('SELECT count(*) FROM outreach_journal_outbox').fetchone()[0]
        c.execute('UPDATE staging_leads SET first_name=first_name, last_name=NULL WHERE id=1')
        assert c.execute('SELECT count(*) FROM outreach_journal_outbox').fetchone()[0] == before
        c.execute("UPDATE staging_leads SET last_name='Tazi' WHERE id=1")
        c.execute('UPDATE staging_leads SET last_name=NULL WHERE id=1')
        c.execute("UPDATE staging_leads SET first_name='Alicia' WHERE id=1")
    assert flush_journal(path) == 3
    record = read_contact(journal_path(path), identity)
    assert [(json.loads(e['payload'])['first_name'], json.loads(e['payload'])['last_name'])
            for e in record['events'][-3:]] == [('Alice', 'Tazi'), ('Alice', None), ('Alicia', None)]


def test_evidence_no_op_update_does_not_duplicate_exact_provider_response(tmp_path):
    path = str(tmp_path / 'evidence.sqlite')
    DirectEnrichment()._init(path)
    with sqlite3.connect(path) as c:
        c.execute('''INSERT INTO enrichment_jobs(id,provider,kind,status,payload,reserved,created_at)
            VALUES ('job-1','no2bounce','verification','SUBMITTED','{}',1,'2026-11-05')''')
        before = c.execute('SELECT count(*) FROM outreach_journal_outbox').fetchone()[0]
        c.execute('UPDATE enrichment_jobs SET response=NULL,status=status WHERE id=\'job-1\'')
        assert c.execute('SELECT count(*) FROM outreach_journal_outbox').fetchone()[0] == before
        c.execute("UPDATE enrichment_jobs SET response='{}' WHERE id='job-1'")
        c.execute("UPDATE enrichment_jobs SET response=NULL WHERE id='job-1'")
        assert c.execute('SELECT count(*) FROM outreach_journal_outbox').fetchone()[0] == before + 2
