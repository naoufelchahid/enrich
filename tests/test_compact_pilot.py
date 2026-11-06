"""Exploratory pilot assignments and manual outcomes are persistent and bounded."""
import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from scripts.compact_copy import CompactCopy
from scripts.compact_pilot import CompactPilot
from scripts.compact_store import CompactStore
from scripts.compact_campaigns import CompactCampaigns
from scripts.workflow_store import encode
from tests.test_compact_copy import accept


STAMP=datetime(2026,11,10,10,tzinfo=timezone.utc)


def selector(instructions,data,schema,**kwargs):
    assert 'observed role' in instructions and 'outcomes' in instructions
    assert all(set(row)=={'contact_id','company_id','role'} for row in data['contacts'])
    return {'selected':[{'contact_id':row['contact_id'],'reason':'Rôle observé pertinent : '+row['role'],
                         'role_evidence':row['role']} for row in data['contacts'][:data['limit']]]}


def build(tmp_path,count=20,groups=None):
    store=CompactStore(tmp_path/'journal.sqlite')
    store.set_authority('campaigns')
    people=[]
    groups=groups or [1]*count
    company_ids=[f'company-{group}' for group,size in enumerate(groups) for _ in range(size)]
    for index in range(count):
        email=f'alice.tazi{index}@example.org'
        lead={'journal_contact_id':f'contact-{index:02d}','first_name':'Alice','last_name':f'Tazi{index}',
            'company_name':company_ids[index],'raw_email':email,'clean_email':email,'validated_email':email,
            'email_validation_source':'no2bounce','email_check_status':'VALID',
            'email_validated_at':STAMP.isoformat(),'civility':'Mme','job_title':'Directrice des opérations'}
        store.put_contact(lead,status='READY_TO_MAIL')
        with store.connection() as c:
            c.execute('UPDATE workflow_contacts SET person_id=?,company_id=?,admitted_at=? WHERE id=?',
                      (f'person-{index}',company_ids[index],STAMP.isoformat(),lead['journal_contact_id']))
        people.append({'id':f'person-{index}','name':{'firstName':'Alice','lastName':f'Tazi{index}'},
            'emails':{'primaryEmail':email},'companyId':company_ids[index],
            'jobTitle':'Directrice des opérations','campaignStatus':'READY_TO_MAIL'})
    class CRM:
        calls=0
        def workflow_list(self,resource):
            assert resource=='people'
            self.calls+=1
            return people
    crm=CRM()
    pilot=CompactPilot(store,crm=crm,selector=selector,clock=lambda:STAMP,rng=random.Random(13))
    return store,people,crm,pilot


def send_fixture(store,contact_id,stamp,*,approved=True,state='SENT'):
    from scripts.telegram_approval import create_draft
    contact=store.contact(contact_id)
    draft=create_draft(store,contact_id,contact['email'],'Objet simulé','Brouillon simulé',{})
    with store.connection() as c:
        c.execute('UPDATE workflow_drafts SET status=?,approved_at=? WHERE id=?',
                  ('SENT' if state=='SENT' else 'APPROVED',stamp.isoformat() if approved else None,draft['id']))
        c.execute('INSERT INTO workflow_sends VALUES (?,?,?,?,?,?,?,?,?)',
            ('send-'+contact_id,draft['id'],contact_id,'INITIAL','slot-'+contact_id,state,
             stamp.isoformat(),stamp.isoformat() if state=='SENT' else None,'{}'))
    return draft


def test_balanced_twenty_assignments_frozen_before_results_and_restart(tmp_path):
    store,people,crm,pilot=build(tmp_path)
    result=pilot.create('pilot')
    assert result['status']=='CREATED' and len(result['assignments'])==20
    assert sum(row['variant_id']=='V2' for row in result['assignments'])==10
    assert sum(row['variant_id']=='V5' for row in result['assignments'])==10
    with store.connection() as c:
        assert c.execute('SELECT count(*) FROM workflow_sends').fetchone()[0]==0
    first=result['assignments']
    send_fixture(store,first[0]['contact_id'],STAMP)
    restarted=CompactPilot(CompactStore(store.path),crm=crm,selector=lambda *a,**kw:pytest.fail('frozen'),clock=lambda:STAMP)
    assert restarted.create('pilot')['assignments']==first
    assert crm.calls==1
    with store.connection() as c:
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            c.execute("UPDATE workflow_pilot_assignments SET variant_id='other'")


def test_company_grouping_keeps_best_possible_balance(tmp_path):
    store,people,crm,pilot=build(tmp_path,20,[8,7,5])
    result=pilot.create('grouped')
    grouped={}
    for assignment in result['assignments']:
        grouped.setdefault(assignment['company_id'],set()).add(assignment['variant_id'])
    assert all(len(variants)==1 for variants in grouped.values())
    assert result['definition']['balance_gap']==4  # reachable nearest split is 8/12.
    assert result['definition']['dependent_company_groups']==3


def test_limit_admission_current_twenty_and_person_dedup(tmp_path):
    store,people,crm,pilot=build(tmp_path,24)
    people[0]['emails']['primaryEmail']='other@example.org'
    people[1]['campaignStatus']='DO_NOT_CONTACT'
    with store.connection() as c:
        c.execute("UPDATE workflow_contacts SET initial_sent_at=? WHERE id='contact-02'",(STAMP.isoformat(),))
    store.exclude(people[3]['emails']['primaryEmail'],'UNSUBSCRIBE')
    result=pilot.create('pilot')
    assert len(result['assignments'])==20
    assert not {f'contact-{n:02d}' for n in range(4)} & {r['contact_id'] for r in result['assignments']}
    with pytest.raises(ValueError,match='1_TO_20'):
        pilot.create('too-big',limit=21)
    assert pilot.create('second')['reason']=='NO_ADMISSIBLE_TWENTY_CONTACTS'


def test_role_selection_must_quote_observed_facts(tmp_path):
    store,people,crm,pilot=build(tmp_path,2)
    pilot.selector=lambda *a,**kw:{'selected':[{'contact_id':'contact-00','reason':'Invented role','role_evidence':'CEO'}]}
    assert pilot.create('pilot')['reason']=='ROLE_SELECTION_REVIEW_REQUIRED'
    assert not pilot.assignments('pilot')


def test_ordinary_writer_skips_assigned_contacts_while_pilot_drafts(tmp_path):
    store,people,crm,pilot=build(tmp_path,2)
    pilot.create('pilot')
    workflow=CompactCampaigns(store.path,root=tmp_path,agent_runner=lambda *a,**kw:pytest.fail('legacy writer must skip'))
    assert workflow.write_one(submit_to_telegram=False)['status']=='NO_DRAFT_NEEDED'
    pilot.copy_writer=CompactCopy(store,critic_runner=accept,clock=lambda:STAMP,output_dir=tmp_path/'reports/critique')
    result=pilot.prepare_drafts('pilot')
    assert result['sends']==0 and all(row['status']=='DRAFT_CREATED' for row in result['results'])
    assert all(row['status']=='ALREADY_DRAFTED' for row in pilot.prepare_drafts('pilot')['results'])
    with store.connection() as c:
        assert c.execute('SELECT count(*) FROM workflow_sends').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM workflow_drafts WHERE telegram_message_id IS NOT NULL').fetchone()[0]==0


def test_manual_reschedule_counts_one_converted_recipient(tmp_path):
    store,people,crm,pilot=build(tmp_path,2)
    assigned=pilot.create('pilot')['assignments']
    identity=assigned[0]['contact_id'];variant=assigned[0]['variant_id']
    send_fixture(store,identity,STAMP)
    pilot.clock=lambda:STAMP+timedelta(days=20)
    confirm=dict(event_id='event-1',contact_id=identity,slot=STAMP+timedelta(days=3),
                 occurred_at=STAMP+timedelta(days=1),evidence='Operator confirmed accepted slot')
    assert pilot.record_meeting('pilot','meeting-1','CONFIRMED',**confirm)['status']=='RECORDED'
    assert pilot.record_meeting('pilot','meeting-1','CONFIRMED',**confirm)['status']=='REPLAY'
    pilot.record_meeting('pilot','meeting-1','RESCHEDULED',event_id='event-2',contact_id=identity,
        slot=STAMP+timedelta(days=5),occurred_at=STAMP+timedelta(days=2),evidence='Operator confirmed replacement slot')
    pilot.record_meeting('pilot','meeting-1','HELD',event_id='event-3',contact_id=identity,
        occurred_at=STAMP+timedelta(days=5),evidence='Operator confirmed exchange took place')
    report=pilot.report('pilot')
    counts=report['variants'][variant]
    assert counts['reserved_recipients']==1 and counts['reservations']==1 and counts['held']==1
    assert counts['sent']==1 and counts['not_sent']==counts['assigned']-1
    assert counts['reservation_rate_sent']==1.0
    assert report['all_sent_windows_mature'] is True
    assert report['winner'] is None and report['automatic_changes'] is False


def test_click_ambiguous_booking_and_after_window_not_conversions(tmp_path):
    store,people,crm,pilot=build(tmp_path,2)
    assignment=pilot.create('pilot')['assignments'][0]
    identity=assignment['contact_id'];variant=assignment['variant_id']
    send_fixture(store,identity,STAMP)
    pilot.clock=lambda:STAMP+timedelta(days=20)
    assert pilot.record_meeting('pilot','click','CLICK',event_id='click',evidence='Clicked link')['status']=='IGNORED_CLICK'
    pilot.record_meeting('pilot','ambiguous','CONFIRMED',event_id='ambiguous',attribution='UNDETERMINED',
        slot=STAMP+timedelta(days=4),occurred_at=STAMP+timedelta(days=1),evidence='Different email; no confirmed match')
    pilot.record_meeting('pilot','late','CONFIRMED',event_id='late',contact_id=identity,
        slot=STAMP+timedelta(days=19),occurred_at=STAMP+timedelta(days=15),evidence='Confirmed outside observation window')
    report=pilot.report('pilot')
    assert report['variants'][variant]['reserved_recipients']==0
    assert report['undetermined_meetings']==1


def test_window_freezes_state_and_unsent_approvals_remain_visible(tmp_path):
    store,people,crm,pilot=build(tmp_path,2)
    assignments=pilot.create('pilot')['assignments']
    one,two=assignments
    send_fixture(store,one['contact_id'],STAMP)
    send_fixture(store,two['contact_id'],STAMP,approved=True,state='UNKNOWN')
    pilot.clock=lambda:STAMP+timedelta(days=20)
    pilot.record_meeting('pilot','meeting','CONFIRMED',event_id='confirmation',contact_id=one['contact_id'],
        slot=STAMP+timedelta(days=5),occurred_at=STAMP+timedelta(days=1),evidence='Accepted slot')
    pilot.record_meeting('pilot','meeting','HELD',event_id='held',contact_id=one['contact_id'],
        occurred_at=STAMP+timedelta(days=5),evidence='Meeting held')
    pilot.record_meeting('pilot','meeting','CANCELLED',event_id='later-correction',contact_id=one['contact_id'],
        occurred_at=STAMP+timedelta(days=16),evidence='Later event outside window')
    report=pilot.report('pilot')
    assert report['variants'][one['variant_id']]['held']==1
    assert report['variants'][one['variant_id']]['cancelled']==0
    counts=report['variants'][two['variant_id']]
    assert counts['approved']==1 and counts['sent']==0 and counts['not_sent']==1
    assert counts['incidents']==1
    early=pilot.report('pilot',at=STAMP+timedelta(days=2))
    assert early['variants'][one['variant_id']]['immature_sent']==1
    assert early['all_sent_windows_mature'] is False


def test_meeting_identity_and_event_replay_cannot_change_attribution(tmp_path):
    store,people,crm,pilot=build(tmp_path,2)
    assignments=pilot.create('pilot')['assignments']
    pilot.clock=lambda:STAMP+timedelta(days=2)
    values=dict(event_id='same',contact_id=assignments[0]['contact_id'],slot=STAMP+timedelta(days=3),evidence='Accepted slot')
    pilot.record_meeting('pilot','one','CONFIRMED',**values)
    with pytest.raises(ValueError,match='ID_REUSED'):
        pilot.record_meeting('pilot','one','CONFIRMED',**{**values,'evidence':'Changed evidence'})
    with pytest.raises(ValueError,match='IDENTITY_CHANGED'):
        pilot.record_meeting('pilot','one','CANCELLED',event_id='new',contact_id=assignments[1]['contact_id'],evidence='Wrong identity')
    with pytest.raises(ValueError,match='ACCEPTED_SLOT'):
        pilot.record_meeting('pilot','missing','CONFIRMED',event_id='missing',contact_id=assignments[0]['contact_id'],evidence='No accepted slot')


def test_analyst_only_sees_aggregates_and_cannot_choose_winner(tmp_path):
    store,people,crm,pilot=build(tmp_path,2)
    pilot.create('pilot')
    calls=[]
    def analyst(instructions,data,schema,**kwargs):
        calls.append(data)
        assert 'contacts' not in data and 'email' not in json.dumps(data)
        return {'observations':[{'variant_id':variant,**{key:counts[key] for key in ('assigned','sent','reserved_recipients','held')}}
                    for variant,counts in data['variants'].items()],
                'limitations':['Small exploratory sample; no reliable winner.'],'winner':None,'automatic_changes':False}
    pilot.analyst=analyst
    result=pilot.analyze('pilot')
    assert result['status']=='ANALYZED' and result['analysis']['winner'] is None
    assert pilot.analyze('pilot')['status']=='ANALYZED' and len(calls)==1
    assert pilot.analyze('pilot',at=STAMP+timedelta(days=1))['status']=='ANALYZED' and len(calls)==1
    send_fixture(store,pilot.assignments('pilot')[0]['contact_id'],STAMP)
    pilot.analyst=lambda *a,**kw:{'observations':[],'limitations':['bad'],'winner':'V2','automatic_changes':True}
    assert pilot.analyze('pilot',at=STAMP+timedelta(days=1))['status']=='WAITING'


@pytest.mark.parametrize('wrong_hook',[False,True])
def test_assigned_contact_cannot_send_wrong_or_missing_variant_after_approval(tmp_path,wrong_hook):
    from scripts.circuit_breaker import CircuitBreaker
    from scripts.email_patterns import render_pilot_pattern, PILOT_REVISION
    from scripts.telegram_approval import create_draft
    from scripts.workflow_sender import send_approved
    from zoneinfo import ZoneInfo
    ZONE=ZoneInfo('Africa/Casablanca')
    from tests.test_workflow_refactor import FakeTelegram, approve
    from tests.test_compact_campaigns import FakeMail
    store,people,crm,pilot=build(tmp_path,2)
    assignment=pilot.create('pilot')['assignments'][0]
    contact=store.contact(assignment['contact_id']);lead=json.loads(contact['snapshot'])
    other='V5' if assignment['variant_id']=='V2' else 'V2'
    rendered=render_pilot_pattern(other,last_name=lead['last_name'],company_name=lead['company_name'],civility=lead['civility'])
    hook={'variant_id':other,'pattern_revision':PILOT_REVISION,'signature_mode':'thunderbird_html','campaign_id':'pilot'} if wrong_hook else {}
    draft=create_draft(store,contact['id'],contact['email'],rendered['subject'],rendered['body'],hook)
    from tests.test_workflow_refactor import configure_test_policy
    configure_test_policy(store)
    telegram=FakeTelegram()
    with store.connection() as c:
        c.execute("UPDATE workflow_drafts SET status='APPROVED',approved_by='101',approved_at=? WHERE id=?",
                  (STAMP.isoformat(),draft['id']))
    mail=FakeMail()
    result=send_approved(store,mail,CircuitBreaker(str(tmp_path/'breaker.json')),'101',
                         at=STAMP.astimezone(ZONE).replace(hour=11,minute=0))
    assert result['status']=='PILOT_ASSIGNMENT_MISMATCH'
    assert not mail.sent


def test_copy_service_refuses_reassignment_and_infers_frozen_pilot_id(tmp_path):
    store,people,crm,pilot=build(tmp_path,2)
    assignment=pilot.create('pilot')['assignments'][0]
    writer=CompactCopy(store,critic_runner=accept,clock=lambda:STAMP)
    other='V5' if assignment['variant_id']=='V2' else 'V2'
    assert writer.prepare(assignment['contact_id'],other)['reason']=='PILOT_ASSIGNMENT_MISMATCH'
    result=writer.prepare(assignment['contact_id'],assignment['variant_id'])
    assert result['status']=='DRAFT_CREATED'
    with store.connection() as c:
        hook=json.loads(c.execute('SELECT hook FROM workflow_drafts WHERE id=?',(result['draft_id'],)).fetchone()[0])
    assert hook['campaign_id']=='pilot'


def test_retired_initial_drafts_do_not_block_selection_for_the_new_pilot(tmp_path,monkeypatch):
    from scripts.telegram_approval import create_draft
    from scripts.retire_mailing_rules import retire
    store,people,crm,pilot=build(tmp_path,2)
    contact=store.contact('contact-00')
    old=create_draft(store,contact['id'],contact['email'],'Ancien objet','Ancien corps',{})
    assert len(pilot._available({person['id']:person for person in people}))==1
    monkeypatch.setenv('WORKFLOW_STOP_AT_DRAFT','1')
    assert retire(store.path,apply=True)['retired_drafts']==1
    result=pilot.create('current-pilot')
    assert len(result['assignments'])==2
    with store.connection() as c:
        assert c.execute('SELECT status FROM workflow_drafts WHERE id=?',(old['id'],)).fetchone()[0]=='SUPERSEDED'


def test_assignment_cannot_race_global_draft_or_send_mutations(tmp_path):
    store,people,crm,pilot=build(tmp_path,2)
    with store.lock():
        with pytest.raises(RuntimeError,match='WORKFLOW_BUSY'):
            pilot.create('pilot')
    assert not pilot.assignments('pilot')
    assert pilot.create('pilot')['status']=='CREATED'


def test_manual_event_replay_without_explicit_timestamp_keeps_original_time(tmp_path):
    store,people,crm,pilot=build(tmp_path,2)
    identity=pilot.create('pilot')['assignments'][0]['contact_id']
    values=dict(event_id='stable-event',contact_id=identity,slot=STAMP+timedelta(days=3),evidence='Operator accepted slot')
    assert pilot.record_meeting('pilot','meeting','CONFIRMED',**values)['status']=='RECORDED'
    pilot.clock=lambda:STAMP+timedelta(hours=1)
    assert pilot.record_meeting('pilot','meeting','CONFIRMED',**values)['status']=='REPLAY'


def test_cli_dry_run_does_not_create_database_or_call_services(tmp_path,monkeypatch,capsys):
    from scripts.compact_pilot import main
    path=tmp_path/'absent.sqlite'
    monkeypatch.setattr('sys.argv',['compact_pilot','--journal-path',str(path),
        '--pilot-id','pilot','--action','create','--dry-run'])
    main()
    result=json.loads(capsys.readouterr().out)
    assert result['external_actions']==0 and result['sends']==0 and result['assigned']==0
    assert not path.exists()
