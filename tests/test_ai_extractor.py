import pytest
from unittest.mock import patch, MagicMock
from enricher.ai_extractor import extract_company_data

@patch('enricher.ai_extractor.genai.GenerativeModel')
@patch('enricher.ai_extractor.genai.configure')
def test_extract_company_data(mock_configure, MockModel):
    mock_model_instance = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '```json\n{"ice": "123", "rc": "456", "capital": "10000", "address": "Casa", "phone": "0600000000", "activity": "Tech"}\n```'
    mock_model_instance.generate_content.return_value = mock_response
    MockModel.return_value = mock_model_instance
    
    res = extract_company_data("Some raw google AI digest text", "fake_key")
    mock_configure.assert_called_once_with(api_key="fake_key")
    MockModel.assert_called_once_with("gemini-1.5-flash")
    assert res["ice"] == "123"
    assert res["rc"] == "456"
    assert res["capital"] == "10000"
    assert res["address"] == "Casa"
    assert res["phone"] == "0600000000"
    assert res["activity"] == "Tech"

@patch('enricher.ai_extractor.genai.GenerativeModel')
def test_extract_company_data_raw_json(MockModel):
    mock_model_instance = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '{"ice": "789", "rc": "1011", "capital": null, "address": null, "phone": null, "activity": null}'
    mock_model_instance.generate_content.return_value = mock_response
    MockModel.return_value = mock_model_instance
    
    res = extract_company_data("Raw text", "fake_key")
    assert res["ice"] == "789"
    assert res["rc"] == "1011"
    assert res["capital"] is None

@patch('enricher.ai_extractor.genai.GenerativeModel')
def test_extract_company_data_exception(MockModel):
    mock_model_instance = MagicMock()
    mock_model_instance.generate_content.side_effect = Exception("API error")
    MockModel.return_value = mock_model_instance
    
    res = extract_company_data("Raw text", "fake_key")
    assert res == {}
