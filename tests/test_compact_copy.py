"""Approved pilot copy is rendered exactly and independently reviewed offline."""
import json
import re
from pathlib import Path

import pytest

from scripts.compact_copy import CompactCopy
from scripts.email_patterns import (BOOKING_URL, HTML_SIGNATURE_MODE, PILOT_PATTERNS,
    PILOT_REVISION, SIGNATURE, editorial_question_count,
    render_pilot_pattern, validate_pilot_copy)
from scripts.telegram_approval import process_update
from tests.test_compact_campaigns import campaign
from tests.test_workflow_refactor import approve


def accept(instructions,dossier,schema,**kwargs):
    assert 'independent critic' in instructions
    assert 'Never access credentials' in instructions
    assert set(dossier)=={'identity','subject','body','variant_id','pattern_revision','copy_hash'}
    assert schema['additionalProperties'] is False
    return {'copy_hash':dossier['copy_hash'],'approved':True,'reasons':[]}


def reference(variant):
    text=(Path(__file__).resolve().parents[1]/'docs/COLD_EMAIL_RESEARCH_2026-11-05.md').read_text()
    section=text.split('### '+variant+' — ',1)[1].split('\n### ',1)[0]
    paragraphs=[line[2:] if line.startswith('> ') else '' for line in section.splitlines() if line.startswith('>')]
    common=text.split('### Fin commune validée — V2 et V5',1)[1]
    common_lines=[line[2:] if line.startswith('> ') else '' for line in common.splitlines() if line.startswith('>')]
    body='\n'.join(paragraphs)+'\n\n'+'\n'.join(common_lines)
    body=body.replace('{Nom de famille}','Tazi').replace('{Entreprise}','Acme')
    return re.sub(r'\[([^]]+)\]\(([^)]+)\)',r'\1 (\2)',body)


@pytest.mark.parametrize('variant',['V2','V5'])
def test_bodies_match_exact_approved_document_and_plaintext_link(variant):
    rendered=render_pilot_pattern(variant,last_name='Tazi',company_name='Acme',civility='Monsieur')
    assert rendered['body']==reference(variant)
    assert rendered['subject']=={'V2':'construire votre avance','V5':'au-delà des chatbots'}[variant]
    assert rendered['body'].count(BOOKING_URL)==1
    assert '30 minutes sur Cal Video' in rendered['body']
    assert len(rendered['body'].split())>85
    assert editorial_question_count(rendered['body'])==0
    assert rendered['body'].count('?')==1  # URL query, not a copy question.
    assert rendered['body'].endswith('Cordialement,') and SIGNATURE not in rendered['body']
    assert validate_pilot_copy(variant,rendered['subject'],rendered['body'],last_name='Tazi',company_name='Acme',civility='Monsieur')==(True,[])


def test_only_two_current_variants_exist():
    assert set(PILOT_PATTERNS)=={'V2','V5'}


@pytest.mark.parametrize('change',['body','subject','link','revision','signature'])
def test_unapproved_changes_rejected(change):
    copy=render_pilot_pattern('V2',last_name='Tazi',company_name='Acme',civility='Mme')
    subject,body,revision=copy['subject'],copy['body'],PILOT_REVISION
    if change=='body':body=body.replace('plusieurs entreprises','de nombreux leaders')
    if change=='subject':subject+=' !'
    if change=='link':body=body.replace(BOOKING_URL,'https://example.org/')
    if change=='revision':revision='unapproved'
    if change=='signature':body+='\n\n'+SIGNATURE
    assert not validate_pilot_copy('V2',subject,body,last_name='Tazi',company_name='Acme',civility='Mme',revision=revision)[0]


def test_unknown_civility_is_not_inferred_from_first_name(campaign):
    store,lead,telegram,breaker,workflow=campaign
    lead['civility']=None
    with store.connection() as c:
        c.execute('UPDATE workflow_contacts SET snapshot=?',(json.dumps(lead),))
    writer=CompactCopy(store,critic_runner=lambda *a,**kw:pytest.fail('no critique on missing civility'))
    assert writer.prepare(lead['journal_contact_id'],'V2')['reason']=='CIVILITY_REVIEW_REQUIRED'
    with store.connection() as c:
        assert c.execute('SELECT count(*) FROM workflow_drafts').fetchone()[0]==0


def test_independent_review_then_pending_draft_only(campaign):
    store,lead,telegram,breaker,workflow=campaign
    calls=[]
    def critic(*args,**kwargs):
        calls.append(args[1])
        return accept(*args,**kwargs)
    writer=CompactCopy(store,critic_runner=critic)
    result=writer.prepare(lead['journal_contact_id'],'V2',campaign_id='pilot')
    assert result['status']=='DRAFT_CREATED' and result['sends']==0
    assert writer.validate_draft(result['draft_id'])==(True,[])
    assert writer.prepare(lead['journal_contact_id'],'V2',campaign_id='pilot')['draft_id']==result['draft_id']
    assert len(calls)==1 and telegram.sent==[]
    with store.connection() as c:
        draft=c.execute('SELECT * FROM workflow_drafts').fetchone()
        hook=json.loads(draft['hook'])
        assert draft['status']=='PENDING' and draft['telegram_message_id'] is None
        assert hook['signature_mode']==HTML_SIGNATURE_MODE and hook['variant_id']=='V2'
        assert c.execute('SELECT count(*) FROM workflow_sends').fetchone()[0]==0


def test_rejection_preserves_explicit_wait_without_creating_draft(campaign):
    store,lead,telegram,breaker,workflow=campaign
    calls=[]
    def critic(instructions,data,schema,**kwargs):
        calls.append(data)
        return {'copy_hash':data['copy_hash'],'approved':False,'reasons':['Identité à revoir']}
    writer=CompactCopy(store,critic_runner=critic)
    result=writer.prepare(lead['journal_contact_id'],'V5')
    assert result['status']=='WAITING' and result['reason']=='COPY_CRITIQUE_REJECTED'
    assert result['reasons']==['Identité à revoir']
    assert writer.prepare(lead['journal_contact_id'],'V5')['reason']=='COPY_CRITIQUE_REJECTED'
    assert len(calls)==1
    with store.connection() as c:
        assert c.execute('SELECT count(*) FROM workflow_drafts').fetchone()[0]==0


@pytest.mark.parametrize('case',['schema','hash','empty_reason'])
def test_invalid_critic_output_waits_without_retry(campaign,case):
    store,lead,telegram,breaker,workflow=campaign
    calls=[]
    def critic(instructions,data,schema,**kwargs):
        calls.append(data)
        if case=='schema':return {'approved':True,'body':'replacement'}
        return {'copy_hash':'0'*64 if case=='hash' else data['copy_hash'],
                'approved':False if case=='empty_reason' else True,'reasons':[]}
    writer=CompactCopy(store,critic_runner=critic)
    assert writer.prepare(lead['journal_contact_id'],'V2')['reason']=='CRITIQUE_SCHEMA_OR_BINDING_INVALID'
    assert writer.prepare(lead['journal_contact_id'],'V2')['reason']=='CRITIQUE_RECONCILIATION_REQUIRED'
    assert len(calls)==1


def test_changed_contact_during_critique_blocks_draft(campaign):
    store,lead,telegram,breaker,workflow=campaign
    def critic(*args,**kwargs):
        store.exclude(lead['clean_email'],'UNSUBSCRIBE')
        return accept(*args,**kwargs)
    assert CompactCopy(store,critic_runner=critic).prepare(lead['journal_contact_id'],'V2')['reason']=='CONTACT_CHANGED_DURING_CRITIQUE'


def test_rewrite_changes_version_and_invalidates_old_callback(campaign):
    store,lead,telegram,breaker,workflow=campaign
    writer=CompactCopy(store,critic_runner=accept)
    first=writer.prepare(lead['journal_contact_id'],'V2')
    with store.connection() as c:
        draft=dict(c.execute('SELECT * FROM workflow_drafts WHERE id=?',(first['draft_id'],)).fetchone())
    assert approve(store,telegram,draft)[0]['status']=='APPROVED'
    second=writer.prepare(lead['journal_contact_id'],'V5')
    assert second['draft_id']!=first['draft_id']
    old={'update_id':500,'callback_query':{'from':{'id':101},'data':'approve:'+draft['callback_token'],
          'message':{'message_id':1,'chat':{'id':202}}}}
    assert process_update(store,telegram,old)['status']=='STALE_CALLBACK'
    assert writer.validate_draft(second['draft_id'])==(True,[])


def test_proof_for_different_current_address_cannot_create_pilot_draft(campaign):
    store,lead,telegram,breaker,workflow=campaign
    with store.connection() as c:
        c.execute("UPDATE workflow_contacts SET email='sara.tazi@example.org'")
    assert CompactCopy(store,critic_runner=accept).prepare(lead['journal_contact_id'],'V2')['reason']=='CONTACT_NOT_ADMISSIBLE'


def test_sender_rejects_forged_pilot_template_even_after_approval(campaign):
    from scripts.telegram_approval import create_draft
    from scripts.workflow_sender import send_approved
    from tests.test_compact_campaigns import FakeMail
    from tests.test_workflow_refactor import next_morning
    store,lead,telegram,breaker,workflow=campaign
    draft=create_draft(store,lead['journal_contact_id'],lead['clean_email'],'construire votre avance',
        'Texte non validé.\n\nCordialement,',{'variant_id':'V2','pattern_revision':PILOT_REVISION,
        'signature_mode':'thunderbird_html'})
    from scripts.telegram_approval import submit_draft
    assert submit_draft(store,telegram,draft['id'])['status']=='PILOT_COPY_INVALID'
    # Even an approval forced into the journal cannot bypass the transport gate.
    with store.connection() as c:
        c.execute("UPDATE workflow_drafts SET status='APPROVED',approved_by='101',approved_at=? WHERE id=?",
                  (lead['email_validated_at'],draft['id']))
    mail=FakeMail()
    result=send_approved(store,mail,breaker,'101',at=next_morning())
    assert result['status']=='PILOT_COPY_INVALID'
    assert not mail.sent


def test_valid_pilot_is_transportable_exactly_with_one_html_signature(campaign):
    from scripts.workflow_sender import send_approved
    from tests.test_compact_campaigns import FakeMail
    from tests.test_workflow_refactor import next_morning
    store,lead,telegram,breaker,workflow=campaign
    writer=CompactCopy(store,critic_runner=accept)
    result=writer.prepare(lead['journal_contact_id'],'V5')
    with store.connection() as c:
        draft=dict(c.execute('SELECT * FROM workflow_drafts WHERE id=?',(result['draft_id'],)).fetchone())
    approve(store,telegram,draft)
    mail=FakeMail()
    assert send_approved(store,mail,breaker,'101',at=next_morning())['status']=='SENT'
    assert len(mail.sent)==1
    assert mail.sent[0]['body_text']==draft['body']
    assert mail.sent[0]['include_signature'] is True and SIGNATURE not in draft['body']
