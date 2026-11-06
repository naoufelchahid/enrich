import copy
import json

import pytest

from scripts.compact_apify import CompactApify
from scripts.compact_briefs import BriefBudget, default_acquisition_brief
from scripts.compact_store import CompactStore
from scripts.compact_telegram import CompactTelegramController
from scripts.telegram_approval import poll_updates, process_update
from tests.test_compact_apify import FakeApify


class Telegram:
    operator_id='123'
    chat_id='-456'
    def __init__(self):
        self.updates=[];self.calls=[]
    def call(self,method,payload):
        self.calls.append((method,payload))
        if method=='getUpdates':
            return self.updates
        assert method=='sendMessage'
        return {'message_id':1000+len(self.calls)}
    def notify(self,*args):
        pytest.fail('No notification may be sent by the controller')


def update(identity,text,*,actor=123,chat=-456):
    return {'update_id':identity,'message':{'message_id':identity,'from':{'id':actor},'chat':{'id':chat},'text':text}}


def definition():
    return {**default_acquisition_brief(),'searches':['industrie Casablanca','industrie Rabat'],
            'volume_caps':{'companies':10},'budgets':{'currency':'USD','campaign_cap':'10',
            'providers':{'apify':{'money_cap':'10','credit_caps':{},'cost_basis':'reviewed fixture price'}}}}


OPTIONS={'limits_by_search':{'industrie Casablanca':2,'industrie Rabat':2},'run_limit':2,'maximum_charge':'2'}


@pytest.fixture
def setup(tmp_path):
    store=CompactStore(tmp_path/'journal.sqlite')
    store.set_authority('apify');store.set_authority('discovery')
    budgets=BriefBudget(store);telegram=Telegram();transport=FakeApify()
    apify=CompactApify(store,budgets,transport,lambda dossier:{'relevant':True,'facts':[dossier['company']['title']],'uncertainties':[],'reason':'fixture'})
    controller=CompactTelegramController(store,budgets,telegram,apify=apify)
    return store,budgets,telegram,transport,controller


def create(controller,identity=1,data=None):
    message=update(identity,'/brief create b '+json.dumps(data or definition()))
    return controller.deliver(message,controller.handle(message))


def launch(controller,identity=2,version=1):
    return controller.handle(update(identity,f'/brief launch b {version} apify '+json.dumps(OPTIONS)))


def test_complete_brief_presented_before_explicit_enqueue_no_provider(setup):
    store,budgets,telegram,transport,controller=setup
    shown=create(controller)
    assert shown['status']=='PRESENTED'
    for text in ('version 1','Maroc','industrie Casablanca','Quantités','Budgets','Objectif admissible','Arrêts'):
        assert text in shown['text']
    assert budgets.brief('b')['state']=='DRAFT'
    queued=launch(controller)
    assert queued['status']=='QUEUED' and len(queued['task_ids'])==2
    assert budgets.brief('b')['state']=='LAUNCHED'
    assert transport.submits==[]
    assert all(method=='sendMessage' for method,payload in telegram.calls)
    assert budgets.summary('b')['money_committed']=='0'


@pytest.mark.parametrize('actor,chat',[(999,-456),(123,-999)])
def test_account_and_chat_authorized_before_any_mutation(setup,actor,chat):
    store,budgets,telegram,transport,controller=setup
    assert controller.handle(update(1,'/brief create b',actor=actor,chat=chat))['status']=='UNAUTHORIZED'
    assert budgets.brief('b') is None
    with store.connection() as c:
        assert c.execute('SELECT count(*) FROM workflow_control_commands').fetchone()[0]==0


def test_update_and_identical_command_replays_never_duplicate(setup):
    store,budgets,telegram,transport,controller=setup
    first=create(controller)
    assert create(controller)['replay']
    assert create(controller,identity=11)['replay']
    assert budgets.brief('b')['version']==1
    original=launch(controller)
    assert launch(controller)['replay']
    resent=launch(controller,identity=22)
    assert resent['replay'] and resent['task_ids']==original['task_ids']
    assert controller.handle(update(2,'/brief create evil'))['status']=='UPDATE_REPLAY_CONTENT_CHANGED'
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM workflow_tasks WHERE kind='APIFY_ACQUIRE'").fetchone()[0]==2


def test_version_change_invalidates_old_confirmation(setup):
    store,budgets,telegram,transport,controller=setup
    create(controller)
    d=definition();d['target']='industrie'
    create(controller,identity=3,data=d)
    assert launch(controller,identity=4)['reason']=='STALE_BRIEF_VERSION'
    assert launch(controller,identity=5,version=2)['status']=='QUEUED'


def test_brief_must_have_been_presented_to_this_operator(setup):
    store,budgets,telegram,transport,controller=setup
    budgets.create_brief('b',definition(),command_id='external-create')
    assert launch(controller)['reason']=='PRESENT_EXACT_BRIEF_VERSION_FIRST'
    message=update(3,'/brief show b 1')
    assert controller.deliver(message,controller.handle(message))['status']=='PRESENTED'
    assert launch(controller,identity=4)['status']=='QUEUED'


def test_incomplete_cost_or_unconfigured_service_stays_waiting(setup):
    store,budgets,telegram,transport,controller=setup
    message=update(1,'/brief create b');controller.deliver(message,controller.handle(message))
    assert 'CAMPAIGN_CAP_REQUIRED' in launch(controller)['reason']
    create(controller,identity=3)
    controller.apify=None
    assert launch(controller,identity=4,version=2)['reason']=='APIFY_TRANSPORT_NOT_CONFIGURED'
    assert budgets.brief('b')['state']=='DRAFT'


def test_status_cannot_mutate_pause_budgets_or_silent_drafts(setup):
    store,budgets,telegram,transport,controller=setup
    create(controller)
    with store.connection() as c:
        c.execute("INSERT INTO workflow_meta VALUES('telegram_submission_paused','operator pause')")
        c.execute("INSERT INTO workflow_meta VALUES('immediate_campaign','unchanged policy')")
    before=budgets.summary('b')
    result=controller.handle(update(3,'/workflows apify'))
    assert result['status']=='STATUS' and result['telegram_submission_paused']
    assert budgets.summary('b')==before
    with store.connection() as c:
        assert c.execute("SELECT value FROM workflow_meta WHERE key='immediate_campaign'").fetchone()[0]=='unchanged policy'
    assert not transport.submits


def test_crash_between_search_enqueues_resumes_local_intent_without_duplicate(setup,monkeypatch):
    store,budgets,telegram,transport,controller=setup
    create(controller)
    original=store.create_task;count=0
    def interrupted(*args,**kwargs):
        nonlocal count
        count+=1
        if count==2:
            raise KeyboardInterrupt('exit between finite searches')
        return original(*args,**kwargs)
    monkeypatch.setattr(store,'create_task',interrupted)
    with pytest.raises(KeyboardInterrupt):
        launch(controller)
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM workflow_tasks WHERE kind='APIFY_ACQUIRE'").fetchone()[0]==1
    monkeypatch.setattr(store,'create_task',original)
    assert launch(controller)['status']=='QUEUED'
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM workflow_tasks WHERE kind='APIFY_ACQUIRE'").fetchone()[0]==2
    assert not transport.submits and budgets.summary('b')['money_committed']=='0'


def test_optional_poll_router_preserves_normal_callbacks(setup):
    store,budgets,telegram,transport,controller=setup
    telegram.updates=[update(1,'/brief create b'),update(2,'/workflows')]
    results=poll_updates(store,telegram,controller=controller)
    assert [r['status'] for r in results]==['PRESENTED','STATUS']
    callback={'update_id':3,'callback_query':{'from':{'id':123},'data':'approve:missing',
        'message':{'chat':{'id':-456},'message_id':99}}}
    assert process_update(store,telegram,callback,controller=controller)['status']=='STALE_CALLBACK'
    assert telegram.calls[0][0]=='getUpdates'


def test_unknown_presentation_is_never_resent_or_accepted_as_view(setup,monkeypatch):
    store,budgets,telegram,transport,controller=setup
    message=update(1,'/brief create b '+json.dumps(definition()))
    result=controller.handle(message)
    assert result['status']=='PREVIEW_READY'
    calls=[]
    def broken(method,payload):
        calls.append((method,payload));raise TimeoutError('unknown receipt')
    monkeypatch.setattr(telegram,'call',broken)
    assert controller.deliver(message,result)['delivery']=='TELEGRAM_PRESENTATION_RECONCILIATION_REQUIRED'
    assert controller.deliver(message,result)['delivery']=='TELEGRAM_PRESENTATION_RECONCILIATION_REQUIRED'
    assert len(calls)==1
    assert launch(controller)['reason']=='PRESENT_EXACT_BRIEF_VERSION_FIRST'
    with store.connection() as c:
        assert c.execute("SELECT status FROM workflow_operations WHERE kind='TELEGRAM_CONTROL_PRESENT'").fetchone()[0]=='UNKNOWN'


def test_crash_after_delivery_intent_never_sends_again(setup,monkeypatch):
    store,budgets,telegram,transport,controller=setup
    message=update(1,'/brief create b '+json.dumps(definition()))
    result=controller.handle(message)
    calls=[]
    def interrupted(method,payload):
        calls.append((method,payload));raise KeyboardInterrupt('process exit')
    monkeypatch.setattr(telegram,'call',interrupted)
    with pytest.raises(KeyboardInterrupt):
        controller.deliver(message,result)
    assert controller.deliver(message,result)['delivery']=='TELEGRAM_PRESENTATION_RECONCILIATION_REQUIRED'
    assert len(calls)==1


def test_linkedin_missing_cost_estimate_does_not_launch(setup):
    from scripts.compact_discovery import CompactDiscovery
    store,budgets,telegram,transport,controller=setup
    data=definition();data['volume_caps']['items']=20
    data['budgets']['providers']['linkedin_mcp']={'money_cap':'1','credit_caps':{},'cost_basis':'account contract'}
    create(controller,data=data)
    company=store.put_candidate({'company_name':'Example SA'},lane='discovery')
    controller.discovery=CompactDiscovery(store,budgets,list_tools=lambda:{},search_people=lambda **kwargs:pytest.fail('must not call'),
        researcher=lambda dossier:{},cost_guard=budgets.guard('b',1,{}))
    result=controller.handle(update(2,'/brief launch b 1 linkedin '+json.dumps({'company_ids':[company]})))
    assert result['reason']=='COST_UNEVALUABLE:linkedin_mcp'
    assert budgets.brief('b')['state']=='DRAFT'


def test_compact_send_tick_delivers_control_reply_with_default_controller(tmp_path,monkeypatch):
    from scripts.compact_campaigns import CompactCampaigns
    from scripts.circuit_breaker import CircuitBreaker
    monkeypatch.setenv('WORKFLOW_STOP_AT_DRAFT','1')
    store=CompactStore(tmp_path/'journal.sqlite');store.set_authority('campaigns')
    telegram=Telegram();telegram.updates=[update(1,'/workflows')]
    workflow=CompactCampaigns(store.path,root=tmp_path,telegram=telegram,
        breaker=CircuitBreaker(str(tmp_path/'breaker.json')),cleanup=lambda _: {'removed':[]})
    result=workflow.send_tick()
    assert result['send']['status']=='STOPPED_AT_DRAFT'
    assert result['telegram_updates']==1
    assert any(method=='sendMessage' and 'État des parcours' in payload['text'] for method,payload in telegram.calls)
    assert workflow._mail is None and workflow._enrichment is None and workflow._crm is None
