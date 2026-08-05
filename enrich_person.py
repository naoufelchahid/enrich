import os
import sys
import logging
from typing import List, Dict, Any

from enricher.crm_client import TwentyCRMClient
from enricher.scraper import LinkedInScraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PERSON_LIMIT = 10

def get_pending_people(crm: TwentyCRMClient) -> List[Dict[str, Any]]:
    """
    Fetch people whose enrichmentStatus is 'pending'.
    """
    # Fetch a large number of pending people to avoid getting stuck on unenriched companies
    return crm.list_people(limit=1000, filters={"enrichmentStatus": "pending"})

def process_person(crm: TwentyCRMClient, scraper: LinkedInScraper, person: Dict[str, Any]) -> bool:
    """Processes a person. Returns True if the person was actually processed (enriched or failed), False if skipped."""
    person_id = person.get("id")
    first_name = person.get("name", {}).get("firstName", "")
    last_name = person.get("name", {}).get("lastName", "")
    full_name = f"{first_name} {last_name}".strip()
    
    company_id = person.get("companyId")
    if not company_id:
        logger.warning(f"Person {full_name} has no companyId. Marking as not_found.")
        crm.update_person(person_id, {"enrichmentStatus": "not_found"})
        return True

    # To get company name, we need to fetch the company
    # We will use list_companies with filter id=company_id
    companies = crm.list_companies(limit=1, filters={"id": company_id})
    if not companies:
        logger.warning(f"Company ID {company_id} not found for {full_name}. Marking as not_found.")
        crm.update_person(person_id, {"enrichmentStatus": "not_found"})
        return True
        
    company = companies[0]
    if company.get("enrichmentStatus") != "company_enriched":
        logger.info(f"Person {full_name}'s company is not 'company_enriched' yet. Skipping.")
        return False

    company_name = company.get("name", "")
    
    logger.info(f"Enriching person: {full_name} at {company_name}")
    
    try:
        data = scraper.scrape_person(full_name, company_name)
        linkedin_url = data.get("linkedin_url")
        headline = data.get("headline")
        
        if linkedin_url:
            update_data = {
                "linkedinLink": {"primaryLinkUrl": linkedin_url, "primaryLinkLabel": "LinkedIn"},
                "enrichmentStatus": "person_enriched",
                "contactStatus": "pending"
            }
            if headline:
                update_data["jobTitle"] = headline

            crm.update_person(person_id, update_data)
            
            note_body = f"**LinkedIn URL:** {linkedin_url}"
            if headline:
                note_body += f"\n**Headline:** {headline}"
                
            bio = data.get("bio")
            if bio:
                note_body += f"\n\n**Bio / Experience Extracted:**\n{bio}"
                
            crm.add_person_note(person_id, "LinkedIn Enrichment Data", note_body)
            logger.info(f"Successfully enriched {full_name}")
        else:
            logger.info(f"No LinkedIn data found for {full_name}")
            crm.update_person(person_id, {"enrichmentStatus": "not_found"})
            
        return True
    except Exception as e:
        logger.error(f"Failed to enrich person {full_name}: {e}")
        crm.update_person(person_id, {"enrichmentStatus": "failed"})
        return True

def main():
    api_url = os.getenv("TWENTY_API_BASE_URL")
    api_key = os.getenv("TWENTY_API_KEY")
    cloak_key = os.getenv("CLOAKBROWSER_LICENSE_KEY")
    
    if not all([api_url, api_key, cloak_key]):
        logger.error("Missing required environment variables (TWENTY_API_BASE_URL, TWENTY_API_KEY, CLOAKBROWSER_LICENSE_KEY).")
        sys.exit(1)
        
    crm = TwentyCRMClient(api_url, api_key)
    scraper = LinkedInScraper(license_key=cloak_key, headless=True)
    
    try:
        people_to_process = get_pending_people(crm)
        logger.info(f"Found {len(people_to_process)} pending people.")
        
        processed_count = 0
        for person in people_to_process:
            if processed_count >= PERSON_LIMIT:
                logger.info(f"Reached PERSON_LIMIT of {PERSON_LIMIT} processed people.")
                break
                
            was_processed = process_person(crm, scraper, person)
            if was_processed:
                processed_count += 1
    finally:
        scraper.close()

if __name__ == "__main__":
    main()
