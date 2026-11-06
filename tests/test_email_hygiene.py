from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import dns.exception
import dns.resolver
import pytest

from scripts.email_hygiene import valid_email, fresh, inspect_email, resolve_mail_domain, email_identity, is_personal_email


@pytest.mark.parametrize('email', ['@example.com', 'a@.', 'a..b@example.com', '.a@example.com',
                                  'a@-example.com', 'a@exam_ple.com', 'a b@example.com', 'a@example..com'])
def test_malformed_addresses_rejected(email):
    assert not valid_email(email)
    assert inspect_email(email) == 'INVALID'


def test_positive_hygiene_is_not_deliverability_and_disposable_waits():
    assert inspect_email('alice@example.org') == 'PASS'
    assert inspect_email('alice@yopmail.com') == 'INVALID'
    assert inspect_email('é@example.org') == 'WAITING'
    assert inspect_email('"quoted"@example.org') == 'WAITING'


def test_dns_distinguishes_absence_from_uncertainty():
    for error, state in [(dns.resolver.NXDOMAIN(), 'INVALID'),
                         (dns.resolver.NoAnswer(), 'WAITING'), (dns.exception.Timeout(), 'WAITING')]:
        with patch('dns.resolver.resolve', side_effect=error):
            assert resolve_mail_domain('example.org') == state
    with patch('dns.resolver.resolve', return_value=[SimpleNamespace(exchange='.')]):
        assert resolve_mail_domain('example.org') == 'INVALID'
    with patch('dns.resolver.resolve', return_value=[SimpleNamespace(exchange='mx.example.org.')]):
        assert resolve_mail_domain('example.org') == 'PASS'


def test_exact_90_day_boundary_and_bad_dates():
    instant = datetime(2026, 10, 6, tzinfo=timezone.utc)
    assert fresh((instant - timedelta(days=90)).isoformat(), at=instant)
    assert not fresh((instant - timedelta(days=90, seconds=1)).isoformat(), at=instant)
    assert not fresh((instant + timedelta(seconds=1)).isoformat(), at=instant)
    assert not fresh('garbage', at=instant)
    assert not fresh(None, at=instant)


@pytest.mark.parametrize('domain', ['gmail.com','googlemail.com','hotmail.com','hotmail.fr',
    'outlook.es','live.be','yahoo.fr','yahoo.co.uk','mail.yahoo.co.jp','ymail.com',
    'icloud.com','proton.me','gmx.de','orange.fr','laposte.net','aol.com','yopmail.com',
    'consultant.com','engineer.com','execs.com','privaterelay.appleid.com','duck.com'])
def test_personal_domains_are_rejected_before_dns_or_identity_matching(domain):
    email = 'alice.tazi@' + domain.upper()
    assert is_personal_email(email)
    assert email_identity(email, 'Alice', 'Tazi')['reason'] == 'PERSONAL_DOMAIN'
    with patch('scripts.email_hygiene.resolve_mail_domain', side_effect=AssertionError('DNS must not run')):
        assert inspect_email(email) == 'INVALID'


def test_custom_business_domain_is_not_rejected_by_hosting_provider_name():
    assert not is_personal_email('alice.tazi@company.ma')
    assert not is_personal_email('alice.tazi@gmail.company.ma')
    assert email_identity('alice.tazi@company.ma','Alice','Tazi')['accepted']
