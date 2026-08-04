import os
import json
import logging
import argparse

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        if os.path.exists(".env"):
            with open(".env") as f:
                for line in f:
                    if line.strip() and not line.startswith("#") and "=" in line:
                        k, v = line.strip().split("=", 1)
                        os.environ.setdefault(k, v)

from enricher.crm_client import TwentyCRMClient
from enricher.ai_extractor import extract_company_data
from enricher.scraper import KerixScraper, CharikaScraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fetch_google_ai_digest(company_name, license_key):
    # Stub: Normally uses CloakBrowser here.
    return f"Google AI Digest says {company_name} ICE is 000000000000000 and RC is 99999 located in Casablanca."

def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    api_url = os.environ.get("TWENTY_API_BASE_URL", "http://localhost:3005/rest")
    api_key = os.environ.get("TWENTY_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    cloak_key = os.environ.get("CLOAKBROWSER_LICENSE_KEY")

    if not all([api_key, gemini_key, cloak_key]):
        logger.error("Missing required environment variables (TWENTY_API_KEY, GEMINI_API_KEY, CLOAKBROWSER_LICENSE_KEY)")
        return

    crm = TwentyCRMClient(api_url, api_key)
    kerix = KerixScraper(cloak_key, headless=True)
    charika = CharikaScraper()

    try:
        companies = crm.list_companies(limit=args.limit)
        logger.info(f"Fetched {len(companies)} companies from CRM.")

        for comp in companies:
            name = comp.get("name")
            comp_id = comp.get("id")
            if not name or not comp_id:
                continue

            logger.info(f"Enriching: {name}")
            
            # Pass 1: Google + LLM
            ai_text = fetch_google_ai_digest(name, cloak_key)
            enriched_data = extract_company_data(ai_text, gemini_key)
            
            # Pass 2: Fallback
            if not enriched_data.get("ice") or not enriched_data.get("rc"):
                logger.info(f"Missing critical fields for {name}. Falling back to scrapers.")
                kerix_data = kerix.scrape_company(name)
                charika_data = charika.scrape_company(name)
                
                # Merge fallback data (LLM takes precedence if existing)
                for k, v in {**charika_data, **kerix_data}.items():
                    if not enriched_data.get(k) and v:
                        enriched_data[k] = v

            if enriched_data:
                # Map back to CRM fields
                payload = {}
                if enriched_data.get("ice"): payload["ice"] = enriched_data["ice"]
                if enriched_data.get("rc"): payload["rc"] = enriched_data["rc"]
                if enriched_data.get("phone"): payload["phone"] = enriched_data["phone"]
                
                if payload:
                    crm.upsert_company({"name": name, **payload})
                    
                crm.add_note(comp_id, "Enrichment Source Data", f"LLM Output:\n{json.dumps(enriched_data, indent=2)}")
    finally:
        kerix.close()

if __name__ == "__main__":
    main()
