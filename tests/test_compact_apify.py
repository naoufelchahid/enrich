import copy
from datetime import datetime, timezone

import pytest

from scripts.compact_apify import ACTOR, CompactApify
from scripts.compact_briefs import BriefBudget, default_acquisition_brief
from scripts.compact_store import CompactStore
from scripts.enrichment_clients import ProviderError


class FakeApify:
    def __init__(self):
        self.submits=[];self.page_calls=[];self.price='0.1';self.status='SUCCEEDED';self.fail=None
        self.records=[{'placeId':'p1','title':'Company A','url':'https://maps.example/p1'},
                      {'placeId':'p2','title':'Company B','url':'https://maps.example/p2'}]
    def inspect_contract(self,actor):
        return {'actor':actor,'version':'fixture-2026-11-05','input_schema':{'type':'object','required':['terms','zone','maximum'],
            'properties':{'terms':{'type':'array','items':{'type':'string'}},'zone':{'type':'string'},'maximum':{'type':'integer'}}},
            'input_fields':{'query':'terms','location':'zone','limit':'maximum'},
            'pricing':{'currency':'USD','upper_bound_per_result':self.price,'source':'synthetic account pricing fixture',
            'verified_at':datetime.now(timezone.utc).isoformat(),'account_scope':'synthetic-test-account','maximum_charge_enforced':True}}
    def submit(self,actor,data,**options):
        self.submits.append((actor,data,options))
        if self.fail:
            raise self.fail
        return {'id':'run-'+str(len(self.submits)),'defaultDatasetId':'dataset'}
    def get_run(self,run_id):
        return {'id':run_id,'defaultDatasetId':'dataset','status':self.status}
    def dataset_page(self,dataset,offset,limit):
        self.page_calls.append((dataset,offset,limit))
        return {'offset':offset,'items':self.records[offset:offset+1],'total':len(self.records)}
    def reconcile(self,operation_id):
        return {'id':'reconciled-run','defaultDatasetId':'dataset'}


@pytest.fixture
def setup(tmp_path):
    store=CompactStore(tmp_path/'journal.sqlite')
    for lane in ('apify','discovery'):
        store.set_authority(lane)
    budget=BriefBudget(store)
    d={**default_acquisition_brief(),'searches':['industrie Casablanca','industrie Rabat'],'volume_caps':{'companies':10},
       'budgets':{'currency':'USD','campaign_cap':'10','providers':{'apify':{'money_cap':'10','credit_caps':{},'cost_basis':'synthetic account price'}}}}
    budget.create_brief('b',d,command_id='create');budget.launch('b',1,command_id='launch')
    client=FakeApify();qualified=[]
    def qualifier(dossier):
        qualified.append(dossier)
        return {'relevant':True,'facts':[dossier['company']['title']],'uncertainties':[],'reason':'Observed activity fits reviewed query'}
    service=CompactApify(store,budget,client,qualifier)
    return store,budget,client,service,qualified


def enqueue(service,query='industrie Casablanca',**kwargs):
    return service.enqueue('b',1,limits_by_search={query:kwargs.get('search_limit',2)},run_limit=kwargs.get('run_limit',2),maximum_charge=kwargs.get('charge','2'))[0]


def test_submit_paginate_restart_and_replay_without_duplicates(setup):
    store,budget,client,service,qualified=setup
    task=enqueue(service)
    assert service.tick(task['id'])['status']=='SUBMITTED'
    assert client.submits[0][1]=={'terms':['industrie Casablanca'],'zone':'Maroc','maximum':2}
    assert service.tick(task['id'])['ingested']==1
    reopened=CompactApify(CompactStore(store.path),budget,client,service.qualifier)
    result=reopened.tick(task['id'])
    assert result['status']=='COMPLETE' and result['offset']==2 and result['ingested']==2
    assert result['dataset_id']=='dataset' and result['input_version']=='fixture-2026-11-05'
    assert reopened.tick(task['id'])['status']=='UNCHANGED'
    assert len(qualified)==2 and len(client.submits)==1
    for candidate in store.candidates():
        assert candidate['data']['company_only'] and candidate['wait_reason']=='CONTACT_DISCOVERY_REQUIRED'
        assert not candidate['data'].get('raw_email')
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM workflow_tasks WHERE kind='COMPANY_CONTACT_NEEDED'").fetchone()[0]==2
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='staging_leads'").fetchone()


def test_repeated_dataset_rows_or_new_search_do_not_requalify(setup):
    store,budget,client,service,qualified=setup
    client.records=[client.records[0],client.records[0]]
    first=enqueue(service);service.tick(first['id']);service.tick(first['id']);service.tick(first['id'])
    assert len(qualified)==1 and len(store.candidates())==1
    other=enqueue(service,'industrie Rabat');service.tick(other['id']);service.tick(other['id']);service.tick(other['id'])
    assert len(qualified)==1 and len(store.candidates())==1


def test_ambiguous_submit_is_reconciled_never_resubmitted(setup):
    store,budget,client,service,qualified=setup
    client.fail=TimeoutError('unknown')
    task=enqueue(service)
    assert service.tick(task['id'])['status']=='UNKNOWN'
    assert budget.reservation(task['id'])['state']=='UNKNOWN'
    assert service.tick(task['id'])['status']=='RECONCILIATION_REQUIRED'
    assert len(client.submits)==1
    assert service.reconcile(task['id'])['run_id']=='reconciled-run'
    assert service.tick(task['id'])['ingested']==1
    assert len(client.submits)==1


def test_certain_failure_releases_cost_but_never_retries_automatically(setup):
    store,budget,client,service,qualified=setup
    client.fail=ProviderError('INVALID_INPUT',uncertain=False)
    task=enqueue(service)
    assert service.tick(task['id'])['status']=='FAILED'
    assert budget.reservation(task['id'])['state']=='FAILED_CERTAIN'
    assert budget.summary('b')['money_committed']=='0'
    service.tick(task['id']);assert len(client.submits)==1


def test_per_search_per_run_and_campaign_limits_all_hold(setup):
    store,budget,client,service,qualified=setup
    task=enqueue(service,search_limit=100,run_limit=1)
    service.tick(task['id']);assert client.submits[0][1]['maximum']==1
    assert service.tick(task['id'])['ingested']==1
    d=copy.deepcopy(budget.brief('b')['definition']);d['volume_caps']['companies']=1
    budget.create_brief('b',d,command_id='v2');budget.launch('b',2,command_id='launch-v2')
    second=service.enqueue('b',2,limits_by_search={'industrie Rabat':1},run_limit=1,maximum_charge='1')[0]
    assert service.tick(second['id'])['reason']=='VOLUME_LIMIT_REACHED:companies'
    assert len(client.submits)==1


def test_contract_account_price_or_bounds_missing_prevents_submit(setup):
    store,budget,client,service,qualified=setup
    client.price='9'
    task=enqueue(service)
    assert service.tick(task['id'])['reason']=='APIFY_MAXIMUM_CHARGE_BELOW_KNOWN_BOUND'
    assert not client.submits


def test_partial_failed_run_keeps_companies_and_evidence(setup):
    store,budget,client,service,qualified=setup
    client.status='FAILED'
    task=enqueue(service);service.tick(task['id']);service.tick(task['id'])
    result=service.tick(task['id'])
    assert result['status']=='PARTIAL' and result['ingested']==2
    assert len(store.candidates())==2
    assert budget.reservation(task['id'])['state']=='SUBMITTED'


def test_invalid_qualifier_becomes_review_not_recipient(setup):
    store,budget,client,service,qualified=setup
    service.qualifier=lambda dossier:{'relevant':True,'facts':['invented'],'uncertainties':[],'reason':'x'}
    task=enqueue(service);service.tick(task['id'])
    assert service.tick(task['id'])['reason']=='COMPANY_QUALIFICATION_REVIEW'
    assert store.candidates()[0]['data']['company_only']


def test_interrupt_before_transport_reply_keeps_request_and_unknown_budget(setup):
    store,budget,client,service,qualified=setup
    def interrupted(*args,**kwargs):
        raise KeyboardInterrupt('simulated process exit')
    client.submit=interrupted
    task=enqueue(service)
    with pytest.raises(KeyboardInterrupt):
        service.tick(task['id'])
    persisted=store.task(task['id'])
    assert persisted['status']=='UNKNOWN'
    assert store.get_blob(persisted['result']['input_uri'])
    assert store.get_blob(persisted['result']['contract_uri'])
    assert budget.reservation(task['id'])['state']=='UNKNOWN'
    service.reconcile(task['id'])
    assert budget.reservation(task['id'])['state']=='SUBMITTED'
    assert budget.summary('b')['money_committed']=='2'


def test_pending_page_replay_preserves_handoff_and_qualification(setup):
    store,budget,client,service,qualified=setup
    class Discovery:
        first=True
        def enqueue_company(self,identity,brief_id,version):
            task=store.create_task('discovery','LINKEDIN_DISCOVER',{'company_id':identity,'brief_id':brief_id,'version':version},candidate_id=identity,key='discovery:'+identity)
            if self.first:
                self.first=False
                raise KeyboardInterrupt('exit after handoff before replay marker')
            return task
    service.discovery=Discovery()
    task=enqueue(service);service.tick(task['id'])
    with pytest.raises(KeyboardInterrupt):
        service.tick(task['id'])
    persisted=store.task(task['id'])
    assert persisted['result']['pending_page_uri']
    assert len(client.page_calls)==1 and len(qualified)==1
    assert service.tick(task['id'])['ingested']==1
    assert len(client.page_calls)==1 and len(qualified)==1
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM workflow_tasks WHERE lane='discovery'").fetchone()[0]==1


def test_attested_real_cost_reconciles_otherwise_upper_bound_remains(setup):
    store,budget,client,service,qualified=setup
    original=client.get_run
    client.get_run=lambda run_id:{**original(run_id),'attested_cost':{'attested':True,'source':'account run receipt', 'currency':'USD','amount':'0.2'}}
    task=enqueue(service);service.tick(task['id']);service.tick(task['id']);result=service.tick(task['id'])
    assert result['cost_reconciliation']=='CONFIRMED'
    assert budget.summary('b')['money_committed']=='0.2'


def test_no_real_transport_is_explicit_and_never_claimed_ready(setup):
    store,budget,client,service,qualified=setup
    service.client=None
    task=enqueue(service)
    assert service.tick(task['id'])['reason']=='APIFY_TRANSPORT_NOT_CONFIGURED'
    assert budget.reservation(task['id']) is None
