import pytest
from unittest.mock import MagicMock, patch
import sys
import os

# Import main from enrich_companies
from enrich_companies import main

@patch("enrich_companies.KerixScraper")
@patch("enrich_companies.TwentyCRMClient")
@patch("enrich_companies.ensure_twenty_fields")
def test_enrich_companies_success(mock_ensure, mock_crm_class, mock_scraper_class, monkeypatch):
    # Set env vars so it doesn't fail early
    monkeypatch.setenv("TWENTY_API_KEY", "test_api_key")
    monkeypatch.setenv("CLOAKBROWSER_LICENSE_KEY", "test_license")
    
    mock_crm = MagicMock()
    mock_crm_class.return_value = mock_crm
    
    mock_scraper = MagicMock()
    mock_scraper_class.return_value = mock_scraper
    
    # Mock data
    mock_crm.list_companies.return_value = [
        {"id": "c1", "name": "Test Company SA"}
    ]
    
    # Mock scrape result
    mock_scraper.scrape_company.return_value = {
        "kerix_url": "https://kerix.net/test-company",
        "ice": "123456789012345",
        "rc": "99999",
        "address": "123 Test St",
        "city": "Casablanca",
        "phone": "0600000000",
        "dirigeant": "John Doe (CEO)"
    }
    
    # Mock sys.argv
    test_args = ["enrich_companies.py", "--limit", "1"]
    with patch.object(sys, 'argv', test_args):
        main()
        
    # Assert CRM was called with the correct flat fields
    mock_crm.update_company.assert_called_once()
    args, kwargs = mock_crm.update_company.call_args
    company_id = args[0]
    payload = args[1]
    
    assert company_id == "c1"
    assert payload["enrichmentStatus"] == "company_enriched"
    # Check flat payload fields we introduced in the refactor
    assert payload["profile_label"] == "Kerix Profile"
    assert payload["profile_url"] == "https://kerix.net/test-company"
    assert payload["address_street"] == "123 Test St"
    assert payload["address_city"] == "Casablanca"
    assert payload["ice"] == "123456789012345"
    assert payload["rc"] == "99999"
    assert payload["phone"] == "0600000000"

    # Verify person creation (John Doe)
    mock_crm.create_person.assert_called_once()
    person_payload = mock_crm.create_person.call_args[0][0]
    assert person_payload["name"]["firstName"] == "John Doe"
    assert person_payload["jobTitle"] == "CEO"
    assert person_payload["companyId"] == "c1"

@patch("enrich_companies.KerixScraper")
@patch("enrich_companies.TwentyCRMClient")
@patch("enrich_companies.ensure_twenty_fields")
def test_enrich_companies_no_results(mock_ensure, mock_crm_class, mock_scraper_class, monkeypatch):
    monkeypatch.setenv("TWENTY_API_KEY", "test_api_key")
    monkeypatch.setenv("CLOAKBROWSER_LICENSE_KEY", "test_license")
    
    mock_crm = MagicMock()
    mock_crm_class.return_value = mock_crm
    
    mock_scraper = MagicMock()
    mock_scraper_class.return_value = mock_scraper
    
    mock_crm.list_companies.return_value = [
        {"id": "c2", "name": "Ghost Company"}
    ]
    
    # Mock empty scrape result
    mock_scraper.scrape_company.return_value = {
        "kerix_url": None,
        "telecontact_url": None,
        "charika_url": None
    }
    
    test_args = ["enrich_companies.py", "--limit", "1"]
    with patch.object(sys, 'argv', test_args):
        main()
        
    # Should update status to failed
    mock_crm.update_company.assert_called_once_with("c2", {"enrichmentStatus": "failed"})
