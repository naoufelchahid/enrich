"""Reconcile, archive and verify CRM transfers before removing staging contacts."""
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from scripts.direct_enrichment import admissible, confirmed_linkedin_input
from scripts.email_hygiene import name_tokens, email_identity
from scripts.enrichment_clients import linkedin_url
from scripts.outreach_journal import flush_journal, read_contact, journal_path
from scripts.workflow_store import encode, now


def domain(value):
    if isinstance(value,dict):
        value = value.get('primaryLinkUrl') or value.get('primaryLinkLabel')
    if not isinstance(value,str):
        return None
    try:
        return urlsplit(value if '://' in value else 'https://'+value).hostname
    except ValueError:
        return None


def same_person(record, lead):
    name = record.get('name') or {}
    names_match = (name_tokens(name.get('firstName')) == name_tokens(lead.get('first_name'))
                   and name_tokens(name.get('lastName')) == name_tokens(lead.get('last_name')))
    url = record.get('linkedinLink') or record.get('linkedinUrl')
    if isinstance(url,dict):
        url = url.get('primaryLinkUrl')
    url_match = linkedin_url(url) and linkedin_url(url) == linkedin_url(lead.get('linkedin_url'))
    email = (record.get('emails') or {}).get('primaryEmail')
    email_match = (email and email.casefold() == (lead.get('clean_email') or '').casefold()
                   and email_identity(email,lead.get('first_name'),lead.get('last_name'))['accepted'])
    return bool(names_match and (url_match or email_match))


def crm_admissible(lead, *, at=None):
    """A fresh, verified professional mailbox is the CRM admission gate."""
    return admissible(lead, at=at) and lead.get('email_check_status') == 'VALID'


def atomic_archive(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as handle:
        json.dump(data,handle,ensure_ascii=False,indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        tmp = handle.name
    os.chmod(tmp,0o600)
    os.replace(tmp,path)


def _ensure_entity(store, client, contact_id, kind, resource, payload, matches, *, allowed=lambda:True):
    operation = store.operation(contact_id,kind,payload)
    if operation['status']=='SUCCEEDED':
        return json.loads(operation['result'])['id']
    if len(matches)>1:
        store.finish_operation(operation['id'],'NEEDS_REVIEW',{'reason':'AMBIGUOUS_MATCH'})
        return None
    if matches:
        identity = matches[0]['id']
        store.finish_operation(operation['id'],'SUCCEEDED',{'id':identity,'reconciled':True})
        return identity
    if operation['status'] in ('SENDING','UNKNOWN','NEEDS_REVIEW'):
        return None
    if not allowed():
        return None
    store.finish_operation(operation['id'],'SENDING')
    try:
        identity = client.workflow_create(resource,payload)
    except Exception:
        store.finish_operation(operation['id'],'UNKNOWN')
        return None
    store.finish_operation(operation['id'],'SUCCEEDED',{'id':identity})
    return identity


def transfer_contact(db_path, lead_id, store, client, *, archive_dir, schema=None,deadline=None,clock=None):
    from scripts.backend_guard import require_legacy
    require_legacy(db_path, lane='crm', journal_path=store.path)
    clock=clock or (lambda:datetime.now(timezone.utc))
    allowed=lambda:deadline is None or clock()<deadline
    if not allowed():
        return {'status':'WINDOW_ENDED'}
    with store.lock():
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            row = c.execute('SELECT * FROM staging_leads WHERE id=?',(lead_id,)).fetchone()
            if not row:
                return {'status':'ABSENT'}
            lead = dict(row)
            proof = confirmed_linkedin_input(c,lead)
            suppressed = c.execute('SELECT 1 FROM email_suppressions WHERE email=?',(lead.get('clean_email'),)).fetchone()
        if not crm_admissible(lead, at=clock()) or suppressed or store.excluded(lead.get('clean_email') or ''):
            return {'status':'NOT_ADMISSIBLE'}
        contact_id = store.put_contact(lead)
        try:
            schema = schema or client.inspect_workflow_schema()
            people_fields = client.workflow_fields('people',schema)
            company_fields = client.workflow_fields('companies',schema)
            if not {'name','emails','companyId','campaignStatus'}<=people_fields or 'name' not in company_fields:
                return {'status':'CRM_MAPPING_REQUIRED'}
            store.event('crm_mapping',contact_id,{'people':sorted(people_fields),'companies':sorted(company_fields)},
                        key='mapping:'+contact_id)
            companies = client.workflow_list('companies')
            confirmed_domain = lead.get('company_domain') if lead.get('company_domain_source') else None
            company_matches = [r for r in companies if confirmed_domain and domain(r.get('domainName'))==confirmed_domain.lower()]
            # A previously verified transfer provides a durable company ID when
            # a forbidden address is replaced and the contact is admitted again.
            prior=store.contact(contact_id) or {}
            known_company_id=lead.get('crm_company_id') or prior.get('company_id')
            if not company_matches and known_company_id:
                company_matches = [r for r in companies if r['id']==known_company_id
                                   and name_tokens(r.get('name'))==name_tokens(lead.get('company_name'))]
            homonyms = [r for r in companies if name_tokens(r.get('name'))==name_tokens(lead.get('company_name'))]
            if not company_matches and homonyms:
                return {'status':'COMPANY_IDENTITY_REVIEW'}
            company_payload = {'name':lead['company_name']}
            if confirmed_domain:
                company_payload['domainName'] = {'primaryLinkUrl':'https://'+confirmed_domain,'primaryLinkLabel':confirmed_domain}
            if lead.get('city') or lead.get('address'):
                company_payload['address'] = {'addressCity':lead.get('city') or '', 'addressStreet1':lead.get('address') or ''}
            if lead.get('activity_sector'):
                company_payload['activitySector'] = lead['activity_sector']
            company_payload = {k:v for k,v in company_payload.items() if k in company_fields}
            company_id = _ensure_entity(store,client,contact_id,'CREATE_COMPANY','companies',company_payload,company_matches,allowed=allowed)
            if not company_id:
                return {'status':'CRM_RECONCILIATION_REQUIRED'}
            people = client.workflow_list('people')
            person_matches = [r for r in people if same_person(r,lead)]
            # Shared email or LinkedIn without concordant identity is an ambiguity.
            confirmed_url = proof['linkedin_url'] if proof else None
            clashes = [r for r in people if not same_person(r,lead) and
                       (((r.get('emails') or {}).get('primaryEmail') or '').lower()==lead['clean_email'].lower()
                        or (confirmed_url and linkedin_url((r.get('linkedinLink') or {}).get('primaryLinkUrl'))==confirmed_url))]
            if clashes:
                return {'status':'PERSON_IDENTITY_REVIEW'}
            payload = {'name':{'firstName':lead['first_name'],'lastName':lead['last_name']},
                       'emails':{'primaryEmail':lead['clean_email']},'companyId':company_id,
                       'campaignStatus':'READY_TO_MAIL','jobTitle':lead.get('job_title') or '',
                       'sourceFile':lead.get('source_file') or ''}
            if confirmed_url:
                payload['linkedinLink']={'primaryLinkUrl':confirmed_url,'primaryLinkLabel':'LinkedIn'}
            if lead.get('city'):
                payload['city']=lead['city']
            mobile=(lead.get('phone_mobile') or '').strip()
            standard=(lead.get('phone_standard') or '').strip()
            primary=mobile or standard
            if primary:
                payload['phones'] = {'primaryPhoneNumber':primary,
                                     'primaryPhoneCountryCode':'MA','primaryPhoneCallingCode':'+212'}
                if mobile and standard and mobile!=standard:
                    payload['phones']['additionalPhones']=[
                        {'number':standard,'countryCode':'MA','callingCode':'+212'}]
            payload = {k:v for k,v in payload.items() if k in people_fields}
            person_id = _ensure_entity(store,client,contact_id,'CREATE_PERSON','people',payload,person_matches,allowed=allowed)
            if not person_id:
                return {'status':'CRM_RECONCILIATION_REQUIRED'}
            person = client.workflow_get('people',person_id)
            if not person or not same_person(person,lead):
                return {'status':'CRM_READBACK_REQUIRED'}
            current_email=(person.get('emails') or {}).get('primaryEmail')
            if current_email and current_email.lower()!=lead['clean_email'].lower():
                return {'status':'PERSON_EMAIL_REVIEW'}
            current_company = person.get('companyId') or (person.get('company') or {}).get('id')
            if current_company and current_company!=company_id:
                return {'status':'PERSON_COMPANY_REVIEW'}
            # Preserve reliable CRM fields; only fill empty fields and admission.
            if person.get('campaignStatus') not in (None,'','READY_TO_MAIL','WAITING_EMAIL'):
                return {'status':'CRM_CAMPAIGN_STATUS_REVIEW'}
            changes = {k:v for k,v in payload.items() if k not in ('name','emails') and not person.get(k)}
            if payload.get('phones') and person.get('phones'):
                existing_phones=person['phones']
                if isinstance(existing_phones,dict) and existing_phones.get('primaryPhoneNumber'):
                    additions=list(existing_phones.get('additionalPhones') or [])
                    known={existing_phones['primaryPhoneNumber']}
                    known.update(p.get('number') for p in additions if isinstance(p,dict))
                    wanted=[payload['phones']['primaryPhoneNumber'],
                            *[p['number'] for p in payload['phones'].get('additionalPhones',[])]]
                    for number in wanted:
                        if number and number not in known:
                            additions.append({'number':number,'countryCode':'MA','callingCode':'+212'})
                            known.add(number)
                    if additions!=list(existing_phones.get('additionalPhones') or []):
                        changes['phones']={**existing_phones,'additionalPhones':additions}
            if not current_email:
                changes['emails']={**(person.get('emails') or {}),**payload['emails']}
            changes['campaignStatus']='READY_TO_MAIL'
            if not allowed():
                return {'status':'WINDOW_ENDED'}
            client.workflow_patch('people',person_id,changes)
            person = client.workflow_get('people',person_id)
            company = client.workflow_get('companies',company_id)
            if (not person or not company or not same_person(person,lead)
                    or (person.get('companyId') or (person.get('company') or {}).get('id'))!=company_id
                    or person.get('campaignStatus')!='READY_TO_MAIL'):
                return {'status':'CRM_READBACK_REQUIRED'}
            lead.update(crm_person_id=person_id,crm_company_id=company_id,crm_synced_at=now())
            flush_journal(db_path)
            control = {'contact':lead,'person':person,'company':company,'archived_at':now(),
                       'journal':read_contact(journal_path(db_path),contact_id)}
            atomic_archive(Path(archive_dir)/(contact_id+'.json'),control)
            with store.connection() as c:
                c.execute("UPDATE workflow_contacts SET snapshot=?,person_id=?,company_id=?,status='READY_TO_MAIL',wait_reason=NULL,admitted_at=? WHERE id=?",
                          (encode(lead),person_id,company_id,now(),contact_id))
                store.event('transfer_verified',contact_id,{'person_id':person_id,'company_id':company_id},
                            key='transfer:'+contact_id,connection=c)
            with sqlite3.connect(db_path) as c:
                # Optimistic check prevents removing a contact edited during CRM calls.
                row = c.execute('SELECT raw_email,linkedin_url,journal_contact_id FROM staging_leads WHERE id=?',(lead_id,)).fetchone()
                if row != (lead['raw_email'],lead['linkedin_url'],contact_id):
                    with store.connection() as archive:
                        archive.execute("UPDATE workflow_contacts SET status='NEEDS_REVIEW',wait_reason='STAGING_CHANGED' WHERE id=?",(contact_id,))
                    return {'status':'STAGING_CHANGED_REVIEW'}
                c.execute('UPDATE staging_leads SET crm_person_id=?,crm_synced_at=? WHERE id=?',(person_id,lead['crm_synced_at'],lead_id))
                c.execute('DELETE FROM staging_leads WHERE id=?',(lead_id,))
            flush_journal(db_path)
            # CRM readback and durable journal are complete; discard the JSON copy.
            from scripts.workflow_files import save_file
            control_path = Path(archive_dir)/(contact_id+'.json')
            try:
                digest = save_file(store, control_path)
                store.event('file_cleanup', contact_id, {'path':str(control_path.resolve()),'sha256':digest})
                control_path.unlink()
            except (OSError, RuntimeError, sqlite3.Error):
                store.event('cleanup_waiting', contact_id, {'path':str(control_path.resolve())})
            return {'status':'TRANSFERRED','contact_id':contact_id,'person_id':person_id,'company_id':company_id}
        except (RuntimeError,ValueError,sqlite3.Error):
            store.event('transfer_waiting',contact_id,{'reason':'CRM_ACCESS_OR_CONTRACT_REQUIRED'})
            return {'status':'CRM_ACCESS_OR_CONTRACT_REQUIRED'}
