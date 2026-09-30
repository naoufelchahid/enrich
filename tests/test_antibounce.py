from unittest.mock import MagicMock, patch
import pytest

from scripts.antibounce_verifier import (
    validate_email_syntax,
    is_disposable_domain,
    check_dns_mx,
    clear_mx_cache,
    process_antibounce_pass1,
    validate_email_zerobounce,
    validate_email_emailable,
    validate_email_api,
    process_antibounce_pass2,
    verify_contact_list,
    MX_CACHE,
    DISPOSABLE_DOMAINS,
)
from scripts.models import (
    NormalizedContact,
    EnrichmentStatusEnum,
    EmailValidationStatusEnum,
)


def test_validate_email_syntax():
    # Valid syntax
    assert validate_email_syntax("chahid.naoufel@contextelab.ma") is True
    assert validate_email_syntax("contact@company.com") is True
    assert validate_email_syntax("dirigeant.associe+tag@morocco-domain.org") is True
    assert validate_email_syntax("prenom_nom-123@sub.domain.ma") is True

    # Invalid syntax from brief
    assert validate_email_syntax("invalid..syntax@domain.com") is False
    assert validate_email_syntax("noatsign.com") is False
    assert validate_email_syntax("") is False
    assert validate_email_syntax(None) is False

    # Additional invalid syntax edge cases
    assert validate_email_syntax("   ") is False
    assert validate_email_syntax("user@domain") is False
    assert validate_email_syntax(".user@domain.com") is False
    assert validate_email_syntax("user.@domain.com") is False
    assert validate_email_syntax("user@domain..com") is False
    assert validate_email_syntax("user@domain.c") is False
    assert validate_email_syntax("user@.domain.com") is False
    assert validate_email_syntax("user@domain.com.") is False
    assert validate_email_syntax("two@at@domain.com") is False
    assert validate_email_syntax("spaces in@domain.com") is False


def test_is_disposable_domain():
    # Known disposable domains must be flagged
    assert is_disposable_domain("mailinator.com") is True
    assert is_disposable_domain("yopmail.com") is True
    assert is_disposable_domain("tempmail.com") is True
    assert is_disposable_domain("10minutemail.com") is True
    assert is_disposable_domain("guerrillamail.com") is True
    assert is_disposable_domain("trashmail.com") is True
    assert is_disposable_domain("sharklasers.com") is True
    assert is_disposable_domain("YOPMAIL.COM") is True  # case insensitive

    # Legitimate domains must pass
    assert is_disposable_domain("contextelab.ma") is False
    assert is_disposable_domain("google.com") is False
    assert is_disposable_domain("gmail.com") is False
    assert is_disposable_domain("menara.ma") is False
    assert is_disposable_domain("wanadoo.net.ma") is False
    assert is_disposable_domain("") is False
    assert is_disposable_domain(None) is False


def test_check_dns_mx():
    clear_mx_cache()
    # google.com should have MX
    assert check_dns_mx("google.com") is True
    # Non-existent domain should return False
    assert check_dns_mx("non-existent-domain-xyz-12345678.ma") is False
    # None or empty
    assert check_dns_mx(None) is False
    assert check_dns_mx("") is False


def test_check_dns_mx_caching():
    clear_mx_cache()
    with patch("dns.resolver.resolve") as mock_resolve:
        mock_resolve.return_value = ["mock_mx_answer"]
        first_call = check_dns_mx("cached-domain.com")
        assert first_call is True
        assert mock_resolve.call_count == 1

        # Second call must hit cache and NOT invoke resolver
        second_call = check_dns_mx("cached-domain.com")
        assert second_call is True
        assert mock_resolve.call_count == 1


def test_process_antibounce_purges_invalid():
    # Direct test from brief
    bad_contact = NormalizedContact(
        company_name="Fictive",
        first_name="Ali",
        last_name="Test",
        raw_email="ali@non-existent-domain-xyz-12345678.ma",
        email="ali@non-existent-domain-xyz-12345678.ma",
        source_file="BDD1",
        is_dirigeant=True,
    )
    processed = process_antibounce_pass1([bad_contact])[0]
    # Invalid MX must purge email and set A_ENRICHIR_EMAIL_INVALIDE
    assert processed.email is None
    assert processed.enrichment_status == EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE
    assert processed.email_validation_status == EmailValidationStatusEnum.INVALID


def test_process_antibounce_purges_syntax_error():
    syntax_error_contact = NormalizedContact(
        company_name="Bad Syntax Corp",
        first_name="Omar",
        last_name="Syntax",
        raw_email="omar..syntax@domain.com",
        email="omar..syntax@domain.com",
        source_file="BDD2",
        is_dirigeant=True,
    )
    processed = process_antibounce_pass1([syntax_error_contact])[0]
    assert processed.email is None
    assert processed.enrichment_status == EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE
    assert processed.email_validation_status == EmailValidationStatusEnum.INVALID


def test_process_antibounce_purges_disposable_domain():
    disposable_contact = NormalizedContact(
        company_name="Temp Corp",
        first_name="Sara",
        last_name="Temp",
        raw_email="sara@mailinator.com",
        email="sara@mailinator.com",
        source_file="BDD3",
        is_dirigeant=True,
    )
    processed = process_antibounce_pass1([disposable_contact])[0]
    assert processed.email is None
    assert processed.enrichment_status == EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE
    assert processed.email_validation_status == EmailValidationStatusEnum.INVALID


def test_process_antibounce_missing_email():
    missing_contact = NormalizedContact(
        company_name="No Email SA",
        first_name="Karim",
        last_name="Bennani",
        raw_email=None,
        email=None,
        source_file="BDD1",
        is_dirigeant=True,
    )
    processed = process_antibounce_pass1([missing_contact])[0]
    assert processed.email is None
    assert processed.enrichment_status == EnrichmentStatusEnum.A_ENRICHIR_EMAIL_MANQUANT
    assert processed.email_validation_status == EmailValidationStatusEnum.MISSING


def test_process_antibounce_valid_email():
    valid_contact = NormalizedContact(
        company_name="Contexte Lab",
        first_name="Naoufel",
        last_name="Chahid",
        raw_email="chahid.naoufel@gmail.com",
        email=None,
        source_file="BDD3",
        is_dirigeant=True,
    )
    with patch("scripts.antibounce_verifier.check_dns_mx", return_value=True):
        processed = process_antibounce_pass1([valid_contact])[0]
        assert processed.email == "chahid.naoufel@gmail.com"
        assert processed.email_validation_status == EmailValidationStatusEnum.DELIVRABLE
        assert processed.enrichment_status == EnrichmentStatusEnum.COMPLET


def test_process_antibounce_deduplication():
    c1 = NormalizedContact(
        company_name="Company Alpha",
        first_name="Ali",
        last_name="Berrada",
        raw_email="ali.berrada@company.ma",
        source_file="BDD1",
        is_dirigeant=True,
    )
    c2 = NormalizedContact(
        company_name="Company Alpha Bis",
        first_name="Ali",
        last_name="Berrada",
        raw_email="ali.berrada@company.ma",
        source_file="BDD2",
        is_dirigeant=True,
    )
    with patch("scripts.antibounce_verifier.check_dns_mx", return_value=True):
        processed = process_antibounce_pass1([c1, c2], purge_duplicates=True)
        assert len(processed) == 2
        # First occurrence is kept deliverable
        assert processed[0].email == "ali.berrada@company.ma"
        assert processed[0].email_validation_status == EmailValidationStatusEnum.DELIVRABLE
        # Second occurrence is purged to prevent duplicate mailing/bounces
        assert processed[1].email is None
        assert processed[1].email_validation_status == EmailValidationStatusEnum.INVALID
        assert processed[1].enrichment_status == EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE


@patch("requests.get")
def test_validate_email_zerobounce(mock_get):
    # Valid response
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"status": "valid", "sub_status": ""}
    is_valid, reason = validate_email_zerobounce("valid@domain.com", api_key="zb-test-key")
    assert is_valid is True
    assert reason == "valid"

    # Invalid response
    mock_get.return_value.json.return_value = {"status": "invalid", "sub_status": "mailbox_not_found"}
    is_valid, reason = validate_email_zerobounce("invalid@domain.com", api_key="zb-test-key")
    assert is_valid is False
    assert reason == "invalid"

    # Spam trap response
    mock_get.return_value.json.return_value = {"status": "spamtrap", "sub_status": ""}
    is_valid, reason = validate_email_zerobounce("trap@domain.com", api_key="zb-test-key")
    assert is_valid is False
    assert reason == "spamtrap"

    # Catch-all response
    mock_get.return_value.json.return_value = {"status": "catch-all", "sub_status": ""}
    is_valid, reason = validate_email_zerobounce("catchall@domain.com", api_key="zb-test-key")
    assert is_valid is False
    assert reason == "catch-all"


@patch("requests.get")
def test_validate_email_emailable(mock_get):
    # Deliverable response
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"state": "deliverable", "reason": "accepted_email"}
    is_valid, reason = validate_email_emailable("valid@domain.com", api_key="em-test-key")
    assert is_valid is True
    assert reason == "deliverable"

    # Undeliverable response
    mock_get.return_value.json.return_value = {"state": "undeliverable", "reason": "rejected_email"}
    is_valid, reason = validate_email_emailable("undeliverable@domain.com", api_key="em-test-key")
    assert is_valid is False
    assert reason == "undeliverable"

    # Risky response
    mock_get.return_value.json.return_value = {"state": "risky", "reason": "low_quality"}
    is_valid, reason = validate_email_emailable("risky@domain.com", api_key="em-test-key")
    assert is_valid is False
    assert reason == "risky"


def test_process_antibounce_pass2():
    c_valid = NormalizedContact(
        company_name="Valid Corp",
        first_name="Driss",
        last_name="Alaoui",
        email="driss@validcorp.ma",
        source_file="BDD3",
        is_dirigeant=True,
    )
    c_bounce = NormalizedContact(
        company_name="Bounce Corp",
        first_name="Leila",
        last_name="Mansouri",
        email="leila@bouncecorp.ma",
        source_file="BDD3",
        is_dirigeant=True,
    )
    c_already_purged = NormalizedContact(
        company_name="Purged Corp",
        first_name="Taha",
        last_name="Amrani",
        email=None,
        email_validation_status=EmailValidationStatusEnum.INVALID,
        enrichment_status=EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE,
        source_file="BDD1",
        is_dirigeant=True,
    )

    def mock_validate(email, api_key, service="zerobounce", timeout=5.0):
        if "valid" in email:
            return True, "valid"
        return False, "invalid"

    with patch("scripts.antibounce_verifier.validate_email_api", side_effect=mock_validate):
        processed = process_antibounce_pass2([c_valid, c_bounce, c_already_purged], api_key="fake-key")
        assert len(processed) == 3

        # Valid contact confirmed
        assert processed[0].email == "driss@validcorp.ma"
        assert processed[0].email_validation_status == EmailValidationStatusEnum.DELIVRABLE
        assert processed[0].enrichment_status == EnrichmentStatusEnum.COMPLET

        # Bounced contact purged in Pass 2
        assert processed[1].email is None
        assert processed[1].email_validation_status == EmailValidationStatusEnum.INVALID
        assert processed[1].enrichment_status == EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE

        # Pre-purged contact untouched
        assert processed[2].email is None
        assert processed[2].enrichment_status == EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE


def test_verify_contact_list_end_to_end():
    contacts = [
        # 1. Valid contact that passes Pass 1 & Pass 2
        NormalizedContact(
            company_name="Valid Corp",
            first_name="Adam",
            last_name="Fassi",
            raw_email="adam@validcorp.ma",
            source_file="BDD3",
            is_dirigeant=True,
        ),
        # 2. Syntax error
        NormalizedContact(
            company_name="Syntax Error Corp",
            first_name="Said",
            last_name="Bad",
            raw_email="said..bad@syntax.ma",
            source_file="BDD1",
            is_dirigeant=True,
        ),
        # 3. Missing email
        NormalizedContact(
            company_name="Missing Email Corp",
            first_name="Rachid",
            last_name="NoMail",
            raw_email=None,
            source_file="BDD1",
            is_dirigeant=True,
        ),
        # 4. Disposable domain
        NormalizedContact(
            company_name="Disposable Corp",
            first_name="Yassine",
            last_name="Junk",
            raw_email="yassine@mailinator.com",
            source_file="BDD2",
            is_dirigeant=True,
        ),
        # 5. Invalid MX domain
        NormalizedContact(
            company_name="No MX Corp",
            first_name="Hamza",
            last_name="Nomx",
            raw_email="hamza@no-mx-server-exists.ma",
            source_file="BDD4",
            is_dirigeant=True,
        ),
        # 6. Duplicate of contact 1
        NormalizedContact(
            company_name="Valid Corp Branch",
            first_name="Adam",
            last_name="Fassi",
            raw_email="adam@validcorp.ma",
            source_file="BDD5",
            is_dirigeant=True,
        ),
        # 7. Pass 1 OK but Pass 2 rejects
        NormalizedContact(
            company_name="Spamtrap Corp",
            first_name="Kenza",
            last_name="Trap",
            raw_email="kenza@trapped.ma",
            source_file="BDD6",
            is_dirigeant=True,
        ),
    ]

    def mock_dns_mx(domain):
        if domain in ("validcorp.ma", "trapped.ma"):
            return True
        return False

    def mock_api(email, api_key, service="zerobounce", timeout=5.0):
        if "adam@validcorp.ma" in email:
            return True, "valid"
        return False, "spamtrap"

    with patch("scripts.antibounce_verifier.check_dns_mx", side_effect=mock_dns_mx):
        with patch("scripts.antibounce_verifier.validate_email_api", side_effect=mock_api):
            valid_for_mailing, purged_to_enrich = verify_contact_list(contacts, api_key="test-api-key")

            # Assert exact segmentation
            assert len(valid_for_mailing) == 1
            assert valid_for_mailing[0].email == "adam@validcorp.ma"
            assert valid_for_mailing[0].email_validationStatus == EmailValidationStatusEnum.DELIVRABLE if hasattr(valid_for_mailing[0], "email_validationStatus") else valid_for_mailing[0].email_validation_status == EmailValidationStatusEnum.DELIVRABLE

            assert len(purged_to_enrich) == 6
            # Ensure every single purged contact has email == None
            for c in purged_to_enrich:
                assert c.email is None
                assert c.enrichment_status in (
                    EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE,
                    EnrichmentStatusEnum.A_ENRICHIR_EMAIL_MANQUANT,
                )

            # Ensure all 7 input contacts are preserved across the two buckets
            assert len(valid_for_mailing) + len(purged_to_enrich) == len(contacts)
