import pytest
import requests
from unittest.mock import patch, MagicMock
from enricher.crm_client import TwentyCRMClient

@patch('enricher.crm_client.requests.get')
def test_list_companies(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"companies": [{"id": "1", "name": "Test"}]}}
    mock_get.return_value = mock_resp

    client = TwentyCRMClient("http://fake", "fake-key")
    companies = client.list_companies(limit=1)
    
    assert len(companies) == 1
    assert companies[0]["name"] == "Test"
    mock_get.assert_called_once_with("http://fake/companies", headers=client.headers, params={"limit": 1})

@patch('enricher.crm_client.requests.get')
def test_list_companies_http_error(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mock_get.return_value = mock_resp

    client = TwentyCRMClient("http://fake", "fake-key")
    companies = client.list_companies()
    assert companies == []

@patch('enricher.crm_client.requests.get')
def test_list_companies_timeout(mock_get):
    mock_get.side_effect = requests.RequestException("Connection timeout")

    client = TwentyCRMClient("http://fake", "fake-key")
    companies = client.list_companies()
    assert companies == []

@patch('enricher.crm_client.requests.get')
def test_search_company_http_error(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = "Not Found"
    mock_get.return_value = mock_resp

    client = TwentyCRMClient("http://fake", "fake-key")
    result = client.search_company("Nonexistent")
    assert result is None

@patch('enricher.crm_client.requests.get')
def test_search_company_timeout(mock_get):
    mock_get.side_effect = requests.RequestException("Connection error")

    client = TwentyCRMClient("http://fake", "fake-key")
    result = client.search_company("Acme")
    assert result is None

@patch('enricher.crm_client.requests.post')
def test_create_company_http_error(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = "Bad Request"
    mock_post.return_value = mock_resp

    client = TwentyCRMClient("http://fake", "fake-key")
    result = client.create_company({"name": "Bad Co"})
    assert result is None

@patch('enricher.crm_client.requests.post')
def test_create_company_timeout(mock_post):
    mock_post.side_effect = requests.RequestException("Timeout")

    client = TwentyCRMClient("http://fake", "fake-key")
    result = client.create_company({"name": "Timeout Co"})
    assert result is None

def test_create_company_missing_name():
    client = TwentyCRMClient("http://fake", "fake-key")
    assert client.create_company({}) is None

@patch('enricher.crm_client.requests.post')
def test_create_person_http_error(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Server Error"
    mock_post.return_value = mock_resp

    client = TwentyCRMClient("http://fake", "fake-key")
    result = client.create_person({"name": {"firstName": "John"}})
    assert result is None

@patch('enricher.crm_client.requests.post')
def test_create_person_timeout(mock_post):
    mock_post.side_effect = requests.RequestException("Connection reset")

    client = TwentyCRMClient("http://fake", "fake-key")
    result = client.create_person({"name": {"firstName": "John"}})
    assert result is None

@patch('enricher.crm_client.requests.post')
def test_add_note_http_error(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Note creation failed"
    mock_post.return_value = mock_resp

    client = TwentyCRMClient("http://fake", "fake-key")
    res = client.add_note("comp_1", "Title", "Body")
    assert res is False

@patch('enricher.crm_client.requests.post')
def test_add_note_timeout(mock_post):
    mock_post.side_effect = requests.RequestException("Timeout")

    client = TwentyCRMClient("http://fake", "fake-key")
    res = client.add_note("comp_1", "Title", "Body")
    assert res is False

@patch.object(TwentyCRMClient, 'search_company')
@patch('enricher.crm_client.requests.patch')
def test_upsert_company_patch_error(mock_patch, mock_search):
    mock_search.return_value = {"id": "comp_123", "name": "Existing Co"}
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Update failed"
    mock_patch.return_value = mock_resp

    client = TwentyCRMClient("http://fake", "fake-key")
    res = client.upsert_company({"name": "Existing Co", "ice": "12345"})
    # Should fallback to returning existing record when patch fails
    assert res == {"id": "comp_123", "name": "Existing Co"}

def test_upsert_company_missing_name():
    client = TwentyCRMClient("http://fake", "fake-key")
    assert client.upsert_company({}) is None

@patch('enricher.setup_twenty_fields.requests.post')
@patch('enricher.setup_twenty_fields.requests.get')
def test_ensure_status_fields(mock_get, mock_post):
    from enricher.setup_twenty_fields import ensure_twenty_company_fields
    
    # Setup mock get response
    mock_meta_resp = MagicMock()
    mock_meta_resp.status_code = 200
    mock_meta_resp.json.return_value = {
        "data": [
            {
                "nameSingular": "company",
                "id": "obj_comp_1",
                "fields": []
            },
            {
                "nameSingular": "person",
                "id": "obj_person_1",
                "fields": []
            }
        ]
    }
    mock_get.return_value = mock_meta_resp
    
    # Setup mock post response
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_post.return_value = mock_post_resp
    
    ensure_twenty_company_fields("http://fake", "fake-key")
    
    # Verify post was called for enrichmentStatus and contactStatus on both objects
    post_calls = mock_post.call_args_list
    posted_payloads = [call[1]['json'] for call in post_calls]
    
    # Check company fields
    company_enrichment = next((p for p in posted_payloads if p["objectMetadataId"] == "obj_comp_1" and p["name"] == "enrichmentStatus"), None)
    assert company_enrichment is not None
    
    company_contact = next((p for p in posted_payloads if p["objectMetadataId"] == "obj_comp_1" and p["name"] == "contactStatus"), None)
    assert company_contact is not None
    
    # Check person fields
    person_enrichment = next((p for p in posted_payloads if p["objectMetadataId"] == "obj_person_1" and p["name"] == "enrichmentStatus"), None)
    assert person_enrichment is not None
    
    person_contact = next((p for p in posted_payloads if p["objectMetadataId"] == "obj_person_1" and p["name"] == "contactStatus"), None)
    assert person_contact is not None

