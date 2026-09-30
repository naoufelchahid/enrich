import json
import sys
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
import dns.resolver
import dns.exception

from scripts.verify_dns_setup import (
    CheckResult,
    DNSCheckStatus,
    VerificationReport,
    check_spf,
    check_dkim,
    check_return_path_mx,
    check_return_path_spf,
    check_dmarc,
    check_cloudflare_proxy,
    run_all_checks,
    main,
)


class MockTxtRecord:
    def __init__(self, text: str):
        self.strings = [text.encode("utf-8")]

    def to_text(self) -> str:
        return f'"{b"".join(self.strings).decode("utf-8")}"'

    def __str__(self) -> str:
        return self.to_text()


class MockCnameRecord:
    def __init__(self, target: str):
        self.target = MagicMock()
        self.target.to_text.return_value = target

    def to_text(self) -> str:
        return self.target.to_text()

    def __str__(self) -> str:
        return self.to_text()


class MockMxRecord:
    def __init__(self, exchange: str, preference: int = 10):
        self.exchange = MagicMock()
        self.exchange.to_text.return_value = exchange
        self.preference = preference

    def to_text(self) -> str:
        return f"{self.preference} {self.exchange.to_text()}"

    def __str__(self) -> str:
        return self.to_text()


class MockARecord:
    def __init__(self, address: str):
        self.address = address

    def to_text(self) -> str:
        return self.address

    def __str__(self) -> str:
        return self.to_text()


@pytest.fixture
def mock_resolver():
    resolver = MagicMock(spec=dns.resolver.Resolver)
    return resolver


# ==============================================================================
# SPF Tests
# ==============================================================================

def test_check_spf_valid(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockTxtRecord("v=spf1 include:amazonses.com ~all")
    ]
    res = check_spf("sales.contextelab.ma", mock_resolver)
    assert res.status == DNSCheckStatus.PASS
    assert "amazonses.com" in res.message
    assert res.record_type == "TXT"
    assert res.host == "sales.contextelab.ma"
    assert res.found == ["v=spf1 include:amazonses.com ~all"]


def test_check_spf_hardfail_valid(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockTxtRecord("v=spf1 include:amazonses.com -all")
    ]
    res = check_spf("sales.contextelab.ma", mock_resolver)
    assert res.status == DNSCheckStatus.PASS
    assert "-all" in res.message


def test_check_spf_resend_include_valid(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockTxtRecord("v=spf1 include:resend.com ~all")
    ]
    res = check_spf("sales.contextelab.ma", mock_resolver, expected_includes=["amazonses.com", "resend.com"])
    assert res.status == DNSCheckStatus.PASS


def test_check_spf_missing_record(mock_resolver):
    mock_resolver.resolve.side_effect = dns.resolver.NXDOMAIN()
    res = check_spf("sales.contextelab.ma", mock_resolver)
    assert res.status == DNSCheckStatus.FAIL
    assert "No SPF" in res.message or "NXDOMAIN" in res.message


def test_check_spf_no_spf_in_txt(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockTxtRecord("google-site-verification=abc123xyz")
    ]
    res = check_spf("sales.contextelab.ma", mock_resolver)
    assert res.status == DNSCheckStatus.FAIL
    assert "No v=spf1" in res.message


def test_check_spf_multiple_records_rfc_violation(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockTxtRecord("v=spf1 include:amazonses.com ~all"),
        MockTxtRecord("v=spf1 include:_spf.google.com ~all"),
    ]
    res = check_spf("sales.contextelab.ma", mock_resolver)
    assert res.status == DNSCheckStatus.FAIL
    assert "Multiple SPF records" in res.message or "RFC 7208" in res.message


def test_check_spf_missing_required_include(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockTxtRecord("v=spf1 include:otherprovider.com ~all")
    ]
    res = check_spf("sales.contextelab.ma", mock_resolver, expected_includes=["amazonses.com"])
    assert res.status == DNSCheckStatus.WARN
    assert "Missing expected include: amazonses.com" in res.message


# ==============================================================================
# DKIM Tests
# ==============================================================================

def test_check_dkim_cname_valid(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockCnameRecord("resend._domainkey.sales.contextelab.ma.resend.email.")
    ]
    res = check_dkim("sales.contextelab.ma", selector="resend", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.PASS
    assert "CNAME" in res.message
    assert "resend.email" in res.found[0]


def test_check_dkim_txt_valid(mock_resolver):
    # When CNAME lookup raises NoAnswer or returns empty, fallback to TXT
    def side_effect(qname, rdatatype, **kwargs):
        if rdatatype == "CNAME":
            raise dns.resolver.NoAnswer()
        elif rdatatype == "TXT":
            return [MockTxtRecord("v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQD...")]
        raise dns.resolver.NXDOMAIN()

    mock_resolver.resolve.side_effect = side_effect
    res = check_dkim("sales.contextelab.ma", selector="resend", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.PASS
    assert "TXT" in res.message
    assert "v=DKIM1" in res.found[0]


def test_check_dkim_missing(mock_resolver):
    mock_resolver.resolve.side_effect = dns.resolver.NXDOMAIN()
    res = check_dkim("sales.contextelab.ma", selector="resend", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.FAIL
    assert "not found" in res.message.lower() or "nxdomain" in res.message.lower()


# ==============================================================================
# Return-Path MX & SPF Tests
# ==============================================================================

def test_check_return_path_mx_valid(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockMxRecord("feedback-smtp.us-east-1.amazonses.com.", preference=10)
    ]
    res = check_return_path_mx("sales.contextelab.ma", return_subdomain="send", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.PASS
    assert "feedback-smtp.us-east-1.amazonses.com" in res.message
    assert res.host == "send.sales.contextelab.ma"


def test_check_return_path_mx_missing(mock_resolver):
    mock_resolver.resolve.side_effect = dns.resolver.NXDOMAIN()
    res = check_return_path_mx("sales.contextelab.ma", return_subdomain="send", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.FAIL
    assert "No MX record" in res.message or "NXDOMAIN" in res.message


def test_check_return_path_mx_unexpected_host(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockMxRecord("mail.otherhost.com.", preference=10)
    ]
    res = check_return_path_mx("sales.contextelab.ma", return_subdomain="send", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.WARN
    assert "does not point to amazonses.com" in res.message


def test_check_return_path_spf_valid(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockTxtRecord("v=spf1 include:amazonses.com ~all")
    ]
    res = check_return_path_spf("sales.contextelab.ma", return_subdomain="send", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.PASS
    assert "amazonses.com" in res.message


def test_check_return_path_spf_missing(mock_resolver):
    mock_resolver.resolve.side_effect = dns.resolver.NXDOMAIN()
    res = check_return_path_spf("sales.contextelab.ma", return_subdomain="send", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.WARN
    assert "missing" in res.message.lower() or "nxdomain" in res.message.lower()


# ==============================================================================
# DMARC Tests
# ==============================================================================

def test_check_dmarc_valid_subdomain(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockTxtRecord("v=DMARC1; p=none; rua=mailto:dmarc@contextelab.ma; sp=none;")
    ]
    res = check_dmarc("sales.contextelab.ma", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.PASS
    assert "p=none" in res.message
    assert "rua=" in res.message
    assert res.host == "_dmarc.sales.contextelab.ma"


def test_check_dmarc_fallback_parent_domain(mock_resolver):
    def side_effect(qname, rdatatype, **kwargs):
        if qname == "_dmarc.sales.contextelab.ma":
            raise dns.resolver.NXDOMAIN()
        elif qname == "_dmarc.contextelab.ma":
            return [MockTxtRecord("v=DMARC1; p=quarantine; rua=mailto:security@contextelab.ma;")]
        raise dns.resolver.NXDOMAIN()

    mock_resolver.resolve.side_effect = side_effect
    res = check_dmarc("sales.contextelab.ma", resolver=mock_resolver, check_parent_fallback=True)
    assert res.status == DNSCheckStatus.PASS
    assert "inherited" in res.message.lower() or "parent" in res.message.lower()
    assert "_dmarc.contextelab.ma" in res.message


def test_check_dmarc_missing_both(mock_resolver):
    mock_resolver.resolve.side_effect = dns.resolver.NXDOMAIN()
    res = check_dmarc("sales.contextelab.ma", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.FAIL
    assert "No DMARC" in res.message or "NXDOMAIN" in res.message


def test_check_dmarc_missing_rua(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockTxtRecord("v=DMARC1; p=none;")
    ]
    res = check_dmarc("sales.contextelab.ma", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.WARN
    assert "rua" in res.message.lower()


def test_check_dmarc_invalid_policy(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockTxtRecord("v=DMARC1; p=invalid_policy; rua=mailto:dmarc@contextelab.ma;")
    ]
    res = check_dmarc("sales.contextelab.ma", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.WARN or res.status == DNSCheckStatus.FAIL


# ==============================================================================
# Cloudflare Proxy Detection Tests
# ==============================================================================

def test_check_cloudflare_proxy_detected(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockARecord("104.21.55.10"),
        MockARecord("172.67.180.20"),
    ]
    res = check_cloudflare_proxy("sales.contextelab.ma", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.WARN
    assert "Cloudflare proxy detected" in res.message or "DNS Only" in res.message


def test_check_cloudflare_proxy_clean_or_nxdomain(mock_resolver):
    mock_resolver.resolve.side_effect = dns.resolver.NXDOMAIN()
    res = check_cloudflare_proxy("sales.contextelab.ma", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.PASS


def test_check_cloudflare_proxy_direct_ip(mock_resolver):
    mock_resolver.resolve.return_value = [
        MockARecord("192.0.2.1")
    ]
    res = check_cloudflare_proxy("sales.contextelab.ma", resolver=mock_resolver)
    assert res.status == DNSCheckStatus.PASS


# ==============================================================================
# run_all_checks & VerificationReport Tests
# ==============================================================================

def test_run_all_checks_all_pass(mock_resolver):
    def side_effect(qname, rdatatype, **kwargs):
        if qname == "sales.contextelab.ma" and rdatatype == "TXT":
            return [MockTxtRecord("v=spf1 include:amazonses.com ~all")]
        elif qname == "sales.contextelab.ma" and rdatatype == "A":
            raise dns.resolver.NXDOMAIN()
        elif qname == "resend._domainkey.sales.contextelab.ma" and rdatatype == "CNAME":
            return [MockCnameRecord("resend._domainkey.sales.contextelab.ma.resend.email.")]
        elif qname == "send.sales.contextelab.ma" and rdatatype == "MX":
            return [MockMxRecord("feedback-smtp.us-east-1.amazonses.com.", preference=10)]
        elif qname == "send.sales.contextelab.ma" and rdatatype == "TXT":
            return [MockTxtRecord("v=spf1 include:amazonses.com ~all")]
        elif qname == "_dmarc.sales.contextelab.ma" and rdatatype == "TXT":
            return [MockTxtRecord("v=DMARC1; p=none; rua=mailto:dmarc@contextelab.ma;")]
        raise dns.resolver.NXDOMAIN()

    mock_resolver.resolve.side_effect = side_effect

    with patch("scripts.verify_dns_setup.dns.resolver.Resolver", return_value=mock_resolver):
        report = run_all_checks(domain="sales.contextelab.ma")
        assert report.is_valid is True
        assert report.summary["FAIL"] == 0
        assert report.summary["PASS"] >= 5
        report_dict = report.to_dict()
        assert report_dict["is_valid"] is True
        assert report_dict["domain"] == "sales.contextelab.ma"
        assert len(report_dict["checks"]) >= 5


def test_run_all_checks_with_failures(mock_resolver):
    mock_resolver.resolve.side_effect = dns.resolver.NXDOMAIN()

    with patch("scripts.verify_dns_setup.dns.resolver.Resolver", return_value=mock_resolver):
        report = run_all_checks(domain="sales.contextelab.ma")
        assert report.is_valid is False
        assert report.summary["FAIL"] > 0


# ==============================================================================
# CLI Entry Point Tests
# ==============================================================================

def test_main_cli_json_output(capsys, mock_resolver):
    def side_effect(qname, rdatatype, **kwargs):
        if "amazonses" in str(qname) or rdatatype == "TXT":
            return [MockTxtRecord("v=spf1 include:amazonses.com ~all")]
        elif rdatatype == "CNAME":
            return [MockCnameRecord("resend._domainkey.resend.email.")]
        elif rdatatype == "MX":
            return [MockMxRecord("feedback-smtp.us-east-1.amazonses.com.", 10)]
        raise dns.resolver.NXDOMAIN()

    mock_resolver.resolve.side_effect = side_effect

    test_args = ["scripts/verify_dns_setup.py", "--domain", "sales.contextelab.ma", "--json"]
    with patch.object(sys, "argv", test_args):
        with patch("scripts.verify_dns_setup.dns.resolver.Resolver", return_value=mock_resolver):
            exit_code = main()
            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["domain"] == "sales.contextelab.ma"
            assert "checks" in data
            assert isinstance(data["checks"], list)


def test_main_cli_exit_code_on_failure(mock_resolver):
    mock_resolver.resolve.side_effect = dns.resolver.NXDOMAIN()

    test_args = ["scripts/verify_dns_setup.py", "--domain", "sales.contextelab.ma"]
    with patch.object(sys, "argv", test_args):
        with patch("scripts.verify_dns_setup.dns.resolver.Resolver", return_value=mock_resolver):
            exit_code = main()
            assert exit_code == 1
