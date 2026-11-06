"""Explicit reviewed maintenance on compact candidates; no SAS or paid calls."""
import csv
import hashlib
import io
import json
import re
from datetime import datetime
from pathlib import Path

from scripts.assign_company_domain import company_key
from scripts.domain_review import domain_name, recent
from scripts.email_hygiene import email_identity, fresh
from scripts.workflow_store import encode


def assign_domain_compact(store, candidate_ids, cache_path, domain, company, *, dry_run=False):
    import sqlite3
    domain = domain_name(domain)
    with sqlite3.connect(Path(cache_path).resolve().as_uri()+'?mode=ro',uri=True) as c:
        cached = c.execute('SELECT observed_at,payload FROM domain_review_cache WHERE key=?',(domain,)).fetchone()
    if not cached:
        raise ValueError('CACHED_DOMAIN_REVIEW_REQUIRED')
    evidence = json.loads(cached[1])
    if (evidence.get('domain') != domain or evidence.get('status') != 'FOUND'
            or evidence.get('observed_at') != cached[0] or not recent(cached[0],7)
            or evidence.get('protocol') not in ('WHOIS','RDAP') or not evidence.get('source')
            or not re.fullmatch('[0-9a-f]{64}',evidence.get('evidence_sha256',''))
            or not any(company_key(n)==company_key(company) for n in evidence.get('registrant_names',[]) if isinstance(n,str))):
        raise ValueError('RECENT_MATCHING_REGISTRANT_REQUIRED')
    if not candidate_ids:
        raise ValueError('EXPLICIT_CANDIDATES_REQUIRED')
    candidates = [store.candidate(i) for i in candidate_ids]
    if any(not c or company_key(c['data'].get('company_name')) != company_key(company)
           or c['data'].get('company_domain') not in (None,'',domain) for c in candidates):
        raise ValueError('DOMAIN_ASSIGNMENT_CONFLICT')
    if dry_run:
        return {'dry_run':True,'candidates':candidate_ids,'domain':domain}
    with store.lock('compact-maintenance'):
        for candidate in candidates:
            uri = store.add_evidence(candidate['id'],cached[1].encode(),kind='reviewed_domain')
            store.update_candidate(candidate['id'],{'company_domain':domain,
                'company_domain_source':encode({'method':'WHOIS_RDAP_OPERATOR_ASSIGNMENT','proof_uri':uri})},
                lane='qualification',expected_revision=candidate['revision'])
    return {'status':'UPDATED','candidates':candidate_ids,'domain':domain}


def import_verification_compact(store, candidate_id, manifest_path, *, dry_run=False, at=None):
    path = Path(manifest_path)
    manifest = json.loads(path.read_bytes())
    content = {}
    for kind in ('response','csv'):
        item = manifest[kind]
        raw = (path.parent/item['file']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('EVIDENCE_HASH_MISMATCH')
        content[kind] = raw
    response = json.loads(content['response'])
    try:
        explicit_timezone = datetime.fromisoformat(manifest['observed_at']).tzinfo is not None
    except (KeyError,TypeError,ValueError):
        explicit_timezone = False
    if (response.get('trackingId') != manifest['tracking_id'] or response.get('overallStatus') != 'Completed'
            or type(response.get('percent')) is not int or response['percent'] != 100
            or not explicit_timezone or not fresh(manifest.get('observed_at'),at=at)):
        raise ValueError('RECENT_COMPLETE_VERIFICATION_REQUIRED')
    reader = csv.DictReader(io.StringIO(content['csv'].decode('utf-8-sig')),strict=True)
    if reader.fieldnames != ['email','finalScore','finalScoreValue','catchall']:
        raise ValueError('UNEXPECTED_CSV_COLUMNS')
    rows = list(reader)
    if type(response.get('totalRecord')) is not int or response['totalRecord'] != len(rows):
        raise ValueError('VERIFICATION_COUNT_MISMATCH')
    candidate = store.candidate(candidate_id)
    if not candidate:
        raise ValueError('CANDIDATE_NOT_FOUND')
    data = candidate['data']
    email = (data.get('raw_email') or '').strip().lower()
    if not email_identity(email,data.get('first_name'),data.get('last_name'))['accepted'] or store.excluded(email):
        raise ValueError('PROFESSIONAL_NOMINATIVE_EMAIL_REQUIRED')
    matches = [r for r in rows if (r.get('email') or '').strip().lower() == email]
    if len(matches) != 1 or any(set(r) != set(reader.fieldnames) or any(v is None for v in r.values()) for r in rows):
        raise ValueError('VERIFICATION_TARGET_MISMATCH')
    row = matches[0]
    verdict = {('Deliverable','false'):'VALID',('UnDeliverable','false'):'INVALID'}.get((row['finalScoreValue'],row['catchall']),'WAITING')
    if dry_run:
        return {'dry_run':True,'candidate_id':candidate_id,'verdict':verdict}
    with store.lock('compact-maintenance'):
        for kind,raw in {**content,'manifest':path.read_bytes()}.items():
            store.add_evidence(candidate_id,raw,kind='no2bounce_import_'+kind)
        store.update_candidate(candidate_id,{'email_check_status':verdict,'email_validation_source':'no2bounce',
            'email_validated_at':manifest['observed_at'],'email_checked_input':email,
            'validated_email':email if verdict=='VALID' else None,'clean_email':email if verdict=='VALID' else None,
            'neverbounce_status':'VALID' if verdict=='VALID' else 'UNKNOWN'},lane='qualification',
            status='QUALIFIED' if verdict=='VALID' else 'WAITING',
            wait_reason=None if verdict=='VALID' else 'VERIFICATION_'+verdict,expected_revision=candidate['revision'])
    return {'status':'IMPORTED','candidate_id':candidate_id,'verdict':verdict}
