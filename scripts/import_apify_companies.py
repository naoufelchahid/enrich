"""Import the reviewed Mohammedia company selection, with a durable mutation log."""
import json
import argparse
import os
import sqlite3
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from scripts.twenty_client import TwentyClient


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value or '').casefold() if c.isalnum())


def domain(value):
    if isinstance(value, dict):
        value = value.get('primaryLinkUrl')
    return (urlsplit(value or '').hostname or '').removeprefix('www.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', default='apify-mohammedia-20261030')
    parser.add_argument('--run-id', default='fuze785UB2fe5EY18')
    args = parser.parse_args()
    if not args.batch or Path(args.batch).name != args.batch:
        parser.error('batch must be a single directory name')
    load_dotenv('.env')
    client = TwentyClient(os.getenv('TWENTY_BASE_URL', 'http://localhost:3005'))
    request = client.workflow_request
    def read_retry(method, path, **kwargs):
        for attempt in range(3):
            time.sleep(1)
            try:
                return request(method, path, **kwargs)
            except RuntimeError:
                if method != 'GET' or attempt == 2:
                    raise
                time.sleep(2 ** (attempt + 1))
    client.workflow_request = read_retry
    source = Path('outputs') / args.batch / 'societes.json'
    rows = json.loads(source.read_text())
    journal = sqlite3.connect(Path('bdd/sources') / args.batch / 'crm-import.sqlite')
    journal.execute('CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, status TEXT, payload TEXT, result TEXT)')
    journal.execute('CREATE TABLE IF NOT EXISTS evidence (place_id TEXT PRIMARY KEY, source TEXT, company TEXT, note TEXT, target TEXT)')
    journal.commit()
    schema = client.inspect_workflow_schema()
    assert {'name', 'address', 'domainName', 'activitySector'} <= client.workflow_fields('companies', schema)
    assert {'title', 'bodyV2'} <= client.workflow_fields('notes', schema)
    assert {'targetCompanyId', 'noteId'} <= client.workflow_fields('noteTargets', schema)
    existing = client.workflow_list('companies')

    def create(resource, singular, key, payload):
        prior = journal.execute('SELECT status,result FROM operations WHERE key=?', (key,)).fetchone()
        if prior:
            if prior[0] != 'CONFIRMED':
                candidates = [r for r in client.workflow_list(resource) if all(r.get(k) == v for k, v in payload.items())]
                if not candidates and resource == 'notes':
                    journal.execute('UPDATE operations SET status=? WHERE key=?', ('RECONCILED_ABSENT', key))
                    journal.commit()
                elif len(candidates) != 1:
                    raise RuntimeError('Uncertain operation requires reconciliation: ' + key)
                else:
                    journal.execute('UPDATE operations SET status=?,result=? WHERE key=?', ('CONFIRMED', json.dumps({'id': candidates[0]['id']}), key))
                    journal.commit()
                    return candidates[0]['id']
            else:
                return json.loads(prior[1])['id']
        journal.execute('INSERT OR REPLACE INTO operations VALUES (?,?,?,NULL)', (key, 'SENDING', json.dumps(payload, ensure_ascii=False)))
        journal.commit()
        response = client.workflow_request('POST', '/rest/' + resource, payload=payload)
        identity = client._extract_id(response, singular)
        if identity.startswith('dummy-'):
            data = response.get('data', {})
            records = [v for v in data.values() if isinstance(v, dict) and v.get('id')]
            if len(records) == 1:
                identity = records[0]['id']
        if not identity or identity.startswith('dummy-'):
            raise RuntimeError('Creation not confirmed: ' + key)
        journal.execute('UPDATE operations SET status=?,result=? WHERE key=?', ('CONFIRMED', json.dumps({'id': identity}), key))
        journal.commit()
        return identity

    results = []
    for row in rows:
        key = row['placeId']
        cached = journal.execute('SELECT company FROM evidence WHERE place_id=?', (key,)).fetchone()
        if cached:
            company = json.loads(cached[0])
            saved_note = json.loads(journal.execute('SELECT note FROM evidence WHERE place_id=?', (key,)).fetchone()[0])
            results.append({'name': row['title'], 'group': row['groupe'], 'company_id': company['id'], 'note_id': saved_note['id'], 'status': 'VERIFIED'})
            continue
        known_operation = journal.execute('SELECT status,result FROM operations WHERE key=?', (key + ':company',)).fetchone()
        matches = [c for c in existing if domain(row.get('website')) and domain(c.get('domainName')) == domain(row['website'])]
        homonyms = [c for c in existing if normalized(c.get('name')) == normalized(row['title'])]
        if len(matches) > 1 or (not matches and homonyms and not cached and not known_operation):
            raise RuntimeError('Company identity requires review: ' + row['title'])
        payload = {'name': row['title'], 'activitySector': row['groupe'],
                   'address': {'addressStreet1': row['address'], 'addressCity': row['city'],
                               'addressCountry': 'Maroc', 'addressLat': row['location']['lat'],
                               'addressLng': row['location']['lng']}}
        if row.get('website'):
            payload['domainName'] = {'primaryLinkLabel': domain(row['website']), 'primaryLinkUrl': row['website']}
        company_id = json.loads(cached[0])['id'] if cached else (matches[0]['id'] if matches else create('companies', 'company', key + ':company', payload))
        company = client.workflow_get('companies', company_id)
        if not company or normalized(company['name']) != normalized(row['title']):
            raise RuntimeError('Company readback failed: ' + row['title'])
        if not matches and any(company.get(k) != v for k, v in payload.items() if k in ('name','activitySector')):
            raise RuntimeError('Company fields mismatch')
        body = '\n'.join([f"Groupe : {row['groupe']}", f"Activité Google Maps : {row.get('categoryName') or ''}",
                          f"Téléphone publié : {row.get('phone') or 'Non renseigné'}", f"Site : {row.get('website') or 'Non renseigné'}",
                          f"Adresse source : {row['address']}", f"Source Google Maps : {row['url']}",
                          f"Place ID : {key}", f"Extraction : {row['scrapedAt']}",
                          'Actor : compass/crawler-google-places ; run : ' + args.run_id, row['qualification']])
        note_id = create('notes', 'note', key + ':note', {'title': 'Source Apify / Google Maps — ' + row['title'], 'bodyV2': {'markdown': body}})
        target_id = create('noteTargets', 'noteTarget', key + ':target', {'targetCompanyId': company_id, 'noteId': note_id})
        note_data = client.workflow_request('GET', '/rest/notes/' + note_id)['data']
        note = note_data.get('note') or note_data
        target_data = client.workflow_request('GET', '/rest/noteTargets/' + target_id)['data']
        target = target_data.get('noteTarget') or target_data.get('findNotetarget') or target_data.get('findNoteTarget') or target_data
        if (note.get('bodyV2') or {}).get('markdown') != body or target.get('targetCompanyId') != company_id or target.get('noteId') != note_id:
            raise RuntimeError('Source note readback failed')
        journal.execute('INSERT OR REPLACE INTO evidence VALUES (?,?,?,?,?)', (key, json.dumps(row, ensure_ascii=False), json.dumps(company, ensure_ascii=False), json.dumps(note, ensure_ascii=False), json.dumps(target, ensure_ascii=False)))
        journal.commit()
        results.append({'name': row['title'], 'group': row['groupe'], 'company_id': company_id, 'note_id': note_id, 'status': 'VERIFIED'})
        print(len(results), row['title'], 'VERIFIED', flush=True)
    companies = {r['id']: r for r in client.workflow_list('companies')}
    notes = {r['id']: r for r in client.workflow_list('notes')}
    targets = client.workflow_list('noteTargets')
    for row, result in zip(rows, results):
        company = companies[result['company_id']]
        assert company['name'] == row['title'] and company['activitySector'] == row['groupe']
        assert company['address']['addressStreet1'] == row['address']
        assert company['address']['addressCity'] == row['city']
        assert domain(company.get('domainName')) == domain(row.get('website'))
        assert row['placeId'] in notes[result['note_id']]['bodyV2']['markdown']
        assert any(t.get('targetCompanyId') == company['id'] and t.get('noteId') == result['note_id'] for t in targets)
    (source.parent / 'crm-import.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print('TOTAL VERIFIED:', len(results))


if __name__ == '__main__':
    main()
