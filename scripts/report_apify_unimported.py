"""Read-only reconciliation of extracted Maps places against the live CRM."""
import html
import json
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from scripts.twenty_client import TwentyClient


def norm(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value or '').casefold() if c.isalnum())


def domain(value):
    if isinstance(value, dict):
        value = value.get('primaryLinkUrl')
    host = (urlsplit(value or '').hostname or '').removeprefix('www.')
    return '' if host in {'facebook.com', 'instagram.com', 'kerix.net', 'tiktok.com'} else host


def main():
    load_dotenv('.env')
    client = TwentyClient(os.getenv('TWENTY_BASE_URL', 'http://localhost:3005'))
    companies = client.workflow_list('companies')
    notes = client.workflow_list('notes')
    sources = [
        'bdd/sources/apify-mohammedia-20261030/resultats-maps.json',
        'bdd/sources/apify-mohammedia-20261030-suite/exclusions-premiere-recherche.json',
        'bdd/sources/apify-mohammedia-20261030-suite/resultats-maps.json',
    ]
    places = {}
    for source in sources:
        data = json.loads(Path(source).read_text())
        for row in data.get('items', []) if isinstance(data, dict) else data:
            places[row['placeId']] = {**places.get(row['placeId'], {}), **row}
    linked_ids = set()
    for note in notes:
        body = (note.get('bodyV2') or {}).get('markdown') or ''
        linked_ids.update(re.findall(r'^Place ID : (\S+)', body, re.M))
    imported = []
    for batch in ['apify-mohammedia-20261030', 'apify-mohammedia-20261030-suite']:
        imported.extend(json.loads(Path('outputs', batch, 'societes.json').read_text()))
    absent, present = [], []
    for key, row in places.items():
        if key in linked_ids:
            present.append(row)
            continue
        flags = []
        names = [c['name'] for c in companies if norm(c['name']) == norm(row['title'])]
        if names:
            flags.append('Même nom que : ' + ', '.join(names) + ' ; rapprochement à vérifier')
        matches = [c['name'] for c in companies if domain(row.get('website')) and domain(c.get('domainName')) == domain(row.get('website'))]
        if matches:
            flags.append('Même domaine que : ' + ', '.join(matches) + ' ; rapprochement à vérifier')
        phone = re.sub(r'\D', '', row.get('phone') or '')
        phone_matches = [c['title'] for c in imported if phone and phone == re.sub(r'\D', '', c.get('phone') or '')]
        if phone_matches:
            flags.append('Même téléphone que : ' + ', '.join(phone_matches) + ' ; rapprochement à vérifier')
        city = row.get('city') or ''
        if norm(city) not in {'mohammedia', 'mohammadia'}:
            flags.append('Ville différente ou non renseignée : localisation à vérifier')
        if row.get('permanentlyClosed') or row.get('temporarilyClosed'):
            flags.append('Fermeture signalée sur Google Maps')
        if row['title'].strip(' .') == '':
            flags.append('Nom inexploitable')
        absent.append({**row, 'observations': flags or ['Non retenue dans les sélections de 50 puis 30 ; qualification restante']})
    absent.sort(key=lambda x: (norm(x.get('city')), norm(x['title'])))
    summary = {'checked_at': datetime.now(timezone.utc).isoformat(), 'extracted_unique_places': len(places),
               'present_places': len(present), 'unimported_places': len(absent), 'crm_companies': len(companies),
               'unimported_by_city': dict(Counter(x.get('city') or 'Non renseignée' for x in absent)),
               'possible_duplicates': sum(any('rapprochement' in flag for flag in x['observations']) for x in absent)}
    directory = Path('outputs/apify-mohammedia-non-integrees-20261030')
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'liste.json').write_text(json.dumps({'summary': summary, 'items': absent}, ensure_ascii=False, indent=2))
    esc = lambda v: html.escape(str(v or 'Non renseigné'), quote=True)
    document = '<!doctype html><html lang="fr"><meta charset="utf-8"><title>Fiches extraites non intégrées au CRM</title><style>body{font:14px system-ui;margin:28px;color:#182a3d}table{border-collapse:collapse;width:100%}td,th{padding:9px;border:1px solid #dce1e8;text-align:left;vertical-align:top}th{background:#eaf0f7;position:sticky;top:0}input{padding:12px;width:50%;margin:16px 0}a{color:#175daf}</style>'
    document += f'<h1>{len(absent)} fiches extraites non intégrées au CRM</h1><p>Contrôle du {esc(summary["checked_at"])}. {len(places)} fiches Maps distinctes extraites, {len(present)} rattachées à des entreprises présentes au CRM. Le CRM contient {len(companies)} entreprises au total.</p><p>Ces résultats incluent des entreprises à qualifier, des établissements périphériques, des organismes publics et des doublons potentiels. Une fiche Maps distincte ne garantit pas une entreprise distincte. Aucun ajout au CRM effectué lors de ce contrôle.</p><input id="filter" placeholder="Filtrer par nom, ville, activité ou observation" aria-label="Filtrer la liste"><p id="count"></p><table><thead><tr><th>#</th><th>Nom</th><th>Activité Maps</th><th>Ville / adresse</th><th>Téléphone</th><th>Observations</th><th>Sources</th></tr></thead><tbody>'
    for i, row in enumerate(absent, 1):
        url = row.get('url') or 'https://www.google.com/maps/search/?api=1&query_place_id=' + row['placeId'] + '&query=' + row['title']
        links = f'<a href="{esc(url)}">Google Maps</a>'
        if row.get('website'):
            links += f'<br><a href="{esc(row["website"])}">Site déclaré</a>'
        document += f'<tr><td>{i}</td><td>{esc(row["title"])}</td><td>{esc(row.get("categoryName"))}</td><td>{esc(row.get("city"))}<br>{esc(row.get("address"))}</td><td>{esc(row.get("phone"))}</td><td>{esc(" ; ".join(row["observations"]))}</td><td>{links}</td></tr>'
    document += '</tbody></table><script>const rows=[...document.querySelectorAll("tbody tr")];function filter(){const q=document.querySelector("input").value.toLocaleLowerCase();let n=0;for(const r of rows){r.hidden=!r.textContent.toLocaleLowerCase().includes(q);if(!r.hidden)n++;}document.querySelector("#count").textContent=n+" fiches affichées";}document.querySelector("input").addEventListener("input",filter);filter();</script></html>'
    (directory / 'liste.html').write_text(document)
    print(json.dumps(summary, ensure_ascii=False))
    print('EXEMPLES', json.dumps([{'nom':x['title'],'activite':x.get('categoryName')} for x in absent if norm(x.get('city'))=='mohammedia'][:15],ensure_ascii=False))


if __name__ == '__main__':
    main()
