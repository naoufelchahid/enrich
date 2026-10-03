import pytest
import responses
from scripts.apify_harvest_client import ApifyHarvestClient

@responses.activate
def test_enrich_profile_via_apify_api():
    responses.add(
        responses.POST,
        "https://api.apify.com/v2/acts/harvest~linkedin-enrichment/run-sync-get-dataset-items",
        json=[
            {
                "jobTitle": "Directeur Général",
                "currentJobStartDate": "2026-01-15",
                "companyName": "Nouvelle Entreprise SAS",
                "companyWebsite": "https://nouvelle-entreprise.ma",
                "companyIndustry": "Technologies",
                "pastCompanies": ["Ancienne SARL"],
                "recentActivity": ["Post sur l'IA appliquée aux banques"],
            }
        ],
        status=200,
    )
    client = ApifyHarvestClient(api_token="apify_token_test")
    res = client.enrich_profile(linkedin_url="https://linkedin.com/in/mohammed-drissi")
    assert res["current_job_title"] == "Directeur Général"
    assert res["current_company_name"] == "Nouvelle Entreprise SAS"
    assert "Ancienne SARL" in res["past_companies"]

def test_enrich_profile_dry_run():
    client = ApifyHarvestClient(dry_run=True)
    res = client.enrich_profile(full_name="Mehdi Bennani", company="Contexte Lab")
    assert res["current_job_title"] is not None
    assert res["current_company_name"] == "Contexte Lab"

@responses.activate
def test_enrich_profile_api_failure():
    responses.add(
        responses.POST,
        "https://api.apify.com/v2/acts/harvest~linkedin-enrichment/run-sync-get-dataset-items",
        status=500,
    )
    client = ApifyHarvestClient(api_token="apify_token_test")
    res = client.enrich_profile(company="Fallback Inc")
    assert res["current_job_title"] == ""
    assert res["current_company_name"] == "Fallback Inc"
    assert res["past_companies"] == []

@responses.activate
def test_enrich_profile_payload_cleanliness():
    import json

    def request_callback(request):
        body = json.loads(request.body)
        assert "city" not in body
        assert "fullName" not in body
        assert body.get("company") == "Tech SA"
        return (200, {}, json.dumps([{"companyName": "Tech SA"}]))

    responses.add_callback(
        responses.POST,
        "https://api.apify.com/v2/acts/harvest~linkedin-enrichment/run-sync-get-dataset-items",
        callback=request_callback,
        content_type="application/json",
    )
    client = ApifyHarvestClient(api_token="apify_token_test")
    res = client.enrich_profile(company="Tech SA")
    assert res["current_company_name"] == "Tech SA"

