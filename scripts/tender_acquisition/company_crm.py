"""Identity-safe Twenty company upsert with operation reconciliation."""

from __future__ import annotations

import hashlib
import json

from .identity import CompanyIdentity, domain, normalized
from .state import OperationStore


def link_url(value):
    return value.get('primaryLinkUrl') if isinstance(value, dict) else value


def company_matches(row: dict, identity: CompanyIdentity) -> bool:
    if identity.domain and domain(link_url(row.get('domainName'))) == identity.domain:
        return True
    if identity.linkedin_url and link_url(row.get('linkedinLink')) == identity.linkedin_url:
        return True
    return False


def company_subject(identity: CompanyIdentity) -> tuple[str, str]:
    if identity.domain:
        return 'company_domain', identity.domain
    if identity.registry_id:
        return 'company_registry', identity.registry_id
    if identity.linkedin_url:
        return 'company_linkedin', identity.linkedin_url
    raise ValueError('Company has no reliable identity')


def payload(identity: CompanyIdentity, fields: set[str]) -> dict:
    proposed = {'name': identity.name, 'activitySector': identity.sector,
                'description': identity.description}
    if identity.domain:
        proposed['domainName'] = {'primaryLinkUrl': 'https://' + identity.domain,
                                  'primaryLinkLabel': identity.domain}
    if identity.city:
        proposed['address'] = {'addressCity': identity.city}
    if identity.linkedin_url:
        proposed['linkedinLink'] = {'primaryLinkUrl': identity.linkedin_url,
                                    'primaryLinkLabel': 'LinkedIn'}
    if identity.phone:
        proposed['phones'] = {'primaryPhoneNumber': identity.phone,
                              'primaryPhoneCountryCode': 'MA', 'primaryPhoneCallingCode': '+212'}
    return {key: value for key, value in proposed.items() if key in fields and value}


def empty_changes(existing: dict, proposed: dict) -> dict:
    changes = {}
    for key, value in proposed.items():
        if key == 'name':
            continue
        current = existing.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            missing = {field: item for field, item in value.items() if item and not current.get(field)}
            if missing:
                changes[key] = {**current, **missing}
        elif value and not current:
            changes[key] = value
    return changes


def _reconciled(store: OperationStore, op: dict, match: dict | None) -> str | None:
    if not match:
        return None
    if op['state'] == 'UNKNOWN':
        store.finish(op['id'], 'SUCCEEDED', remote_id=match['id'], outcome='reconciled')
    return match['id']


def upsert_company(client, store: OperationStore, identity: CompanyIdentity) -> str | None:
    if not identity.domain and not identity.linkedin_url:
        return None  # The CRM has no verified field to reconcile a later write.
    fields = client.workflow_fields('companies', client.inspect_workflow_schema())
    if 'name' not in fields:
        raise RuntimeError('TWENTY_COMPANY_MAPPING_REQUIRED')
    if not ((identity.domain and 'domainName' in fields)
            or (identity.linkedin_url and 'linkedinLink' in fields)):
        return None
    proposed = payload(identity, fields)
    subject_kind, subject_id = company_subject(identity)
    companies = client.workflow_list('companies')
    matches = [row for row in companies if company_matches(row, identity)]
    if len(matches) > 1:
        return None
    match = matches[0] if matches else None
    if match and normalized(match.get('name')) != normalized(identity.name):
        return None
    if match and identity.domain and domain(link_url(match.get('domainName'))) not in (None, identity.domain):
        return None
    if match and identity.linkedin_url and link_url(match.get('linkedinLink')) not in (None, identity.linkedin_url):
        return None
    if not match and any(normalized(row.get('name')) == normalized(identity.name) for row in companies):
        return None
    if not match:
        op = store.reserve('CRM_COMPANY_CREATE', subject_kind, subject_id)
        if op['state'] == 'SUCCEEDED' and op['remote_id']:
            existing = client.workflow_get('companies', op['remote_id'])
            return op['remote_id'] if existing and company_matches(existing, identity) else None
        if op['state'] in ('SENDING', 'UNKNOWN'):
            return None  # A fresh CRM list could not reconcile the uncertain write.
        if op['state'] != 'PENDING' and op['state'] != 'RETRYABLE':
            return None
        store.begin(op['id'])
        try:
            record_id = client.workflow_create('companies', proposed)
        except Exception:
            store.finish(op['id'], 'UNKNOWN')
            return None
        readback = client.workflow_get('companies', record_id)
        if not readback or not company_matches(readback, identity):
            store.finish(op['id'], 'UNKNOWN', remote_id=record_id)
            return None
        store.finish(op['id'], 'SUCCEEDED', remote_id=record_id)
        match = readback
    else:
        op = store.get('CRM_COMPANY_CREATE', subject_kind, subject_id)
        if op and op['state'] == 'UNKNOWN':
            _reconciled(store, op, match)
    current = client.workflow_get('companies', match['id'])
    if not current or not company_matches(current, identity):
        return None
    changes = empty_changes(current, proposed)
    if not changes:
        with store.connection() as db:
            uncertain = db.execute('''SELECT id FROM operations WHERE kind='CRM_COMPANY_PATCH'
                AND subject_kind=? AND subject_id LIKE ? AND state='UNKNOWN' ''',
                (subject_kind, subject_id + '#%')).fetchall()
        for row in uncertain:
            store.finish(row['id'], 'SUCCEEDED', remote_id=match['id'], outcome='reconciled')
        return match['id']
    marker = hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()[:20]
    patch_op = store.reserve('CRM_COMPANY_PATCH', subject_kind, subject_id + '#' + marker)
    if patch_op['state'] == 'UNKNOWN':
        latest = client.workflow_get('companies', match['id'])
        if latest and not empty_changes(latest, proposed):
            store.finish(patch_op['id'], 'SUCCEEDED', remote_id=match['id'], outcome='reconciled')
            return match['id']
        return None
    if patch_op['state'] == 'SUCCEEDED':
        return match['id']
    if patch_op['state'] not in ('PENDING', 'RETRYABLE'):
        return None
    store.begin(patch_op['id'])
    try:
        client.workflow_patch('companies', match['id'], changes)
        latest = client.workflow_get('companies', match['id'])
    except Exception:
        store.finish(patch_op['id'], 'UNKNOWN', remote_id=match['id'])
        return None
    if not latest or empty_changes(latest, proposed):
        store.finish(patch_op['id'], 'UNKNOWN', remote_id=match['id'])
        return None
    store.finish(patch_op['id'], 'SUCCEEDED', remote_id=match['id'])
    return match['id']
