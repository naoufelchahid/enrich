import pytest
from scripts.models import (
    NormalizedContact,
    CompanyRecord,
    EnrichmentStatusEnum,
    CampaignStatusEnum,
    EmailValidationStatusEnum,
)

def test_normalized_contact_valid():
    contact = NormalizedContact(
        company_name="Contexte Lab",
        activity_sector="Conseil IA",
        website="https://www.contextelab.ma",
        city="Casablanca",
        address="82 bd Abdelmoumen",
        phone_standard="0522000000",
        phone_mobile="+212661000000",
        civility="M.",
        first_name="Naoufel",
        last_name="Chahid",
        job_title="Directeur Général",
        is_dirigeant=True,
        raw_email="n.chahid@contextelab.ma",
        email="n.chahid@contextelab.ma",
        email_validation_status=EmailValidationStatusEnum.DELIVRABLE,
        source_file="BDD1",
        enrichment_status=EnrichmentStatusEnum.COMPLET,
        missing_fields=[],
    )
    assert contact.is_dirigeant is True
    assert contact.email == "n.chahid@contextelab.ma"

def test_contact_purged_email_for_enrichment():
    contact = NormalizedContact(
        company_name="Test SARL",
        activity_sector="BTP",
        city="Casablanca",
        first_name="Karim",
        last_name="Bennani",
        job_title="Gérant",
        is_dirigeant=True,
        raw_email="invalid-bounce@test.ma",
        email=None,
        email_validation_status=EmailValidationStatusEnum.BOUNCED,
        source_file="BDD2",
        enrichment_status=EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE,
        missing_fields=["email"],
    )
    assert contact.email is None
    assert contact.enrichment_status == EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE

def test_company_record_valid():
    company = CompanyRecord(
        name="Contexte Lab",
        activity_sector="Conseil IA",
        domain_name="contextelab.ma",
        city="Casablanca",
        address="82 bd Abdelmoumen",
        phone_standard="0522000000",
        source_file="BDD1",
    )
    assert company.name == "Contexte Lab"
    assert company.domain_name == "contextelab.ma"

