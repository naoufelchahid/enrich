"""The retired API must stay unreachable even with legacy keys/budgets."""
from unittest.mock import Mock
import sqlite3
import pytest
from scripts.direct_enrichment import DirectEnrichment
from scripts.enrichment_clients import HarvestClient, ProviderError
from scripts.staging_db import init_staging_db, insert_staging_leads

@pytest.mark.parametrize('method,args', [('profile',('https://www.linkedin.com/in/alice',)),('posts',('https://www.linkedin.com/in/alice',)),('search_profiles',('Alice','Example','Example SA'))])
def test_legacy_harvest_client_cannot_spend(method,args):
    with pytest.raises(ProviderError, match='HARVEST_RETIRED'):
        getattr(HarvestClient('real-looking-key'), method)(*args)

@pytest.mark.parametrize('phase',['profiles','linkedin-search','linkedin','all'])
def test_pipeline_never_calls_harvest(tmp_path,phase):
    path=str(tmp_path/'sas.sqlite')
    init_staging_db(path).close()
    insert_staging_leads(path,[{'source_file':'test','first_name':'Alice','last_name':'Example','company_name':'Example','linkedin_url':'https://www.linkedin.com/in/alice'}])
    client=Mock(api_key='configured')
    worker=DirectEnrichment(harvest=client,phase=phase,harvest_daily_requests=100,
        posts_enabled=True,fullenrich_daily_credits=0,no2bounce_daily_addresses=0)
    result=worker.audit_staging_db(path)
    assert result['linkedin_provider']=='linkedin_mcp'
    assert result['harvest_configured'] is False
    assert client.mock_calls==[]
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM enrichment_jobs WHERE provider='harvest'").fetchone()[0]==0


def test_historical_harvest_response_cannot_apply(tmp_path):
    import json
    path=str(tmp_path/'sas.sqlite');init_staging_db(path).close()
    insert_staging_leads(path,[{'source_file':'test','first_name':'Alice','last_name':'Example','company_name':'Example'}])
    w=DirectEnrichment(phase='profiles',fullenrich_daily_credits=0,no2bounce_daily_addresses=0)
    w._init(path)
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO enrichment_jobs(id,provider,kind,status,payload,response,reserved,created_at) VALUES('old','harvest','profile','RECEIVED','{}',?,1,'2026-10-01T00:00:00+00:00')", (json.dumps({'element':{'linkedinUrl':'https://www.linkedin.com/in/wrong','currentPosition':[{'position':'CEO','companyName':'Wrong'}]}}),))
        c.execute("INSERT INTO enrichment_cache VALUES('harvest','profile','key','old','RECEIVED',NULL)")
        c.execute("INSERT INTO enrichment_targets VALUES('old','key',1,?,0)", (json.dumps({'url':'https://www.linkedin.com/in/wrong'}),))
    w.audit_staging_db(path)
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT company_name,linkedin_url FROM staging_leads').fetchone()==('Example',None)
