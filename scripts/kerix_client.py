"""Observed Kerix search contract; uncertain company identity remains pending."""
from datetime import datetime, timezone, timedelta

import requests

from scripts.crm_transfer import domain
from scripts.workflow_store import encode


class KerixClient:
    SEARCH_URL = 'https://www.kerix.net/fr/search'

    def search(self, name):
        try:
            response = requests.get(self.SEARCH_URL,params={'q':name},timeout=(5,20),allow_redirects=False)
            response.raise_for_status()
            data = response.json()
            rows = data.get('companies')
            if not isinstance(rows,list):
                raise ValueError()
            return rows
        except (requests.RequestException,ValueError):
            raise RuntimeError('KERIX_ACCESS_OR_CONTRACT_UNAVAILABLE') from None


def enrich_company(store, crm, kerix, contact_id, *, at=None):
    contact = store.contact(contact_id)
    if not contact or not contact['company_id']:
        return {'status':'NO_TRANSFERRED_COMPANY'}
    lead = __import__('json').loads(contact['snapshot'])
    operation = store.operation(contact_id,'KERIX',{'company_id':contact['company_id'],'company_name':lead['company_name']})
    if operation['status'] in ('SUCCEEDED','NO_MATCH','NEEDS_REVIEW'):
        return {'status':operation['status']}
    current_time=at or datetime.now(timezone.utc)
    if operation['next_attempt_at'] and datetime.fromisoformat(operation['next_attempt_at'])>current_time:
        return {'status':'WAITING'}
    try:
        candidates = kerix.search(lead['company_name'])
    except RuntimeError:
        attempts=operation['attempts']+1
        with store.connection() as c:
            c.execute('UPDATE workflow_operations SET attempts=?,next_attempt_at=? WHERE id=?',
                      (attempts,(current_time+timedelta(seconds=60*2**(attempts-1))).isoformat(),operation['id']))
        store.finish_operation(operation['id'],'NEEDS_REVIEW' if attempts>=3 else 'WAITING',{'reason':'KERIX_ACCESS_OR_CONTRACT_UNAVAILABLE'})
        return {'status':'WAITING'}
    # Search names/descriptions alone never bind a Kerix company. Additional
    # independently confirmed domain or registry identifier is mandatory.
    matches = [r for r in candidates if
               (lead.get('company_domain_source') and domain(r.get('website'))==lead.get('company_domain'))
               or (lead.get('company_registry_id') and r.get('registryId')==lead.get('company_registry_id'))]
    if len(matches)!=1:
        store.finish_operation(operation['id'],'NEEDS_REVIEW' if candidates else 'NO_MATCH',{'candidates':candidates})
        return {'status':'NEEDS_REVIEW' if candidates else 'NO_MATCH'}
    row = matches[0]
    existing = crm.workflow_get('companies',contact['company_id'])
    fields = crm.workflow_fields('companies',crm.inspect_workflow_schema())
    proposed = {'activitySector':row.get('companyDescriptionTextShort'), 'name':row.get('companyName')}
    update = {k:v for k,v in proposed.items() if k in fields and v and not existing.get(k)}
    if update:
        crm.workflow_patch('companies',contact['company_id'],update)
    proof = {'source':KerixClient.SEARCH_URL,'companyId':row.get('companyId'),
             'observed_at':(at or datetime.now(timezone.utc)).isoformat(),'fields':update,'raw':row}
    store.finish_operation(operation['id'],'SUCCEEDED',proof)
    return {'status':'SUCCEEDED','fields':list(update)}
