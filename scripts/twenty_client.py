import logging
from typing import Any, Dict, List, Optional
import requests
from scripts.models import (
    CampaignStatusEnum,
    CompanyRecord,
    EnrichmentStatusEnum,
    NormalizedContact,
)

logger = logging.getLogger(__name__)


class TwentyClient:
    """Client for Twenty CRM REST API.

    Handles company deduplication/upsert, contact creation with deliverability/enrichment tagging,
    email purging on bounce, and bulk synchronization.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:3005",
        api_key: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers: Dict[str, str] = {
            "Content-Type": "application/json",
        }
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

        # In-memory deduplication cache for companies: normalized_name -> company_id
        self._company_cache: Dict[str, str] = {}

    def _extract_id(self, data: Dict[str, Any], entity_name: str) -> str:
        """Extract entity ID from various Twenty REST API response shapes."""
        data_block = data.get("data", {})
        if isinstance(data_block, dict):
            # Check create<Entity> / update<Entity>
            for key in (
                f"create{entity_name.capitalize()}",
                f"update{entity_name.capitalize()}",
                entity_name.lower(),
            ):
                if key in data_block and isinstance(data_block[key], dict):
                    extracted = data_block[key].get("id")
                    if extracted:
                        return str(extracted)
            if "id" in data_block and data_block["id"]:
                return str(data_block["id"])

        if "id" in data and data["id"]:
            return str(data["id"])

        return f"dummy-{entity_name}-id"

    def upsert_company(self, company: CompanyRecord) -> str:
        """Upsert a company record, deduplicating against the internal cache.

        Returns companyId.
        """
        normalized_name = company.name.strip().lower()
        if normalized_name in self._company_cache:
            logger.debug(
                "Company '%s' found in cache: %s",
                company.name,
                self._company_cache[normalized_name],
            )
            return self._company_cache[normalized_name]

        payload: Dict[str, Any] = {
            "name": company.name,
        }
        if company.domain_name and company.domain_name.strip():
            clean_dom = company.domain_name.strip()
            label = clean_dom.replace("https://", "").replace("http://", "").rstrip("/")
            url = clean_dom if clean_dom.startswith("http") else f"https://{clean_dom}"
            payload["domainName"] = {
                "primaryLinkLabel": label,
                "primaryLinkUrl": url,
            }
        if company.address or company.city:
            addr_obj: Dict[str, Any] = {}
            if company.address:
                addr_obj["addressStreet1"] = company.address
            if company.city:
                addr_obj["addressCity"] = company.city
            payload["address"] = addr_obj
        if company.activity_sector:
            payload["activitySector"] = company.activity_sector

        endpoint = f"{self.base_url}/rest/companies"
        res = requests.post(endpoint, json=payload, headers=self.headers)
        res.raise_for_status()
        data = res.json() if res.content else {}
        company_id = self._extract_id(data, "company")

        self._company_cache[normalized_name] = company_id
        return company_id

    def upsert_person(
        self,
        contact: NormalizedContact,
        company_id: Optional[str] = None,
    ) -> str:
        """Create/upsert a contact (person) in Twenty CRM.

        Enforces the deliverability and enrichment rules:
        - Deliverable/clean emails: injected with campaignStatus=READY_TO_MAIL.
        - Purged/invalid/missing emails: created WITHOUT active email (emails=None)
          and tagged with appropriate enrichmentStatus.
        """
        payload: Dict[str, Any] = {
            "name": {
                "firstName": contact.first_name or "",
                "lastName": contact.last_name or "",
            },
            "jobTitle": contact.job_title or "",
            "companyId": company_id,
        }

        # Deliverability rule
        if contact.email:
            payload["emails"] = {"primaryEmail": contact.email}
            payload["campaignStatus"] = CampaignStatusEnum.READY_TO_MAIL.value
            payload["enrichmentStatus"] = (
                contact.enrichment_status.value
                if contact.enrichment_status
                else EnrichmentStatusEnum.COMPLET.value
            )
        else:
            payload["emails"] = None
            payload["campaignStatus"] = None
            if contact.enrichment_status in (
                EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE,
                EnrichmentStatusEnum.A_ENRICHIR_EMAIL_MANQUANT,
                EnrichmentStatusEnum.A_ENRICHIR_DONNEES_INCOMPLETES,
            ):
                payload["enrichmentStatus"] = contact.enrichment_status.value
            else:
                payload["enrichmentStatus"] = (
                    EnrichmentStatusEnum.A_ENRICHIR_EMAIL_MANQUANT.value
                )

        if contact.phone_mobile:
            payload["phones"] = {
                "primaryPhoneNumber": contact.phone_mobile,
                "primaryPhoneCountryCode": "MA",
                "primaryPhoneCallingCode": "+212",
            }

        if contact.city:
            payload["city"] = contact.city

        if contact.source_file:
            payload["sourceFile"] = contact.source_file

        endpoint = f"{self.base_url}/rest/people"
        res = requests.post(endpoint, json=payload, headers=self.headers)
        res.raise_for_status()
        data = res.json() if res.content else {}
        return self._extract_id(data, "person")

    def purge_bounced_email(self, person_id: str) -> Dict[str, Any]:
        """Purge bounced email from contact in Twenty CRM.

        Wipes the email field and updates status to BOUNCED and A_ENRICHIR_EMAIL_INVALIDE.
        """
        payload = {
            "emails": None,
            "enrichmentStatus": EnrichmentStatusEnum.A_ENRICHIR_EMAIL_INVALIDE.value,
            "campaignStatus": CampaignStatusEnum.BOUNCED.value,
        }
        endpoint = f"{self.base_url}/rest/people/{person_id}"
        res = requests.patch(endpoint, json=payload, headers=self.headers)
        res.raise_for_status()
        return res.json() if res.content else {}

    def update_campaign_status(
        self,
        person_id: str,
        status: Any,
    ) -> Dict[str, Any]:
        """Update campaign status for a person in Twenty CRM."""
        val = status.value if hasattr(status, "value") else str(status)
        payload = {"campaignStatus": val}
        endpoint = f"{self.base_url}/rest/people/{person_id}"
        res = requests.patch(endpoint, json=payload, headers=self.headers)
        res.raise_for_status()
        return res.json() if res.content else {}

    def batch_sync(
        self,
        contacts: List[NormalizedContact],
        continue_on_error: bool = False,
    ) -> Dict[str, Any]:
        """Bulk synchronizes a list of normalized contacts into Twenty CRM.

        Automatically deduplicates companies, creates companies and associates people,
        and enforces delivery/enrichment tagging.
        """
        people_ids: List[str] = []
        errors: List[Dict[str, Any]] = []

        for contact in contacts:
            try:
                company_id: Optional[str] = None
                if contact.company_name and contact.company_name.strip():
                    company_record = CompanyRecord(
                        name=contact.company_name.strip(),
                        activity_sector=contact.activity_sector,
                        domain_name=contact.website,
                        city=contact.city,
                        address=contact.address,
                        phone_standard=contact.phone_standard,
                        source_file=contact.source_file,
                    )
                    company_id = self.upsert_company(company_record)

                person_id = self.upsert_person(contact, company_id=company_id)
                people_ids.append(person_id)
            except Exception as e:
                logger.error("Error syncing contact %s: %s", contact, e)
                if continue_on_error:
                    errors.append({"contact": contact, "error": str(e)})
                else:
                    raise

        return {
            "synced": len(people_ids),
            "companies_count": len(self._company_cache),
            "people_count": len(people_ids),
            "people": people_ids,
            "errors": errors,
        }
