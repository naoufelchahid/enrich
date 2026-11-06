from scripts.tender_acquisition.contacts import choose_contacts, search_contacts
from scripts.tender_acquisition.identity import CompanyIdentity


COMPANY = CompanyIdentity('Atlas SARL', 'https://www.linkedin.com/company/atlas/',
                          'atlas.ma', 'https://atlas.ma', 'Rabat', None, None, None, '123')


def row(name='a', role='LEADER', current=True, employer='https://www.linkedin.com/company/atlas/'):
    return {'first_name': 'Amina', 'last_name': name, 'title': 'Directrice', 'role': role,
            'linkedin_url': f'https://www.linkedin.com/in/amina-{name}/',
            'current_employer_url': employer, 'current_employer_urn': '123',
            'current_employer_name': 'Atlas SARL', 'current': current}


def test_two_current_confirmed_contacts_maximum():
    contacts = choose_contacts(COMPANY, [row(), row('b', 'COMMERCIAL')])
    assert [c.role for c in contacts] == ['LEADER', 'COMMERCIAL']


def test_old_employer_and_homonymous_profile_skipped():
    assert choose_contacts(COMPANY, [row(current=False)]) == []
    assert choose_contacts(COMPANY, [row(), row('other')]) == []


def test_no_search_without_company_linkedin_identity():
    company = CompanyIdentity('Atlas SARL', None, 'atlas.ma', None, None, None, None, None, None)
    assert search_contacts(company, agent=lambda *_: (_ for _ in ()).throw(AssertionError())) == []


def test_contact_search_reads_profile_before_accepting(monkeypatch):
    class MCP:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def call(self, tool, arguments):
            if tool == 'search_people':
                if 'commercial' in arguments['keywords']:
                    return {'references': {'search_results': []}}
                return {'references': {'search_results': [
                    {'kind': 'person', 'url': '/in/amina-a/', 'text': 'Amina A'}]}}
            return {'sections': {'experience': 'Amina A Directrice Atlas SARL Current'},
                    'references': {'experience': [{'kind': 'company', 'url': '/company/atlas/'}]}}

    candidate = row()
    candidate['last_name'] = 'A'
    candidate['linkedin_url'] = 'https://www.linkedin.com/in/amina-a/'
    candidate['current_employer_urn'] = None
    agent = lambda *args: {'candidates': [candidate]}
    result = search_contacts(COMPANY, agent=agent, mcp_factory=MCP)
    assert result and result[0].last_name == 'A'
