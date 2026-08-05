import pytest
from unittest.mock import patch, MagicMock
from enricher.ai_extractor import extract_company_data

@patch('enricher.ai_extractor.subprocess.run')
def test_extract_company_data(mock_run):
    mock_result = MagicMock()
    mock_result.stdout = '```json\n{"ice": "123", "rc": "456", "capital": "10000", "address": "Casa", "phone": "0600000000", "activity": "Tech"}\n```'
    mock_run.return_value = mock_result
    
    res = extract_company_data("Some raw google AI digest text")
    assert mock_run.call_count == 1
    assert "agy" in mock_run.call_args[0][0][0]
    assert res["ice"] == "123"
    assert res["rc"] == "456"
    assert res["capital"] == "10000"
    assert res["address"] == "Casa"
    assert res["phone"] == "0600000000"
    assert res["activity"] == "Tech"

@patch('enricher.ai_extractor.subprocess.run')
def test_extract_company_data_raw_json(mock_run):
    mock_result = MagicMock()
    mock_result.stdout = '{"ice": "789", "rc": "1011", "capital": null, "address": null, "phone": null, "activity": null}'
    mock_run.return_value = mock_result
    
    res = extract_company_data("Raw text")
    assert res["ice"] == "789"
    assert res["rc"] == "1011"
    assert res["capital"] is None

@patch('enricher.ai_extractor.subprocess.run')
def test_extract_company_data_exception(mock_run):
    mock_run.side_effect = Exception("CLI error")
    
    res = extract_company_data("Raw text")
    assert res == {}
