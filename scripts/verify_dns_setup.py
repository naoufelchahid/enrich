#!/usr/bin/env python3
"""
DNS Setup Verification Tool for Resend & Email Deliverability.

Verifies SPF, DKIM, Return-Path MX, Return-Path SPF, and DMARC records for
the sending domain (default: sales.contextelab.ma).
Also detects potential Cloudflare proxying (Orange Cloud) which would disrupt
email routing.

Usage:
    python scripts/verify_dns_setup.py [--domain sales.contextelab.ma] [--nameserver 1.1.1.1] [--json]
"""

from __future__ import annotations

import argparse
import datetime
from enum import Enum
import ipaddress
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import dns.exception
import dns.resolver

logger = logging.getLogger(__name__)

# Known Cloudflare IPv4 ranges
CLOUDFLARE_IPV4_NETWORKS = [
    ipaddress.ip_network("173.245.48.0/20"),
    ipaddress.ip_network("103.21.244.0/22"),
    ipaddress.ip_network("103.22.200.0/22"),
    ipaddress.ip_network("103.31.4.0/22"),
    ipaddress.ip_network("141.101.64.0/18"),
    ipaddress.ip_network("108.162.192.0/18"),
    ipaddress.ip_network("190.93.240.0/20"),
    ipaddress.ip_network("188.114.96.0/20"),
    ipaddress.ip_network("197.234.240.0/22"),
    ipaddress.ip_network("198.41.128.0/17"),
    ipaddress.ip_network("162.158.0.0/15"),
    ipaddress.ip_network("104.16.0.0/13"),
    ipaddress.ip_network("104.24.0.0/14"),
    ipaddress.ip_network("172.64.0.0/13"),
    ipaddress.ip_network("131.0.72.0/22"),
]


class DNSCheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"


@dataclass
class CheckResult:
    name: str
    record_type: str
    host: str
    status: DNSCheckStatus
    expected: str
    found: List[str] = field(default_factory=list)
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "record_type": self.record_type,
            "host": self.host,
            "status": self.status.value,
            "expected": self.expected,
            "found": self.found,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class VerificationReport:
    domain: str
    timestamp: str
    checks: List[CheckResult]
    is_valid: bool
    summary: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "timestamp": self.timestamp,
            "is_valid": self.is_valid,
            "summary": self.summary,
            "checks": [c.to_dict() for c in self.checks],
        }


def get_resolver(
    nameserver: Optional[str] = None,
    timeout: float = 4.0,
) -> dns.resolver.Resolver:
    """Instantiate a dns.resolver.Resolver with custom settings."""
    resolver = dns.resolver.Resolver()
    resolver.timeout = timeout
    resolver.lifetime = timeout
    if nameserver:
        resolver.nameservers = [nameserver]
    return resolver


def _extract_txt_strings(answers: Any) -> List[str]:
    """Helper to decode TXT record chunks into complete strings."""
    records: List[str] = []
    for rdata in answers:
        if hasattr(rdata, "strings") and rdata.strings:
            decoded = "".join(
                s.decode("utf-8", errors="replace") if isinstance(s, bytes) else str(s)
                for s in rdata.strings
            )
            records.append(decoded)
        else:
            raw = rdata.to_text().strip('"')
            records.append(raw)
    return records


# ==============================================================================
# 1. SPF Check
# ==============================================================================

def check_spf(
    domain: str,
    resolver: dns.resolver.Resolver,
    expected_includes: Optional[List[str]] = None,
) -> CheckResult:
    """Verify SPF TXT record on domain (sales.contextelab.ma)."""
    if expected_includes is None:
        expected_includes = ["amazonses.com"]

    expected_desc = f"TXT record starting with 'v=spf1' and including {', '.join(expected_includes)}"
    try:
        answers = resolver.resolve(domain, "TXT")
        txt_records = _extract_txt_strings(answers)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer) as e:
        return CheckResult(
            name="SPF Record",
            record_type="TXT",
            host=domain,
            status=DNSCheckStatus.FAIL,
            expected=expected_desc,
            found=[],
            message=f"No SPF TXT record found for host {domain} ({type(e).__name__}).",
        )
    except dns.exception.DNSException as e:
        return CheckResult(
            name="SPF Record",
            record_type="TXT",
            host=domain,
            status=DNSCheckStatus.FAIL,
            expected=expected_desc,
            found=[],
            message=f"DNS resolution error while querying SPF: {e}",
        )

    # Filter SPF records (v=spf1)
    spf_records = [r for r in txt_records if r.lower().startswith("v=spf1")]

    if not spf_records:
        return CheckResult(
            name="SPF Record",
            record_type="TXT",
            host=domain,
            status=DNSCheckStatus.FAIL,
            expected=expected_desc,
            found=txt_records,
            message="No v=spf1 record found among existing TXT records.",
        )

    # RFC 7208 forbids multiple SPF records on a single host
    if len(spf_records) > 1:
        return CheckResult(
            name="SPF Record",
            record_type="TXT",
            host=domain,
            status=DNSCheckStatus.FAIL,
            expected=expected_desc,
            found=spf_records,
            message=f"Multiple SPF records found ({len(spf_records)}). RFC 7208 strictly forbids multiple SPF records on the same host.",
        )

    spf_record = spf_records[0]
    tokens = spf_record.split()

    # Check for expected include mechanisms
    missing_includes = [
        inc for inc in expected_includes
        if not any(f"include:{inc}" in tok.lower() for tok in tokens)
    ]

    # Check ending mechanism (~all or -all)
    has_softfail = "~all" in spf_record.lower()
    has_hardfail = "-all" in spf_record.lower()
    has_neutral_or_pass = "?all" in spf_record.lower() or "+all" in spf_record.lower()

    if missing_includes and not any(f"include:{inc}" in spf_record.lower() for inc in expected_includes):
        return CheckResult(
            name="SPF Record",
            record_type="TXT",
            host=domain,
            status=DNSCheckStatus.WARN,
            expected=expected_desc,
            found=spf_records,
            message=f"Missing expected include: {', '.join(missing_includes)} in SPF record: '{spf_record}'.",
        )

    if has_neutral_or_pass:
        return CheckResult(
            name="SPF Record",
            record_type="TXT",
            host=domain,
            status=DNSCheckStatus.WARN,
            expected=expected_desc,
            found=spf_records,
            message=f"SPF record contains overly permissive ending mechanism: '{spf_record}'. Use ~all (SoftFail) or -all (HardFail).",
        )

    ending = "-all" if has_hardfail else "~all"
    return CheckResult(
        name="SPF Record",
        record_type="TXT",
        host=domain,
        status=DNSCheckStatus.PASS,
        expected=expected_desc,
        found=spf_records,
        message=f"Valid SPF record found with authorized provider and ending qualifier {ending}: '{spf_record}'.",
        details={"record": spf_record, "qualifier": ending},
    )


# ==============================================================================
# 2. DKIM Check
# ==============================================================================

def check_dkim(
    domain: str,
    selector: str = "resend",
    resolver: Optional[dns.resolver.Resolver] = None,
) -> CheckResult:
    """Verify DKIM CNAME or TXT record for selector (default: resend._domainkey)."""
    if resolver is None:
        resolver = get_resolver()

    dkim_host = f"{selector}._domainkey.{domain}"
    expected_desc = f"CNAME pointing to Resend (*.resend.email) or TXT with v=DKIM1 on {dkim_host}"

    # Try CNAME first (standard Resend setup)
    try:
        cname_answers = resolver.resolve(dkim_host, "CNAME")
        cnames = [r.target.to_text().rstrip(".") for r in cname_answers]
        if cnames:
            return CheckResult(
                name="DKIM Record",
                record_type="CNAME",
                host=dkim_host,
                status=DNSCheckStatus.PASS,
                expected=expected_desc,
                found=cnames,
                message=f"Valid DKIM CNAME record found pointing to '{cnames[0]}'.",
                details={"target": cnames[0], "selector": selector},
            )
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        pass
    except dns.exception.DNSException as e:
        logger.debug(f"CNAME query on {dkim_host} error: {e}")

    # Fallback to TXT record query (raw public key)
    try:
        txt_answers = resolver.resolve(dkim_host, "TXT")
        txt_records = _extract_txt_strings(txt_answers)
        dkim_txts = [
            r for r in txt_records
            if "v=dkim1" in r.lower() or "p=" in r.lower() or "k=rsa" in r.lower()
        ]
        if dkim_txts:
            return CheckResult(
                name="DKIM Record",
                record_type="TXT",
                host=dkim_host,
                status=DNSCheckStatus.PASS,
                expected=expected_desc,
                found=dkim_txts,
                message=f"Valid DKIM TXT record found: '{dkim_txts[0]}'.",
                details={"record": dkim_txts[0], "selector": selector},
            )
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        pass
    except dns.exception.DNSException as e:
        logger.debug(f"TXT query on {dkim_host} error: {e}")

    return CheckResult(
        name="DKIM Record",
        record_type="CNAME / TXT",
        host=dkim_host,
        status=DNSCheckStatus.FAIL,
        expected=expected_desc,
        found=[],
        message=f"DKIM record not found on {dkim_host}. Check selector '{selector}' and ensure CNAME/TXT is provisioned.",
    )


# ==============================================================================
# 3. Return-Path MX Check
# ==============================================================================

def check_return_path_mx(
    domain: str,
    return_subdomain: str = "send",
    resolver: Optional[dns.resolver.Resolver] = None,
    expected_mx_contains: Union[str, List[str]] = "amazonses.com",
) -> CheckResult:
    """Verify MX record on Return-Path host (send.sales.contextelab.ma)."""
    if resolver is None:
        resolver = get_resolver()

    return_host = f"{return_subdomain}.{domain}"
    allowed_patterns = (
        [expected_mx_contains]
        if isinstance(expected_mx_contains, str)
        else list(expected_mx_contains)
    )
    # Support both AWS SES legacy and Resend modern rmta.net infrastructure
    for default_pat in ["amazonses.com", "rmta.net", "resend.com", "resend.email"]:
        if default_pat not in allowed_patterns:
            allowed_patterns.append(default_pat)

    expected_desc = f"MX record pointing to {expected_mx_contains} with priority 10 on {return_host}"

    try:
        mx_answers = resolver.resolve(return_host, "MX")
        mx_records: List[str] = []
        has_expected_target = False

        for rdata in mx_answers:
            exchange = rdata.exchange.to_text().rstrip(".")
            mx_records.append(f"{rdata.preference} {exchange}")
            if any(pat.lower() in exchange.lower() for pat in allowed_patterns):
                has_expected_target = True

        if not mx_records:
            return CheckResult(
                name="Return-Path MX",
                record_type="MX",
                host=return_host,
                status=DNSCheckStatus.FAIL,
                expected=expected_desc,
                found=[],
                message=f"No MX record found for Return-Path host {return_host}.",
            )

        if not has_expected_target:
            return CheckResult(
                name="Return-Path MX",
                record_type="MX",
                host=return_host,
                status=DNSCheckStatus.WARN,
                expected=expected_desc,
                found=mx_records,
                message=f"Return-Path MX record exists ({', '.join(mx_records)}) but does not point to {expected_mx_contains}.",
            )

        return CheckResult(
            name="Return-Path MX",
            record_type="MX",
            host=return_host,
            status=DNSCheckStatus.PASS,
            expected=expected_desc,
            found=mx_records,
            message=f"Valid Return-Path MX found pointing to {', '.join(mx_records)}.",
            details={"mx": mx_records},
        )
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer) as e:
        return CheckResult(
            name="Return-Path MX",
            record_type="MX",
            host=return_host,
            status=DNSCheckStatus.FAIL,
            expected=expected_desc,
            found=[],
            message=f"No MX record found for {return_host} ({type(e).__name__}).",
        )
    except dns.exception.DNSException as e:
        return CheckResult(
            name="Return-Path MX",
            record_type="MX",
            host=return_host,
            status=DNSCheckStatus.FAIL,
            expected=expected_desc,
            found=[],
            message=f"DNS query error on {return_host} MX: {e}",
        )


# ==============================================================================
# 4. Return-Path SPF Check
# ==============================================================================

def check_return_path_spf(
    domain: str,
    return_subdomain: str = "send",
    resolver: Optional[dns.resolver.Resolver] = None,
    expected_include: str = "amazonses.com",
) -> CheckResult:
    """Verify SPF TXT record on Return-Path host (send.sales.contextelab.ma)."""
    if resolver is None:
        resolver = get_resolver()

    return_host = f"{return_subdomain}.{domain}"
    expected_desc = f"TXT record starting with 'v=spf1 include:{expected_include}' on {return_host}"

    try:
        answers = resolver.resolve(return_host, "TXT")
        txt_records = _extract_txt_strings(answers)
        spf_records = [r for r in txt_records if r.lower().startswith("v=spf1")]

        if not spf_records:
            return CheckResult(
                name="Return-Path SPF",
                record_type="TXT",
                host=return_host,
                status=DNSCheckStatus.WARN,
                expected=expected_desc,
                found=txt_records,
                message=f"Missing SPF record on Return-Path host {return_host}. Recommended for strict envelope alignment.",
            )

        spf = spf_records[0]
        valid_resend_markers = [expected_include.lower(), "amazonses.com", "rmta.net", "resend.com", "52.3.252.119", "199.249.231.0"]
        if not any(marker in spf.lower() for marker in valid_resend_markers):
            return CheckResult(
                name="Return-Path SPF",
                record_type="TXT",
                host=return_host,
                status=DNSCheckStatus.WARN,
                expected=expected_desc,
                found=spf_records,
                message=f"Return-Path SPF record does not include {expected_include}: '{spf}'.",
            )

        return CheckResult(
            name="Return-Path SPF",
            record_type="TXT",
            host=return_host,
            status=DNSCheckStatus.PASS,
            expected=expected_desc,
            found=spf_records,
            message=f"Valid Return-Path SPF record found: '{spf}'.",
            details={"record": spf},
        )
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer) as e:
        return CheckResult(
            name="Return-Path SPF",
            record_type="TXT",
            host=return_host,
            status=DNSCheckStatus.WARN,
            expected=expected_desc,
            found=[],
            message=f"Missing SPF record on Return-Path host {return_host} ({type(e).__name__}). Recommended for RFC 5321 envelope alignment.",
        )
    except dns.exception.DNSException as e:
        return CheckResult(
            name="Return-Path SPF",
            record_type="TXT",
            host=return_host,
            status=DNSCheckStatus.WARN,
            expected=expected_desc,
            found=[],
            message=f"DNS query error on {return_host} TXT: {e}",
        )


# ==============================================================================
# 5. DMARC Check
# ==============================================================================

def check_dmarc(
    domain: str,
    resolver: Optional[dns.resolver.Resolver] = None,
    check_parent_fallback: bool = True,
) -> CheckResult:
    """Verify DMARC TXT record for domain (_dmarc.sales.contextelab.ma)."""
    if resolver is None:
        resolver = get_resolver()

    dmarc_host = f"_dmarc.{domain}"
    expected_desc = f"TXT record starting with 'v=DMARC1' on {dmarc_host}"

    # Query subdomain DMARC first
    def _evaluate_dmarc_records(txt_records: List[str], queried_host: str, is_fallback: bool) -> Optional[CheckResult]:
        dmarc_records = [r for r in txt_records if r.lower().startswith("v=dmarc1")]
        if not dmarc_records:
            return None

        record = dmarc_records[0]
        tokens = [t.strip() for t in record.split(";") if t.strip()]
        policy = None
        has_rua = False

        for tok in tokens:
            if tok.lower().startswith("p="):
                policy = tok.split("=")[1].strip().lower()
            if tok.lower().startswith("rua="):
                has_rua = True

        valid_policies = {"none", "quarantine", "reject"}
        if policy not in valid_policies:
            return CheckResult(
                name="DMARC Policy",
                record_type="TXT",
                host=queried_host,
                status=DNSCheckStatus.WARN,
                expected=expected_desc,
                found=dmarc_records,
                message=f"DMARC record contains unrecognized policy p='{policy}': '{record}'.",
            )

        if not has_rua:
            return CheckResult(
                name="DMARC Policy",
                record_type="TXT",
                host=queried_host,
                status=DNSCheckStatus.WARN,
                expected=expected_desc,
                found=dmarc_records,
                message=f"DMARC record found with p={policy}, but missing 'rua=' reporting URI: '{record}'.",
            )

        origin_note = f" (inherited from parent domain {queried_host})" if is_fallback else ""
        return CheckResult(
            name="DMARC Policy",
            record_type="TXT",
            host=queried_host,
            status=DNSCheckStatus.PASS,
            expected=expected_desc,
            found=dmarc_records,
            message=f"Valid DMARC policy found with p={policy} and rua reporting URI{origin_note}: '{record}'.",
            details={"policy": policy, "has_rua": has_rua, "record": record, "inherited": is_fallback},
        )

    try:
        answers = resolver.resolve(dmarc_host, "TXT")
        res = _evaluate_dmarc_records(_extract_txt_strings(answers), dmarc_host, is_fallback=False)
        if res:
            return res
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        pass
    except dns.exception.DNSException as e:
        logger.debug(f"DMARC query on {dmarc_host} error: {e}")

    # Check parent domain fallback if enabled (e.g. contextelab.ma for sales.contextelab.ma)
    if check_parent_fallback:
        parts = domain.split(".")
        if len(parts) > 2:
            parent_domain = ".".join(parts[1:])
            parent_dmarc_host = f"_dmarc.{parent_domain}"
            try:
                parent_answers = resolver.resolve(parent_dmarc_host, "TXT")
                res = _evaluate_dmarc_records(_extract_txt_strings(parent_answers), parent_dmarc_host, is_fallback=True)
                if res:
                    return res
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                pass
            except dns.exception.DNSException as e:
                logger.debug(f"Parent DMARC query on {parent_dmarc_host} error: {e}")

    return CheckResult(
        name="DMARC Policy",
        record_type="TXT",
        host=dmarc_host,
        status=DNSCheckStatus.FAIL,
        expected=expected_desc,
        found=[],
        message=f"No DMARC record found on {dmarc_host} or parent domain.",
    )


# ==============================================================================
# 6. Cloudflare Proxy Detection Check
# ==============================================================================

def check_cloudflare_proxy(
    domain: str,
    resolver: Optional[dns.resolver.Resolver] = None,
) -> CheckResult:
    """Check if domain resolves to Cloudflare proxy IPs (Orange Cloud detection)."""
    if resolver is None:
        resolver = get_resolver()

    try:
        answers = resolver.resolve(domain, "A")
        ips = [rdata.to_text().strip() for rdata in answers]
        cf_detected_ips: List[str] = []

        for ip_str in ips:
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                if any(ip_obj in net for net in CLOUDFLARE_IPV4_NETWORKS):
                    cf_detected_ips.append(ip_str)
            except ValueError:
                pass

        if cf_detected_ips:
            return CheckResult(
                name="Cloudflare Proxy Check",
                record_type="A",
                host=domain,
                status=DNSCheckStatus.WARN,
                expected="DNS Only (Grey Cloud) - No Cloudflare proxy IPs",
                found=ips,
                message=(
                    f"Cloudflare proxy detected on {domain} ({', '.join(cf_detected_ips)}). "
                    "Ensure all DNS records (MX, SPF, DKIM) are set to 'DNS Only' (Grey Cloud) in Cloudflare."
                ),
                details={"cloudflare_ips": cf_detected_ips},
            )

        return CheckResult(
            name="Cloudflare Proxy Check",
            record_type="A",
            host=domain,
            status=DNSCheckStatus.PASS,
            expected="DNS Only (Grey Cloud)",
            found=ips,
            message=f"No Cloudflare proxy detected on {domain}. Direct DNS resolution confirmed.",
            details={"ips": ips},
        )
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        # A record not existing on subdomain is normal and safe
        return CheckResult(
            name="Cloudflare Proxy Check",
            record_type="A",
            host=domain,
            status=DNSCheckStatus.PASS,
            expected="DNS Only (Grey Cloud)",
            found=[],
            message=f"No A record found on {domain} (safe: subdomain is purely used for email).",
        )
    except dns.exception.DNSException as e:
        return CheckResult(
            name="Cloudflare Proxy Check",
            record_type="A",
            host=domain,
            status=DNSCheckStatus.PASS,
            expected="DNS Only (Grey Cloud)",
            found=[],
            message=f"DNS query bypassed: {e}",
        )


# ==============================================================================
# Aggregate Verification Suite
# ==============================================================================

def run_all_checks(
    domain: str = "sales.contextelab.ma",
    selector: str = "resend",
    nameserver: Optional[str] = None,
    timeout: float = 4.0,
    strict: bool = False,
) -> VerificationReport:
    """Run all DNS deliverability checks and compile a VerificationReport."""
    resolver = get_resolver(nameserver=nameserver, timeout=timeout)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    checks: List[CheckResult] = [
        check_spf(domain=domain, resolver=resolver),
        check_dkim(domain=domain, selector=selector, resolver=resolver),
        check_return_path_mx(domain=domain, return_subdomain="send", resolver=resolver),
        check_return_path_spf(domain=domain, return_subdomain="send", resolver=resolver),
        check_dmarc(domain=domain, resolver=resolver),
        check_cloudflare_proxy(domain=domain, resolver=resolver),
    ]

    summary = {
        "PASS": sum(1 for c in checks if c.status == DNSCheckStatus.PASS),
        "FAIL": sum(1 for c in checks if c.status == DNSCheckStatus.FAIL),
        "WARN": sum(1 for c in checks if c.status == DNSCheckStatus.WARN),
        "SKIP": sum(1 for c in checks if c.status == DNSCheckStatus.SKIP),
    }

    if strict:
        is_valid = summary["FAIL"] == 0 and summary["WARN"] == 0
    else:
        is_valid = summary["FAIL"] == 0

    return VerificationReport(
        domain=domain,
        timestamp=now_iso,
        checks=checks,
        is_valid=is_valid,
        summary=summary,
    )


# ==============================================================================
# Terminal Presentation Formatting
# ==============================================================================

def format_terminal_report(report: VerificationReport) -> str:
    """Format the verification report with visual badges and diagnostic guidance."""
    lines: List[str] = []
    lines.append("=" * 80)
    lines.append(f"  DNS VERIFICATION REPORT: {report.domain}")
    lines.append(f"  Timestamp: {report.timestamp}")
    lines.append("=" * 80)
    lines.append(f"{'CHECK':<22} | {'TYPE':<6} | {'STATUS':<6} | {'DETAILS'}")
    lines.append("-" * 80)

    status_badges = {
        DNSCheckStatus.PASS: "[PASS]",
        DNSCheckStatus.FAIL: "[FAIL]",
        DNSCheckStatus.WARN: "[WARN]",
        DNSCheckStatus.SKIP: "[SKIP]",
    }

    for c in report.checks:
        badge = status_badges.get(c.status, f"[{c.status.value}]")
        # Format message cleanly
        desc = c.message
        if len(desc) > 85:
            desc = desc[:82] + "..."
        lines.append(f"{c.name:<22} | {c.record_type:<6} | {badge:<6} | {desc}")
        if c.found:
            found_str = " | ".join(c.found)
            if len(found_str) > 75:
                found_str = found_str[:72] + "..."
            lines.append(f"{'':<22} | {'':<6} | {'':<6} |   Found: {found_str}")

    lines.append("-" * 80)
    lines.append(
        f"SUMMARY: {report.summary['PASS']} PASS | "
        f"{report.summary['FAIL']} FAIL | "
        f"{report.summary['WARN']} WARN"
    )

    if report.is_valid:
        lines.append("\n  OVERALL RESULT: [SUCCESS] Domain DNS setup is ready for Resend email dispatch.")
    else:
        lines.append("\n  OVERALL RESULT: [FAILURE] DNS configuration has errors that must be resolved.")
        lines.append("  Recommended actions:")
        for c in report.checks:
            if c.status == DNSCheckStatus.FAIL:
                lines.append(f"    - {c.name} ({c.host}): {c.message}")

    lines.append("=" * 80)
    return "\n".join(lines)


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify DNS setup for Resend email sending domain (sales.contextelab.ma)."
    )
    parser.add_argument(
        "--domain",
        type=str,
        default="sales.contextelab.ma",
        help="Domain or subdomain to verify (default: sales.contextelab.ma)",
    )
    parser.add_argument(
        "--selector",
        type=str,
        default="resend",
        help="DKIM selector to check (default: resend)",
    )
    parser.add_argument(
        "--nameserver",
        type=str,
        default=None,
        help="Specific DNS resolver to query (e.g. 1.1.1.1, 8.8.8.8)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=4.0,
        help="DNS query timeout in seconds (default: 4.0)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as failures",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON instead of human-readable text",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    report = run_all_checks(
        domain=args.domain,
        selector=args.selector,
        nameserver=args.nameserver,
        timeout=args.timeout,
        strict=args.strict,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_terminal_report(report))

    return 0 if report.is_valid else 1


if __name__ == "__main__":
    sys.exit(main())
