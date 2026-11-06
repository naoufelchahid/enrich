"""Current rules cannot fall back to a historical model, exception or cadence."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from scripts.mailing_policy import load_policy, send_slot, validate_policy
from scripts.retire_mailing_rules import retire
from scripts.telegram_approval import create_draft, process_update, submit_draft
from scripts.thunderbird_client import ThunderbirdClient
from scripts.workflow_sender import send_approved
from tests.test_workflow_refactor import state, sample_draft, approve


def explicit_policy():
    return {'timezone':'UTC','weekdays':[5,6],'start_time':'21:13',
            'end_time':'23:59','min_interval_minutes':17}


def configure(store, policy):
    with store.connection() as c:
        c.execute("INSERT INTO workflow_meta VALUES('mailing_policy',?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(policy),))


def test_missing_policy_does_not_reuse_any_historical_schedule(state):
    store,lead,telegram,breaker=state
    with store.connection() as c:
        c.execute("DELETE FROM workflow_meta WHERE key='mailing_policy'")
        c.execute("INSERT INTO workflow_meta VALUES('immediate_campaign',?)",(json.dumps({
            'id':'old','min_interval_minutes':10,'recipients':[lead['clean_email']],
            'date':'2026-11-07','limit':30}),))
    draft=sample_draft(store,lead)
    approve(store,telegram,draft)
    mail=ThunderbirdClient(dry_run=True)
    assert send_approved(store,mail,breaker,'101',at=datetime(2026,11,7,21,13,tzinfo=timezone.utc))['status']=='MAILING_POLICY_REQUIRED'
    assert not mail._sent_emails
    with store.connection() as c:
        assert c.execute('SELECT count(*) FROM workflow_sends').fetchone()[0]==0


@pytest.mark.parametrize('change',[{}, {'min_interval_minutes':0}, {'min_interval_minutes':True},
    {'weekdays':[]}, {'weekdays':[7]}, {'weekdays':[{}]}, {'timezone':'Unknown/Zone'},
    {'start_time':'8:00'}, {'start_time':'23:59','end_time':'21:13'}])
def test_invalid_or_partial_cadence_has_no_defaults(change):
    policy={**explicit_policy(),**change} if change else {}
    with pytest.raises(ValueError):
        validate_policy(policy)


def test_explicit_weekend_evening_cadence_controls_slots():
    at=datetime(2026,11,7,21,13,tzinfo=timezone.utc)
    assert send_slot(at,explicit_policy())==at.isoformat()
    assert send_slot(at+timedelta(minutes=17),explicit_policy())
    assert send_slot(at+timedelta(minutes=10),explicit_policy()) is None
    assert send_slot(at-timedelta(minutes=1),explicit_policy()) is None
    assert send_slot(at+timedelta(days=2),explicit_policy()) is None


def test_custom_interval_is_global_for_initial_and_reply(state):
    store,lead,telegram,breaker=state
    configure(store,explicit_policy())
    initial=sample_draft(store,lead)
    reply=sample_draft(store,lead,kind='REPLY')
    approve(store,telegram,initial)
    approve(store,telegram,reply,update_id=2)
    at=datetime.now(timezone.utc)+timedelta(days=1)
    while at.weekday()!=5:
        at+=timedelta(days=1)
    at=at.replace(hour=21,minute=13,second=0,microsecond=0)
    mail=ThunderbirdClient(dry_run=True)
    assert send_approved(store,mail,breaker,'101',at=at,clock=lambda:at+timedelta(seconds=20))['status']=='SENT'
    assert send_approved(store,mail,breaker,'101',at=at+timedelta(minutes=17))['status']=='INTERVAL_NOT_ELAPSED'
    assert send_approved(store,mail,breaker,'101',at=at+timedelta(minutes=34))['status']=='SENT'
    with store.connection() as c:
        assert [r[0] for r in c.execute('SELECT kind FROM workflow_sends ORDER BY confirmed_at')]==['REPLY','INITIAL']


def historical_draft(store,lead):
    return create_draft(store,lead['journal_contact_id'],lead['clean_email'],'Ancien objet','Ancien texte',
                        {'pattern_id':'rythme_travail','immediate_only':True})


def test_old_draft_cannot_be_submitted_approved_or_sent(state):
    store,lead,telegram,breaker=state
    draft=historical_draft(store,lead)
    assert submit_draft(store,telegram,draft['id'])['status']=='RETIRED_MAILING_RULES'
    with store.connection() as c:
        c.execute('UPDATE workflow_drafts SET telegram_message_id=12 WHERE id=?',(draft['id'],))
    update={'update_id':55,'callback_query':{'from':{'id':101},'data':'approve:'+draft['callback_token'],
            'message':{'message_id':12,'chat':{'id':202}}}}
    assert process_update(store,telegram,update)['status']=='RETIRED_MAILING_RULES'
    with store.connection() as c:
        c.execute("UPDATE workflow_drafts SET status='APPROVED',approved_by='101',approved_at=? WHERE id=?",
                  ((datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),draft['id']))
    mail=ThunderbirdClient(dry_run=True)
    from tests.test_workflow_refactor import next_morning
    assert send_approved(store,mail,breaker,'101',at=next_morning())['status']=='RETIRED_MAILING_RULES'
    assert not mail._sent_emails and not telegram.sent


def test_exact_v2_without_independent_review_is_blocked(state):
    store,lead,telegram,breaker=state
    draft=sample_draft(store,lead)
    with store.connection() as c:
        c.execute("UPDATE workflow_operations SET status='NEEDS_REVIEW' WHERE kind='PILOT_COPY_CRITIQUE'")
    assert submit_draft(store,telegram,draft['id'])['status']=='INDEPENDENT_COPY_REVIEW_REQUIRED'
    assert not telegram.sent


def test_retirement_is_readonly_in_preview_then_preserves_evidence_and_is_idempotent(state,monkeypatch):
    store,lead,telegram,breaker=state
    old=historical_draft(store,lead)
    current=sample_draft(store,lead)
    # Simulate an old pending row alongside the current version.
    with store.connection() as c:
        c.execute("UPDATE workflow_drafts SET status='PENDING' WHERE id=?",(old['id'],))
        c.execute("INSERT INTO workflow_meta VALUES('immediate_campaign','exact old policy')")
        before=dict(c.execute('SELECT * FROM workflow_drafts WHERE id=?',(old['id'],)).fetchone())
    original=store.path.read_bytes() if hasattr(store.path,'read_bytes') else open(store.path,'rb').read()
    plan=retire(store.path)
    assert plan['retired_drafts']==1 and plan['retired_policies']==1 and not plan['applied']
    assert original==open(store.path,'rb').read()
    monkeypatch.setenv('WORKFLOW_STOP_AT_DRAFT','1')
    result=retire(store.path,apply=True)
    assert result['retired_drafts']==1 and result['sends']==0
    assert retire(store.path,apply=True)['retired_drafts']==0
    with store.connection() as c:
        assert c.execute('SELECT status FROM workflow_drafts WHERE id=?',(old['id'],)).fetchone()[0]=='SUPERSEDED'
        assert c.execute('SELECT status FROM workflow_drafts WHERE id=?',(current['id'],)).fetchone()[0]=='PENDING'
        assert not c.execute("SELECT 1 FROM workflow_meta WHERE key='immediate_campaign'").fetchone()
        events=c.execute("SELECT payload FROM workflow_events WHERE kind='historical_mailing_rules_retired'").fetchall()
        assert len(events)==1
        assert json.loads(events[0][0])['drafts']==[before]
    assert not telegram.sent


def test_retirement_requires_pause_and_reconciled_transport(state,monkeypatch):
    store,lead,telegram,breaker=state
    monkeypatch.delenv('WORKFLOW_STOP_AT_DRAFT',raising=False)
    with pytest.raises(ValueError,match='MAILING_PAUSE_REQUIRED'):
        retire(store.path,apply=True)
    monkeypatch.setenv('WORKFLOW_STOP_AT_DRAFT','1')
    with store.connection() as c:
        c.execute("INSERT INTO workflow_sends VALUES('x','d','c','INITIAL','slot','UNKNOWN',?,NULL,NULL)",
                  (datetime.now(timezone.utc).isoformat(),))
    with pytest.raises(ValueError,match='SEND_RECONCILIATION_REQUIRED'):
        retire(store.path,apply=True)


def test_confirmed_unsent_retry_still_requires_exact_evidence(state):
    from tests.test_workflow_refactor import next_morning
    store,lead,telegram,breaker=state
    draft=sample_draft(store,lead)
    approve(store,telegram,draft)
    mail=ThunderbirdClient(dry_run=True)
    at=next_morning()
    with patch.object(mail,'send_email',side_effect=TimeoutError('unknown')):
        first=send_approved(store,mail,breaker,'101',at=at)
    with store.connection() as c:
        c.execute("UPDATE workflow_sends SET state='NOT_SENT_CONFIRMED'")
        c.execute("UPDATE workflow_drafts SET status='APPROVED'")
    assert send_approved(store,mail,breaker,'101',at=at+timedelta(minutes=45))['status']=='NO_APPROVED_EMAIL'
    store.event('send_not_sent_confirmed',lead['journal_contact_id'],{'send_id':first['send_id']})
    assert send_approved(store,mail,breaker,'101',at=at+timedelta(minutes=45))['status']=='SENT'
    with store.connection() as c:
        assert c.execute('SELECT count(*) FROM workflow_sends').fetchone()[0]==1
