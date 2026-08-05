import os
import argparse
import json
import logging
from enricher.crm_client import TwentyCRMClient
from enricher.scraper import KerixScraper
from enricher.setup_twenty_fields import ensure_twenty_company_fields

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MAX_ENRICH_LIMIT = 10

def load_env():
    env_file = ".env"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k] = v

def main():
    parser = argparse.ArgumentParser(description="Enrich Twenty CRM with company data via CloakBrowser and Google search")
    parser.add_argument("--limit", type=int, default=MAX_ENRICH_LIMIT, help="Number of records to process")
    parser.add_argument("--headless", action="store_true", default=False, help="Run browser in headless mode (default: False)")
    args = parser.parse_args()

    load_env()

    api_url = os.environ.get("TWENTY_API_BASE_URL", "http://localhost:3005/rest")
    api_key = os.environ.get("TWENTY_API_KEY")
    license_key = os.environ.get("CLOAKBROWSER_LICENSE_KEY")

    if not api_key:
        logger.error("TWENTY_API_KEY environment variable is required")
        return
    if not license_key:
        logger.error("CLOAKBROWSER_LICENSE_KEY environment variable is required")
        return

    # Ensure custom company columns exist in Twenty CRM
    ensure_twenty_company_fields(api_url, api_key)

    limit = min(args.limit, MAX_ENRICH_LIMIT)
    crm = TwentyCRMClient(api_url, api_key)
    scraper = KerixScraper(license_key, headless=args.headless)

    companies = crm.list_companies(limit=limit, filters={"enrichmentStatus": "pending"})
    logger.info(f"Found {len(companies)} pending companies in CRM to enrich (limit {limit})")

    try:
        for idx, company in enumerate(companies):
            company_id = company["id"]
            company_name = company.get("name", "").strip()
            if not company_name:
                continue

            logger.info(f"[{idx+1}/{len(companies)}] Processing company: {company_name}")

            # Scrape company
            scraped = scraper.scrape_company(company_name)
            print(f"\n================ SCRAPED JSON FOR '{company_name}' ================")
            print(json.dumps(scraped, indent=2, ensure_ascii=False))
            print("================================================================")

            has_data = any([
                scraped.get("kerix_url"),
                scraped.get("telecontact_url"),
                scraped.get("charika_url")
            ])

            if not has_data:
                logger.info(f"Skipping {company_name} - no results found.")
                continue

            # Build CRM payload with custom company columns
            payload = {
                "enrichmentStatus": "company_enriched"
            }
            if scraped.get("kerix_url"):
                payload["domainName"] = {
                    "primaryLinkLabel": "Kerix Profile",
                    "primaryLinkUrl": scraped["kerix_url"]
                }
            elif scraped.get("telecontact_url"):
                payload["domainName"] = {
                    "primaryLinkLabel": "Telecontact Profile",
                    "primaryLinkUrl": scraped["telecontact_url"]
                }
            elif scraped.get("charika_url"):
                payload["domainName"] = {
                    "primaryLinkLabel": "Charika Profile",
                    "primaryLinkUrl": scraped["charika_url"]
                }

            if scraped.get("address"):
                payload["address"] = {
                    "addressStreet1": scraped["address"],
                    "addressCity": scraped.get("city", "Casablanca")
                }

            # Map custom company fields directly to CRM columns
            if scraped.get("ice"):
                payload["ice"] = scraped["ice"]
            if scraped.get("rc"):
                payload["rc"] = scraped["rc"]
            if scraped.get("creation"):
                payload["creation"] = scraped["creation"]
            if scraped.get("capital"):
                payload["capital"] = scraped["capital"]
            if scraped.get("chiffre_d_affaires"):
                payload["chiffreDAffaires"] = scraped["chiffre_d_affaires"]
            if scraped.get("effectif"):
                payload["effectif"] = scraped["effectif"]
            if scraped.get("activity"):
                payload["activity"] = scraped["activity"]
            if scraped.get("phone"):
                payload["phone"] = scraped["phone"]

            res = crm.update_company(company_id, payload)
            if res:
                import re
                people_payloads = {}
                
                # Parse Kerix dirigeants
                if scraped.get("dirigeant"):
                    dirigeants = [d.strip() for d in scraped["dirigeant"].split(",")]
                    for dir_name in dirigeants:
                        if not dir_name:
                            continue
                        
                        job_title_match = re.search(r"\((.*?)\)", dir_name)
                        job_title = job_title_match.group(1).strip() if job_title_match else "Dirigeant"
                        clean_name = re.sub(r"\(.*?\)", "", dir_name).strip()
                        
                        people_payloads[clean_name.lower()] = {
                            "name": {
                                "firstName": clean_name,
                                "lastName": ""
                            },
                            "jobTitle": job_title,
                            "companyId": company_id
                        }
                
                for p_payload in people_payloads.values():
                    crm.create_person(p_payload)

                # Add note with enriched details
                note_lines = []
                if scraped.get("ice"):
                    note_lines.append(f"**ICE**: {scraped['ice']}")
                if scraped.get("rc"):
                    note_lines.append(f"**RC**: {scraped['rc']}")
                if scraped.get("creation"):
                    note_lines.append(f"**Création**: {scraped['creation']}")
                if scraped.get("effectif"):
                    note_lines.append(f"**Effectif**: {scraped['effectif']}")
                if scraped.get("chiffre_d_affaires"):
                    note_lines.append(f"**Chiffre D'affaires**: {scraped['chiffre_d_affaires']}")
                if scraped.get("capital"):
                    note_lines.append(f"**Capital**: {scraped['capital']}")
                if scraped.get("dirigeant"):
                    note_lines.append(f"**Dirigeant**: {scraped['dirigeant']}")
                if scraped.get("activity"):
                    note_lines.append(f"**Activités**: {scraped['activity']}")
                if scraped.get("phone"):
                    note_lines.append(f"**Téléphone**: {scraped['phone']}")

                if note_lines:
                    crm.add_note(company_id, "Enriched Metadata", "\n".join(note_lines))

    finally:
        scraper.close()
        logger.info("Enrichment run finished.")

if __name__ == "__main__":
    main()
