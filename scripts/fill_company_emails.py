"""Populate Companies/Mail from retained Mohammedia sources, with a reviewable plan.

Preparation is local. --apply uses the existing Twenty client, preserves existing
values, journals every mutation before submission, and verifies the readback.
"""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from scripts.email_hygiene import is_personal_email
from scripts.import_mohammedia_remaining import connect, norm, own_domain

SOURCE = Path('bdd/sources/apify-google-search-20261030-test')
RUN = Path('reports/company-emails-20261104')
OUT = Path('outputs/company-emails-20261104')
BACKUP = Path('backups/2026-11-04-company-emails/before.json.gz')
JOURNAL = Path('bdd/workflow/company-emails-20261104.sqlite')
EMAIL = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')


def valid_email(value):
    return bool(value and EMAIL.fullmatch(value) and '..' not in value
                and not value.startswith('.') and not value.split('@')[0].endswith('.')
                and not is_personal_email(value))


def same_domain(left, right):
    return bool(left and right and (left == right or left.endswith('.' + right)
                                   or right.endswith('.' + left)))


def match_lead(row, companies):
    if row.get('country') != 'Morocco':
        return [], 'CONTACT_OUTSIDE_MOROCCO'
    email = (row.get('email') or '').lower().strip()
    if not valid_email(email):
        return [], 'MISSING_OR_FORBIDDEN_EMAIL'
    declared = (row.get('company_domain') or '').lower().removeprefix('www.')
    website = own_domain(row.get('company_website'))
    if declared and website and not same_domain(declared, website):
        return [], 'CONFLICTING_COMPANY_DOMAINS'
    domain = declared or website
    if not same_domain(email.split('@')[1], domain):
        return [], 'EMAIL_COMPANY_DOMAIN_MISMATCH'
    matches = [c for c in companies if same_domain(domain, own_domain(c.get('domainName')))]
    return (matches, 'COMPANY_DOMAIN') if len(matches) == 1 else ([], 'AMBIGUOUS_OR_MISSING_COMPANY')


def match_excerpt(email, result, companies):
    domain = email.split('@')[1]
    matches = [c for c in companies if same_domain(domain, own_domain(c.get('domainName')))]
    if len(matches) == 1:
        return matches, 'COMPANY_DOMAIN'
    if matches:
        return [], 'SHARED_COMPANY_DOMAIN'
    # A search query is not evidence of the identity in the returned excerpt.
    body = ' '.join(str(result.get(k) or '') for k in ('title', 'description', 'url'))
    text = norm(body)
    local = any(city in text for city in ('mohammedia', 'mohammadia', 'mohamedia', 'mohammedida'))
    digits = re.sub(r'\D', '', body)
    matches = []
    for company in companies:
        name = norm(company['name'])
        number = (company.get('phones') or {}).get('primaryPhoneNumber')
        phone_matches = bool(number and len(number) >= 9 and number in digits)
        if len(name) >= 4 and name in text and (local or phone_matches):
            # Refuse an excerpt that conflicts with an already known domain.
            if not own_domain(company.get('domainName')):
                matches.append(company)
    return (matches, 'NAME_AND_LOCATION_OR_PHONE') if len(matches) == 1 else ([], 'UNCONFIRMED_COMPANY_IDENTITY')


def prepare():
    snapshot = json.loads(gzip.decompress(BACKUP.read_bytes()))
    companies = snapshot['companies']
    selected = defaultdict(lambda: defaultdict(list))
    rejected = []
    hashes = {}
    for path in sorted(SOURCE.glob('*.json')):
        if path.name not in {'resultats.json', 'resultats-autres.json', 'sites-manquants-resultats.json',
                             'leads-finder-test.json', 'leads-finder-ma-dirigeants.json',
                             'leads-finder-ma-maroc.json', 'leads-finder-sans-contact.json'}:
            continue
        raw = path.read_bytes()
        hashes[str(path)] = hashlib.sha256(raw).hexdigest()
        rows = json.loads(raw)
        if not isinstance(rows, list):
            rejected.append({'file': str(path), 'reason': 'NON_LIST_SOURCE'})
            continue
        for index, row in enumerate(rows):
            if 'email' in row:
                matches, reason = match_lead(row, companies)
                email = (row.get('email') or '').lower().strip()
                proof = {'file': str(path), 'row': index, 'kind': 'lead', 'reason': reason,
                         'person': row.get('full_name'), 'company': row.get('company_name'),
                         'city': row.get('city')}
                if matches:
                    selected[matches[0]['id']][email].append(proof)
                else:
                    rejected.append(dict(proof, email=email))
                continue
            for pos, result in enumerate(row.get('organicResults', [])):
                body = json.dumps(result, ensure_ascii=False)
                for email in sorted({e.lower() for e in EMAIL.findall(body)}):
                    matches, reason = match_excerpt(email, result, companies) if valid_email(email) else ([], 'MALFORMED_OR_FORBIDDEN_EMAIL')
                    proof = {'file': str(path), 'row': index, 'organic_index': pos,
                             'kind': 'organic', 'reason': reason,
                             'url': result.get('url'), 'title': result.get('title'),
                             'description': result.get('description')}
                    if matches:
                        selected[matches[0]['id']][email].append(proof)
                    else:
                        rejected.append(dict(proof, email=email))
            for email in sorted({e.lower() for e in EMAIL.findall(json.dumps(row.get('aiOverview') or {}, ensure_ascii=False))}):
                rejected.append({'file': str(path), 'row': index, 'email': email,
                                 'reason': 'AI_OVERVIEW_REQUIRES_CORROBORATION'})
    by_id = {c['id']: c for c in companies}
    generic = {'contact', 'info', 'infos', 'commercial', 'commerciale', 'administration'}
    rows = []
    for cid, emails in selected.items():
        ordered = sorted(emails, key=lambda e: (e.split('@')[0] not in generic, e))
        rows.append({'company_id': cid, 'name': by_id[cid]['name'], 'emails': ordered,
                     'evidence': dict(emails), 'domain_before': own_domain(by_id[cid].get('domainName'))})
    plan = {'source_sha256': hashes, 'companies': sorted(rows, key=lambda r: r['name']),
            'review': rejected, 'companies_before': len(companies),
            'note': 'Adresses générales et nominatives de l’entreprise ou du groupe. '
                    'Le rattachement au site local et la délivrabilité ne sont pas vérifiés.'}
    RUN.mkdir(parents=True, exist_ok=True)
    (RUN / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    print(json.dumps({'companies': len(rows), 'emails': sum(len(r['emails']) for r in rows),
                      'rows': [{'company': r['name'], 'emails': len(r['emails'])} for r in plan['companies']]}, ensure_ascii=False, indent=2))


def apply():
    plan = json.loads((RUN / 'plan.json').read_text())
    for filename, digest in plan['source_sha256'].items():
        assert hashlib.sha256(Path(filename).read_bytes()).hexdigest() == digest
    client = connect()
    journal = sqlite3.connect(JOURNAL)
    journal.execute('CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, status TEXT, method TEXT, path TEXT, payload TEXT, result TEXT)')
    journal.execute('CREATE TABLE IF NOT EXISTS evidence (company_id TEXT PRIMARY KEY, plan TEXT, verified TEXT)')
    for row in plan['companies']:
        previous = journal.execute('SELECT plan FROM evidence WHERE company_id=?', (row['company_id'],)).fetchone()
        exact = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if previous:
            assert previous[0] == exact, 'Plan changed after journal creation'
        else:
            journal.execute('INSERT INTO evidence VALUES (?,?,NULL)', (row['company_id'], exact))
    journal.commit()

    def mutate(key, method, path, payload):
        previous = journal.execute('SELECT status,payload FROM operations WHERE key=?', (key,)).fetchone()
        if previous:
            assert json.loads(previous[1]) == payload, 'Payload differs from recorded operation'
            if previous[0] != 'CONFIRMED':
                raise RuntimeError('Reconcile uncertain operation before retry: ' + key)
            return
        journal.execute('INSERT INTO operations VALUES (?,?,?,?,?,NULL)',
                        (key, 'SENDING', method, path, json.dumps(payload, ensure_ascii=False)))
        journal.commit()
        result = client.workflow_request(method, path, payload=payload)
        journal.execute('UPDATE operations SET status=?,result=? WHERE key=?',
                        ('CONFIRMED', json.dumps(result, ensure_ascii=False), key))
        journal.commit()

    objects = client.workflow_request('GET', '/rest/metadata/objects')['data']
    obj = next(o for o in objects if o['nameSingular'] == 'company')
    fields = [f for f in obj['fields'] if f['name'] == 'emails' or f['label'].lower() == 'mail']
    if not fields:
        mutate('create-mail', 'POST', '/rest/metadata/fields', {
            'objectMetadataId': obj['id'], 'type': 'EMAILS', 'name': 'emails', 'label': 'Mail',
            'description': plan['note'], 'icon': 'IconMail', 'isNullable': True,
            'settings': {'maxNumberOfValues': 1000}})
        obj = next(o for o in client.workflow_request('GET', '/rest/metadata/objects')['data'] if o['nameSingular'] == 'company')
        fields = [f for f in obj['fields'] if f['name'] == 'emails']
    assert len(fields) == 1 and fields[0]['type'] == 'EMAILS' and fields[0]['name'] == 'emails'
    field = fields[0]
    assert 'emails' in client.workflow_fields('companies', client.inspect_workflow_schema())
    views = client.workflow_request('GET', '/rest/metadata/views')
    for view in views:
        if view['objectMetadataId'] != obj['id']:
            continue
        columns = [f for f in view.get('viewFields', []) if f['fieldMetadataId'] == field['id']]
        if not columns:
            mutate('column:' + view['id'], 'POST', '/rest/metadata/viewFields', {
                'fieldMetadataId': field['id'], 'viewId': view['id'], 'isVisible': True, 'size': 260,
                'position': max((f['position'] for f in view.get('viewFields', [])), default=0) + 1})
        elif not columns[0]['isVisible']:
            mutate('show:' + view['id'], 'PATCH', '/rest/metadata/viewFields/' + columns[0]['id'], {'isVisible': True, 'size': 260})
    print('Mail field created and visible', flush=True)
    current = {c['id']: c for c in client.workflow_list('companies')}
    for row in plan['companies']:
        co = current[row['company_id']]
        assert co['name'] == row['name'] and own_domain(co.get('domainName')) == row['domain_before']
        old = co.get('emails') or {}
        primary = old.get('primaryEmail') or row['emails'][0]
        extra = list(dict.fromkeys((old.get('additionalEmails') or []) + row['emails']))
        extra = [e for e in extra if e.lower() != primary.lower()]
        wanted = {'primaryEmail': primary, 'additionalEmails': extra}
        if old != wanted:
            mutate('fill:' + co['id'], 'PATCH', '/rest/companies/' + co['id'], {'emails': wanted})
        print('Populated:', row['name'], len(row['emails']), flush=True)
    after = {c['id']: c for c in client.workflow_list('companies')}
    for row in plan['companies']:
        actual = after[row['company_id']].get('emails') or {}
        emails = {e.lower() for e in [actual.get('primaryEmail', '')] + (actual.get('additionalEmails') or [])}
        assert set(row['emails']) <= emails, row['name']
        before = current[row['company_id']].get('emails') or {}
        previous = {e.lower() for e in [before.get('primaryEmail') or ''] + (before.get('additionalEmails') or []) if e}
        assert previous <= emails, 'Existing email lost'
        journal.execute('UPDATE evidence SET verified=? WHERE company_id=?', (json.dumps(after[row['company_id']], ensure_ascii=False), row['company_id']))
    for view in client.workflow_request('GET', '/rest/metadata/views'):
        if view['objectMetadataId'] == obj['id']:
            assert any(f['fieldMetadataId'] == field['id'] and f['isVisible'] for f in view['viewFields'])
    journal.commit()
    summary = {'field_id': field['id'], 'label': field['label'], 'companies_populated': len(plan['companies']),
               'emails': sum(len(r['emails']) for r in plan['companies']), 'companies_total': len(after),
               'deliverability_verified': False, 'verified': True}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    import csv
    with (OUT / 'entreprises-emails.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['Entreprise', 'Email', 'Type de source', 'Fichier', 'Ligne JSON (index)', 'Company ID'])
        for row in plan['companies']:
            for email in row['emails']:
                proof = row['evidence'][email][0]
                writer.writerow([row['name'], email, proof['kind'], proof['file'], proof['row'], row['company_id']])
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    apply() if args.apply else prepare()
