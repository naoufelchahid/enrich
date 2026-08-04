import os
import argparse
import json
import pandas as pd
import logging
from enricher.crm_client import TwentyCRMClient
from enricher.scraper import KerixScraper
from enricher.setup_twenty_fields import ensure_twenty_company_fields

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def load_env():
    env_file = ".env"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k] = v

def main():
    parser = argparse.ArgumentParser(description="Enrich Twenty CRM with company data via CloakBrowser and Kerix.net")
    parser.add_argument("--csv", default="by_direction/ANFA.csv", help="Path to input CSV file")
    parser.add_argument("--limit", type=int, default=5, help="Number of records to process")
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

    crm = TwentyCRMClient(api_url, api_key)
    scraper = KerixScraper(license_key, headless=args.headless)

    df = pd.read_csv(args.csv)
    logger.info(f"Loaded {len(df)} companies from {args.csv}")

    try:
        for idx, row in df.head(args.limit).iterrows():
            company_name = str(row.get("companyName", "")).strip('"\t ').replace('"', '').strip()
            if not company_name:
                continue

            logger.info(f"[{idx+1}/{args.limit}] Processing company: {company_name}")

            # Scrape Kerix
            scraped = scraper.scrape_company(company_name)
            print(f"\n================ SCRAPED JSON FOR '{company_name}' ================")
            print(json.dumps(scraped, indent=2, ensure_ascii=False))
            print("================================================================")

            if not scraped.get("kerix_url"):
                logger.info(f"Skipping {company_name} - no results found on Kerix.")
                continue

            # Build CRM payload with custom company columns
            payload = {
                "name": company_name,
            }
            if scraped.get("kerix_url"):
                payload["domainName"] = {
                    "primaryLinkLabel": "Kerix Profile",
                    "primaryLinkUrl": scraped["kerix_url"]
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

            res = crm.create_company(payload)
            if res and res.get("id"):
                company_id = res["id"]
                
                import re

                # Get admin info from CSV
                admin_first = str(row.get("admin_firstName", "")).strip()
                if admin_first.lower() == "nan": admin_first = ""
                admin_last = str(row.get("admin_lastName", "")).strip()
                if admin_last.lower() == "nan": admin_last = ""
                admin_email = str(row.get("admin_email", "")).strip()
                if admin_email.lower() == "nan": admin_email = ""
                admin_phone = str(row.get("admin_phoneNumber", "")).strip()
                if admin_phone.lower() == "nan": admin_phone = ""
                
                admin_full_name = f"{admin_first} {admin_last}".strip()

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
                
                # Check admin and merge or add
                if admin_full_name:
                    matched_key = None
                    admin_lower = admin_full_name.lower()
                    
                    # Try to match names
                    for k in people_payloads.keys():
                        if admin_lower in k or k in admin_lower:
                            matched_key = k
                            break
                    
                    if matched_key:
                        # Merge email and phone into the matched person
                        if admin_email:
                            people_payloads[matched_key]["emails"] = {"primaryEmail": admin_email}
                        if admin_phone:
                            # Prepend + if not present to ensure valid format, but TwentyCRM accepts any valid intl format. We'll pass as is.
                            # Just to be safe with Moroccan numbers, pass as string.
                            people_payloads[matched_key]["phones"] = {"primaryPhoneNumber": f"+212{admin_phone}" if admin_phone.startswith("6") or admin_phone.startswith("7") or admin_phone.startswith("5") else admin_phone}
                    else:
                        # Create new person for admin
                        admin_payload = {
                            "name": {
                                "firstName": admin_first,
                                "lastName": admin_last
                            },
                            "jobTitle": "Admin",
                            "companyId": company_id
                        }
                        if admin_email:
                            admin_payload["emails"] = {"primaryEmail": admin_email}
                        if admin_phone:
                            admin_payload["phones"] = {"primaryPhoneNumber": f"+212{admin_phone}" if admin_phone.startswith("6") or admin_phone.startswith("7") or admin_phone.startswith("5") else admin_phone}
                        people_payloads[admin_lower] = admin_payload

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
                if admin_email:
                    note_lines.append(f"**Admin Email**: {admin_email}")

                if note_lines:
                    crm.add_note(company_id, "Enriched Metadata", "\n".join(note_lines))

    finally:
        scraper.close()
        logger.info("Enrichment run finished.")

if __name__ == "__main__":
    main()
