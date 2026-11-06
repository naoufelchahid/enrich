import json
import sqlite3
from unittest.mock import Mock

import pytest
import responses

from scripts import domain_review as d


@pytest.mark.parametrize('value', ['https://example.com', 'x@example.com', 'example.com\r\nother.com', '127.0.0.1', 'localhost'])
def test_bad_input_never_opens_cache(tmp_path, value):
    p=tmp_path/'cache.sqlite'
    with pytest.raises(ValueError):d.DomainReview(p).lookup(value)
    assert not p.exists()


def test_whois_match_cache_shared_between_companies(tmp_path, monkeypatch):
    transport=Mock(return_value='Domain Name: ACCESSENERGY.MA\nRegistrant Name: Access Energy\nRegistrant Email: private@example.ma\n')
    monkeypatch.setattr(d,'whois_ma',transport)
    r=d.DomainReview(tmp_path/'cache.sqlite')
    first=r.review('AccessEnergy.ma','ACCESS ENERGY')
    assert first['assessment']=='NAME_MATCH' and first['requires_human_review']
    assert not first['email_deliverability_proven']
    second=r.review('accessenergy.ma','Other Company')
    assert second['cache_hit'] and second['assessment']=='NEEDS_REVIEW'
    assert second['observed_at']==first['observed_at']
    assert 'private@example.ma' not in json.dumps(second)
    transport.assert_called_once()


@pytest.mark.parametrize('answer', ['Domain Name: wrong.ma\nRegistrant Name: Example', 'No match', 'Domain Name: example.ma\n'])
def test_missing_or_mismatching_information_stays_reviewable(tmp_path,monkeypatch,answer):
    monkeypatch.setattr(d,'whois_ma',lambda domain:answer)
    r=d.DomainReview(tmp_path/'cache.sqlite').review('example.ma','Example')
    assert r['assessment']=='NEEDS_REVIEW'
    assert r['status'] in ('FOUND','UNKNOWN')


def test_timeout_cached_briefly_then_refetched(tmp_path,monkeypatch):
    transport=Mock(side_effect=TimeoutError('private diagnostic'))
    monkeypatch.setattr(d,'whois_ma',transport)
    p=tmp_path/'cache.sqlite';r=d.DomainReview(p)
    assert r.lookup('example.ma')['status']=='UNKNOWN'
    assert r.lookup('example.ma')['cache_hit']
    assert transport.call_count==1
    with sqlite3.connect(p) as c:c.execute("UPDATE domain_review_cache SET observed_at='2000-01-01T00:00:00+00:00'")
    assert r.lookup('example.ma')['status']=='UNKNOWN'
    assert transport.call_count==2


@responses.activate
def test_rdap_iana_and_registrant_only(tmp_path):
    responses.get(d.BOOTSTRAP,json={'services':[[['com'],['https://rdap.example.com/']]]})
    responses.get('https://rdap.example.com/domain/example.com',json={'objectClassName':'domain','ldhName':'EXAMPLE.COM','entities':[
        {'roles':['registrar'],'vcardArray':['vcard',[['fn',{},'text','Not the company']]]},
        {'roles':['registrant'],'vcardArray':['vcard',[['org',{},'text','Example SA'],['email',{},'text','private@example.com']]]}]})
    r=d.DomainReview(tmp_path/'cache.sqlite').review('example.com','Example SA')
    assert r['assessment']=='NAME_MATCH' and r['registrant_names']==['Example SA']
    assert len(responses.calls)==2
    assert 'private@example.com' not in json.dumps(r)


@responses.activate
def test_rdap_unsupported_is_not_invalid(tmp_path):
    responses.get(d.BOOTSTRAP,json={'services':[]})
    r=d.DomainReview(tmp_path/'cache.sqlite').review('example.zz','Example')
    assert r['status']=='UNSUPPORTED' and r['assessment']=='NEEDS_REVIEW'


@responses.activate
def test_no_redirects_or_untrusted_referrals(tmp_path):
    responses.get(d.BOOTSTRAP,json={'services':[[['com'],['https://rdap.example.com/']]]})
    responses.get('https://rdap.example.com/domain/example.com',status=302,headers={'Location':'http://127.0.0.1/'})
    assert d.DomainReview(tmp_path/'cache.sqlite').lookup('example.com')['status']=='UNKNOWN'
    assert len(responses.calls)==2


@responses.activate
def test_bounded_download(tmp_path):
    responses.get(d.BOOTSTRAP,body='x'*(d.MAX_BYTES+1))
    assert d.DomainReview(tmp_path/'cache.sqlite').lookup('example.com')['status']=='UNKNOWN'
