"""Import local workbooks into a new staging database; never replace existing data."""
import argparse
import json
from pathlib import Path

from scripts.etl_normalize import parse_all_sources
from scripts.staging_db import init_staging_db, insert_staging_leads


def import_leads(data_dir, db_path, *, limit=None, dry_run=False):
    from scripts.backend_guard import require_legacy
    require_legacy(db_path)
    source = Path(data_dir)
    target = Path(db_path)
    if not source.is_dir():
        raise ValueError('Source directory does not exist')
    if limit is not None and limit < 1:
        raise ValueError('Limit must be positive')
    if target.exists():
        raise FileExistsError('Target database already exists; use a new path')
    contacts = parse_all_sources(str(source))
    if limit is not None:
        contacts = contacts[:limit]
    if not contacts:
        raise ValueError('No contacts found in supported BDD workbooks')
    leads = []
    for contact in contacts:
        lead = contact.model_dump()
        # Workbook content is not evidence of email deliverability.
        lead.update(clean_email=None, neverbounce_status='PENDING', enrichment_status='PENDING')
        leads.append(lead)
    if dry_run:
        return {'dry_run': True, 'contacts': len(leads), 'inserted': 0}
    # Exclusive creation also prevents two simultaneous imports overwriting a DB.
    with target.open('xb'):
        pass
    init_staging_db(str(target)).close()
    return {'dry_run': False, 'inserted': insert_staging_leads(str(target), leads)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default='bdd/sources')
    parser.add_argument('--db-path', required=True, help='New SQLite file; existing files are refused')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    print(json.dumps(import_leads(args.data_dir, args.db_path, limit=args.limit, dry_run=args.dry_run)))


if __name__ == '__main__':
    main()
