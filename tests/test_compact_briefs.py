from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from scripts.compact_briefs import BriefBudget, default_acquisition_brief
from scripts.compact_store import CompactStore


def definition(cap='10'):
    return {**default_acquisition_brief(), 'searches': ['industrie Casablanca'],
            'volume_caps': {'items': 30}, 'budgets': {'currency': 'USD', 'campaign_cap': cap,
            'providers': {'apify': {'money_cap': cap, 'credit_caps': {'records': '100'}, 'cost_basis': 'verified-account-contract-v1'},
                          'no2bounce': {'money_cap': '10', 'credit_caps': {'checks': '30'}, 'cost_basis': 'verified-account-credit-v1'}}}}


@pytest.fixture
def budget(tmp_path):
    b = BriefBudget(CompactStore(tmp_path / 'journal.sqlite'))
    b.create_brief('campaign', definition(), command_id='create')
    b.launch('campaign', 1, command_id='launch')
    return b


def test_defaults_have_no_invented_limits_and_cannot_launch(tmp_path):
    b = BriefBudget(CompactStore(tmp_path / 'journal.sqlite'))
    d = default_acquisition_brief()
    assert (d['zone'], d['target'], d['admissible_goal'], d['purpose']) == ('Maroc', 'tous secteurs', 20, 'new_acquisition')
    b.create_brief('campaign', d, command_id='a')
    assert 'CAMPAIGN_CAP_REQUIRED' in b.blockers('campaign')
    assert 'VOLUME_CAPS_REQUIRED' in b.blockers('campaign')
    with pytest.raises(RuntimeError, match='REQUIRED'):
        b.launch('campaign', 1, command_id='b')


def test_create_launch_replay_and_old_version(budget):
    assert budget.create_brief('campaign', definition(), command_id='create')['version'] == 1
    assert budget.launch('campaign', 1, command_id='launch')['state'] == 'LAUNCHED'
    new = definition(); new['zone'] = 'Rabat'
    with pytest.raises(ValueError, match='COMMAND_REPLAY_CONTENT_CHANGED'):
        budget.create_brief('campaign', new, command_id='create')
    assert budget.create_brief('campaign', new, command_id='create-v2')['version'] == 2
    with pytest.raises(RuntimeError, match='STALE_BRIEF_VERSION'):
        budget.launch('campaign', 1, command_id='late-confirmation')
    with pytest.raises(RuntimeError, match='STALE_BRIEF_VERSION'):
        budget.reserve('campaign', 1, 'apify', key='run', amount='1')
    budget.launch('campaign', 2, command_id='launch-v2')
    with pytest.raises(ValueError, match='COMMAND_REPLAY_CONTENT_CHANGED'):
        budget.launch('campaign', 2, command_id='launch')


def test_reservation_replay_and_campaign_cumulative_all_providers(budget):
    first = budget.reserve('campaign', 1, 'apify', key='run-1', amount='6', payload={'run':1})
    assert budget.reserve('campaign', 1, 'apify', key='run-1', amount='6', payload={'run':1}) == first
    with pytest.raises(ValueError, match='RESERVATION_REPLAY_CONTENT_CHANGED'):
        budget.reserve('campaign', 1, 'apify', key='run-1', amount='6', payload={'run':2})
    with pytest.raises(RuntimeError, match='CAMPAIGN_BUDGET_REACHED'):
        budget.reserve('campaign', 1, 'no2bounce', key='run-2', amount='5')
    assert budget.summary('campaign')['money_committed'] == '6'
    newer = definition(); budget.create_brief('campaign', newer, command_id='v2')
    budget.launch('campaign', 2, command_id='launch-v2')
    with pytest.raises(RuntimeError, match='CAMPAIGN_BUDGET_REACHED'):
        budget.reserve('campaign', 2, 'apify', key='run-3', amount='5')


def test_concurrent_reservations_do_not_spend_same_balance(budget):
    def reserve(key):
        try:
            return budget.reserve('campaign', 1, 'apify', key=key, amount='6')['state']
        except RuntimeError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(reserve, ['run-1','run-2']))
    assert sorted(result) == ['CAMPAIGN_BUDGET_REACHED', 'RESERVED']
    assert budget.summary('campaign')['money_committed'] == '6'


def test_ambiguous_keeps_reservation_certain_failure_releases(budget):
    budget.reserve('campaign', 1, 'apify', key='run-1', amount='8')
    budget.mark_submitted('run-1')
    budget.reconcile('run-1', 'UNKNOWN')
    reopened = BriefBudget(CompactStore(budget.store.path))
    assert reopened.reservation('run-1')['state'] == 'UNKNOWN'
    with pytest.raises(RuntimeError, match='CAMPAIGN_BUDGET_REACHED'):
        reopened.reserve('campaign', 1, 'apify', key='run-2', amount='3')
    reopened.reconcile('run-1', 'FAILED_CERTAIN')
    assert reopened.summary('campaign')['money_committed'] == '0'
    reopened.reserve('campaign', 1, 'apify', key='run-2', amount='3')
    with pytest.raises(RuntimeError, match='ALREADY_RECONCILED'):
        reopened.reconcile('run-1', 'CONFIRMED', actual_amount='1')


def test_actual_cost_and_overrun_blocks_further_calls(budget):
    budget.reserve('campaign', 1, 'apify', key='run-1', amount='3', credits={'records':'10'})
    budget.reconcile('run-1', 'CONFIRMED', actual_amount='2', actual_credits={'records':'7'})
    assert budget.summary('campaign')['providers']['apify'] == {'money':'2','credits':{'records':'7'}}
    budget.reserve('campaign', 1, 'apify', key='run-2', amount='2')
    assert budget.reconcile('run-2', 'CONFIRMED', actual_amount='4')['state'] == 'OVERRUN'
    assert budget.summary('campaign')['money_committed'] == '6'
    with pytest.raises(RuntimeError, match='OVERRUN'):
        budget.reserve('campaign', 1, 'apify', key='run-3', amount='0')


@pytest.mark.parametrize('amount', [None, '-1', 'NaN', 'Infinity', True])
def test_unknown_or_invalid_cost_is_blocked(budget, amount):
    with pytest.raises((RuntimeError, ValueError)):
        budget.reserve('campaign', 1, 'apify', key='bad', amount=amount)
    assert budget.reservation('bad') is None


def test_credit_and_quantity_caps_are_independent(budget):
    budget.reserve('campaign', 1, 'apify', key='a', amount='0', credits={'records':'100'}, quantity=29)
    with pytest.raises(RuntimeError, match='CREDIT_LIMIT_REACHED'):
        budget.reserve('campaign', 1, 'apify', key='b', amount='0', credits={'records':'1'})
    with pytest.raises(RuntimeError, match='VOLUME_LIMIT_REACHED'):
        budget.reserve('campaign', 1, 'apify', key='c', amount='0', quantity=2)
    with pytest.raises(RuntimeError, match='CREDIT_CAP_REQUIRED'):
        budget.reserve('campaign', 1, 'apify', key='d', amount='0', credits={'usd':'1'})
    with pytest.raises(RuntimeError, match='VOLUME_CAP_REQUIRED'):
        budget.reserve('campaign', 1, 'apify', key='e', amount='0', quantity_unit='companies')


def test_cap_or_currency_changes_do_not_reset_consumption(budget):
    budget.reserve('campaign', 1, 'apify', key='a', amount='8')
    changed = definition('7'); changed['budgets']['currency'] = 'MAD'
    budget.create_brief('campaign', changed, command_id='changed')
    reasons = budget.blockers('campaign', 2)
    assert 'CAMPAIGN_CAP_BELOW_COMMITTED' in reasons
    assert 'CURRENCY_CHANGED_WITH_EXISTING_RESERVATIONS' in reasons
    with pytest.raises(RuntimeError):
        budget.launch('campaign', 2, command_id='launch-v2')


def test_guard_and_goal_stop_without_admission_loosening(budget):
    guard = budget.guard('campaign', 1, {'no2bounce':{'amount':'0.1','credits':{'checks':'1'}}})
    op = guard(provider='no2bounce', operation_id='check', quantity=2, payload={'email':'a@company.test'})
    assert op['amount'] == '0.2' and op['credits'] == {'checks':'2'}
    assert guard.submitted(operation_id='check')['state'] == 'SUBMITTED'
    assert guard.unknown(operation_id='check')['state'] == 'UNKNOWN'
    assert guard.reconcile(operation_id='check', actual_amount='0.15', actual_credits={'checks':'2'})['state'] == 'CONFIRMED'
    with pytest.raises(RuntimeError, match='COST_UNEVALUABLE'):
        guard(provider='missing', operation_id='missing', quantity=1, payload={})
    for i in range(20):
        budget.record_admitted('campaign', 'c'+str(i))
    budget.record_admitted('campaign', 'c0')
    assert budget.summary('campaign')['admitted'] == 20
    with pytest.raises(RuntimeError, match='ADMISSIBLE_GOAL_REACHED'):
        guard(provider='no2bounce', operation_id='next', quantity=1, payload={})


def test_strategy_has_only_dossier_and_never_launches_or_versions(budget):
    def specialist(dossier):
        assert set(dossier) == {'target','zone','admissible_goal','searches'}
        return {'searches':['industrie Rabat'], 'rationale':'Recherche finie pertinente'}
    assert budget.propose_strategy('campaign',1,specialist)['searches'] == ['industrie Rabat']
    assert budget.brief('campaign')['version'] == 1
    with pytest.raises(ValueError, match='INVALID_STRATEGY_SCHEMA'):
        budget.propose_strategy('campaign',1,lambda dossier: {'searches':['x'],'rationale':'x','budgets':{}})


def test_reconciliation_cannot_silently_omit_credit_dimensions(budget):
    budget.reserve('campaign', 1, 'apify', key='a', amount='1', credits={'records':'2'})
    for actual in (None, {}):
        with pytest.raises(ValueError, match='ACTUAL_CREDITS_REQUIRED'):
            budget.reconcile('a', 'CONFIRMED', actual_amount='1', actual_credits=actual)
    assert budget.reservation('a')['state'] == 'RESERVED'
    assert budget.summary('campaign')['providers']['apify']['credits'] == {'records':'2'}
    result = budget.reconcile('a', 'CONFIRMED', actual_amount='1', actual_credits={'records':'2','unreserved':'1'})
    assert result['state'] == 'OVERRUN'


def test_provider_ceiling_without_campaign_ceiling_reached(budget):
    newer = definition('100'); newer['budgets']['providers']['apify']['money_cap'] = '1'
    budget.create_brief('campaign', newer, command_id='v2')
    budget.launch('campaign', 2, command_id='launch-v2')
    with pytest.raises(RuntimeError, match='PROVIDER_BUDGET_REACHED'):
        budget.reserve('campaign',2,'apify',key='expensive',amount='2')


def test_small_reservation_is_not_rounded_out_of_large_balance(tmp_path):
    b = BriefBudget(CompactStore(tmp_path / 'journal.sqlite'))
    cap = '1000000000000000000000000000000'
    b.create_brief('campaign', definition(cap), command_id='create')
    b.launch('campaign', 1, command_id='launch')
    b.reserve('campaign', 1, 'apify', key='tiny', amount='0.00000001')
    with pytest.raises(RuntimeError, match='CAMPAIGN_BUDGET_REACHED'):
        b.reserve('campaign', 1, 'apify', key='large', amount=cap)


def test_unknown_remote_reconciliation_keeps_the_entire_reservation(budget):
    budget.reserve('campaign',1,'apify',key='uncertain-run',amount='7',credits={'records':'25'})
    budget.reconcile('uncertain-run','UNKNOWN')
    restored=budget.mark_submitted('uncertain-run')
    assert restored['state']=='SUBMITTED'
    assert restored['amount']=='7' and restored['credits']=={'records':'25'}
    assert budget.summary('campaign')['money_committed']=='7'
    budget.reconcile('uncertain-run','CONFIRMED',actual_amount='6',actual_credits={'records':'20'})
    assert budget.mark_submitted('uncertain-run')['state']=='CONFIRMED'


def test_guard_preflight_is_readonly_and_blocks_unknown_estimate(budget):
    guard=budget.guard('campaign',1,{'apify':{'amount':'2','credits':{'records':'3'}}})
    before=budget.summary('campaign')
    assert guard.preflight(provider='apify',quantity=2)=={'amount':'4','credits':{'records':'6'},'quantity':2,'quantity_unit':'items'}
    assert budget.summary('campaign')==before
    with pytest.raises(RuntimeError,match='COST_UNEVALUABLE'):
        guard.preflight(provider='missing',quantity=1)
