"""Only exact Telegram-approved versions can cross the Thunderbird boundary."""
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from scripts.direct_enrichment import admissible
from scripts.telegram_approval import draft_identity, current_signature_revision
from scripts.workflow_store import encode, now

from scripts.mailing_policy import load_policy, send_slot, initial_copy_error


def _reserve(store, breaker, operator_id, at, mail, *, policy):
    slot = send_slot(at, policy)
    if not slot:
        return {'status':'OUTSIDE_SLOT'}
    if breaker.is_tripped():
        return {'status':'CIRCUIT_BREAKER_ACTIVE'}
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        unresolved = c.execute("SELECT 1 FROM workflow_sends WHERE state IN ('SENDING','UNKNOWN')").fetchone()
        if unresolved:
            return {'status':'SEND_RECONCILIATION_REQUIRED'}
        if c.execute('SELECT 1 FROM workflow_sends WHERE slot=?',(slot,)).fetchone():
            return {'status':'SLOT_ALREADY_USED'}
        last = c.execute("SELECT coalesce(confirmed_at,submitted_at) FROM workflow_sends WHERE state IN ('SENDING','UNKNOWN','SENT') ORDER BY coalesce(confirmed_at,submitted_at) DESC LIMIT 1").fetchone()
        minimum_gap=policy['min_interval_minutes']
        if last and at-datetime.fromisoformat(last[0])<timedelta(minutes=minimum_gap):
            return {'status':'INTERVAL_NOT_ELAPSED'}
        drafts = c.execute("""SELECT d.* FROM workflow_drafts d JOIN workflow_contacts p ON p.id=d.contact_id
            WHERE d.status='APPROVED' AND p.status IN ('READY_TO_MAIL','SENT','REPLIED')
            ORDER BY CASE d.kind WHEN 'REPLY' THEN 0 ELSE 1 END,d.approved_at,d.id""").fetchall()
        for draft in drafts:
            hook = json.loads(draft['hook'])
            contact = c.execute('SELECT * FROM workflow_contacts WHERE id=?',(draft['contact_id'],)).fetchone()
            lead = json.loads(contact['snapshot'])
            assignment=(c.execute('SELECT * FROM workflow_pilot_assignments WHERE contact_id=?',(draft['contact_id'],)).fetchone()
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_pilot_assignments'").fetchone() else None)
            if assignment and draft['kind']=='INITIAL':
                if (hook.get('variant_id')!=assignment['variant_id']
                        or hook.get('pattern_revision')!=assignment['pattern_revision']
                        or hook.get('campaign_id')!=assignment['pilot_id']
                        or draft['recipient'].lower()!=assignment['email']
                        or contact['person_id']!=assignment['person_id'] or contact['company_id']!=assignment['company_id']):
                    return {'status':'PILOT_ASSIGNMENT_MISMATCH'}
            latest = c.execute('SELECT max(version) FROM workflow_drafts WHERE contact_id=? AND kind=?',(draft['contact_id'],draft['kind'])).fetchone()[0]
            if (not operator_id or draft['approved_by']!=str(operator_id) or latest!=draft['version']
                    or not draft['approved_at'] or datetime.fromisoformat(draft['approved_at'])>datetime.fromisoformat(slot)
                    or draft['content_hash']!=draft_identity(draft)
                    or draft['recipient']!=contact['email'] or not contact['person_id'] or not contact['company_id']
                    or contact['wait_reason']=='REVERIFY_EMAIL_REQUIRED'
                    or store.excluded(draft['recipient'],c)
                    or (draft['kind']=='INITIAL' and (contact['initial_sent_at'] or contact['human_exchange']))):
                continue
            if (lead.get('email_check_status') != 'VALID'
                    or lead.get('clean_email') != draft['recipient']
                    or not admissible(lead,at=at)):
                c.execute("UPDATE workflow_contacts SET wait_reason='REVERIFY_EMAIL_REQUIRED' WHERE id=?",(contact['id'],))
                continue
            copy_error = initial_copy_error(store, draft, c)
            if copy_error:
                return {'status':copy_error}
            if (draft['kind']=='REPLY' and draft['reply_message_id'] and
                    not getattr(mail,'supports_exact_reply',lambda:False)()):
                store.event('reply_transport_waiting',draft['contact_id'],{'draft_id':draft['id']},
                            key='reply-transport:'+draft['id'],connection=c)
                return {'status':'THUNDERBIRD_EXACT_REPLY_CAPABILITY_REQUIRED'}
            if breaker.is_tripped():
                return {'status':'CIRCUIT_BREAKER_ACTIVE'}
            if hook.get('signature_mode') == 'thunderbird_html':
                if hook.get('approval_identity_version')==2:
                    try:
                        if hook.get('signature_revision')!=current_signature_revision():
                            return {'status':'SIGNATURE_VERSION_CHANGED'}
                    except ValueError:
                        return {'status':'HTML_SIGNATURE_UNAVAILABLE'}
                if draft['reply_message_id'] or draft['body'].endswith('Naoufel Chahid | Contexte Lab Casablanca'):
                    return {'status':'HTML_SIGNATURE_DRAFT_INVALID'}
                if draft['kind']=='INITIAL' and not draft['body'].rstrip().endswith('Cordialement,'):
                    return {'status':'SIGN_OFF_REQUIRED'}
                verify_signature = getattr(mail, '_assert_html_signature_available', None)
                if verify_signature:
                    try:
                        verify_signature()
                    except RuntimeError:
                        return {'status':'HTML_SIGNATURE_UNAVAILABLE'}
            prior=c.execute('SELECT * FROM workflow_sends WHERE draft_id=?',(draft['id'],)).fetchone()
            if prior:
                if prior['state']!='NOT_SENT_CONFIRMED':
                    continue
                confirmation=c.execute("SELECT 1 FROM workflow_events WHERE kind='send_not_sent_confirmed' AND json_extract(payload,'$.send_id')=?",(prior['id'],)).fetchone()
                if not confirmation:
                    continue
                send_id=prior['id']
                store.event('send_retry_reserved',draft['contact_id'],{'previous_attempt':dict(prior)},connection=c)
                c.execute("UPDATE workflow_sends SET state='SENDING',slot=?,submitted_at=?,confirmed_at=NULL,receipt=NULL WHERE id=?",(slot,at.isoformat(),send_id))
            else:
                send_id = uuid.uuid4().hex
                c.execute('INSERT INTO workflow_sends VALUES (?,?,?,?,?,?,?,NULL,NULL)',
                          (send_id,draft['id'],draft['contact_id'],draft['kind'],slot,'SENDING',at.isoformat()))
            c.execute("UPDATE workflow_drafts SET status='SENDING' WHERE id=?",(draft['id'],))
            store.event('send_reserved',draft['contact_id'],{'id':send_id,'hash':draft['content_hash'],'slot':slot},connection=c)
            return {'status':'SENDING','id':send_id,'draft':dict(draft)}
        return {'status':'NO_APPROVED_EMAIL'}


def send_approved(store, mail, breaker, operator_id, *, at=None, dry_run=False, clock=None):
    if os.getenv('WORKFLOW_STOP_AT_DRAFT') == '1':
        return {'status':'STOPPED_AT_DRAFT','sends':0}
    clock_is_injected = at is not None
    at = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if dry_run:
        try:
            policy=load_policy(store)
        except ValueError:
            return {'status':'MAILING_POLICY_REQUIRED','sends':0}
        return {'dry_run':True,'slot':send_slot(at,policy),'sends':0}
    with store.lock():
        try:
            policy=load_policy(store)
        except ValueError:
            return {'status':'MAILING_POLICY_REQUIRED','sends':0}
        result = _reserve(store,breaker,operator_id,at,mail,policy=policy)
        if result['status']!='SENDING':
            return result
        draft, send_id = result['draft'],result['id']
        try:
            breaker.assert_can_send()
            transport=getattr(mail,'send_approved_email',mail.send_email)
            transport_args={'to':draft['recipient'],'subject':draft['subject'],'body_text':draft['body'],
                            'reply_to':draft['reply_message_id']}
            if json.loads(draft['hook']).get('signature_mode') == 'thunderbird_html':
                if draft['reply_message_id'] or draft['body'].endswith('Naoufel Chahid | Contexte Lab Casablanca'):
                    raise ValueError('HTML_SIGNATURE_DRAFT_INVALID')
                if draft['kind']=='INITIAL' and not draft['body'].rstrip().endswith('Cordialement,'):
                    raise ValueError('SIGN_OFF_REQUIRED')
                transport_args['include_signature']=True
            response = transport(**transport_args)
            if not isinstance(response,dict) or response.get('success') is not True:
                raise RuntimeError('MAIL_SUBMISSION_UNCONFIRMED')
        except Exception as error:
            with store.connection() as c:
                diagnostic={'error':str(error),'receipt':getattr(error,'receipt',None)}
                c.execute("UPDATE workflow_sends SET state='UNKNOWN',receipt=? WHERE id=?",(encode(diagnostic),send_id))
                store.event('send_unknown',draft['contact_id'],{'id':send_id,**diagnostic},connection=c)
            return {'status':'SEND_RECONCILIATION_REQUIRED','send_id':send_id}
        with store.connection() as c:
            confirmed_at=(clock().astimezone(timezone.utc).isoformat() if clock is not None else
                          at.isoformat() if clock_is_injected else now())
            c.execute("UPDATE workflow_sends SET state='SENT',confirmed_at=?,receipt=? WHERE id=?",(confirmed_at,encode(response),send_id))
            c.execute("UPDATE workflow_drafts SET status='SENT' WHERE id=?",(draft['id'],))
            if draft['kind']=='INITIAL':
                c.execute("UPDATE workflow_contacts SET status='SENT',initial_sent_at=? WHERE id=?",(at.isoformat(),draft['contact_id']))
            store.event('send_confirmed',draft['contact_id'],{'id':send_id},connection=c)
        return {'status':'SENT','send_id':send_id}


def reconcile_send(store, mail, send_id):
    """An exact Sent-folder match can confirm; absence never licenses a resend."""
    with store.lock():
        with store.connection() as c:
            row = c.execute('''SELECT s.*,d.recipient,d.subject,d.body FROM workflow_sends s
                JOIN workflow_drafts d ON d.id=s.draft_id WHERE s.id=?''',(send_id,)).fetchone()
        if not row or row['state'] not in ('SENDING','UNKNOWN'):
            return {'status':'NOT_UNRESOLVED'}
        matches = mail.find_sent_exact(row['recipient'],row['subject'],row['body'],row['submitted_at'])
        if len(matches)!=1:
            return {'status':'REVIEW_REQUIRED','matches':len(matches)}
        with store.connection() as c:
            c.execute("UPDATE workflow_sends SET state='SENT',confirmed_at=?,receipt=? WHERE id=?",(now(),encode(matches[0]),send_id))
            c.execute("UPDATE workflow_drafts SET status='SENT' WHERE id=?",(row['draft_id'],))
            if row['kind']=='INITIAL':
                c.execute("UPDATE workflow_contacts SET status='SENT',initial_sent_at=? WHERE id=?",(row['submitted_at'],row['contact_id']))
            store.event('send_reconciled',row['contact_id'],{'id':send_id},connection=c)
        return {'status':'RECONCILED'}
