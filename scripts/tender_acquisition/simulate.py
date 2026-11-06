"""Exercise the independent orchestration entirely with synthetic in-memory data."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from .config import Config
from .contacts import Contact
from .coordinates import QualifiedContact
from .identity import CompanyIdentity
from .runner import run
from .state import OperationStore


class SimulatedCRM:
    def __init__(self):
        self.companies = {}
        self.people = {}

    def inspect_workflow_schema(self): return {}

    def workflow_fields(self, resource, schema):
        return ({'name', 'domainName', 'address', 'linkedinLink', 'activitySector'} if resource == 'companies'
                else {'name', 'companyId', 'linkedinLink', 'phones', 'emails', 'jobTitle'})

    def workflow_list(self, resource): return list(getattr(self, resource).values())

    def workflow_get(self, resource, record_id): return getattr(self, resource).get(record_id)

    def workflow_create(self, resource, payload):
        values = getattr(self, resource)
        record_id = ('c' if resource == 'companies' else 'p') + str(len(values) + 1)
        values[record_id] = {'id': record_id, **payload}
        return record_id

    def workflow_patch(self, resource, record_id, payload):
        getattr(self, resource)[record_id].update(payload)


class SimulatedPortal:
    def iter_announcements(self, **kwargs):
        yield object()

    def download_pdfs(self, announcement):
        return [b'%PDF-1.4 synthetic in-memory sample']


def simulate() -> dict:
    company = CompanyIdentity('Atlas SARL', 'https://www.linkedin.com/company/atlas/',
                              'atlas.ma', 'https://atlas.ma', 'Rabat', None, None, None, '123')
    contact = Contact('Amina', 'Alaoui', 'Directrice', 'LEADER',
                      'https://www.linkedin.com/in/amina-alaoui/', company.name)
    scratch = Path('.scratch').resolve()
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='tender-sim-', dir=scratch) as temp:
        config = Config(db_path=Path(temp) / 'operations.sqlite', max_companies=1,
                        max_documents=2)
        counts = run(since=date.today(), config=config, portal=SimulatedPortal(),
                     store=OperationStore(config.db_path), crm=SimulatedCRM(),
                     extractor=lambda _: [company.name], resolver=lambda _: company,
                     contact_search=lambda _: [contact],
                     qualifier=lambda person, identity, store: QualifiedContact(person, None, '+212600000000'))
    return {'task': 'simulate', 'services_called': False, **counts}
