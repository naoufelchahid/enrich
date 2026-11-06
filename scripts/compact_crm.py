"""Journal-only CRM admission with durable creation intents and exact readback."""
import json
import sqlite3
from datetime import datetime, timezone

from scripts.crm_transfer import _ensure_entity, crm_admissible, domain, same_person
from scripts.email_hygiene import name_tokens
from scripts.enrichment_clients import linkedin_url
from scripts.workflow_store import encode, now


def _person_payload(lead, company_id, confirmed_url, fields):
    payload = {'name':{'firstName':lead['first_name'],'lastName':lead['last_name']},
        'emails':{'primaryEmail':lead['clean_email']}, 'companyId':company_id,
        'campaignStatus':'READY_TO_MAIL', 'jobTitle':lead.get('job_title') or '',
        'sourceFile':lead.get('source_file') or ''}
    if confirmed_url:
        payload['linkedinLink'] = {'primaryLinkUrl':confirmed_url,'primaryLinkLabel':'LinkedIn'}
    if lead.get('city'):
        payload['city'] = lead['city']
    mobile = (lead.get('phone_mobile') or '').strip()
    standard = (lead.get('phone_standard') or '').strip()
    if mobile or standard:
        payload['phones'] = {'primaryPhoneNumber':mobile or standard,
            'primaryPhoneCountryCode':'MA','primaryPhoneCallingCode':'+212'}
        if mobile and standard and mobile != standard:
            payload['phones']['additionalPhones'] = [{'number':standard,'countryCode':'MA','callingCode':'+212'}]
    return {k:v for k,v in payload.items() if k in fields}


def _patch_person(store, client, identity, person_id, changes):
    operation = store.operation(identity, 'PATCH_PERSON', {'person_id':person_id,'changes':changes})
    current = client.workflow_get('people', person_id)
    if current and all(current.get(k) == v for k,v in changes.items()):
        store.finish_operation(operation['id'], 'SUCCEEDED', {'id':person_id,'reconciled':True})
        return True
    if operation['status'] in ('SENDING','UNKNOWN','NEEDS_REVIEW'):
        return False
    store.finish_operation(operation['id'], 'SENDING')
    try:
        client.workflow_patch('people', person_id, changes)
    except Exception:
        store.finish_operation(operation['id'], 'UNKNOWN')
        return False
    store.finish_operation(operation['id'], 'SUCCEEDED', {'id':person_id})
    return True


def _acquisition_goal(c, brief_id):
    row = c.execute("SELECT definition FROM workflow_briefs WHERE brief_id=? AND state='LAUNCHED' ORDER BY version DESC LIMIT 1",(brief_id,)).fetchone()
    if row is None:
        raise ValueError('ACQUISITION_BRIEF_NOT_FOUND')
    goal = json.loads(row[0]).get('admissible_goal')
    if type(goal) is not int or goal <= 0:
        raise ValueError('ACQUISITION_GOAL_REQUIRED')
    return goal


def _acquisition_count(c, brief_id):
    return c.execute('SELECT count(*) FROM workflow_brief_admissions WHERE brief_id=?',(brief_id,)).fetchone()[0]


def _already_admitted(c, candidate_id, person_id=None):
    return bool(c.execute('SELECT 1 FROM workflow_contacts WHERE admitted_at IS NOT NULL AND (id=? OR person_id=?) LIMIT 1',
                          (candidate_id,person_id)).fetchone())


def _record_acquisition_admission(c, lead, candidate_id, person_id, *, previously_admitted, person_operation_id):
    brief_id = lead.get('acquisition_brief_id')
    if not brief_id or previously_admitted:
        return False
    # Matching a person that existed before this acquisition is reuse, not a
    # newly acquired contact. A persisted submission intent also covers a lost
    # create response that was later reconciled by exact CRM readback.
    submitted = c.execute("""SELECT 1 FROM workflow_events WHERE kind='operation'
        AND json_extract(payload,'$.id')=? AND json_extract(payload,'$.state')='SENDING' LIMIT 1""",(person_operation_id,)).fetchone()
    if not submitted:
        return False
    c.execute('''CREATE TABLE IF NOT EXISTS workflow_acquisition_persons (
        person_id TEXT PRIMARY KEY, brief_id TEXT NOT NULL, version INTEGER NOT NULL,
        candidate_id TEXT NOT NULL, admitted_at TEXT NOT NULL)''')
    inserted = c.execute('INSERT OR IGNORE INTO workflow_acquisition_persons VALUES(?,?,?,?,?)',
        (person_id,brief_id,lead['acquisition_brief_version'],candidate_id,now())).rowcount
    if inserted:
        c.execute('INSERT OR IGNORE INTO workflow_brief_admissions VALUES(?,?,?)',
            (brief_id,'crm-person:'+person_id,now()))
    return bool(inserted)


def transfer_compact(store, candidate_id, client, *, archive_dir=None, schema=None,
                     deadline=None, clock=None, lane='crm'):
    """Transfer one candidate; never create, read, remove or initialize a SAS.

archive_dir is accepted for orchestration compatibility. Control evidence is
written directly into the verified blob store, without a temporary JSON copy.
"""
    clock = clock or (lambda: datetime.now(timezone.utc))
    allowed = lambda: deadline is None or clock() < deadline
    if not allowed():
        return {'status':'WINDOW_ENDED'}
    with store.lock():
        with store.connection() as c:
            store._require_authority(c, lane)
        candidate = store.candidate(candidate_id)
        if candidate is None:
            return {'status':'ABSENT'}
        lead = dict(candidate['data'])
        if lead.get('pending_legacy_jobs'):
            return {'status':'CRM_RECONCILIATION_REQUIRED'}
        if lead.get('discovery_identity_required') or lead.get('discovery_task_id'):
            from scripts.compact_linkedin import CompactLinkedIn
            if not CompactLinkedIn(store).confirmed_input(candidate):
                return {'status':'IDENTITY_CONFIRMATION_REQUIRED'}
        if not crm_admissible(lead, at=clock()) or any(store.excluded(str(lead.get(k) or ''))
                for k in ('raw_email','clean_email','validated_email','original_email')):
            return {'status':'NOT_ADMISSIBLE'}
        if candidate['wait_reason'] == 'IDENTITY_CONFLICT':
            return {'status':'PERSON_IDENTITY_REVIEW'}
        if candidate['status'] == 'TRANSFERRED':
            from scripts.workflow_files import confirmed
            if confirmed(store, None, [candidate_id]):
                return {'status':'TRANSFERRED','contact_id':candidate_id,
                        'person_id':lead['crm_person_id'],'company_id':lead['crm_company_id']}
        brief_id = lead.get('acquisition_brief_id')
        if brief_id:
            with store.connection() as c:
                if not _already_admitted(c,candidate_id,lead.get('crm_person_id')) and _acquisition_count(c,brief_id) >= _acquisition_goal(c,brief_id):
                    return {'status':'WAITING','reason':'ADMISSIBLE_GOAL_REACHED'}
        try:
            from scripts.compact_linkedin import CompactLinkedIn
            proof = CompactLinkedIn(store).confirmed_input(candidate)
            confirmed_url = proof['linkedin_url'] if proof else None
            # An unconfirmed source URL cannot participate in CRM identity matching.
            identity_lead = {**lead, 'linkedin_url':confirmed_url}
            schema = schema if schema is not None else client.inspect_workflow_schema()
            people_fields = client.workflow_fields('people', schema)
            company_fields = client.workflow_fields('companies', schema)
            if not {'name','emails','companyId','campaignStatus'} <= people_fields or 'name' not in company_fields:
                return {'status':'CRM_MAPPING_REQUIRED'}
            company_payload = {'name':lead['company_name']}
            confirmed_domain = lead.get('company_domain') if lead.get('company_domain_source') else None
            if confirmed_domain:
                confirmed_domain = confirmed_domain.strip().lower()
                company_payload['domainName'] = {'primaryLinkUrl':'https://' + confirmed_domain,'primaryLinkLabel':confirmed_domain}
            if lead.get('city') or lead.get('address'):
                company_payload['address'] = {'addressCity':lead.get('city') or '', 'addressStreet1':lead.get('address') or ''}
            if lead.get('activity_sector'):
                company_payload['activitySector'] = lead['activity_sector']
            company_payload = {k:v for k,v in company_payload.items() if k in company_fields}
            company_op = store.operation(candidate_id, 'CREATE_COMPANY', company_payload)
            known_company_id = lead.get('crm_company_id')
            if company_op['status'] == 'SUCCEEDED':
                known_company_id = json.loads(company_op['result'])['id']
            companies = client.workflow_list('companies')
            company_matches = [r for r in companies if
                (confirmed_domain and domain(r.get('domainName')) == confirmed_domain)
                or (known_company_id and r['id'] == known_company_id
                    and name_tokens(r.get('name')) == name_tokens(lead.get('company_name')))]
            if not company_matches and any(name_tokens(r.get('name')) == name_tokens(lead.get('company_name')) for r in companies):
                return {'status':'COMPANY_IDENTITY_REVIEW'}
            company_id = _ensure_entity(store, client, candidate_id, 'CREATE_COMPANY',
                'companies', company_payload, company_matches, allowed=allowed)
            if not company_id:
                return {'status':'CRM_RECONCILIATION_REQUIRED'}
            people = client.workflow_list('people')
            person_matches = [r for r in people if same_person(r, identity_lead)]
            clashes = [r for r in people if not same_person(r, identity_lead) and (
                ((r.get('emails') or {}).get('primaryEmail') or '').lower() == lead['clean_email'].lower()
                or (confirmed_url and linkedin_url((r.get('linkedinLink') or {}).get('primaryLinkUrl')) == confirmed_url))]
            if clashes:
                return {'status':'PERSON_IDENTITY_REVIEW'}
            payload = _person_payload(lead, company_id, confirmed_url, people_fields)
            person_operation = store.operation(candidate_id,'CREATE_PERSON',payload)
            person_id = _ensure_entity(store, client, candidate_id, 'CREATE_PERSON',
                'people', payload, person_matches, allowed=allowed)
            if not person_id:
                return {'status':'CRM_RECONCILIATION_REQUIRED'}
            person = client.workflow_get('people', person_id)
            if not person or not same_person(person, identity_lead):
                return {'status':'CRM_READBACK_REQUIRED'}
            if ((person.get('emails') or {}).get('primaryEmail') or '').lower() != lead['clean_email'].lower():
                return {'status':'PERSON_EMAIL_REVIEW'}
            current_company = person.get('companyId') or (person.get('company') or {}).get('id')
            if current_company and current_company != company_id:
                return {'status':'PERSON_COMPANY_REVIEW'}
            if person.get('campaignStatus') not in (None,'','READY_TO_MAIL','WAITING_EMAIL'):
                return {'status':'CRM_CAMPAIGN_STATUS_REVIEW'}
            changes = {k:v for k,v in payload.items() if k not in ('name','emails') and not person.get(k)}
            if payload.get('phones') and isinstance(person.get('phones'), dict) and person['phones'].get('primaryPhoneNumber'):
                existing = person['phones']
                additions = list(existing.get('additionalPhones') or [])
                known = {existing['primaryPhoneNumber'], *[p.get('number') for p in additions if isinstance(p,dict)]}
                wanted = [payload['phones']['primaryPhoneNumber'], *[p['number'] for p in payload['phones'].get('additionalPhones',[])]]
                for number in wanted:
                    if number not in known:
                        additions.append({'number':number,'countryCode':'MA','callingCode':'+212'})
                        known.add(number)
                if additions != list(existing.get('additionalPhones') or []):
                    changes['phones'] = {**existing,'additionalPhones':additions}
            changes['campaignStatus'] = 'READY_TO_MAIL'
            if not allowed():
                return {'status':'WINDOW_ENDED'}
            if not _patch_person(store, client, candidate_id, person_id, changes):
                return {'status':'CRM_RECONCILIATION_REQUIRED'}
            person = client.workflow_get('people', person_id)
            company = client.workflow_get('companies', company_id)
            if (not person or not company or not same_person(person, identity_lead)
                    or name_tokens(company.get('name')) != name_tokens(lead.get('company_name'))
                    or (confirmed_domain and domain(company.get('domainName')) != confirmed_domain)
                    or (person.get('companyId') or (person.get('company') or {}).get('id')) != company_id
                    or person.get('campaignStatus') != 'READY_TO_MAIL'):
                return {'status':'CRM_READBACK_REQUIRED'}
            if not crm_admissible(lead, at=clock()) or store.excluded(lead['clean_email']):
                return {'status':'NOT_ADMISSIBLE'}
            lead.update(crm_person_id=person_id, crm_company_id=company_id, crm_synced_at=now())
            control = {'contact':lead,'person':person,'company':company,'archived_at':now()}
            raw = encode(control).encode()
            uri = store.add_evidence(candidate_id, raw, kind='crm_control')
            if store.get_blob(uri) != raw:
                raise RuntimeError('CRM_PROOF_VERIFICATION_FAILED')
            lead['crm_control_uri'] = uri
            # One commit publishes the admission, matching proof and candidate state.
            with store.connection() as c:
                c.execute('BEGIN IMMEDIATE')
                current = c.execute('SELECT revision FROM workflow_candidates WHERE id=?', (candidate_id,)).fetchone()
                if current is None or current[0] != candidate['revision']:
                    return {'status':'CANDIDATE_CHANGED_REVIEW'}
                previously_admitted = _already_admitted(c,candidate_id,person_id)
                if brief_id and not previously_admitted and _acquisition_count(c,brief_id) >= _acquisition_goal(c,brief_id):
                    return {'status':'WAITING','reason':'ADMISSIBLE_GOAL_REACHED'}
                c.execute('''INSERT INTO workflow_contacts(id,snapshot,person_id,company_id,status,email,admitted_at)
                    VALUES(?,?,?,?,'READY_TO_MAIL',?,?) ON CONFLICT(id) DO UPDATE SET
                    snapshot=excluded.snapshot,person_id=excluded.person_id,company_id=excluded.company_id,
                    status=excluded.status,email=excluded.email,admitted_at=excluded.admitted_at,wait_reason=NULL''',
                    (candidate_id,encode(lead),person_id,company_id,lead['clean_email'],now()))
                _record_acquisition_admission(c,lead,candidate_id,person_id,
                    previously_admitted=previously_admitted,person_operation_id=person_operation['id'])
                store.event('transfer_verified',candidate_id,{'person_id':person_id,'company_id':company_id,'proof_uri':uri},
                    key='transfer:'+candidate_id+':'+uri.rsplit('/',1)[-1],connection=c)
                c.execute("""UPDATE workflow_candidates SET data=?,status='TRANSFERRED',wait_reason=NULL,
                    revision=revision+1,updated_at=? WHERE id=?""", (encode(lead),now(),candidate_id))
            return {'status':'TRANSFERRED','contact_id':candidate_id,'person_id':person_id,'company_id':company_id,'proof_uri':uri}
        except (RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error):
            store.event('transfer_waiting',candidate_id,{'reason':'CRM_ACCESS_OR_CONTRACT_REQUIRED'})
            return {'status':'CRM_ACCESS_OR_CONTRACT_REQUIRED'}
