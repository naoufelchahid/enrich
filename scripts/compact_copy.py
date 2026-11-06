"""Render frozen pilot copy, then require an independent structured critique."""
import json
from pathlib import Path

import jsonschema

from scripts.direct_enrichment import admissible
from scripts.crm_transfer import atomic_archive
from scripts.email_patterns import PILOT_REVISION, render_pilot_pattern, validate_pilot_copy
from scripts.prompt_engine import pilot_critique_instructions
from scripts.structured_agent import run_agent
from scripts.telegram_approval import create_draft, current_signature_revision, draft_identity
from scripts.workflow_store import encode, fingerprint

CRITIQUE_SCHEMA={'type':'object','properties':{
    'copy_hash':{'type':'string','pattern':'^[0-9a-f]{64}$'},
    'approved':{'type':'boolean'},
    'reasons':{'type':'array','items':{'type':'string','minLength':1}}},
    'required':['copy_hash','approved','reasons'],'additionalProperties':False}


class CompactCopy:
    def __init__(self,store,*,critic_runner=run_agent,clock=None,output_dir=None):
        from datetime import datetime, timezone
        self.store=store
        self.critic_runner=critic_runner
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.output_dir=Path(output_dir or Path(store.path).parent/'reports'/'pilot-critique')

    def _eligible(self,contact):
        return bool(contact and contact['status']=='READY_TO_MAIL' and contact['person_id']
            and contact['company_id'] and not contact['initial_sent_at'] and not contact['human_exchange']
            and not self.store.excluded(contact['email'] or '')
            and contact['wait_reason']!='REVERIFY_EMAIL_REQUIRED'
            and json.loads(contact['snapshot']).get('email_check_status')=='VALID'
            and (json.loads(contact['snapshot']).get('clean_email') or '').lower()==(contact['email'] or '').lower()
            and admissible(json.loads(contact['snapshot']),at=self.clock()))

    def prepare(self,contact_id,variant_id,*,campaign_id=None,request_key=None):
        """Create a reviewable PENDING draft, never Telegram or an email send.

The parent renders the approved model deterministically. A separate read-only
worker critiques its minimal dossier. A rejection/schema error remains waiting.
"""
        if self.store.authority('campaigns')!='compact':
            raise RuntimeError('COMPACT_AUTHORITY_REQUIRED:campaigns')
        with self.store.lock('pilot-copy-'+fingerprint(contact_id)):
            contact=self.store.contact(contact_id)
            if not self._eligible(contact):
                return {'status':'WAITING','reason':'CONTACT_NOT_ADMISSIBLE'}
            with self.store.connection() as c:
                assignment=(c.execute('SELECT * FROM workflow_pilot_assignments WHERE contact_id=?',(contact_id,)).fetchone()
                    if c.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_pilot_assignments'").fetchone() else None)
            if assignment:
                if (variant_id!=assignment['variant_id'] or assignment['pattern_revision']!=PILOT_REVISION
                        or (campaign_id is not None and campaign_id!=assignment['pilot_id'])
                        or contact['person_id']!=assignment['person_id'] or contact['company_id']!=assignment['company_id']
                        or contact['email'].lower()!=assignment['email']):
                    return {'status':'WAITING','reason':'PILOT_ASSIGNMENT_MISMATCH'}
                campaign_id=assignment['pilot_id']
            lead=json.loads(contact['snapshot'])
            identity={'last_name':lead.get('last_name'),'company_name':lead.get('company_display_name') or lead.get('company_name'),
                      'civility':lead.get('civility')}
            try:
                rendered=render_pilot_pattern(variant_id,**identity)
                signature_revision=current_signature_revision()
            except ValueError as exc:
                return {'status':'WAITING','reason':str(exc)}
            hook={'variant_id':variant_id,'pattern_revision':PILOT_REVISION,
                  'signature_mode':'thunderbird_html','signature_revision':signature_revision}
            if campaign_id is not None:
                hook['campaign_id']=campaign_id
            copy_hash=fingerprint({'recipient':contact['email'],'subject':rendered['subject'],
                'body':rendered['body'],'variant_id':variant_id,'pattern_revision':PILOT_REVISION,
                'signature_mode':hook['signature_mode'],'signature_revision':signature_revision})
            dossier={'identity':identity,'subject':rendered['subject'],'body':rendered['body'],
                     'variant_id':variant_id,'pattern_revision':PILOT_REVISION,'copy_hash':copy_hash}
            operation=self.store.operation(contact_id,'PILOT_COPY_CRITIQUE',dossier)
            if operation['status']=='SUCCEEDED':
                critique=json.loads(operation['result'])
            elif operation['status'] in ('SENDING','UNKNOWN','NEEDS_REVIEW'):
                return {'status':'WAITING','reason':'CRITIQUE_RECONCILIATION_REQUIRED','operation_id':operation['id']}
            else:
                self.store.finish_operation(operation['id'],'SENDING')
                try:
                    atomic_archive(self.output_dir/operation['id']/'run.json',
                                   {'contacts':[{'journal_contact_id':contact_id}]})
                    critique=self.critic_runner(pilot_critique_instructions(),dossier,CRITIQUE_SCHEMA,
                                               output_dir=self.output_dir/operation['id'])
                    jsonschema.validate(critique,CRITIQUE_SCHEMA)
                    if critique['copy_hash']!=copy_hash or any(not reason.strip() for reason in critique['reasons']) or (not critique['approved'] and not critique['reasons']):
                        raise ValueError('CRITIQUE_BINDING_OR_REASON_INVALID')
                except (ValueError,jsonschema.ValidationError):
                    self.store.finish_operation(operation['id'],'NEEDS_REVIEW',{'reason':'CRITIQUE_SCHEMA_OR_BINDING_INVALID'})
                    return {'status':'WAITING','reason':'CRITIQUE_SCHEMA_OR_BINDING_INVALID'}
                except Exception:
                    self.store.finish_operation(operation['id'],'UNKNOWN')
                    return {'status':'WAITING','reason':'CRITIQUE_RECONCILIATION_REQUIRED'}
                self.store.finish_operation(operation['id'],'SUCCEEDED',critique)
            if not critique['approved']:
                return {'status':'WAITING','reason':'COPY_CRITIQUE_REJECTED','reasons':critique['reasons']}
            valid,errors=validate_pilot_copy(variant_id,rendered['subject'],rendered['body'],**identity)
            if not valid:
                return {'status':'WAITING','reason':'PILOT_COPY_INVALID','errors':errors}
            if not self._eligible(self.store.contact(contact_id)) or self.store.contact(contact_id)['snapshot']!=contact['snapshot']:
                return {'status':'WAITING','reason':'CONTACT_CHANGED_DURING_CRITIQUE'}
            hook['critique_hash']=copy_hash
            draft=create_draft(self.store,contact_id,contact['email'],rendered['subject'],rendered['body'],hook,
                               request_key=request_key or 'pilot-copy:'+operation['id'])
            return {'status':'DRAFT_CREATED','draft_id':draft['id'],'variant_id':variant_id,'sends':0,
                    'submission':{'status':'STOPPED_AT_DRAFT'}}

    def validate_draft(self,draft_id):
        """Recheck a stored pilot draft without applying any action."""
        with self.store.connection() as c:
            row=c.execute('SELECT * FROM workflow_drafts WHERE id=?',(draft_id,)).fetchone()
        if row is None:
            return False,['DRAFT_NOT_FOUND']
        draft=dict(row)
        hook=json.loads(draft['hook'])
        contact=self.store.contact(draft['contact_id'])
        if not contact:
            return False,['CONTACT_NOT_FOUND']
        lead=json.loads(contact['snapshot'])
        valid,errors=validate_pilot_copy(hook.get('variant_id'),draft['subject'],draft['body'],
            last_name=lead.get('last_name'),company_name=lead.get('company_display_name') or lead.get('company_name'),
            civility=lead.get('civility'),revision=hook.get('pattern_revision'))
        if draft_identity(draft)!=draft['content_hash']:
            errors.append('APPROVAL_IDENTITY_CHANGED')
        if hook.get('signature_mode')!='thunderbird_html':
            errors.append('PILOT_SIGNATURE_INVALID')
        return not errors,errors
