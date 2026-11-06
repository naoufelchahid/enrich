"""Cross-service identity and acquisition-goal gates on temporary journals only."""
import copy
from datetime import datetime, timezone

import pytest

from scripts.compact_briefs import BriefBudget, default_acquisition_brief
from scripts.compact_crm import transfer_compact
from scripts.compact_discovery import CompactDiscovery
from scripts.compact_qualification import CompactQualification, import_compact
from scripts.compact_store import CompactStore
from tests.test_compact_discovery import tools, person, proposal
from tests.test_compact_linkedin import extraction
from tests.test_compact_qualification import NB
from tests.test_workflow_refactor import FakeCRM

AT=datetime(2026,11,5,12,tzinfo=timezone.utc)


def context(tmp_path, goal=20):
    store=CompactStore(tmp_path/'journal.sqlite')
    for lane in ('apify','discovery','linkedin','qualification','crm'):
        store.set_authority(lane)
    budgets=BriefBudget(store)
    d={**default_acquisition_brief(),'admissible_goal':goal,'searches':['Example SA Maroc'],
       'volume_caps':{'items':200,'companies':200},'budgets':{'currency':'USD','campaign_cap':'100',
       'providers':{'linkedin_mcp':{'money_cap':'0','credit_caps':{},'cost_basis':'fixture no fee'},
                    'no2bounce':{'money_cap':'50','credit_caps':{'checks':'200'},'cost_basis':'fixture price'},
                    'apify':{'money_cap':'50','credit_caps':{},'cost_basis':'fixture account contract'}}}}
    budgets.create_brief('acquisition',d,command_id='create');budgets.launch('acquisition',1,command_id='launch')
    company=store.put_candidate({'company_name':'Example SA'},lane='apify')
    guard=budgets.guard('acquisition',1,{'linkedin_mcp':{'amount':'0'},'no2bounce':{'amount':'0.1','credits':{'checks':'1'}}})
    nb=NB();qualification=CompactQualification(store,no2bounce=nb,cost_guard=guard,hygiene=lambda email:'PASS',clock=lambda:AT)
    discovery=CompactDiscovery(store,budgets,list_tools=tools,search_people=lambda **kwargs:{'people':[person()]},
        researcher=lambda dossier:proposal(dossier['observed_results']),cost_guard=guard,qualification=qualification)
    discovery.enqueue_company(company,'acquisition',1)
    result=discovery.run_one();identity=result['candidate_id']
    # A separately reviewed company-domain fact is independent from discovering a
    # profile or email. It enables exact company matching during CRM replay.
    store.update_candidate(identity,{'company_domain':'example.org','company_domain_source':'operator'},lane='qualification')
    return store,budgets,discovery,qualification,nb,identity,FakeCRM()


def confirm(discovery):
    return discovery.confirmation.run_one(runner=lambda payload:extraction(payload))


def qualify(qualification,identity):
    assert qualification.tick(identity)['status']=='SUBMITTED'
    assert qualification.tick(identity)['status']=='QUALIFIED'


def transfer(store,identity,crm):
    return transfer_compact(store,identity,crm,clock=lambda:AT)


def test_full_discovery_confirmation_qualification_admission_reaches_shared_goal(tmp_path,monkeypatch):
    from scripts import linkedin_queue
    monkeypatch.setattr(linkedin_queue,'ROOT',tmp_path)
    store,budgets,discovery,qualification,nb,identity,crm=context(tmp_path)
    candidate=store.candidate(identity)
    assert candidate['data']['acquisition_brief_id']=='acquisition'
    assert candidate['data']['acquisition_brief_version']==1
    assert candidate['data']['discovery_identity_required'] is True
    assert qualification.tick(identity)['reason']=='IDENTITY_CONFIRMATION_REQUIRED'
    assert transfer(store,identity,crm)['status']=='IDENTITY_CONFIRMATION_REQUIRED'
    assert not nb.submissions and not crm.creations
    assert confirm(discovery)['status']=='IMPORTED'
    qualify(qualification,identity)
    # Nineteen earlier, distinct admissions in the same acquisition campaign.
    for i in range(19):
        budgets.record_admitted('acquisition','crm-person:previous-'+str(i))
    result=transfer(store,identity,crm)
    assert result['status']=='TRANSFERRED'
    assert budgets.summary('acquisition')['admitted']==20
    assert transfer(store,identity,crm)['status']=='TRANSFERRED'
    assert budgets.summary('acquisition')['admitted']==20
    for provider,unit in [('apify','companies'),('linkedin_mcp','items')]:
        with pytest.raises(RuntimeError,match='ADMISSIBLE_GOAL_REACHED'):
            budgets.reserve('acquisition',1,provider,key='next-'+provider,amount='0',quantity_unit=unit)


def test_failed_or_pending_confirmation_cannot_be_bypassed_even_with_valid_email(tmp_path,monkeypatch):
    from scripts import linkedin_queue
    monkeypatch.setattr(linkedin_queue,'ROOT',tmp_path)
    store,budgets,discovery,qualification,nb,identity,crm=context(tmp_path)
    store.update_candidate(identity,{'clean_email':'alice.tazi@example.org','validated_email':'alice.tazi@example.org',
        'email_check_status':'VALID','email_validation_source':'no2bounce','email_validated_at':AT.isoformat()},lane='qualification')
    assert qualification.tick(identity)['reason']=='IDENTITY_CONFIRMATION_REQUIRED'
    assert transfer(store,identity,crm)['status']=='IDENTITY_CONFIRMATION_REQUIRED'
    discovery.confirmation.run_one(runner=lambda payload:extraction(payload,'NEEDS_REVIEW'))
    assert qualification.tick(identity)['reason']=='IDENTITY_CONFIRMATION_REQUIRED'
    assert transfer(store,identity,crm)['status']=='IDENTITY_CONFIRMATION_REQUIRED'
    assert not nb.submissions and not crm.creations


def test_unknown_person_creation_reconciliation_counts_once(tmp_path,monkeypatch):
    from scripts import linkedin_queue
    monkeypatch.setattr(linkedin_queue,'ROOT',tmp_path)
    store,budgets,discovery,qualification,nb,identity,crm=context(tmp_path)
    confirm(discovery);qualify(qualification,identity)
    crm.unknown_once='people'
    assert transfer(store,identity,crm)['status']=='CRM_RECONCILIATION_REQUIRED'
    assert budgets.summary('acquisition')['admitted']==0
    assert transfer(CompactStore(store.path),identity,crm)['status']=='TRANSFERRED'
    assert budgets.summary('acquisition')['admitted']==1
    assert [resource for resource,payload in crm.creations].count('people')==1
    assert transfer(store,identity,crm)['status']=='TRANSFERRED'
    assert budgets.summary('acquisition')['admitted']==1


def test_existing_crm_person_and_two_candidate_ids_never_count_twice(tmp_path,monkeypatch):
    from scripts import linkedin_queue
    monkeypatch.setattr(linkedin_queue,'ROOT',tmp_path)
    store,budgets,discovery,qualification,nb,identity,crm=context(tmp_path)
    confirm(discovery);qualify(qualification,identity)
    assert transfer(store,identity,crm)['status']=='TRANSFERRED'
    data=store.candidate(identity)['data']
    # A second local identity refers to the exact already-admitted CRM person.
    other=store.put_candidate({**data,'journal_contact_id':'other','discovery_identity_required':False,'discovery_task_id':None},
        lane='qualification',candidate_id='other')
    assert transfer(store,other,crm)['status']=='TRANSFERRED'
    assert store.contact(other)['person_id']==store.contact(identity)['person_id']
    assert budgets.summary('acquisition')['admitted']==1


def test_admission_publication_and_counter_rollback_together(tmp_path,monkeypatch):
    import scripts.compact_crm as module
    from scripts import linkedin_queue
    monkeypatch.setattr(linkedin_queue,'ROOT',tmp_path)
    store,budgets,discovery,qualification,nb,identity,crm=context(tmp_path)
    confirm(discovery);qualify(qualification,identity)
    original=module._record_acquisition_admission
    def interrupted(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('exit after count insert before publication commit')
    monkeypatch.setattr(module,'_record_acquisition_admission',interrupted)
    assert transfer(store,identity,crm)['status']=='CRM_ACCESS_OR_CONTRACT_REQUIRED'
    assert store.contact(identity) is None and budgets.summary('acquisition')['admitted']==0
    monkeypatch.setattr(module,'_record_acquisition_admission',original)
    assert transfer(store,identity,crm)['status']=='TRANSFERRED'
    assert budgets.summary('acquisition')['admitted']==1
    assert [resource for resource,payload in crm.creations].count('people')==1


def test_goal_blocks_new_crm_creation_without_discarding_paid_result(tmp_path,monkeypatch):
    from scripts import linkedin_queue
    monkeypatch.setattr(linkedin_queue,'ROOT',tmp_path)
    store,budgets,discovery,qualification,nb,identity,crm=context(tmp_path,goal=1)
    confirm(discovery);qualify(qualification,identity)
    budgets.record_admitted('acquisition','crm-person:other')
    before=store.candidate(identity)
    assert transfer(store,identity,crm)=={'status':'WAITING','reason':'ADMISSIBLE_GOAL_REACHED'}
    assert not crm.creations
    assert store.candidate(identity)==before
    assert nb.submissions


def test_general_source_imports_cannot_claim_discovery_or_brief_and_keep_optional_linkedin(tmp_path):
    store=CompactStore(tmp_path/'journal.sqlite');store.set_authority('qualification');store.set_authority('crm')
    row={'source_file':'fixture.xlsx','first_name':'Alice','last_name':'Tazi','company_name':'Example SA',
        'raw_email':'alice.tazi@example.org','acquisition_brief_id':'forged','acquisition_brief_version':1,
        'discovery_identity_required':True,'discovery_task_id':'forged'}
    import_compact(store,[row]);identity=store.candidates()[0]['id']
    data=store.candidate(identity)['data']
    assert not data['acquisition_brief_id'] and not data['discovery_identity_required'] and not data['discovery_task_id']
    nb=NB();qualification=CompactQualification(store,no2bounce=nb,cost_guard=lambda **kwargs:True,hygiene=lambda email:'PASS',clock=lambda:AT)
    qualify(qualification,identity)
    assert transfer(store,identity,FakeCRM())['status']=='TRANSFERRED'


def test_preexisting_remote_person_is_reused_without_new_acquisition_count(tmp_path,monkeypatch):
    from scripts import linkedin_queue
    monkeypatch.setattr(linkedin_queue,'ROOT',tmp_path)
    store,budgets,discovery,qualification,nb,identity,crm=context(tmp_path)
    confirm(discovery);qualify(qualification,identity)
    crm.rows['companies']['old-company']={'id':'old-company','name':'Example SA',
        'domainName':{'primaryLinkUrl':'https://example.org','primaryLinkLabel':'example.org'}}
    crm.rows['people']['old-person']={'id':'old-person','name':{'firstName':'Alice','lastName':'Tazi'},
        'emails':{'primaryEmail':'alice.tazi@example.org'},'companyId':'old-company','campaignStatus':'READY_TO_MAIL'}
    assert transfer(store,identity,crm)['status']=='TRANSFERRED'
    assert budgets.summary('acquisition')['admitted']==0
    assert not crm.creations


def test_concurrent_transfers_cannot_publish_beyond_goal(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event
    from scripts import linkedin_queue
    monkeypatch.setattr(linkedin_queue,'ROOT',tmp_path)
    store,budgets,discovery,qualification,nb,identity,crm=context(tmp_path,goal=1)
    confirm(discovery);qualify(qualification,identity)
    data=store.candidate(identity)['data']
    second=store.put_candidate({**data,'journal_contact_id':'second','first_name':'Sara',
        'raw_email':'sara.tazi@example.org','clean_email':'sara.tazi@example.org','validated_email':'sara.tazi@example.org',
        'linkedin_url':None,'linkedin_search_status':'PENDING','linkedin_proof_uri':None,'linkedin_task_id':None,
        'qualification_applied_tasks':[]},lane='qualification',candidate_id='second',status='QUALIFIED')
    discovery.confirmation.enqueue(second)
    def confirmed(payload):
        value=extraction(payload);value['profile']['full_name']='Sara Tazi'
        value['profile']['source_url']='https://www.linkedin.com/in/sara-tazi';value['linkedin_url']='https://www.linkedin.com/in/sara-tazi'
        return value
    assert discovery.confirmation.run_one(runner=confirmed)['status']=='IMPORTED'
    barrier=Barrier(2);finished=Event()
    def run(candidate_id):
        barrier.wait(timeout=5)
        try:
            result=transfer(store,candidate_id,crm)
        except RuntimeError as exc:
            assert str(exc)=='WORKFLOW_BUSY'
            assert finished.wait(timeout=5)
            result=transfer(store,candidate_id,crm)
        if result['status']=='TRANSFERRED':
            finished.set()
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,[identity,second]))
    assert sorted(r['status'] for r in results)==['TRANSFERRED','WAITING']
    assert next(r for r in results if r['status']=='WAITING')['reason']=='ADMISSIBLE_GOAL_REACHED'
    assert budgets.summary('acquisition')['admitted']==1
    assert [resource for resource,payload in crm.creations].count('people')==1


def test_unlaunched_brief_revision_cannot_relax_transfer_goal(tmp_path,monkeypatch):
    from scripts import linkedin_queue
    monkeypatch.setattr(linkedin_queue,'ROOT',tmp_path)
    store,budgets,discovery,qualification,nb,identity,crm=context(tmp_path,goal=1)
    confirm(discovery);qualify(qualification,identity)
    budgets.record_admitted('acquisition','crm-person:other')
    revised=copy.deepcopy(budgets.brief('acquisition')['definition']);revised['admissible_goal']=100
    budgets.create_brief('acquisition',revised,command_id='unlaunched-larger-goal')
    assert transfer(store,identity,crm)['reason']=='ADMISSIBLE_GOAL_REACHED'
    assert not crm.creations
