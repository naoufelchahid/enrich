import pytest
from unittest.mock import patch, MagicMock
import requests
from enricher.scraper import CharikaScraper

@patch('enricher.scraper.requests.get')
def test_charika_scraper_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '<html><body><div class="info">ICE: 987654321</div></body></html>'
    mock_get.return_value = mock_resp
    
    scraper = CharikaScraper()
    res = scraper.scrape_company("Test Company")
    
    assert res["ice"] == "987654321"
    mock_get.assert_called_once_with(
        "https://www.charika.ma/societe-Test-Company",
        headers={"User-Agent": CharikaScraper.DEFAULT_USER_AGENT},
        timeout=10
    )

@patch('enricher.scraper.requests.get')
def test_charika_scraper_sanitization_and_encoding(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '<html><body>ICE: 111222333</body></html>'
    mock_get.return_value = mock_resp
    
    scraper = CharikaScraper()
    res = scraper.scrape_company("Société Extra & Co., SARL")
    
    assert res["ice"] == "111222333"
    # Legal entity suffix 'SARL' stripped, company name sanitized, URL encoded
    mock_get.assert_called_once_with(
        "https://www.charika.ma/societe-Soci%C3%A9t%C3%A9-Extra-Co",
        headers={"User-Agent": CharikaScraper.DEFAULT_USER_AGENT},
        timeout=10
    )

@patch('enricher.scraper.requests.get')
def test_charika_scraper_no_ice(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '<html><body><div class="info">No code here</div></body></html>'
    mock_get.return_value = mock_resp
    
    scraper = CharikaScraper()
    res = scraper.scrape_company("Test Company")
    
    assert "ice" not in res
    assert res == {}

@patch('enricher.scraper.requests.get')
def test_charika_scraper_http_error(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.text = 'Not Found'
    mock_get.return_value = mock_resp
    
    scraper = CharikaScraper()
    res = scraper.scrape_company("Test Company")
    
    assert res == {}

@patch('enricher.scraper.requests.get')
def test_charika_scraper_exception(mock_get):
    mock_get.side_effect = requests.RequestException("Connection timeout")
    
    scraper = CharikaScraper()
    res = scraper.scrape_company("Test Company")
    
    assert res == {}

