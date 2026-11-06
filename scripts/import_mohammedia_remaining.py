"""Prepare and execute journaled company-only imports from the Maps inventory."""
import argparse
import html
import json
import os
import re
import sqlite3
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from scripts.twenty_client import TwentyClient

BASE = Path('bdd/sources/apify-mohammedia-20261030-reste')
OUT = Path('outputs/apify-mohammedia-20261030-reste')
SHARED_HOSTS = {'facebook.com', 'instagram.com', 'tiktok.com', 'kerix.net', 'wa.me', 'linkedin.com', 'youtube.com'}


def norm(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value or '').casefold() if c.isalnum())


def host(value):
    if isinstance(value, dict):
        value = value.get('primaryLinkUrl')
    return (urlsplit(value or '').hostname or '').removeprefix('www.')


def own_domain(value):
    domain = host(value)
    return '' if any(domain == h or domain.endswith('.' + h) for h in SHARED_HOSTS) else domain


def phone(value):
    return re.sub(r'\D', '', value or '')


def eligibility(row):
    city, address, title = norm(row.get('city')), norm(row.get('address')), row['title']
    if city not in {'mohammedia', 'mohammadia', 'mohamedia'} and not any(v in address for v in ('mohammedia', 'mohammadia', 'mohamedia')):
        return 'Ville hors Mohammedia ou non renseignée'
    if any(p in address for p in ('ainharrouda', 'chelalat', 'chellalat', 'echallalat', 'zenata', 'beniyakhalf', 'beniyakhlef')):
        return 'Adresse indiquant une localité périphérique : revue géographique'
    if not address:
        return 'Adresse absente'
    if not norm(title) or norm(title) in {'grossisteproduitsalimentaires', 'vendeurdelaitnido', 'fabricationtubespehd', 'transportsmarchandises', 'derbrabat', 'دربرباط', 'خدماتالتوصيلالسريعة'}:
        return 'Identité commerciale insuffisante'
    if norm(title).startswith(('anapec', 'mastermaroc', 'marcheauxlegumes', 'bureaudesadels')):
        return 'Organisme public, marché ou association ; entreprise non identifiée'
    if row.get('permanentlyClosed') or row.get('temporarilyClosed'):
        return 'Fermeture signalée'
    return None


def group(row):
    text = norm(' '.join(str(row.get(k) or '') for k in ('title', 'categoryName', 'searchString')))
    if any(t in text for t in ('compt', 'fiduc', 'audit', 'fiscal', 'finance')):
        return 'Comptabilité, audit et fiduciaires'
    if any(t in text for t in ('construction', 'travaux', 'btp', 'isolation', 'etanche', 'batiment', 'geniecivil', 'peintre')):
        return 'Travaux et BTP'
    if any(t in text for t in ('grossiste', 'fournisseur', 'distribution', 'distributeur', 'materiel', 'importexport')):
        return 'Distribution B2B'
    if any(t in text for t in ('usine', 'fabric', 'industrie', 'textile', 'chaudron', 'centrale', 'raffinerie', 'pharma', 'emballage', 'usinage')):
        return 'Industrie'
    return 'Services aux entreprises'


def connect():
    load_dotenv('.env')
    client = TwentyClient(os.getenv('TWENTY_BASE_URL', 'http://localhost:3005'))
    original = client.workflow_request
    def request(method, path, **kwargs):
        for attempt in range(3):
            time.sleep(.7)
            try:
                return original(method, path, **kwargs)
            except RuntimeError:
                if method != 'GET' or attempt == 2:
                    raise
                time.sleep(2 ** (attempt + 1))
    client.workflow_request = request
    return client


def prepare(client):
    rows = json.loads(Path('outputs/apify-mohammedia-non-integrees-20261030/liste.json').read_text())['items']
    existing = client.workflow_list('companies')
    known = []
    for batch in ('apify-mohammedia-20261030', 'apify-mohammedia-20261030-suite'):
        known.extend(json.loads(Path('outputs', batch, 'societes.json').read_text()))
    chosen, review = [], []
    for row in rows:
        reason = eligibility(row)
        similar = [x['name'] for x in existing if norm(x['name']) == norm(row['title']) or (own_domain(row.get('website')) and own_domain(x.get('domainName')) == own_domain(row.get('website')))]
        candidates = [*known, *chosen]
        similar += [x['title'] for x in candidates if norm(x['title']) == norm(row['title']) or (own_domain(row.get('website')) and own_domain(x.get('website')) == own_domain(row.get('website'))) or (phone(row.get('phone')) and phone(x.get('phone')) == phone(row.get('phone')))]
        if similar:
            reason = 'Rapprochement à vérifier : ' + ', '.join(sorted(set(similar)))
        if norm(row['title']).startswith('mediafidus') and any(norm(x['title']).startswith('mediafidus') for x in chosen):
            reason = 'Variantes Media Fidus à la même adresse : identité à vérifier'
        if reason:
            review.append({**row, 'reason': reason})
        else:
            chosen.append({**row, 'groupe': group(row)})
    OUT.mkdir(parents=True, exist_ok=True)
    BASE.mkdir(parents=True, exist_ok=True)
    plan = {'crm_before_ids': [x['id'] for x in existing], 'selected': chosen, 'review': review}
    (BASE / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    print(json.dumps({'selected': len(chosen), 'review': len(review), 'crm_before': len(existing)}, ensure_ascii=False))
    for r in review:
        print('REVIEW', r['title'], ':', r['reason'])
    return plan


def run(client, plan):
    schema = client.inspect_workflow_schema()
    for resource in ('companies', 'notes', 'noteTargets'):
        if '/batch/' + resource not in schema['paths']:
            raise RuntimeError('Batch contract unavailable: ' + resource)
    journal = sqlite3.connect(BASE / 'crm-import.sqlite')
    journal.execute('CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, status TEXT, payload TEXT, result TEXT)')
    journal.execute('CREATE TABLE IF NOT EXISTS evidence (place_id TEXT PRIMARY KEY, source TEXT, company TEXT, note TEXT, target TEXT)')
    journal.commit()

    def batch(resource, marker, tasks):
        # Log every item before submitting a batch. Never retry a missing result.
        identities, pending = {}, []
        for key, payload in tasks:
            previous = journal.execute('SELECT status,result FROM operations WHERE key=?', (key,)).fetchone()
            if previous:
                if previous[0] != 'CONFIRMED':
                    records = client.workflow_list(resource)
                    found = [r for r in records if r.get(marker) == payload[marker]]
                    if len(found) != 1:
                        raise RuntimeError('Unknown operation requires review: ' + key)
                    journal.execute('UPDATE operations SET status=?,result=? WHERE key=?', ('CONFIRMED', json.dumps(found[0]), key))
                    journal.commit()
                    identities[key] = found[0]['id']
                else:
                    identities[key] = json.loads(previous[1])['id']
            else:
                pending.append((key, payload))
        for start in range(0, len(pending), 20):
            chunk = pending[start:start + 20]
            journal.executemany('INSERT INTO operations VALUES (?,?,?,NULL)', [(k, 'SENDING', json.dumps(p, ensure_ascii=False)) for k, p in chunk])
            journal.commit()
            response = client.workflow_request('POST', '/rest/batch/' + resource, payload=[p for _, p in chunk])
            data = response.get('data', {})
            lists = [v for v in data.values() if isinstance(v, list)]
            if len(lists) != 1:
                raise RuntimeError('Batch response requires reconciliation')
            records = lists[0]
            for key, payload in chunk:
                matches = [r for r in records if r.get(marker) == payload[marker]]
                if len(matches) != 1 or not matches[0].get('id'):
                    raise RuntimeError('Batch item requires reconciliation: ' + key)
                journal.execute('UPDATE operations SET status=?,result=? WHERE key=?', ('CONFIRMED', json.dumps(matches[0], ensure_ascii=False), key))
                journal.commit()
                identities[key] = matches[0]['id']
            print(resource, min(start + 20, len(pending)), '/', len(pending), 'CONFIRMED', flush=True)
        return identities

    rows = plan['selected']
    # Recheck the live inventory immediately before the first company mutation.
    current = client.workflow_list('companies')
    known_ids = set(plan['crm_before_ids'])
    own_ids = {json.loads(r[0])['id'] for r in journal.execute("SELECT result FROM operations WHERE key LIKE '%:company' AND status='CONFIRMED'")}
    for x in current:
        if x['id'] in known_ids or x['id'] in own_ids:
            continue
        if any(norm(x['name']) == norm(r['title']) or (own_domain(r.get('website')) and own_domain(x.get('domainName')) == own_domain(r.get('website'))) for r in rows):
            raise RuntimeError('Concurrent company addition requires review')
    payloads = []
    for row in rows:
        city = row['city'] if norm(row.get('city')) in {'mohammedia', 'mohammadia', 'mohamedia'} else 'Mohammedia'
        address = {'addressCity': city, 'addressStreet1': row['address'], 'addressCountry': 'Maroc'}
        location = row.get('location') or {}
        if location.get('lat') is not None:
            address.update(addressLat=location['lat'], addressLng=location['lng'])
        payload = {'name': row['title'], 'activitySector': row['groupe'], 'address': address}
        if own_domain(row.get('website')):
            payload['domainName'] = {'primaryLinkLabel': own_domain(row['website']), 'primaryLinkUrl': row['website']}
        payloads.append((row['placeId'] + ':company', payload))
    companies = batch('companies', 'name', payloads)
    note_payloads = []
    for row in rows:
        body = '\n'.join([f"Groupe : {row['groupe']}", f"Activité Google Maps : {row.get('categoryName') or 'Non renseignée'}",
                          f"Téléphone publié : {row.get('phone') or 'Non renseigné'}", f"Site ou lien déclaré : {row.get('website') or 'Non renseigné'}",
                          f"Adresse source : {row['address']}", f"Ville source : {row['city']}",
                          f"Source Google Maps : {row.get('url') or ''}", f"Place ID : {row['placeId']}",
                          f"Extraction : {row.get('scrapedAt') or '2026-10-30'}",
                          'Source : compass/crawler-google-places, inventaire des deux extractions du 30 octobre 2026.',
                          'Activité, adresse et statut déclarés sur Google Maps ; non vérifiés indépendamment.'])
        note_payloads.append((row['placeId'] + ':note', {'title': 'Source Maps — ' + row['placeId'], 'bodyV2': {'markdown': body}}))
    notes = batch('notes', 'title', note_payloads)
    targets = batch('noteTargets', 'noteId', [(r['placeId'] + ':target', {'noteId': notes[r['placeId'] + ':note'], 'targetCompanyId': companies[r['placeId'] + ':company']}) for r in rows])
    company_rows = {x['id']: x for x in client.workflow_list('companies')}
    note_rows = {x['id']: x for x in client.workflow_list('notes')}
    target_rows = {x['id']: x for x in client.workflow_list('noteTargets')}
    results = []
    for row, (_, payload), (_, note_payload) in zip(rows, payloads, note_payloads):
        key = row['placeId']
        company = company_rows[companies[key + ':company']]
        note = note_rows[notes[key + ':note']]
        target = target_rows[targets[key + ':target']]
        assert all(company.get(k) == payload[k] for k in ('name', 'activitySector'))
        assert all(company['address'].get(k) == v for k, v in payload['address'].items())
        assert own_domain(company.get('domainName')) == own_domain(row.get('website'))
        assert note['bodyV2']['markdown'] == note_payload['bodyV2']['markdown']
        assert target['noteId'] == note['id'] and target['targetCompanyId'] == company['id']
        journal.execute('INSERT OR REPLACE INTO evidence VALUES (?,?,?,?,?)', (key, json.dumps(row, ensure_ascii=False), json.dumps(company, ensure_ascii=False), json.dumps(note, ensure_ascii=False), json.dumps(target, ensure_ascii=False)))
        results.append({'name': row['title'], 'place_id': key, 'company_id': company['id'], 'note_id': note['id'], 'status': 'VERIFIED'})
    journal.commit()
    assert not journal.execute("SELECT 1 FROM operations WHERE status!='CONFIRMED'").fetchone()
    assert len({r['company_id'] for r in results}) == len(rows)
    assert not {r['company_id'] for r in results} & set(plan['crm_before_ids'])
    report = {'created': len(results), 'crm_before': len(plan['crm_before_ids']), 'crm_after': len(company_rows), 'review': len(plan['review']), 'results': results}
    (OUT / 'crm-import.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    esc = lambda s: html.escape(str(s or 'Non renseigné'))
    page = '<!doctype html><html lang="fr"><meta charset="utf-8"><title>Intégration Mohammedia</title><style>body{font:14px system-ui;margin:28px}td,th{border:1px solid #ddd;padding:9px}table{border-collapse:collapse;width:100%}</style>'
    page += f'<h1>{len(results)} entreprises ajoutées et vérifiées</h1><p>{len(company_rows)} entreprises au CRM au total. {len(plan["review"])} fiches non ajoutées, motifs ci-dessous.</p><h2>Entreprises intégrées</h2><table><tr><th>Nom</th><th>Adresse</th><th>Activité</th></tr>'
    for r in rows:
        page += f'<tr><td>{esc(r["title"])}</td><td>{esc(r["address"])}</td><td>{esc(r.get("categoryName"))}</td></tr>'
    page += '</table><h2>Fiches non ajoutées</h2><table><tr><th>Nom</th><th>Adresse</th><th>Motif</th></tr>'
    for r in plan['review']:
        page += f'<tr><td>{esc(r["title"])}</td><td>{esc(r.get("address"))}</td><td>{esc(r["reason"])}</td></tr>'
    (OUT / 'resultats.html').write_text(page + '</table></html>')
    print(json.dumps({k: v for k, v in report.items() if k != 'results'}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    client = connect()
    if args.apply:
        run(client, json.loads((BASE / 'plan.json').read_text()))
    else:
        prepare(client)


if __name__ == '__main__':
    main()
