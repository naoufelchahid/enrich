from datetime import date

from scripts.tender_acquisition.config import Config
from scripts.tender_acquisition.contacts import Contact
from scripts.tender_acquisition.coordinates import QualifiedContact
from scripts.tender_acquisition.identity import CompanyIdentity
from scripts.tender_acquisition.runner import run
from scripts.tender_acquisition.state import OperationStore


def test_partial_failure_continues_and_respects_company_budget(tmp_path, monkeypatch):
    from scripts.tender_acquisition import runner
    company = CompanyIdentity('Atlas SARL', 'https://www.linkedin.com/company/atlas/',
                              'atlas.ma', None, None, None, None, None, '123')
    person = Contact('Amina', 'Alaoui', None, 'LEADER',
                     'https://www.linkedin.com/in/amina-alaoui/', 'Atlas SARL')

    class Portal:
        def iter_announcements(self, **kwargs):
            yield object()
            yield object()
        def download_pdfs(self, item):
            return [b'bad', b'good']

    monkeypatch.setattr(runner, 'upsert_company', lambda *args: 'c1')
    monkeypatch.setattr(runner, 'upsert_person', lambda *args: 'p1')
    counts = run(since=date(2026, 10, 1),
                 config=Config(db_path=tmp_path / 'ops.sqlite', max_companies=1),
                 portal=Portal(), store=OperationStore(tmp_path / 'ops.sqlite'), crm=object(),
                 extractor=lambda blob: (_ for _ in ()).throw(ValueError()) if blob == b'bad' else ['Atlas SARL'],
                 resolver=lambda _: company, contact_search=lambda _: [person],
                 qualifier=lambda person, company, store: QualifiedContact(person, None, '+212600000000'))
    assert counts['skipped_items'] == 1
    assert counts['companies_identified'] == 1
    assert counts['people_in_crm'] == 1
