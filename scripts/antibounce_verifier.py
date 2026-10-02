import logging
import re
from typing import Dict, List, Optional, Tuple
import dns.resolver
import requests

from scripts.models import (
    NormalizedContact,
    EnrichmentStatusEnum,
    EmailValidationStatusEnum,
)

logger = logging.getLogger(__name__)

# Strict RFC 5322-compliant pattern allowing standard mailbox characters
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+$"
)

# Known disposable and temporary email domains
DISPOSABLE_DOMAINS = {
    "mailinator.com",
    "yopmail.com",
    "yopmail.fr",
    "yopmail.net",
    "tempmail.com",
    "temp-mail.org",
    "10minutemail.com",
    "10minutemail.net",
    "guerrillamail.com",
    "guerrillamailblock.com",
    "guerrillamail.biz",
    "guerrillamail.org",
    "sharklasers.com",
    "grr.la",
    "throwaway.email",
    "throwawaymail.com",
    "trashmail.com",
    "trashmail.net",
    "trashmail.me",
    "dispostable.com",
    "getairmail.com",
    "fakeinbox.com",
    "mohmal.com",
    "maildrop.cc",
    "inboxkitten.com",
    "burnermail.io",
    "crazymailing.com",
    "mailnesia.com",
    "tempr.email",
    "discard.email",
    "dropmail.me",
    "mytemp.email",
    "nada.ltd",
    "getnada.com",
}

# Cache mapping lowercase domain -> bool (True if MX records exist)
MX_CACHE: Dict[str, bool] = {}


def clear_mx_cache() -> None:
    """Clear cached MX resolution results."""
    MX_CACHE.clear()


def validate_email_syntax(email: Optional[str]) -> bool:
    """
    Validate RFC 5322 syntax for an email address.
    Rejects missing emails, whitespace-only, consecutive dots,
    missing domain/TLD, and malformed characters.
    """
    if not email or not isinstance(email, str):
        return False
    clean = email.strip()
    if not clean:
        return False
    if ".." in clean:
        return False
    if clean.startswith(".") or clean.endswith("."):
        return False
    if "@" not in clean:
        return False

    parts = clean.split("@")
    if len(parts) != 2:
        return False

    local_part, domain_part = parts
    if not local_part or not domain_part:
        return False
    if local_part.startswith(".") or local_part.endswith("."):
        return False
    if domain_part.startswith(".") or domain_part.endswith(".") or domain_part.startswith("-") or domain_part.endswith("-"):
        return False

    domain_labels = domain_part.split(".")
    if len(domain_labels) < 2:
        return False
    tld = domain_labels[-1]
    if len(tld) < 2 or not tld.isalpha():
        return False

    return bool(EMAIL_REGEX.match(clean))


def is_disposable_domain(domain: Optional[str]) -> bool:
    """Check if the domain belongs to a known temporary or disposable email provider."""
    if not domain or not isinstance(domain, str):
        return False
    clean_domain = domain.strip().lower()
    return clean_domain in DISPOSABLE_DOMAINS


def check_dns_mx(domain: Optional[str], timeout: float = 3.0) -> bool:
    """
    Check DNS MX records for the given domain with memory caching.
    Returns True if valid mail exchange servers exist, False otherwise.
    """
    if not domain or not isinstance(domain, str):
        return False
    clean_domain = domain.lower().strip()
    if not clean_domain:
        return False

    if clean_domain in MX_CACHE:
        return MX_CACHE[clean_domain]

    try:
        answers = dns.resolver.resolve(clean_domain, "MX", lifetime=timeout)
        has_mx = len(answers) > 0
        MX_CACHE[clean_domain] = has_mx
        return has_mx
    except Exception:
        MX_CACHE[clean_domain] = False
        return False


def process_antibounce_pass1(
    contacts: List[NormalizedContact],
    purge_duplicates: bool = True,
) -> List[NormalizedContact]:
    """
    Execute Pass 1 (local hygiene):
    1. Check for missing emails -> mark MISSING & A_ENRICHIR_EMAIL_MANQUANT.
    2. Check RFC syntax -> purge email (None), mark INVALID & A_ENRICHIR_EMAIL_INVALIDE.
    3. Global deduplication -> first contact keeps email; duplicates are purged to prevent duplicate campaigns.
    4. Disposable email blacklist -> purge email (None), mark INVALID & A_ENRICHIR_EMAIL_INVALIDE.
    5. DNS MX record validation -> purge email (None), mark INVALID & A_ENRICHIR_EMAIL_INVALIDE.
    6. Deliverable emails keep email set, status DELIVRABLE & COMPLET.
    """
    # Pre-warm DNS MX cache concurrently for unique valid domains
    unique_domains = set()
    for c in contacts:
        if c.raw_email:
            em = c.raw_email.strip().lower()
            if validate_email_syntax(em):
                dom = em.split("@")[-1]
                if not is_disposable_domain(dom) and dom not in MX_CACHE:
                    unique_domains.add(dom)

    if unique_domains:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=30) as executor:
            list(executor.map(check_dns_mx, unique_domains))

    seen_emails = set()
    output: List[NormalizedContact] = []

    for c in contacts:
        if not c.raw_email:
            c.email = None
            c.enrichment_status = EnrichmentStatusEnum.A_ENRICHIR_EMAIL_MANQUANT
            c.email_validation_status = EmailValidationStatusEnum.MISSING
            output.append(c)
            continue

        clean_email = c.raw_email.strip().lower()

        # 1. Syntax check
        if not validate_email_syntax(clean_email):
            c.email = None
            c.enrichment_status = EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE
            c.email_validation_status = EmailValidationStatusEnum.INVALID
            output.append(c)
            continue

        # 2. Deduplication check
        if clean_email in seen_emails:
            if purge_duplicates:
                c.email = None
                c.enrichment_status = EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE
                c.email_validation_status = EmailValidationStatusEnum.INVALID
                output.append(c)
            continue
        seen_emails.add(clean_email)

        # 3. Disposable domain check
        domain = clean_email.split("@")[-1]
        if is_disposable_domain(domain):
            c.email = None
            c.enrichment_status = EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE
            c.email_validation_status = EmailValidationStatusEnum.INVALID
            output.append(c)
            continue

        # 4. DNS MX record check
        if not check_dns_mx(domain):
            c.email = None
            c.enrichment_status = EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE
            c.email_validation_status = EmailValidationStatusEnum.INVALID
            output.append(c)
            continue

        # Passed all Pass 1 checks
        c.email = clean_email
        c.email_validation_status = EmailValidationStatusEnum.DELIVRABLE
        c.enrichment_status = EnrichmentStatusEnum.COMPLET
        output.append(c)

    return output


def validate_email_zerobounce(
    email: str,
    api_key: str,
    timeout: float = 5.0,
) -> Tuple[bool, str]:
    """
    Verify an email address via ZeroBounce API.
    Returns (is_deliverable, reason).
    """
    url = "https://api.zerobounce.net/v2/validate"
    params = {"api_key": api_key, "email": email}
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        if resp.status_code != 200:
            return False, f"http_{resp.status_code}"
        data = resp.json()
        status = str(data.get("status", "")).lower()
        if status == "valid":
            return True, "valid"
        return False, status or "invalid"
    except Exception as exc:
        logger.warning("ZeroBounce validation request failed for %s: %s", email, exc)
        return False, "api_error"


def validate_email_emailable(
    email: str,
    api_key: str,
    timeout: float = 5.0,
) -> Tuple[bool, str]:
    """
    Verify an email address via Emailable API.
    Returns (is_deliverable, reason).
    """
    url = "https://api.emailable.com/v1/verify"
    params = {"api_key": api_key, "email": email}
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        if resp.status_code != 200:
            return False, f"http_{resp.status_code}"
        data = resp.json()
        state = str(data.get("state", "")).lower()
        if state == "deliverable":
            return True, "deliverable"
        return False, state or "undeliverable"
    except Exception as exc:
        logger.warning("Emailable validation request failed for %s: %s", email, exc)
        return False, "api_error"


def validate_email_api(
    email: str,
    api_key: str,
    service: str = "zerobounce",
    timeout: float = 5.0,
) -> Tuple[bool, str]:
    """
    Dispatcher for external verification API (ZeroBounce / Emailable).
    Returns (is_deliverable, reason).
    """
    service_clean = service.lower().strip()
    if service_clean == "zerobounce":
        return validate_email_zerobounce(email, api_key, timeout=timeout)
    elif service_clean == "emailable":
        return validate_email_emailable(email, api_key, timeout=timeout)
    else:
        raise ValueError(f"Unsupported email validation service: {service}")


def process_antibounce_pass2(
    contacts: List[NormalizedContact],
    api_key: str,
    service: str = "zerobounce",
    timeout: float = 5.0,
) -> List[NormalizedContact]:
    """
    Execute Pass 2 (external validation API):
    Only contacts that survived Pass 1 (having c.email != None) are checked.
    Invalid, spam trap, catch-all, and abusive emails are purged (c.email = None),
    with email_validation_status set to INVALID or BOUNCED and
    enrichment_status set to A_ENRICHIR_EMAIL_INVALIDE.
    """
    for c in contacts:
        if not c.email:
            continue

        is_valid, reason = validate_email_api(
            c.email,
            api_key=api_key,
            service=service,
            timeout=timeout,
        )

        if is_valid:
            c.email_validation_status = EmailValidationStatusEnum.DELIVRABLE
            c.enrichment_status = EnrichmentStatusEnum.COMPLET
        else:
            c.email = None
            if any(term in reason for term in ("bounce", "spamtrap", "abuse", "undeliverable")):
                c.email_validation_status = EmailValidationStatusEnum.BOUNCED
            else:
                c.email_validation_status = EmailValidationStatusEnum.INVALID
            c.enrichment_status = EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE

    return contacts


def verify_contact_list(
    contacts: List[NormalizedContact],
    api_key: Optional[str] = None,
    service: str = "zerobounce",
    purge_duplicates: bool = True,
) -> Tuple[List[NormalizedContact], List[NormalizedContact]]:
    """
    Two-pass anti-bounce verification engine:
    1. Pass 1: Local hygiene (syntax, deduplication, disposable blacklist, DNS MX resolution).
    2. Pass 2: External API verification (ZeroBounce / Emailable) if api_key is supplied.

    Returns:
    (valid_for_mailing, purged_to_enrich)
    - valid_for_mailing: Contacts with validated emails ready for campaign dispatch.
    - purged_to_enrich: Contacts with emails purged (email is None) destined for Twenty CRM enrichment.
    """
    processed = process_antibounce_pass1(contacts, purge_duplicates=purge_duplicates)

    if api_key:
        processed = process_antibounce_pass2(
            processed,
            api_key=api_key,
            service=service,
        )

    valid_for_mailing: List[NormalizedContact] = []
    purged_to_enrich: List[NormalizedContact] = []

    for c in processed:
        if c.email and c.email_validation_status == EmailValidationStatusEnum.DELIVRABLE:
            valid_for_mailing.append(c)
        else:
            purged_to_enrich.append(c)

    return valid_for_mailing, purged_to_enrich
