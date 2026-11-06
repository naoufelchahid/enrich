"""Independent, bounded tender-company acquisition runner."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import date
from time import monotonic

from scripts.twenty_client import TwentyClient

from .company_crm import upsert_company
from .config import Config
from .contacts import search_contacts
from .coordinates import enrich_contact
from .identity import resolve_company
from .linkedin_direct import linkedin_scope
from .person_crm import upsert_person
from .portal import PortalClient
from .state import OperationStore
from .vision import extract_names


def run(*, since: date, config: Config, portal=None, store=None, crm=None,
        extractor=extract_names, resolver=resolve_company,
        contact_search=search_contacts, qualifier=enrich_contact) -> dict:
    portal = portal or PortalClient()
    store = store or OperationStore(config.db_path)
    crm = crm or TwentyClient()
    counts = {'announcements_seen': 0, 'pdfs_seen': 0, 'companies_identified': 0,
              'companies_in_crm': 0, 'people_in_crm': 0, 'skipped_items': 0,
              'download_failures': 0, 'vision_failures': 0,
              'identity_failures': 0, 'crm_failures': 0, 'contact_failures': 0}
    seen = set()
    deadline = monotonic() + config.max_run_minutes * 60
    with store.lock():
        store.mark_interrupted()
        for announcement in portal.iter_announcements(since=since, max_announcements=config.max_documents):
            if monotonic() >= deadline:
                counts['time_limit_reached'] = True
                return counts
            counts['announcements_seen'] += 1
            try:
                pdfs = portal.download_pdfs(announcement)
            except Exception:
                counts['skipped_items'] += 1
                counts['download_failures'] += 1
                continue
            while pdfs:
                if monotonic() >= deadline:
                    counts['time_limit_reached'] = True
                    return counts
                if counts['pdfs_seen'] >= config.max_documents:
                    return counts
                pdf = pdfs.pop(0)
                counts['pdfs_seen'] += 1
                try:
                    names = extractor(pdf)
                except Exception:
                    counts['skipped_items'] += 1
                    counts['vision_failures'] += 1
                    continue
                finally:
                    del pdf
                scope = linkedin_scope() if resolver is resolve_company and names else nullcontext()
                try:
                    with scope:
                        for name in names:
                            if monotonic() >= deadline:
                                counts['time_limit_reached'] = True
                                return counts
                            try:
                                identity = resolver(name)
                            except Exception:
                                counts['skipped_items'] += 1
                                counts['identity_failures'] += 1
                                continue
                            if identity is None:
                                continue
                            key = identity.domain or identity.linkedin_url or identity.registry_id
                            if not key or key in seen:
                                continue
                            seen.add(key)
                            counts['companies_identified'] += 1
                            try:
                                company_id = upsert_company(crm, store, identity)
                            except Exception:
                                counts['skipped_items'] += 1
                                counts['crm_failures'] += 1
                                continue
                            if not company_id:
                                continue
                            counts['companies_in_crm'] += 1
                            try:
                                people = contact_search(identity)
                            except Exception:
                                counts['skipped_items'] += 1
                                counts['contact_failures'] += 1
                                people = []
                            for person in people:
                                try:
                                    qualified = qualifier(person, identity, store)
                                    if qualified and upsert_person(crm, store, qualified, company_id):
                                        counts['people_in_crm'] += 1
                                except Exception:
                                    counts['skipped_items'] += 1
                                    counts['contact_failures'] += 1
                            if counts['companies_identified'] >= config.max_companies:
                                return counts
                except Exception:
                    # A failed browser session skips this PDF and lets the next one retry.
                    counts['skipped_items'] += 1
                    counts['identity_failures'] += 1
    return counts
