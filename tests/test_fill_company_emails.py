from scripts.fill_company_emails import match_excerpt, match_lead, valid_email


def company(name='Example', domain='example.ma'):
    return {'id': 'test', 'name': name, 'domainName': {'primaryLinkUrl': 'https://' + domain} if domain else {}}


def test_foreign_homonym_is_not_attached_by_domain():
    row = {'country': 'United States', 'email': 'alice@example.ma', 'company_domain': 'example.ma'}
    assert not match_lead(row, [company()])[0]


def test_moroccan_employee_has_consistent_domains():
    row = {'country': 'Morocco', 'email': 'alice@example.ma', 'company_domain': 'example.ma',
           'company_website': 'https://www.example.ma'}
    assert match_lead(row, [company()])[0] == [company()]
    row['email'] = 'alice@unrelated.ma'
    assert not match_lead(row, [company()])[0]


def test_shared_domain_requires_review():
    row = {'country': 'Morocco', 'email': 'alice@example.ma', 'company_domain': 'example.ma'}
    assert not match_lead(row, [company(), company('Other branch')])[0]


def test_search_result_needs_more_than_name():
    co = company('Example', '')
    assert not match_excerpt('contact@example.ma', {'title': 'Example Casablanca'}, [co])[0]
    assert match_excerpt('contact@example.ma', {'title': 'Example', 'description': 'Mohammedia'}, [co])[0] == [co]
    assert not match_excerpt('contact@other.ma', {'title': 'Other firm Mohammedia'}, [co])[0]


def test_incomplete_and_personal_addresses_rejected():
    assert not valid_email('fidu...@example.ma')
    assert not valid_email('alice@gmail.com')
    assert valid_email('contact@example.ma')
