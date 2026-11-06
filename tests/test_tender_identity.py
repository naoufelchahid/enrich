from scripts.tender_acquisition.identity import choose_company, resolve_company, search_linkedin_company


def candidate(url='https://www.linkedin.com/company/atlas-maroc/', site='https://atlas.ma'):
    return {'name': 'Atlas SARL', 'linkedin_url': url, 'website': site,
            'country': 'Maroc', 'city': 'Rabat', 'sector': 'Construction',
            'description': None, 'phone': None, 'urn': '123'}


def test_unique_linkedin_company_with_official_domain_and_country():
    result = choose_company('Atlas SARL', [candidate()], [])
    assert result and result.domain == 'atlas.ma'


def test_homonym_skipped_without_independent_match():
    assert choose_company('Atlas SARL', [candidate(), candidate(
        url='https://www.linkedin.com/company/atlas-2/', site='https://atlas2.ma')], []) is None


def test_kerix_resolves_homonym_with_domain():
    rows = [candidate(), candidate(url='https://www.linkedin.com/company/atlas-2/',
                                   site='https://atlas2.ma')]
    kerix = [{'companyName': 'Atlas SARL', 'website': 'https://atlas2.ma', 'registryId': 'RC-2'}]
    assert choose_company('Atlas SARL', rows, kerix).domain == 'atlas2.ma'


def test_kerix_unavailable_does_not_block_confirmed_linkedin():
    class KerixUnavailable:
        def search(self, name):
            raise RuntimeError('unavailable')

    result = resolve_company('Atlas SARL', linkedin_search=lambda _: [candidate()],
                             kerix_client=KerixUnavailable())
    assert result and result.linkedin_url.endswith('/atlas-maroc/')


def test_kerix_only_requires_unique_legal_identifier():
    rows = [{'companyName': 'Atlas SARL', 'website': 'atlas.ma', 'registryId': 'RC-1'}]
    assert choose_company('Atlas SARL', [], rows).registry_id == 'RC-1'
    assert choose_company('Atlas SARL', [], rows + rows) is None


def test_company_search_uses_observed_mcp_profile(monkeypatch):
    from scripts.tender_acquisition import identity
    class MCP:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def call(self, tool, arguments):
            if tool == 'search_companies':
                return {'references': {'search_results': [
                    {'kind': 'company', 'url': '/company/atlas/', 'text': 'Atlas SARL'}]}}
            return {'sections': {'about': 'Atlas SARL Maroc https://atlas.ma'},
                    'references': {'about': []}}
    monkeypatch.setattr(identity, 'LinkedInMcp', MCP)
    monkeypatch.setattr(identity, 'parse_observations', lambda *args: {'candidates': [candidate(
        url='https://www.linkedin.com/company/atlas/')]})
    rows = search_linkedin_company('Atlas SARL')
    assert len(rows) == 1 and rows[0]['website'] == 'https://atlas.ma'
