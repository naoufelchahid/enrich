"""Telegram approvals are tied to an immutable exact version, never READY_TO_MAIL."""
import json
import hashlib
from pathlib import Path
import os
import uuid

import requests

from scripts.workflow_store import encode, fingerprint, now
from scripts.email_hygiene import is_personal_email


class TelegramClient:
    def __init__(self, token=None, operator_id=None, chat_id=None):
        self.token = token or os.getenv('TELEGRAM_BOT_TOKEN')
        self.operator_id = str(operator_id or os.getenv('TELEGRAM_OPERATOR_ID') or '')
        self.chat_id = str(chat_id or os.getenv('TELEGRAM_CHAT_ID') or '')

    @property
    def configured(self):
        return bool(self.token and self.operator_id.isdigit() and self.chat_id.lstrip('-').isdigit())

    def call(self, method, payload):
        if not self.configured:
            raise RuntimeError('TELEGRAM_CONFIGURATION_MISSING')
        try:
            response = requests.post('https://api.telegram.org/bot' + self.token + '/' + method,
                                     json=payload, timeout=(5, 30), allow_redirects=False)
            data = response.json()
            if not response.ok or data.get('ok') is not True:
                raise ValueError()
            return data['result']
        except (requests.RequestException, ValueError, KeyError):
            raise RuntimeError('TELEGRAM_REQUEST_FAILED') from None

    def notify(self, text):
        return self.call('sendMessage', {'chat_id': self.chat_id, 'text': text})


def current_signature_revision():
    path=Path(__file__).resolve().parents[1]/'outputs/signature-contextelab/signature.html'
    if not path.is_file():
        raise ValueError('HTML_SIGNATURE_VERSION_REQUIRED')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def draft_hash(recipient, subject, body, *, hook=None, kind=None, reply_message_id=None):
    identity={'recipient':recipient,'subject':subject,'body':body}
    if hook and hook.get('approval_identity_version')==2:
        identity.update(kind=kind,reply_message_id=reply_message_id,
            signature_mode=hook.get('signature_mode'),signature_revision=hook.get('signature_revision'),
            pattern_revision=hook.get('pattern_revision'),approval_identity_version=2)
    return fingerprint(identity)


def draft_identity(draft):
    return draft_hash(draft['recipient'],draft['subject'],draft['body'],hook=json.loads(draft['hook']),
                      kind=draft['kind'],reply_message_id=draft['reply_message_id'])


def approval_text(draft):
    hook = json.loads(draft['hook'])
    summary = hook.get('operator_summary')
    prefix = ('Synthèse pour vous (non incluse dans l’email) :\n' + summary + '\n\n') if summary else ''
    if hook.get('signature_mode') == 'thunderbird_html':
        prefix += 'La signature HTML du compte Thunderbird est ajoutée après le texte ci-dessous.\n\n'
    return prefix + f"Version {draft['version']}\nDestinataire : {draft['recipient']}\nObjet : {draft['subject']}\n\n{draft['body']}"


def create_draft(store, contact_id, recipient, subject, body, hook, *, kind='INITIAL', reply_message_id=None, request_key=None):
    if is_personal_email(recipient):
        raise ValueError('PERSONAL_EMAIL_FORBIDDEN')
    if kind not in ('INITIAL', 'REPLY') or not recipient or not subject or not body:
        raise ValueError('Invalid draft')
    if '\n' in recipient or '\r' in recipient or '\n' in subject or '\r' in subject:
        raise ValueError('Invalid draft headers')
    hook=dict(hook)
    from scripts.compact_store import CompactStore
    if isinstance(store,CompactStore):
        hook['approval_identity_version']=2
        if hook.get('signature_mode')=='thunderbird_html':
            hook.setdefault('signature_revision',current_signature_revision())
    content_hash=draft_hash(recipient,subject,body,hook=hook,kind=kind,reply_message_id=reply_message_id)
    with store.lock():
        with store.connection() as c:
            if request_key:
                existing=c.execute('SELECT * FROM workflow_drafts WHERE request_key=?',(request_key,)).fetchone()
                if existing:
                    if (existing['recipient'],existing['subject'],existing['body'])!=(recipient,subject,body) or existing['content_hash']!=content_hash:
                        raise ValueError('REQUEST_KEY_REUSED_WITH_DIFFERENT_CONTENT')
                    return dict(existing)
            contact = c.execute('SELECT * FROM workflow_contacts WHERE id=?', (contact_id,)).fetchone()
            if not contact or contact['email'] != recipient or contact['status'] not in ('READY_TO_MAIL', 'SENT', 'REPLIED'):
                raise ValueError('Draft requires an admitted CRM contact and current address')
            if kind == 'INITIAL' and contact['initial_sent_at']:
                raise ValueError('Initial outreach already sent')
            if store.excluded(recipient, c):
                raise ValueError('Suppressed recipient')
            if c.execute("SELECT 1 FROM workflow_sends WHERE contact_id=? AND kind=? AND state IN ('SENDING','UNKNOWN')", (contact_id,kind)).fetchone():
                raise ValueError('Reconcile unresolved send before rewriting')
            version = c.execute('SELECT coalesce(max(version),0)+1 FROM workflow_drafts WHERE contact_id=? AND kind=?', (contact_id,kind)).fetchone()[0]
            c.execute("UPDATE workflow_drafts SET status='SUPERSEDED',approved_by=NULL,approved_at=NULL WHERE contact_id=? AND kind=? AND status IN ('PENDING','APPROVED','REWRITE')", (contact_id,kind))
            identity, callback = uuid.uuid4().hex, uuid.uuid4().hex
            c.execute('''INSERT INTO workflow_drafts(id,contact_id,kind,version,recipient,subject,body,content_hash,
                hook,status,callback_token,created_at,reply_message_id,request_key) VALUES (?,?,?,?,?,?,?,?,?,'PENDING',?,?,?,?)''',
                (identity,contact_id,kind,version,recipient,subject,body,content_hash,encode(hook),callback,now(),reply_message_id,request_key))
            store.event('draft_created',contact_id,{'id':identity,'version':version,'hash':content_hash},connection=c)
            return dict(c.execute('SELECT * FROM workflow_drafts WHERE id=?', (identity,)).fetchone())


def submit_draft(store, client, draft_id):
    with store.lock('telegram'):
        with store.connection() as c:
            if store.telegram_submission_paused(c):
                return {'status':'TELEGRAM_SUBMISSION_PAUSED'}
            draft = c.execute('SELECT * FROM workflow_drafts WHERE id=?', (draft_id,)).fetchone()
        if not draft or draft['status'] != 'PENDING':
            return {'status': 'NOT_PENDING'}
        from scripts.mailing_policy import initial_copy_error
        error = initial_copy_error(store, draft)
        if error:
            return {'status':error}
        if draft['telegram_message_id']:
            return {'status': 'ALREADY_SUBMITTED'}
        content = approval_text(draft)
        if len(content) > 4096:
            raise ValueError('Draft exceeds Telegram message limit')
        operation = store.operation(draft['contact_id'], 'TELEGRAM_SUBMIT', {'draft_id':draft_id})
        if operation['status']=='SUCCEEDED':
            message_id=json.loads(operation['result'])['message_id']
            with store.connection() as c:
                c.execute('UPDATE workflow_drafts SET telegram_message_id=? WHERE id=?',(message_id,draft_id))
            return {'status':'ALREADY_SUBMITTED'}
        if operation['status'] in ('SENDING','UNKNOWN'):
            return {'status':'TELEGRAM_RECONCILIATION_REQUIRED'}
        buttons = [[{'text':label,'callback_data': action + ':' + draft['callback_token']}
                    for action,label in [('approve','Approuver'),('reject','Refuser'),('rewrite','Réécrire')]]]
        store.finish_operation(operation['id'],'SENDING')
        try:
            result = client.call('sendMessage', {'chat_id':client.chat_id,'text':content,
                                                 'reply_markup':{'inline_keyboard':buttons}})
            message_id = result['message_id']
        except Exception:
            store.finish_operation(operation['id'],'UNKNOWN')
            return {'status':'TELEGRAM_RECONCILIATION_REQUIRED'}
        with store.connection() as c:
            c.execute('UPDATE workflow_drafts SET telegram_message_id=? WHERE id=?', (message_id,draft_id))
        store.finish_operation(operation['id'],'SUCCEEDED',{'message_id':message_id})
        return {'status':'SUBMITTED','message_id':message_id}


def process_update(store, client, update, *, breaker=None, controller=None):
    if controller is not None and controller.recognizes(update):
        return controller.handle(update)
    key = 'telegram-update:' + str(update['update_id'])
    callback = update.get('callback_query') or {}
    message = callback.get('message') or update.get('message') or {}
    actor = callback.get('from') or message.get('from') or {}
    if str(actor.get('id')) != client.operator_id or str((message.get('chat') or {}).get('id')) != client.chat_id:
        store.event('telegram_rejected',None,{'reason':'UNAUTHORIZED'},key=key)
        return {'status':'UNAUTHORIZED'}
    with store.lock():
        with store.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM workflow_events WHERE key=?', (key,)).fetchone():
                return {'status':'REPLAY'}
            if not callback:
                text = message.get('text') or ''
                if text.startswith('/resume ') and breaker is not None:
                    parts=text.split(maxsplit=2)
                    incident=c.execute("SELECT value FROM workflow_meta WHERE key='active_incident'").fetchone()
                    if len(parts)!=3 or not parts[2].strip() or not incident or incident[0]!=parts[1] or not breaker.is_tripped():
                        store.event('telegram_invalid_resume',None,{'text':text},key=key,connection=c)
                        return {'status':'INVALID_RESUME'}
                    store.event('breaker_resume_authorized',None,{'incident':parts[1],'reason':parts[2],
                                'operator_id':client.operator_id},key=key,connection=c)
                    # Persist approval before reset. A reset failure stays blocked.
                    c.commit()
                    breaker.reset()
                    incident_event=c.execute("SELECT payload FROM workflow_events WHERE key=? AND kind='bounce_incident'",(parts[1],)).fetchone()
                    if incident_event:
                        evidence=json.loads(incident_event[0])
                        if evidence.get('type') in ('BOUNCE_TEMPORARY','BOUNCE_UNKNOWN'):
                            c.execute("""UPDATE workflow_contacts SET status=CASE WHEN human_exchange=1 THEN 'REPLIED'
                                WHEN initial_sent_at IS NOT NULL THEN 'SENT' ELSE 'READY_TO_MAIL' END,
                                wait_reason='REVERIFY_EMAIL_REQUIRED' WHERE email=? AND status='WAITING_BOUNCE'""",(evidence.get('email'),))
                    c.execute("DELETE FROM workflow_meta WHERE key='active_incident'")
                    return {'status':'RESUMED'}
                reference=(message.get('reply_to_message') or {}).get('message_id')
                draft=c.execute('SELECT * FROM workflow_drafts WHERE telegram_message_id=? ORDER BY version DESC LIMIT 1',(reference,)).fetchone()
                if not draft or draft['status'] not in ('PENDING','APPROVED','REWRITE') or not text.strip():
                    store.event('telegram_rejected',None,{'reason':'UNKNOWN_DIRECTIVE','text':text,
                                'message_id':message.get('message_id'),'reply_to_message_id':reference},key=key,connection=c)
                    return {'status':'UNKNOWN_DIRECTIVE'}
                c.execute("UPDATE workflow_drafts SET status='REWRITE',approved_by=NULL,approved_at=NULL,rewrite_instruction=? WHERE id=?",(text,draft['id']))
                store.event('telegram_rewrite_directive',draft['contact_id'],{'draft_id':draft['id'],'directive':text},key=key,connection=c)
                return {'status':'REWRITE','draft_id':draft['id']}
            action, _, token = str(callback.get('data') or '').partition(':')
            draft = c.execute('SELECT * FROM workflow_drafts WHERE callback_token=?', (token,)).fetchone()
            if draft and draft['telegram_message_id'] is None:
                expected=approval_text(draft)
                if message.get('text')==expected:
                    c.execute('UPDATE workflow_drafts SET telegram_message_id=? WHERE id=?',(message.get('message_id'),draft['id']))
                    c.execute("UPDATE workflow_operations SET status='SUCCEEDED',result=? WHERE kind='TELEGRAM_SUBMIT' AND json_extract(payload,'$.draft_id')=?",(encode({'message_id':message['message_id']}),draft['id']))
                    draft=c.execute('SELECT * FROM workflow_drafts WHERE id=?',(draft['id'],)).fetchone()
            if not draft or draft['status'] != 'PENDING' or draft['telegram_message_id'] != message.get('message_id'):
                store.event('telegram_rejected',None,{'reason':'STALE_CALLBACK'},key=key,connection=c)
                return {'status':'STALE_CALLBACK'}
            if draft_identity(draft) != draft['content_hash']:
                store.event('telegram_rejected',draft['contact_id'],{'reason':'CONTENT_CHANGED'},key=key,connection=c)
                return {'status':'CONTENT_CHANGED'}
            from scripts.mailing_policy import initial_copy_error
            error = initial_copy_error(store, draft, c)
            if error:
                store.event('telegram_rejected',draft['contact_id'],{'reason':error},key=key,connection=c)
                return {'status':error}
            state = {'approve':'APPROVED','reject':'REJECTED','rewrite':'REWRITE'}.get(action)
            if not state:
                store.event('telegram_invalid_action',None,{'action':action},key=key,connection=c)
                return {'status':'INVALID_ACTION'}
            c.execute('UPDATE workflow_drafts SET status=?,approved_by=?,approved_at=? WHERE id=?',
                      (state,client.operator_id if state=='APPROVED' else None,now() if state=='APPROVED' else None,draft['id']))
            store.event('telegram_'+action,draft['contact_id'],{'draft_id':draft['id'],'hash':draft['content_hash'],
                                                              'operator_id':client.operator_id},key=key,connection=c)
            return {'status':state,'draft_id':draft['id']}


def poll_updates(store, client, *, breaker=None, controller=None):
    with store.lock('telegram-poll'):
        with store.connection() as c:
            row = c.execute("SELECT value FROM workflow_meta WHERE key='telegram_offset'").fetchone()
        offset = int(row[0]) if row else 0
        updates = client.call('getUpdates', {'offset':offset,'timeout':0,'allowed_updates':['callback_query','message']})
        results = []
        for update in sorted(updates,key=lambda x:x['update_id']):
            result=process_update(store,client,update,breaker=breaker,controller=controller)
            if controller is not None and controller.recognizes(update):
                result=controller.deliver(update,result)
            results.append(result)
            with store.connection() as c:
                c.execute("INSERT INTO workflow_meta VALUES ('telegram_offset',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(update['update_id']+1),))
        return results
