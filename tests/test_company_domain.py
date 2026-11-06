import json
import sqlite3
from unittest.mock import Mock

import pytest
import responses

from scripts.direct_enrichment import DirectEnrichment, contact_input, contact_key, fingerprint, normalized
from scripts.enrichment_clients import FullEnrichClient
from scripts.staging_db import init_staging_db, insert_staging_leads
from tests.helpers import set_test_linkedin_confirmation

BASE = {'first_name': 'Karim', 'last_name': 'Belcadi', 'company_name': 'ACCESS ENERGY'}
DOMAIN = dict(BASE, company_domain=' AccessEnergy.MA ', company_domain_source='Reviewed source email domain')


def test_reviewed_domain_sent_without_source_email_or_provenance():
    data = contact_input(dict(DOMAIN, raw_email='known@accessenergy.ma'))
    assert data['domain'] == 'accessenergy.ma'
    assert 'raw_email' not in data and 'company_domain_source' not in data
    assert contact_input(dict(DOMAIN, company_name=''))['domain'] == 'accessenergy.ma'


@pytest.mark.parametrize('domain,source', [('https://accessenergy.ma', 'reviewed'), ('bad@accessenergy.ma', 'reviewed'), ('accessenergy.ma/path', 'reviewed'), ('localhost', 'reviewed'), ('accessenergy.ma', ''), (42, 'reviewed')])
def test_unusable_declaration_blocks_paid_discovery(domain, source):
    assert contact_input(dict(BASE, company_domain=domain, company_domain_source=source)) is None


def test_no_automatic_domain_inference_and_legacy_cache_stable():
    data = contact_input(dict(BASE, raw_email='karim@accessenergy.ma', website='https://accessenergy.ma'))
    assert 'domain' not in data
    legacy = fingerprint({'version': 2, 'identity': {f: normalized(BASE[f]) for f in BASE}, 'fields': ['contact.work_emails']})
    assert contact_key(data) == legacy
    assert contact_key(contact_input(DOMAIN)) != legacy
    assert contact_key(contact_input(DOMAIN)) != contact_key(contact_input(dict(DOMAIN, company_domain='other.ma')))
    assert contact_key(contact_input(DOMAIN)) == contact_key(contact_input(dict(DOMAIN, company_domain_source='Another provenance')))


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / 'pilot.sqlite')
    init_staging_db(p).close()
    insert_staging_leads(p, [dict(DOMAIN, source_file='test')])
    set_test_linkedin_confirmation(p,1,'https://www.linkedin.com/in/karim-belcadi')
    return p


def worker():
    return DirectEnrichment(fullenrich=FullEnrichClient('test'), fullenrich_daily_credits=1,
                            no2bounce_daily_addresses=0, harvest_daily_requests=0, phase='enrich')


@responses.activate
def test_url_only_batch_ignores_optional_domain_and_no_relaunch_after_empty(db):
    responses.get(FullEnrichClient.BASE + '/account/credits', json={'balance': 50})
    responses.post(FullEnrichClient.BASE + '/contact/enrich/bulk', json={'enrichment_id': 'job'})
    worker().audit_staging_db(db,1)
    payload = json.loads(responses.calls[-1].request.body)
    assert payload['data'][0]['linkedin_url'] == 'https://www.linkedin.com/in/karim-belcadi'
    assert 'domain' not in payload['data'][0]
    assert payload['data'][0]['enrich_fields'] == ['contact.work_emails']
    responses.get(FullEnrichClient.BASE + '/contact/enrich/bulk/job', json={'id':'job', 'status':'FINISHED','cost':{'credits':0},'data':[{'custom':payload['data'][0]['custom'],'contact_info':{}}]})
    worker().audit_staging_db(db,1)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE staging_leads SET company_domain='changed.ma'")
    worker().audit_staging_db(db,1)
    assert sum(c.request.method == 'POST' for c in responses.calls) == 1


@responses.activate
def test_optional_domain_change_does_not_change_url_only_identity(db):
    responses.get(FullEnrichClient.BASE + '/account/credits', json={'balance':50})
    responses.post(FullEnrichClient.BASE + '/contact/enrich/bulk', json={'enrichment_id':'job'})
    worker().audit_staging_db(db,1)
    payload=json.loads(responses.calls[-1].request.body)
    with sqlite3.connect(db) as c:c.execute("UPDATE staging_leads SET company_domain='other.ma'")
    responses.get(FullEnrichClient.BASE + '/contact/enrich/bulk/job',json={'id':'job','status':'FINISHED','cost':{'credits':1},'data':[{'custom':payload['data'][0]['custom'],'contact_info':{'most_probable_work_email':{'email':'new@accessenergy.ma','status':'DELIVERABLE'}}}]})
    worker().audit_staging_db(db,1)
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT contact_discovery_status,raw_email FROM staging_leads').fetchone()==('SUCCEEDED','new@accessenergy.ma')


def test_migration_and_import_preserve_domain_and_original(db):
    init_staging_db(db).close()
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT company_domain,company_domain_source FROM staging_leads').fetchone()==(DOMAIN['company_domain'],DOMAIN['company_domain_source'])
        # A pre-domain database also predates journal snapshot triggers.
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'journal_contact_%'").fetchall():
            c.execute('DROP TRIGGER "' + name + '"')
        c.execute('ALTER TABLE staging_leads DROP COLUMN company_domain')
        c.execute('ALTER TABLE staging_leads DROP COLUMN company_domain_source')
    init_staging_db(db).close()
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT company_domain,company_domain_source,first_name FROM staging_leads').fetchone()==(None,None,'Karim')
