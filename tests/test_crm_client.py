import pytest
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
