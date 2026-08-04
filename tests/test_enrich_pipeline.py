import os
import json
from unittest.mock import patch, MagicMock
import pytest
import enrich_pipeline

def test_fetch_google_ai_digest():
    digest = enrich_pipeline.fetch_google_ai_digest("Acme Corp", "fake_cloak_key")
    assert "Acme Corp" in digest
    assert "ICE" in digest

@patch.dict(os.environ, {}, clear=True)
def test_main_missing_env_vars():
    with patch("enrich_pipeline.logger.error") as mock_log_error:
        enrich_pipeline.main([])
        mock_log_error.assert_called_once()
        assert "Missing required environment variables" in mock_log_error.call_args[0][0]

@patch.dict(os.environ, {
    "TWENTY_API_KEY": "test_key",
    "GEMINI_API_KEY": "test_gemini",
    "CLOAKBROWSER_LICENSE_KEY": "test_cloak"
})
@patch("enrich_pipeline.TwentyCRMClient")
@patch("enrich_pipeline.KerixScraper")
@patch("enrich_pipeline.CharikaScraper")
def test_main_kerix_init_exception(mock_charika_cls, mock_kerix_cls, mock_crm_cls):
    mock_kerix_cls.side_effect = RuntimeError("Failed to launch browser")
    with pytest.raises(RuntimeError, match="Failed to launch browser"):
        enrich_pipeline.main([])

@patch.dict(os.environ, {
    "TWENTY_API_KEY": "test_key",
    "GEMINI_API_KEY": "test_gemini",
    "CLOAKBROWSER_LICENSE_KEY": "test_cloak"
})
@patch("enrich_pipeline.TwentyCRMClient")
@patch("enrich_pipeline.extract_company_data")
@patch("enrich_pipeline.KerixScraper")
@patch("enrich_pipeline.CharikaScraper")
def test_main_pipeline_pass1_success(mock_charika_cls, mock_kerix_cls, mock_extract, mock_crm_cls):
    mock_crm = MagicMock()
    mock_crm_cls.return_value = mock_crm
    mock_crm.list_companies.return_value = [
        {"id": "comp_1", "name": "Tech Corp"}
    ]
    
    mock_extract.return_value = {
        "ice": "123456789000000",
        "rc": "12345",
        "phone": "+212500000000"
    }

    mock_kerix = MagicMock()
    mock_kerix_cls.return_value = mock_kerix

    with patch("argparse.ArgumentParser.parse_args") as mock_args:
        mock_args.return_value = MagicMock(limit=5)
        enrich_pipeline.main()

    mock_crm.list_companies.assert_called_once_with(limit=5)
    mock_extract.assert_called_once()
    mock_crm.upsert_company.assert_called_once_with({
        "name": "Tech Corp",
        "ice": "123456789000000",
        "rc": "12345",
        "phone": "+212500000000"
    })
    mock_crm.add_note.assert_called_once()
    mock_kerix.scrape_company.assert_not_called()
    mock_kerix.close.assert_called_once()

@patch.dict(os.environ, {
    "TWENTY_API_KEY": "test_key",
    "GEMINI_API_KEY": "test_gemini",
    "CLOAKBROWSER_LICENSE_KEY": "test_cloak"
})
@patch("enrich_pipeline.TwentyCRMClient")
@patch("enrich_pipeline.extract_company_data")
@patch("enrich_pipeline.KerixScraper")
@patch("enrich_pipeline.CharikaScraper")
def test_main_pipeline_fallback(mock_charika_cls, mock_kerix_cls, mock_extract, mock_crm_cls):
    mock_crm = MagicMock()
    mock_crm_cls.return_value = mock_crm
    mock_crm.list_companies.return_value = [
        {"id": "comp_2", "name": "Fallback Corp"}
    ]
    
    # Pass 1 missing ice and rc
    mock_extract.return_value = {
        "phone": "+212600000000"
    }

    mock_kerix = MagicMock()
    mock_kerix_cls.return_value = mock_kerix
    mock_kerix.scrape_company.return_value = {
        "ice": "987654321000000",
        "rc": "54321"
    }

    mock_charika = MagicMock()
    mock_charika_cls.return_value = mock_charika
    mock_charika.scrape_company.return_value = {
        "ice": "000000000000000"
    }

    with patch("argparse.ArgumentParser.parse_args") as mock_args:
        mock_args.return_value = MagicMock(limit=5)
        enrich_pipeline.main()

    mock_kerix.scrape_company.assert_called_once_with("Fallback Corp")
    mock_charika.scrape_company.assert_called_once_with("Fallback Corp")
    # Check that ice and rc from kerix were merged into payload (or charika/kerix merged)
    mock_crm.upsert_company.assert_called_once_with({
        "name": "Fallback Corp",
        "ice": "987654321000000",
        "rc": "54321",
        "phone": "+212600000000"
    })
    mock_crm.add_note.assert_called_once()
    mock_kerix.close.assert_called_once()
