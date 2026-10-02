import json
import pytest
import responses
from scripts.models import (
    NormalizedContact,
    CompanyRecord,
    EnrichmentStatusEnum,
    CampaignStatusEnum,
    EmailValidationStatusEnum,
)
from scripts.twenty_client import TwentyClient


@pytest.fixture
def client():
    return TwentyClient(base_url="http://localhost:3005", api_key="test-api-key")


@responses.activate
def test_upsert_company(client):
    responses.add(
        responses.POST,
        "http://localhost:3005/rest/companies",
        json={"data": {"createCompany": {"id": "comp-123"}}},
        status=201,
    )
    company = CompanyRecord(
        name="Atlas SARL",
        domain_name="atlas.ma",
        city="Casablanca",
        address="10 Boulevard Zerktouni",
        phone_standard="0522000000",
        source_file="BDD3",
    )
    company_id = client.upsert_company(company)
    assert company_id == "comp-123"
    assert len(responses.calls) == 1
    req_body = json.loads(responses.calls[0].request.body)
    assert req_body["name"] == "Atlas SARL"
    assert req_body["domainName"] == {
        "primaryLinkLabel": "atlas.ma",
        "primaryLinkUrl": "https://atlas.ma",
    }
    assert req_body["address"] == {
        "addressStreet1": "10 Boulevard Zerktouni",
        "addressCity": "Casablanca",
    }


@responses.activate
def test_upsert_company_deduplication(client):
    responses.add(
        responses.POST,
        "http://localhost:3005/rest/companies",
        json={"data": {"createCompany": {"id": "comp-123"}}},
        status=201,
    )
    company1 = CompanyRecord(name="Atlas SARL", source_file="BDD1")
    company2 = CompanyRecord(name="Atlas SARL", source_file="BDD2")

    comp_id1 = client.upsert_company(company1)
    comp_id2 = client.upsert_company(company2)

    assert comp_id1 == "comp-123"
    assert comp_id2 == "comp-123"
    # Deduplication should ensure only 1 POST call was made
    assert len(responses.calls) == 1


@responses.activate
def test_upsert_person_with_clean_email(client):
    responses.add(
        responses.POST,
        "http://localhost:3005/rest/people",
        json={"data": {"createPerson": {"id": "person-123"}}},
        status=201,
    )
    contact = NormalizedContact(
        company_name="Atlas SARL",
        first_name="Hassan",
        last_name="Tahiri",
        job_title="DG",
        is_dirigeant=True,
        email="hassan@atlas.ma",
        phone_mobile="0661000000",
        source_file="BDD3",
        enrichment_status=EnrichmentStatusEnum.COMPLET,
        email_validation_status=EmailValidationStatusEnum.DELIVRABLE,
    )
    person_id = client.upsert_person(contact, company_id="comp-1")
    assert person_id == "person-123"

    assert len(responses.calls) == 1
    req_body = json.loads(responses.calls[0].request.body)
    assert req_body["name"]["firstName"] == "Hassan"
    assert req_body["name"]["lastName"] == "Tahiri"
    assert req_body["jobTitle"] == "DG"
    assert req_body["companyId"] == "comp-1"
    assert req_body["emails"] == {"primaryEmail": "hassan@atlas.ma"}
    assert req_body["phones"] == {
        "primaryPhoneNumber": "0661000000",
        "primaryPhoneCountryCode": "MA",
        "primaryPhoneCallingCode": "+212",
    }
    assert req_body["campaignStatus"] == CampaignStatusEnum.READY_TO_MAIL.value
    assert req_body["enrichmentStatus"] == EnrichmentStatusEnum.COMPLET.value


@responses.activate
def test_upsert_person_purged_for_enrichment(client):
    responses.add(
        responses.POST,
        "http://localhost:3005/rest/people",
        json={"data": {"createPerson": {"id": "person-456"}}},
        status=201,
    )
    contact = NormalizedContact(
        company_name="Atlas SARL",
        first_name="Hassan",
        last_name="Tahiri",
        job_title="DG",
        is_dirigeant=True,
        email=None,  # Purged
        source_file="BDD1",
        enrichment_status=EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE,
        email_validation_status=EmailValidationStatusEnum.INVALID,
    )
    person_id = client.upsert_person(contact, company_id="comp-1")
    assert person_id == "person-456"

    assert len(responses.calls) == 1
    req_body = json.loads(responses.calls[0].request.body)
    assert req_body["name"]["firstName"] == "Hassan"
    assert req_body["emails"] is None
    assert req_body.get("campaignStatus") is None
    assert req_body["enrichmentStatus"] == EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE.value


@responses.activate
def test_purge_bounced_email(client):
    responses.add(
        responses.PATCH,
        "http://localhost:3005/rest/people/person-999",
        json={"data": {"updatePerson": {"id": "person-999"}}},
        status=200,
    )
    result = client.purge_bounced_email(person_id="person-999")
    assert len(responses.calls) == 1
    req_body = json.loads(responses.calls[0].request.body)
    assert req_body["emails"] is None
    assert req_body["enrichmentStatus"] == EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE.value
    assert req_body["campaignStatus"] == CampaignStatusEnum.BOUNCED.value
    assert result.get("data", {}).get("updatePerson", {}).get("id") == "person-999"


@responses.activate
def test_update_campaign_status(client):
    responses.add(
        responses.PATCH,
        "http://localhost:3005/rest/people/person-888",
        json={"data": {"updatePerson": {"id": "person-888"}}},
        status=200,
    )
    result = client.update_campaign_status(
        person_id="person-888",
        status=CampaignStatusEnum.EMAIL_DELIVERED,
    )
    assert len(responses.calls) == 1
    req_body = json.loads(responses.calls[0].request.body)
    assert req_body["campaignStatus"] == CampaignStatusEnum.EMAIL_DELIVERED.value
    assert result.get("data", {}).get("updatePerson", {}).get("id") == "person-888"


@responses.activate
def test_batch_sync(client):
    # Mock company creation (only called once due to deduplication)
    responses.add(
        responses.POST,
        "http://localhost:3005/rest/companies",
        json={"data": {"createCompany": {"id": "comp-777"}}},
        status=201,
    )
    # Mock person creation 1
    responses.add(
        responses.POST,
        "http://localhost:3005/rest/people",
        json={"data": {"createPerson": {"id": "person-1"}}},
        status=201,
    )
    # Mock person creation 2
    responses.add(
        responses.POST,
        "http://localhost:3005/rest/people",
        json={"data": {"createPerson": {"id": "person-2"}}},
        status=201,
    )

    contacts = [
        NormalizedContact(
            company_name="MegaCorp",
            first_name="Alice",
            last_name="Smith",
            job_title="CEO",
            email="alice@megacorp.com",
            source_file="BDD3",
            enrichment_status=EnrichmentStatusEnum.COMPLET,
            email_validation_status=EmailValidationStatusEnum.DELIVRABLE,
        ),
        NormalizedContact(
            company_name="MegaCorp",
            first_name="Bob",
            last_name="Jones",
            job_title="CTO",
            email=None,
            source_file="BDD1",
            enrichment_status=EnrichmentStatusEnum.A_ENRICHIR_EMAIL_MANQUANT,
            email_validation_status=EmailValidationStatusEnum.MISSING,
        ),
    ]

    summary = client.batch_sync(contacts)
    assert summary["synced"] == 2
    assert summary["companies_count"] == 1
    assert summary["people_count"] == 2
    assert summary["people"] == ["person-1", "person-2"]
    assert len(summary["errors"]) == 0
    # 1 company POST + 2 people POST = 3 API calls
    assert len(responses.calls) == 3


def test_client_headers():
    c_no_key = TwentyClient(base_url="http://localhost:3005")
    assert "Authorization" not in c_no_key.headers

    c_with_key = TwentyClient(base_url="http://localhost:3005", api_key="secret-token")
    assert c_with_key.headers["Authorization"] == "Bearer secret-token"


@responses.activate
def test_batch_sync_without_company_name(client):
    responses.add(
        responses.POST,
        "http://localhost:3005/rest/people",
        json={"data": {"createPerson": {"id": "person-no-comp"}}},
        status=201,
    )
    contacts = [
        NormalizedContact(
            company_name="",
            first_name="Solo",
            last_name="Entrepreneur",
            email="solo@freelance.ma",
            source_file="BDD2",
            enrichment_status=EnrichmentStatusEnum.COMPLET,
            email_validation_status=EmailValidationStatusEnum.DELIVRABLE,
        )
    ]
    summary = client.batch_sync(contacts)
    assert summary["synced"] == 1
    assert summary["companies_count"] == 0
    assert summary["people"] == ["person-no-comp"]

    req_body = json.loads(responses.calls[0].request.body)
    assert req_body["companyId"] is None


@responses.activate
def test_batch_sync_error_handling(client):
    responses.add(
        responses.POST,
        "http://localhost:3005/rest/people",
        json={"error": "Bad Request"},
        status=400,
    )
    contacts = [
        NormalizedContact(
            company_name="",
            first_name="Fail",
            last_name="Case",
            email="fail@error.ma",
            source_file="BDD2",
        )
    ]
    # Default raises
    with pytest.raises(Exception):
        client.batch_sync(contacts, continue_on_error=False)

    # continue_on_error collects errors
    summary = client.batch_sync(contacts, continue_on_error=True)
    assert summary["synced"] == 0
    assert len(summary["errors"]) == 1
    assert "400" in summary["errors"][0]["error"]
