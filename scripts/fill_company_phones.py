"""Add a native company phone column and copy phones from linked source notes."""
import gzip
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

from scripts.import_mohammedia_remaining import connect

BASE = Path('bdd/sources/apify-mohammedia-company-phones-20261030')


def phone_payload(raw):
    digits = re.sub(r'\D', '', raw)
    if digits.startswith('212'):
        return {'primaryPhoneNumber': digits[3:], 'primaryPhoneCallingCode': '+212', 'primaryPhoneCountryCode': 'MA'}
    if digits.startswith('33'):
        return {'primaryPhoneNumber': digits[2:], 'primaryPhoneCallingCode': '+33', 'primaryPhoneCountryCode': 'FR'}
    if digits.startswith('1514') and len(digits) == 11:
        return {'primaryPhoneNumber': digits[1:], 'primaryPhoneCallingCode': '+1', 'primaryPhoneCountryCode': 'CA'}
    raise ValueError('Phone country requires review: ' + raw)


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    journal = sqlite3.connect(BASE / 'operations.sqlite')
    journal.execute('CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, status TEXT, payload TEXT, result TEXT)')
    journal.commit()
    client = connect()
    objects = client.workflow_request('GET', '/rest/metadata/objects')['data']
    company_object = next(o for o in objects if o['nameSingular'] == 'company')
    companies = client.workflow_list('companies')
    views = client.workflow_request('GET', '/rest/metadata/views')
    backup = Path('backups/2026-10-30-company-phones')
    backup.mkdir(parents=True, exist_ok=True)
    snapshot = backup / 'before.json.gz'
    if not snapshot.exists():
        raw = json.dumps({'object': company_object, 'companies': companies, 'views': [v for v in views if v['objectMetadataId'] == company_object['id']]}, ensure_ascii=False).encode()
        snapshot.write_bytes(gzip.compress(raw))
        assert hashlib.sha256(gzip.decompress(snapshot.read_bytes())).digest() == hashlib.sha256(raw).digest()

    def mutate(key, method, path, payload):
        prior = journal.execute('SELECT status FROM operations WHERE key=?', (key,)).fetchone()
        if prior and prior[0] != 'CONFIRMED':
            raise RuntimeError('Operation requires reconciliation: ' + key)
        if prior:
            return
        journal.execute('INSERT INTO operations VALUES (?,?,?,NULL)', (key, 'SENDING', json.dumps(payload, ensure_ascii=False)))
        journal.commit()
        result = client.workflow_request(method, path, payload=payload)
        journal.execute('UPDATE operations SET status=?,result=? WHERE key=?', ('CONFIRMED', json.dumps(result, ensure_ascii=False), key))
        journal.commit()

    fields = [f for f in company_object['fields'] if f['name'] == 'phones']
    if not fields:
        mutate('create-phone-field', 'POST', '/rest/metadata/fields', {
            'objectMetadataId': company_object['id'], 'type': 'PHONES', 'name': 'phones',
            'label': 'Téléphone', 'description': 'Numéro de téléphone publié par l’entreprise, issu des sources conservées.',
            'icon': 'IconPhone', 'isNullable': True, 'settings': {'maxNumberOfValues': 10}})
        objects = client.workflow_request('GET', '/rest/metadata/objects')['data']
        company_object = next(o for o in objects if o['nameSingular'] == 'company')
        fields = [f for f in company_object['fields'] if f['name'] == 'phones']
    assert len(fields) == 1 and fields[0]['type'] == 'PHONES'
    field = fields[0]
    core = client.inspect_workflow_schema()
    assert 'phones' in client.workflow_fields('companies', core)
    views = client.workflow_request('GET', '/rest/metadata/views')
    for view in views:
        if view['objectMetadataId'] != company_object['id']:
            continue
        columns = [f for f in view.get('viewFields', []) if f['fieldMetadataId'] == field['id']]
        if columns:
            if not columns[0]['isVisible']:
                mutate('show-phone:' + view['id'], 'PATCH', '/rest/metadata/viewFields/' + columns[0]['id'], {'isVisible': True, 'size': 190})
        else:
            mutate('add-phone:' + view['id'], 'POST', '/rest/metadata/viewFields', {
                'fieldMetadataId': field['id'], 'viewId': view['id'], 'isVisible': True, 'size': 190,
                'position': max((f['position'] for f in view.get('viewFields', [])), default=0) + 1})
    print('Native phone column created and visible', flush=True)
    companies = client.workflow_list('companies')
    notes = {n['id']: n for n in client.workflow_list('notes')}
    targets = client.workflow_list('noteTargets')
    by_company = {}
    for target in targets:
        cid = target.get('targetCompanyId')
        note = notes.get(target.get('noteId'))
        if not cid or not note:
            continue
        body = (note.get('bodyV2') or {}).get('markdown') or ''
        found = re.search(r'^Téléphone publié : (.+)$', body, re.M)
        if found and found[1].strip() != 'Non renseigné':
            by_company.setdefault(cid, set()).add(found[1].strip())
    expected, skipped = {}, []
    for company in companies:
        numbers = by_company.get(company['id'], set())
        if not numbers:
            skipped.append(company['id'])
            continue
        if len(numbers) != 1:
            raise RuntimeError('Multiple source phone numbers require review')
        wanted = phone_payload(next(iter(numbers)))
        existing = company.get('phones') or {}
        if existing.get('primaryPhoneNumber'):
            if re.sub(r'\D', '', existing['primaryPhoneNumber']) != wanted['primaryPhoneNumber']:
                raise RuntimeError('Existing phone differs from source')
        else:
            mutate('fill:' + company['id'], 'PATCH', '/rest/companies/' + company['id'], {'phones': wanted})
        expected[company['id']] = wanted
        if len(expected) % 25 == 0:
            print('Phone fields populated:', len(expected), flush=True)
    verified = client.workflow_list('companies')
    for company in verified:
        if company['id'] in expected:
            actual = company.get('phones') or {}
            assert all(actual.get(k) == v for k, v in expected[company['id']].items()), company['name']
    updated_views = client.workflow_request('GET', '/rest/metadata/views')
    for view in updated_views:
        if view['objectMetadataId'] == company_object['id']:
            assert any(f['fieldMetadataId'] == field['id'] and f['isVisible'] for f in view['viewFields'])
    summary = {'field_id': field['id'], 'field': 'phones', 'label': field['label'],
               'populated_and_verified': len(expected), 'no_source_phone': len(skipped), 'companies_total': len(verified)}
    (BASE / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
