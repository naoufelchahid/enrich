import pytest
from unittest.mock import MagicMock
from enrich_person import get_pending_people, process_person

def test_get_pending_people():
    mock_crm = MagicMock()
    mock_crm.list_people.return_value = [
        {"id": "p1", "companyId": "c1", "enrichmentStatus": "pending"},
        {"id": "p2", "enrichmentStatus": "pending"}, # No companyId
        {"id": "p3", "companyId": "c3", "enrichmentStatus": "pending"}
    ]
    
    valid_people = get_pending_people(mock_crm)
    
    # It should filter out those without companyId
    assert len(valid_people) == 2
    assert valid_people[0]["id"] == "p1"
    assert valid_people[1]["id"] == "p3"

def test_process_person_no_company():
    mock_crm = MagicMock()
    mock_scraper = MagicMock()
    
    person = {"id": "p1", "name": {"firstName": "John"}} # no companyId
    process_person(mock_crm, mock_scraper, person)
    
    mock_crm.update_person.assert_called_once_with("p1", {"enrichmentStatus": "not_found"})
    mock_scraper.scrape_person.assert_not_called()

def test_process_person_company_not_found():
    mock_crm = MagicMock()
    mock_scraper = MagicMock()
    
    person = {"id": "p1", "companyId": "c1", "name": {"firstName": "John", "lastName": "Doe"}}
    mock_crm.list_companies.return_value = []
    
    process_person(mock_crm, mock_scraper, person)
    
    mock_crm.list_companies.assert_called_once_with(limit=1, filters={"id": "c1"})
    mock_crm.update_person.assert_called_once_with("p1", {"enrichmentStatus": "not_found"})
    mock_scraper.scrape_person.assert_not_called()

def test_process_person_company_not_enriched():
    mock_crm = MagicMock()
    mock_scraper = MagicMock()
    
    person = {"id": "p1", "companyId": "c1", "name": {"firstName": "John"}}
    mock_crm.list_companies.return_value = [{"id": "c1", "enrichmentStatus": "pending"}]
    
    process_person(mock_crm, mock_scraper, person)
    
    # Should not update anything, just skip
    mock_crm.update_person.assert_not_called()
    mock_scraper.scrape_person.assert_not_called()

def test_process_person_success():
    mock_crm = MagicMock()
    mock_scraper = MagicMock()
    
    person = {"id": "p1", "companyId": "c1", "name": {"firstName": "John", "lastName": "Doe"}}
    mock_crm.list_companies.return_value = [{"id": "c1", "name": "ACME Corp", "enrichmentStatus": "company_enriched"}]
    mock_scraper.scrape_person.return_value = {
        "linkedin_url": "https://linkedin.com/in/johndoe",
        "headline": "Software Engineer"
    }
    
    process_person(mock_crm, mock_scraper, person)
    
    mock_scraper.scrape_person.assert_called_once_with("John Doe", "ACME Corp")
    mock_crm.update_person.assert_called_once_with("p1", {
        "linkedinLink": {"primaryLinkUrl": "https://linkedin.com/in/johndoe", "primaryLinkLabel": "LinkedIn"},
        "enrichmentStatus": "person_enriched",
        "contactStatus": "pending",
        "jobTitle": "Software Engineer"
    })
    mock_crm.add_person_note.assert_called_once()
    args, _ = mock_crm.add_person_note.call_args
    assert args[0] == "p1"
    assert args[1] == "LinkedIn Enrichment Data"
    assert "https://linkedin.com/in/johndoe" in args[2]
    assert "Software Engineer" in args[2]

def test_process_person_no_linkedin_data():
    mock_crm = MagicMock()
    mock_scraper = MagicMock()
    
    person = {"id": "p1", "companyId": "c1", "name": {"firstName": "John", "lastName": "Doe"}}
    mock_crm.list_companies.return_value = [{"id": "c1", "name": "ACME Corp", "enrichmentStatus": "company_enriched"}]
    mock_scraper.scrape_person.return_value = {
        "linkedin_url": None,
        "headline": None
    }
    
    process_person(mock_crm, mock_scraper, person)
    
    mock_crm.update_person.assert_called_once_with("p1", {"enrichmentStatus": "not_found"})

def test_process_person_exception():
    mock_crm = MagicMock()
    mock_scraper = MagicMock()
    
    person = {"id": "p1", "companyId": "c1", "name": {"firstName": "John", "lastName": "Doe"}}
    mock_crm.list_companies.return_value = [{"id": "c1", "name": "ACME Corp", "enrichmentStatus": "company_enriched"}]
    mock_scraper.scrape_person.side_effect = Exception("Scrape failed")
    
    process_person(mock_crm, mock_scraper, person)
    
    mock_crm.update_person.assert_called_once_with("p1", {"enrichmentStatus": "failed"})
