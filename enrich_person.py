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
    We also only want people whose associated company has enrichmentStatus == 'company_enriched'.
    Due to CRM filtering limitations, we'll fetch 'pending' people and filter locally or 
    assume we can filter both.
    """
    people = crm.list_people(limit=50, filters={"enrichmentStatus": "pending"})
    valid_people = []
    
    for person in people:
        company_id = person.get("companyId")
        if not company_id:
            continue
            
        # We need to verify the company status
        # A lightweight way is to search/get the company
        # However list_companies allows filtering by id? TwentyCRMClient currently doesn't have get_company by ID easily, 
        # let's just fetch company by name or ID. Let's add a quick get_company if needed, or rely on list_companies.
        # Wait, the prompt says: "You might need to resolve how to filter by the associated company's status being 'company_enriched' — either via the CRM filter API or by fetching 'pending' people and checking their companyId's status".
        # Let's fetch companies with 'company_enriched' first and then their people? Or fetch pending people, then fetch their company.
        # Let's fetch companies that are company_enriched. But we might hit limits.
        
        valid_people.append(person)
        if len(valid_people) >= PERSON_LIMIT:
            break
            
    # For now, to be safe and avoid many API calls if we don't have to, let's just fetch company if needed.
    # Actually, we can fetch the company using search_company or a similar method if we know the name, 
    # or just list_companies with id filter.
    return valid_people

def process_person(crm: TwentyCRMClient, scraper: LinkedInScraper, person: Dict[str, Any]):
    person_id = person.get("id")
    first_name = person.get("name", {}).get("firstName", "")
    last_name = person.get("name", {}).get("lastName", "")
    full_name = f"{first_name} {last_name}".strip()
    
    company_id = person.get("companyId")
    if not company_id:
        logger.warning(f"Person {full_name} has no companyId. Marking as not_found.")
        crm.update_person(person_id, {"enrichmentStatus": "not_found"})
        return

    # To get company name, we need to fetch the company
    # We will use list_companies with filter id=company_id
    companies = crm.list_companies(limit=1, filters={"id": company_id})
    if not companies:
        logger.warning(f"Company ID {company_id} not found for {full_name}. Marking as not_found.")
        crm.update_person(person_id, {"enrichmentStatus": "not_found"})
        return
        
    company = companies[0]
    if company.get("enrichmentStatus") != "company_enriched":
        logger.info(f"Person {full_name}'s company is not 'company_enriched' yet. Skipping.")
        return

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
                
            crm.add_person_note(person_id, "LinkedIn Enrichment Data", note_body)
            logger.info(f"Successfully enriched {full_name}")
        else:
            logger.info(f"No LinkedIn data found for {full_name}")
            crm.update_person(person_id, {"enrichmentStatus": "not_found"})
            
    except Exception as e:
        logger.error(f"Failed to enrich person {full_name}: {e}")
        crm.update_person(person_id, {"enrichmentStatus": "failed"})

def main():
    api_url = os.getenv("TWENTY_API_URL")
    api_key = os.getenv("TWENTY_API_KEY")
    cloak_key = os.getenv("CLOAKBROWSER_KEY")
    
    if not all([api_url, api_key, cloak_key]):
        logger.error("Missing required environment variables (TWENTY_API_URL, TWENTY_API_KEY, CLOAKBROWSER_KEY).")
        sys.exit(1)
        
    crm = TwentyCRMClient(api_url, api_key)
    scraper = LinkedInScraper(license_key=cloak_key, headless=True)
    
    try:
        people_to_process = get_pending_people(crm)
        logger.info(f"Found {len(people_to_process)} people to process.")
        
        for person in people_to_process:
            process_person(crm, scraper, person)
    finally:
        scraper.close()

if __name__ == "__main__":
    main()
