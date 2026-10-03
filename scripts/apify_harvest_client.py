import logging
import os
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger(__name__)
APIFY_ACTOR_URL = "https://api.apify.com/v2/acts/harvest~linkedin-enrichment/run-sync-get-dataset-items"


class ApifyHarvestClient:
    """Client for Apify Actor Harvest: extracts fresh LinkedIn career, company, and activity data."""

    def __init__(self, api_token: Optional[str] = None, dry_run: bool = False):
        self.api_token = api_token or os.getenv("APIFY_API_TOKEN")
        self.dry_run = dry_run or (self.api_token is None)

    def enrich_profile(
        self,
        linkedin_url: Optional[str] = None,
        full_name: Optional[str] = None,
        company: Optional[str] = None,
        city: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch career history, current company, and recent activity via Apify Harvest."""
        if self.dry_run:
            logger.info("[DRY-RUN] Apify Harvest query for %s / %s", full_name, company)
            return {
                "current_job_title": "Directeur Général",
                "current_job_start_date": "2025-06-01",
                "current_company_name": company or "Contexte Lab",
                "company_website": "https://example.com",
                "company_industry": "Conseil & Digital",
                "past_companies": ["Cabinet Antérieur"],
                "recent_activity": ["Publication sur la transformation numérique"],
                "raw_data": {},
            }

        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "profileUrl": linkedin_url,
            "fullName": full_name,
            "company": company,
            "city": city,
        }

        try:
            res = requests.post(APIFY_ACTOR_URL, json=payload, headers=headers, timeout=60)
            res.raise_for_status()
            items = res.json()
            if isinstance(items, list) and len(items) > 0:
                item = items[0]
                return {
                    "current_job_title": item.get("jobTitle") or item.get("current_job_title") or "",
                    "current_job_start_date": item.get("currentJobStartDate"),
                    "current_company_name": item.get("companyName") or item.get("current_company_name") or company or "",
                    "company_website": item.get("companyWebsite"),
                    "company_industry": item.get("companyIndustry"),
                    "past_companies": item.get("pastCompanies") or [],
                    "recent_activity": item.get("recentActivity") or [],
                    "raw_data": item,
                }
        except Exception as e:
            logger.error("Apify harvest request failed: %s", e)

        return {
            "current_job_title": "",
            "current_job_start_date": None,
            "current_company_name": company or "",
            "company_website": None,
            "company_industry": None,
            "past_companies": [],
            "recent_activity": [],
            "raw_data": {},
        }
