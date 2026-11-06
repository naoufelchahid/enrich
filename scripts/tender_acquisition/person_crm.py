"""Admit verified business contacts to Twenty without triggering outreach."""

from __future__ import annotations

import hashlib
import json

from scripts.enrichment_clients import linkedin_url

from .company_crm import empty_changes, link_url
from .coordinates import QualifiedContact
from .identity import normalized
from .state import OperationStore


def person_company_id(row):
    return row.get('companyId') or (row.get('company') or {}).get('id')


def same_person(row: dict, qualified: QualifiedContact, company_id: str) -> bool:
    person = qualified.person
    name = row.get('name') or {}
    return (normalized(name.get('firstName')) == normalized(person.first_name)
            and normalized(name.get('lastName')) == normalized(person.last_name)
            and linkedin_url(link_url(row.get('linkedinLink'))) == linkedin_url(person.linkedin_url)
            and person_company_id(row) in (None, company_id))


def existing_person_match(row: dict, qualified: QualifiedContact, company_id: str) -> bool:
    if same_person(row, qualified, company_id):
        return True
    if not qualified.email or person_company_id(row) not in (None, company_id):
        return False
    name = row.get('name') or {}
    existing_url = linkedin_url(link_url(row.get('linkedinLink')))
    existing_email = (row.get('emails') or {}).get('primaryEmail')
    return (not existing_url and isinstance(existing_email, str)
            and existing_email.casefold() == qualified.email.casefold()
            and normalized(name.get('firstName')) == normalized(qualified.person.first_name)
            and normalized(name.get('lastName')) == normalized(qualified.person.last_name))


def person_payload(qualified: QualifiedContact, company_id: str, fields: set[str]) -> dict:
    person = qualified.person
    result = {'name': {'firstName': person.first_name, 'lastName': person.last_name},
              'companyId': company_id,
              'linkedinLink': {'primaryLinkUrl': linkedin_url(person.linkedin_url),
                               'primaryLinkLabel': 'LinkedIn'},
              'jobTitle': person.title}
    if qualified.email:
        result['emails'] = {'primaryEmail': qualified.email}
    if qualified.phone:
        result['phones'] = {'primaryPhoneNumber': qualified.phone}
        if qualified.phone.startswith('+212'):
            result['phones'].update(primaryPhoneCountryCode='MA', primaryPhoneCallingCode='+212')
    return {key: value for key, value in result.items() if key in fields and value}


def upsert_person(client, store: OperationStore, qualified: QualifiedContact,
                  company_id: str) -> str | None:
    if not qualified.email and not qualified.phone:
        return None
    fields = client.workflow_fields('people', client.inspect_workflow_schema())
    if not {'name', 'companyId', 'linkedinLink'} <= fields:
        raise RuntimeError('TWENTY_PERSON_MAPPING_REQUIRED')
    proposed = person_payload(qualified, company_id, fields)
    if qualified.email and 'emails' not in proposed and not qualified.phone:
        return None
    if qualified.phone and 'phones' not in proposed and not qualified.email:
        return None
    people = client.workflow_list('people')
    matches = [row for row in people if existing_person_match(row, qualified, company_id)]
    if len(matches) > 1:
        return None
    url = linkedin_url(qualified.person.linkedin_url)
    for row in people:
        if row in matches:
            continue
        if linkedin_url(link_url(row.get('linkedinLink'))) == url:
            return None
        current_email = (row.get('emails') or {}).get('primaryEmail')
        if qualified.email and current_email and current_email.casefold() == qualified.email.casefold():
            return None
        name = row.get('name') or {}
        if (person_company_id(row) == company_id
                and normalized(name.get('firstName')) == normalized(qualified.person.first_name)
                and normalized(name.get('lastName')) == normalized(qualified.person.last_name)):
            return None
    match = matches[0] if matches else None
    if match and qualified.email:
        current_email = (match.get('emails') or {}).get('primaryEmail')
        if current_email and current_email.casefold() != qualified.email.casefold():
            return None
    if not match:
        op = store.reserve('CRM_PERSON_CREATE', 'person_linkedin', url)
        if op['state'] == 'SUCCEEDED' and op['remote_id']:
            row = client.workflow_get('people', op['remote_id'])
            return op['remote_id'] if row and same_person(row, qualified, company_id) else None
        if op['state'] in ('SENDING', 'UNKNOWN'):
            return None
        if op['state'] not in ('PENDING', 'RETRYABLE'):
            return None
        store.begin(op['id'])
        try:
            record_id = client.workflow_create('people', proposed)
        except Exception:
            store.finish(op['id'], 'UNKNOWN')
            return None
        readback = client.workflow_get('people', record_id)
        if not readback or not same_person(readback, qualified, company_id):
            store.finish(op['id'], 'UNKNOWN', remote_id=record_id)
            return None
        store.finish(op['id'], 'SUCCEEDED', remote_id=record_id)
        match = readback
    else:
        op = store.get('CRM_PERSON_CREATE', 'person_linkedin', url)
        if op and op['state'] == 'UNKNOWN':
            store.finish(op['id'], 'SUCCEEDED', remote_id=match['id'], outcome='reconciled')
    current = client.workflow_get('people', match['id'])
    if not current or not existing_person_match(current, qualified, company_id):
        return None
    changes = empty_changes(current, proposed)
    if not person_company_id(current):
        changes['companyId'] = company_id
    if not changes:
        with store.connection() as db:
            uncertain = db.execute('''SELECT id FROM operations WHERE kind='CRM_PERSON_PATCH'
                AND subject_kind='person_linkedin' AND subject_id LIKE ? AND state='UNKNOWN' ''',
                (url + '#%',)).fetchall()
        for row in uncertain:
            store.finish(row['id'], 'SUCCEEDED', remote_id=match['id'], outcome='reconciled')
        return match['id']
    marker = hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()[:20]
    patch_op = store.reserve('CRM_PERSON_PATCH', 'person_linkedin', url + '#' + marker)
    if patch_op['state'] == 'UNKNOWN':
        latest = client.workflow_get('people', match['id'])
        if latest and not empty_changes(latest, proposed) and person_company_id(latest) == company_id:
            store.finish(patch_op['id'], 'SUCCEEDED', remote_id=match['id'], outcome='reconciled')
            return match['id']
        return None
    if patch_op['state'] == 'SUCCEEDED':
        return match['id']
    if patch_op['state'] not in ('PENDING', 'RETRYABLE'):
        return None
    store.begin(patch_op['id'])
    try:
        client.workflow_patch('people', match['id'], changes)
        latest = client.workflow_get('people', match['id'])
    except Exception:
        store.finish(patch_op['id'], 'UNKNOWN', remote_id=match['id'])
        return None
    if not latest or not same_person(latest, qualified, company_id) or empty_changes(latest, proposed):
        store.finish(patch_op['id'], 'UNKNOWN', remote_id=match['id'])
        return None
    store.finish(patch_op['id'], 'SUCCEEDED', remote_id=match['id'])
    return match['id']
