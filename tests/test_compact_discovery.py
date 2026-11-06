import copy
import fcntl
import json

import pytest

from scripts import linkedin_queue
from scripts.compact_briefs import BriefBudget, default_acquisition_brief
from scripts.compact_discovery import CompactDiscovery, inspect_contract
from scripts.compact_store import CompactStore
from scripts.workflow_store import encode
from tests.test_compact_linkedin import extraction


def tools():
    return {'tools': [
        {'name':'search_people','inputSchema':{'type':'object','required':['keywords'],'properties':{
            'keywords':{'type':'string'},'current_company':{'type':'string'},'location':{'type':'string'},'network':{'type':'array'}}}},
        {'name':'get_person_profile','inputSchema':{'type':'object','required':['linkedin_username'],'properties':{'linkedin_username':{'type':'string'}}}}]}


def person(name='Alice Tazi', url='https://www.linkedin.com/in/alice-tazi'):
    return {'full_name':name,'company_name':'Example SA','role':'Responsable transformation',
            'linkedin_url':url,'source_url':url,'email':'alice.tazi@example.org'}


def proposal(observed, *, uncertain=False, extra=None):
    record = person(); records = [record] + ([extra] if extra else [])
    people=[]
    for item in records:
        first,last=item['full_name'].split(' ',1)
        people.append({'first_name':first,'last_name':last,'company_name':item['company_name'],'role':item['role'],
            'linkedin_url':item['linkedin_url'],'source_url':item['source_url'],'basis':[encode(item),item['full_name']],
            'professional_link_certain':not uncertain,'email':item['email']})
    return {'status':'SELECTED','people':people,'selected_url':record['linkedin_url'],
            'reason':'Responsable transformation : rôle observé pertinent pour ce brief'}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(linkedin_queue,'ROOT',tmp_path)
    store=CompactStore(tmp_path/'journal.sqlite')
    for lane in ('discovery','linkedin','qualification'):
        store.set_authority(lane)
    company=store.put_candidate({'company_name':'Example SA'},lane='discovery')
    budgets=BriefBudget(store)
    d={**default_acquisition_brief(),'searches':['Example SA Maroc'],'volume_caps':{'items':20},
       'budgets':{'currency':'USD','campaign_cap':'2','providers':{
       'linkedin_mcp':{'money_cap':'2','credit_caps':{},'cost_basis':'verified no additional monetary charge'}}}}
    budgets.create_brief('b',d,command_id='create');budgets.launch('b',1,command_id='launch')
    calls=[]
    def search(**kwargs):
        calls.append(kwargs);return {'people':[person()]}
    service=CompactDiscovery(store,budgets,list_tools=tools,search_people=search,
        researcher=lambda dossier:proposal(dossier['observed_results']),
        cost_guard=budgets.guard('b',1,{'linkedin_mcp':{'amount':'0'}}))
    return store,budgets,company,service,calls


def test_discovery_enqueues_named_confirmation_and_reuses_result(setup):
    store,budgets,company,service,calls=setup
    task=service.enqueue_company(company,'b',1)
    result=service.run_one()
    assert result['status']=='CONFIRMATION_PENDING'
    assert calls==[{'keywords':'Example SA Maroc'}]
    candidate=store.candidate(result['candidate_id'])
    assert candidate['data']['first_name']=='Alice'
    assert candidate['data'].get('linkedin_search_status') is None
    assert store.task(result['confirmation_task_id'])['kind']=='LINKEDIN_CONFIRM'
    assert service.run_one()['status']=='EMPTY'
    assert service.reconcile(task['id'])['status']=='UNCHANGED'
    assert len(calls)==1
    assert service.qualify_confirmed(candidate['id'])['reason']=='IDENTITY_CONFIRMATION_REQUIRED'
    service.confirmation.run_one(runner=lambda payload:extraction(payload))
    assert service.qualify_confirmed(candidate['id'])['reason']=='QUALIFICATION_SERVICE_REQUIRED'
    store.exclude('alice.tazi@example.org','bounce')
    assert service.qualify_confirmed(candidate['id'])['reason']=='EXCLUDED'


def test_schema_or_capability_missing_is_explicit_without_search(setup):
    store,budgets,company,service,calls=setup
    service.list_tools=lambda:{'tools':[]}
    task=service.enqueue_company(company,'b',1)
    assert service.run_one()['reason']=='LINKEDIN_DISCOVERY_CAPABILITY_ABSENT'
    assert not calls and store.task(task['id'])['status']=='WAITING'
    assert not inspect_contract({})['usable']


def test_queries_finite_reviewed_and_numeric_filters_only(setup):
    store,budgets,company,service,calls=setup
    for query,reason in [({'keywords':'Invented Person'},'SEARCH_NOT_IN_REVIEWED_BRIEF'),
       ({'keywords':'Example SA Maroc','current_company':'Example SA'},'NUMERIC_URN_REQUIRED'),
       ({'keywords':'Example SA Maroc','location':'Maroc'},'NUMERIC_URN_REQUIRED')]:
        with pytest.raises(ValueError,match=reason):
            service.enqueue_company(company,'b',1,queries=[query])
    service.enqueue_company(company,'b',1,queries=[{'keywords':'Example SA Maroc','current_company':'123','location':'456','network':['F','S']}])
    assert service.run_one()['status']=='CONFIRMATION_PENDING'
    assert calls[0]['current_company']=='123'


def test_global_mcp_lock_is_shared_with_confirmation(setup,tmp_path):
    store,budgets,company,service,calls=setup
    task=service.enqueue_company(company,'b',1)
    path=tmp_path/'bdd/linkedin-mcp-worker.lock';path.parent.mkdir()
    with path.open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert service.run_one()['status']=='MCP_BUSY'
    assert store.task(task['id'])['status']=='PENDING' and not calls


def test_ambiguous_submission_never_retries_and_keeps_budget(setup):
    store,budgets,company,service,calls=setup
    def broken(**kwargs):
        calls.append(kwargs);raise TimeoutError('network')
    service.search_people=broken
    task=service.enqueue_company(company,'b',1)
    assert service.run_one()['status']=='UNKNOWN'
    assert service.run_one()['status']=='EMPTY'
    assert service.reconcile(task['id'])['status']=='RECONCILIATION_REQUIRED'
    assert budgets.reservation(task['id']+':search:0')['state']=='UNKNOWN'
    assert len(calls)==1


@pytest.mark.parametrize('uncertain,homonym',[(True,False),(False,True)])
def test_uncertain_professional_link_and_homonym_stay_in_review(setup,uncertain,homonym):
    store,budgets,company,service,calls=setup
    other=person(url='https://www.linkedin.com/in/alice-tazi-other') if homonym else None
    service.search_people=lambda **kwargs:{'people':[person()]+([other] if other else [])}
    service.researcher=lambda dossier:proposal(dossier['observed_results'],uncertain=uncertain,extra=other)
    task=service.enqueue_company(company,'b',1)
    assert service.run_one()['status']=='NEEDS_REVIEW'
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM workflow_tasks WHERE kind='LINKEDIN_CONFIRM'").fetchone()[0]==0


def test_specialist_cannot_invent_identity_or_mutate_rights(setup):
    store,budgets,company,service,calls=setup
    def invented(dossier):
        assert set(dossier)=={'company_name','observed_results','purpose'}
        value=proposal(dossier['observed_results']);value['people'][0]['first_name']='Invented';return value
    service.researcher=invented
    service.enqueue_company(company,'b',1)
    assert service.run_one()['reason']=='INVALID_RESEARCH_SCHEMA_OR_UNSUPPORTED_FACT'
    assert len(store.candidates())==1


def test_excluded_contact_and_exhausted_brief_no_paid_qualification(setup):
    store,budgets,company,service,calls=setup
    store.exclude('alice.tazi@example.org','unsubscribe')
    service.enqueue_company(company,'b',1)
    assert service.run_one()['reason']=='EXCLUDED'
    assert len(store.candidates())==1


def test_missing_budget_guard_never_searches(setup):
    store,budgets,company,service,calls=setup
    service.cost_guard=None
    service.enqueue_company(company,'b',1)
    assert service.run_one()['reason']=='BUDGET_GUARD_REQUIRED' and not calls


def test_mixing_facts_from_different_people_is_rejected(setup):
    store,budgets,company,service,calls=setup
    first=person();first['role']='Directrice opérations'
    other=person(name='Bob Rami',url='https://www.linkedin.com/in/bob-rami')
    service.search_people=lambda **kwargs:{'people':[first,other]}
    def mixed(dossier):
        value=proposal(dossier['observed_results'])
        # Every scalar is observed globally, but this role belongs to Bob.
        value['people'][0]['basis']=[dossier['observed_results'][0], 'Alice Tazi']
        return value
    service.researcher=mixed
    service.enqueue_company(company,'b',1)
    assert service.run_one()['reason']=='INVALID_RESEARCH_SCHEMA_OR_UNSUPPORTED_FACT'
    assert len(store.candidates())==1


def test_partial_unknown_search_keeps_uncertainty_visible(setup):
    store,budgets,company,service,calls=setup
    d=copy.deepcopy(budgets.brief('b')['definition']);d['searches'].append('Example SA Rabat')
    budgets.create_brief('b',d,command_id='v2');budgets.launch('b',2,command_id='launch-v2')
    service.cost_guard=budgets.guard('b',2,{'linkedin_mcp':{'amount':'0'}})
    def search(**kwargs):
        calls.append(kwargs)
        if len(calls)==2:
            raise TimeoutError('second search unknown')
        return {'people':[person()]}
    service.search_people=search
    task=service.enqueue_company(company,'b',2)
    assert service.run_one()['status']=='UNKNOWN'
    reconciled=service.reconcile(task['id'])
    assert reconciled['reason']=='PARTIAL_OR_UNKNOWN_SEARCH' and len(reconciled['results'])==1
    assert store.task(task['id'])['status']=='UNKNOWN'
    assert len(calls)==2
    assert budgets.reservation(task['id']+':search:1')['state']=='UNKNOWN'


def test_previously_confirmed_profile_is_reused_with_exact_proof(setup):
    store,budgets,company,service,calls=setup
    prior=store.put_candidate({'first_name':'Alice','last_name':'Tazi','company_name':'Example SA',
        'raw_email':'alice.tazi@example.org','source_file':'existing verified'},lane='qualification')
    service.confirmation.enqueue(prior)
    assert service.confirmation.run_one(runner=lambda p:extraction(p))['status']=='IMPORTED'
    service.enqueue_company(company,'b',1)
    result=service.run_one()
    assert result['candidate_id']==prior
    assert result['confirmation_task_id'] is None
    assert service.confirmation.confirmed_input(store.candidate(prior))


def test_volume_exhausted_blocks_search_before_mcp(setup):
    store,budgets,company,service,calls=setup
    budgets.reserve('b',1,'linkedin_mcp',key='previous-runs',amount='0',quantity=20)
    service.enqueue_company(company,'b',1)
    assert service.run_one()['reason']=='VOLUME_LIMIT_REACHED:items'
    assert not calls
