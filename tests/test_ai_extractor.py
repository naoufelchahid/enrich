import pytest
from unittest.mock import MagicMock
from enricher.ai_extractor import extract_company_data

def test_extract_company_data(mocker):
    mock_model = mocker.patch('enricher.ai_extractor.genai.GenerativeModel')
    mock_instance = MagicMock()
    mock_model.return_value = mock_instance
    mock_response = MagicMock()
    mock_response.text = '```json\n{"ice": "123", "rc": "456", "capital": "10000", "address": "Casa", "phone": "0600000000", "activity": "Tech"}\n```'
    mock_instance.generate_content.return_value = mock_response
    
    res = extract_company_data("Some raw google AI digest text")
    assert mock_instance.generate_content.call_count == 1
    assert "Extract the following information" in mock_instance.generate_content.call_args[0][0]
    assert res["ice"] == "123"
    assert res["rc"] == "456"
    assert res["capital"] == "10000"
    assert res["address"] == "Casa"
    assert res["phone"] == "0600000000"
    assert res["activity"] == "Tech"

def test_extract_company_data_raw_json(mocker):
    mock_model = mocker.patch('enricher.ai_extractor.genai.GenerativeModel')
    mock_instance = MagicMock()
    mock_model.return_value = mock_instance
    mock_response = MagicMock()
    mock_response.text = '{"ice": "789", "rc": "1011", "capital": null, "address": null, "phone": null, "activity": null}'
    mock_instance.generate_content.return_value = mock_response
    
    res = extract_company_data("Raw text")
    assert res["ice"] == "789"
    assert res["rc"] == "1011"
    assert res["capital"] is None

def test_extract_company_data_exception(mocker):
    mock_model = mocker.patch('enricher.ai_extractor.genai.GenerativeModel')
    mock_instance = MagicMock()
    mock_model.return_value = mock_instance
    mock_instance.generate_content.side_effect = Exception("API error")
    
    res = extract_company_data("Raw text")
    assert res == {}
