"""Create an isolated, non-sending pilot from the reviewed local selection."""
import argparse
import json
from pathlib import Path

from scripts.direct_enrichment import DirectEnrichment
from scripts.staging_db import init_staging_db, insert_staging_leads


def prepare_pilot(selection_path, db_path, *, dry_run=False):
    selection = json.loads(Path(selection_path).read_text())
    contacts = selection.get('contacts')
    if not isinstance(contacts, list) or not 1 <= len(contacts) <= 3:
        raise ValueError('Pilot requires one to three selected contacts')
    if any(not all(c.get(k) for k in ('first_name', 'last_name', 'company_name')) for c in contacts):
        raise ValueError('Pilot requires separate first name, last name and company')
    if len({(c['first_name'], c['last_name'], c['company_name']) for c in contacts}) != len(contacts):
        raise ValueError('Duplicate pilot identities')
    target = Path(db_path)
    if target.exists():
        raise FileExistsError('Pilot destination exists; refusing to replace it')
    if dry_run:
        return {'dry_run': True, 'contacts': len(contacts), 'created': False}
    with target.open('xb'):
        pass
    init_staging_db(str(target)).close()
    leads = [{k: c[k] for k in ('first_name', 'last_name', 'company_name')} |
             {'source_file': c.get('source_file', 'pilot'), 'raw_email': c.get('original_email'),
              'original_email': c.get('original_email'),
              'company_domain': c.get('company_domain'),
              'company_domain_source': c.get('company_domain_source')} for c in contacts]
    insert_staging_leads(str(target), leads)
    # Persist caps now, before any command could request enrichment.
    DirectEnrichment(pilot=True)._init(str(target))
    return {'created': True, 'contacts': len(leads), 'no2bounce_cap': 3,
            'fullenrich_cap': 3, 'harvest_cap': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', default='bdd/pilot/selection.json')
    parser.add_argument('--db-path', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    print(json.dumps(prepare_pilot(args.selection, args.db_path, dry_run=args.dry_run)))


if __name__ == '__main__':
    main()
