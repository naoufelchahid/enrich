"""Campaign orchestration on the shared journal; transport rules are reused.

No acquisition client is initialized to prepare an admitted contact's draft.
Callers explicitly activate the campaigns/qualification authorities. This
module does not schedule work, resume paused sends or migrate the active SAS.
"""
import json

from scripts.direct_enrichment import admissible
from scripts.workflow import Workflow
from scripts.workflow_store import encode


def reverify_compact(workflow):
    """Reverify an admitted mailbox in the journal without a temporary SAS."""
    store = workflow.store
    with store.connection() as c:
        contacts=[dict(r) for r in c.execute("""SELECT * FROM workflow_contacts
            WHERE wait_reason='REVERIFY_EMAIL_REQUIRED'
            AND status IN ('READY_TO_MAIL','SENT','REPLIED')""")]
    if not contacts:
        return {'status':'NO_REVERIFICATION'}
    if store.authority('qualification') != 'compact':
        return {'status':'QUALIFICATION_AUTHORITY_REQUIRED','contacts':len(contacts)}
    if workflow._qualification is None:
        from scripts.compact_qualification import CompactQualification
        workflow._qualification=CompactQualification(store,clock=workflow.clock)
    results=[]
    for contact in contacts:
        if store.excluded(contact['email']):
            results.append({'status':'EXCLUDED','candidate_id':contact['id']})
            continue
        lead=json.loads(contact['snapshot'])
        generation=encode({'email':contact['email'],'previous_verification':lead.get('email_validated_at')})
        candidate=store.candidate(contact['id'])
        if candidate is None:
            store.put_candidate(lead,lane='qualification',candidate_id=contact['id'])
            candidate=store.candidate(contact['id'])
        if candidate['data'].get('campaign_reverify_for') != generation:
            store.update_candidate(contact['id'],{
                'raw_email':contact['email'],'clean_email':None,'validated_email':None,
                'email_validation_source':None,'email_check_status':'PENDING',
                'email_checked_input':None,'neverbounce_status':'PENDING',
                'campaign_reverify_for':generation},lane='qualification',
                status='PENDING',wait_reason=None)
        result=workflow._qualification.tick(contact['id'])
        results.append(result)
        current=store.candidate(contact['id'])['data']
        if admissible(current,at=workflow.clock()) and current.get('email_check_status')=='VALID':
            snapshot={**lead,**{key:current.get(key) for key in (
                'email_check_status','email_validation_source','email_validated_at','validated_email',
                'clean_email','verification_verdict','email_checked_input','neverbounce_status')}}
            with store.connection() as c:
                c.execute('UPDATE workflow_contacts SET snapshot=?,wait_reason=NULL WHERE id=?',
                          (encode(snapshot),contact['id']))
        elif current.get('email_check_status') in ('INVALID','CATCHALL'):
            with store.connection() as c:
                c.execute("UPDATE workflow_contacts SET status='WAITING_EMAIL',wait_reason=? WHERE id=?",
                          (current['email_check_status'],contact['id']))
    return {'status':'REVERIFICATION_PROCESSED','contacts':len(contacts),'results':results}


class CompactCampaigns(Workflow):
    """Use write_one/send_tick/review_inbox with the existing durable guards."""
    def __init__(self,journal_path,**kwargs):
        kwargs.pop('state_path',None)
        kwargs.pop('backend',None)
        super().__init__(journal_path,state_path=journal_path,backend='compact',**kwargs)

    def reverify_archived(self):
        return reverify_compact(self)
