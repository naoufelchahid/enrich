import json

import pytest

from scripts.tender_acquisition.linkedin_direct import LinkedInMcp, linkedin_scope


def test_empty_search_is_no_match_not_transport_failure(monkeypatch):
    client = LinkedInMcp(browser_path='/unused')
    monkeypatch.setattr(client, '_request', lambda *args, **kwargs: {
        'content': [{'type': 'text', 'text': json.dumps({'url': 'https://www.linkedin.com/search',
                                                          'sections': {}})}]})
    assert client.call('search_companies', {'keywords': 'absent'})['references'] == {}
    with pytest.raises(ValueError, match='not allowed'):
        client.call('send_message', {})


def test_section_error_is_not_misread_as_no_match(monkeypatch):
    client = LinkedInMcp(browser_path='/unused')
    monkeypatch.setattr(client, '_request', lambda *args, **kwargs: {
        'content': [{'type': 'text', 'text': json.dumps({'sections': {},
                    'section_errors': {'search_results': {'code': 'RATE_LIMITED'}}})}]})
    with pytest.raises(RuntimeError, match='SECTION_ERROR'):
        client.call('search_companies', {'keywords': 'anything'})


def test_nested_scope_reuses_one_linkedin_worker():
    events = []
    class Fake:
        def __enter__(self): events.append('open'); return self
        def __exit__(self, *args): events.append('close')
    with linkedin_scope(Fake) as outer:
        with linkedin_scope(Fake) as inner:
            assert inner is outer
    assert events == ['open', 'close']
