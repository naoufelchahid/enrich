import pytest
import responses
from scripts.twenty_client import TwentyClient

@responses.activate
def test_sync_career_change_creates_new_company_and_updates_person():
    client = TwentyClient(base_url="http://localhost:3005", api_key="test_key")

    # Mock GET person: currently at "Old Corp" (companyId: comp-1)
    responses.add(
        responses.GET,
        "http://localhost:3005/rest/people/person-100",
        json={
            "data": {
                "id": "person-100",
                "jobTitle": "Chef de Projet",
                "companyId": "comp-1",
                "company": {"name": "Old Corp"},
            }
        },
        status=200,
    )

    # Mock GET companies search for "New Corp": returns empty (new company doesn't exist yet)
    responses.add(
        responses.GET,
        "http://localhost:3005/rest/companies?filter=%7B%22name%22%3A%7B%22eq%22%3A%22New+Corp%22%7D%7D",
        json={"data": {"companies": []}},
        status=200,
    )

    # Mock POST company create for "New Corp"
    responses.add(
        responses.POST,
        "http://localhost:3005/rest/companies",
        json={"data": {"id": "comp-2", "name": "New Corp"}},
        status=201,
    )

    # Mock PATCH person update
    responses.add(
        responses.PATCH,
        "http://localhost:3005/rest/people/person-100",
        json={"data": {"id": "person-100", "companyId": "comp-2", "jobTitle": "Directeur Technique"}},
        status=200,
    )

    harvest_data = {
        "current_company_name": "New Corp",
        "current_job_title": "Directeur Technique",
        "company_website": "https://newcorp.ma",
        "company_industry": "SaaS B2B",
    }

    result = client.sync_career_change("person-100", harvest_data)
    assert result["career_changed"] is True
    assert result["old_company"] == "Old Corp"
    assert result["new_company"] == "New Corp"
    assert result["new_company_id"] == "comp-2"

@responses.activate
def test_sync_career_change_no_change_when_company_same():
    client = TwentyClient(base_url="http://localhost:3005", api_key="test_key")

    responses.add(
        responses.GET,
        "http://localhost:3005/rest/people/person-200",
        json={
            "data": {
                "id": "person-200",
                "jobTitle": "Directeur Commercial",
                "company": {"name": "Existing Corp"},
            }
        },
        status=200,
    )

    harvest_data = {
        "current_company_name": "Existing Corp",
        "current_job_title": "Directeur Commercial Senior",
    }

    result = client.sync_career_change("person-200", harvest_data)
    assert result["career_changed"] is False
    assert result["old_company"] == "Existing Corp"
    assert result["new_company"] == "Existing Corp"

@responses.activate
def test_create_opportunity():
    client = TwentyClient(base_url="http://localhost:3005", api_key="test_key")

    responses.add(
        responses.POST,
        "http://localhost:3005/rest/opportunities",
        json={"data": {"id": "opp-123", "name": "Nouveau Projet CRM"}},
        status=201,
    )

    opp_id = client.create_opportunity(
        name="Nouveau Projet CRM",
        stage="QUALIFIED",
        person_id="person-100",
        company_id="comp-2",
        amount=50000.0,
    )
    assert opp_id == "opp-123"

@responses.activate
def test_find_company_by_name_caching():
    client = TwentyClient(base_url="http://localhost:3005", api_key="test_key")

    responses.add(
        responses.GET,
        "http://localhost:3005/rest/companies?filter=%7B%22name%22%3A%7B%22eq%22%3A%22Alpha+Corp%22%7D%7D",
        json={"data": {"companies": [{"id": "alpha-1"}]}},
        status=200,
    )

    cid1 = client.find_company_by_name("Alpha Corp")
    assert cid1 == "alpha-1"

    # Second call should hit in-memory cache without an additional HTTP call
    cid2 = client.find_company_by_name("alpha corp")
    assert cid2 == "alpha-1"
