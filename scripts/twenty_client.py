import json
import logging
from typing import Any, Dict, List, Optional
import requests
from scripts.email_hygiene import is_personal_email
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
        import os
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("TWENTY_API_KEY")
        self.headers: Dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            self.headers["Authorization"] = f"Bearer {self.api_key}"

        # In-memory deduplication cache for companies: normalized_name -> company_id
        self._company_cache: Dict[str, str] = {}
        self.workflow_store = None

    def workflow_request(self, method, path, *, payload=None, params=None):
        """Strict adapter: errors never become a fake ID or an empty search."""
        if method in ('POST','PATCH') and isinstance(payload,dict):
            emails = payload.get('emails') or {}
            if isinstance(emails,dict) and (is_personal_email(emails.get('primaryEmail'))
                    or any(is_personal_email(e) for e in emails.get('additionalEmails',[]) or [])):
                raise ValueError('PERSONAL_EMAIL_FORBIDDEN')
        if not self.api_key:
            raise RuntimeError('TWENTY_API_KEY_MISSING')
        operation=None
        if method=='GET' and self.workflow_store is not None:
            from datetime import datetime, timezone
            operation=self.workflow_store.operation(None,'CRM_READ',{'path':path,'params':params})
            if operation['status']=='NEEDS_REVIEW':
                raise RuntimeError('TWENTY_READ_RETRY_EXHAUSTED')
            if operation['next_attempt_at'] and datetime.fromisoformat(operation['next_attempt_at'])>datetime.now(timezone.utc):
                raise RuntimeError('TWENTY_READ_BACKOFF')
        try:
            response = requests.request(method,self.base_url + path,json=payload,params=params,
                                        headers=self.headers,timeout=(5,30),allow_redirects=False)
            if response.status_code == 404 and method == 'GET':
                data=None
            else:
                response.raise_for_status()
                data={} if response.status_code==204 else response.json()
            if operation:
                with self.workflow_store.connection() as c:
                    c.execute("UPDATE workflow_operations SET status='SUCCEEDED',result=?,attempts=0,next_attempt_at=NULL WHERE id=?",(json.dumps(data),operation['id']))
            return data
        except (requests.RequestException,ValueError):
            if operation:
                from datetime import datetime, timezone, timedelta
                attempts=operation['attempts']+1
                with self.workflow_store.connection() as c:
                    c.execute('UPDATE workflow_operations SET status=?,attempts=?,next_attempt_at=? WHERE id=?',
                              ('NEEDS_REVIEW' if attempts>=3 else 'WAITING',attempts,
                               (datetime.now(timezone.utc)+timedelta(seconds=60*2**(attempts-1))).isoformat(),operation['id']))
            raise RuntimeError('TWENTY_REQUEST_FAILED') from None

    def inspect_workflow_schema(self):
        schema = self.workflow_request('GET','/rest/open-api/core')
        if not isinstance(schema,dict) or not isinstance(schema.get('paths'),dict):
            raise RuntimeError('TWENTY_SCHEMA_UNAVAILABLE')
        return schema

    def workflow_fields(self, resource, schema):
        document = schema['paths'].get('/'+resource) or schema['paths'].get('/rest/'+resource)
        if not document or 'post' not in document:
            raise RuntimeError('TWENTY_RESOURCE_UNAVAILABLE')
        node = document['post'].get('requestBody',{}).get('content',{}).get('application/json',{}).get('schema',{})
        visited = set()
        def fields(value):
            if '$ref' in value:
                ref = value['$ref']
                if ref in visited or not ref.startswith('#/'):
                    return set()
                visited.add(ref)
                resolved = schema
                for part in ref[2:].split('/'):
                    resolved = resolved.get(part,{})
                return fields(resolved)
            result = set(value.get('properties',{}))
            for kind in ('allOf','oneOf','anyOf'):
                for child in value.get(kind,[]):
                    result.update(fields(child))
            return result
        result = fields(node)
        if not result:
            raise RuntimeError('TWENTY_FIELDS_UNAVAILABLE')
        return result

    def workflow_list(self, resource):
        records, cursor, seen = [], None, set()
        while True:
            params = {'limit':60}
            if cursor:
                params['starting_after'] = cursor
            result = self.workflow_request('GET','/rest/'+resource,params=params)
            data = (result or {}).get('data',{})
            rows = data.get(resource)
            if not isinstance(rows,list):
                raise RuntimeError('TWENTY_LIST_CONTRACT_MISMATCH')
            records.extend(rows)
            page = (result or {}).get('pageInfo') or data.get('pageInfo')
            if page and not page.get('hasNextPage'):
                return records
            if not page and len(rows)<60:
                return records
            next_cursor = page.get('endCursor') if page else None
            if not next_cursor or next_cursor in seen:
                raise RuntimeError('TWENTY_PAGINATION_REQUIRES_REVIEW')
            seen.add(next_cursor)
            cursor = next_cursor

    def workflow_get(self, resource, record_id):
        result = self.workflow_request('GET','/rest/'+resource+'/'+record_id)
        if result is None:
            return None
        data = result.get('data',{})
        singular = {'people':'person','companies':'company','opportunities':'opportunity'}[resource]
        row = data.get(singular) or data.get('find'+singular.capitalize()) or data
        if not isinstance(row,dict) or row.get('id') != record_id:
            raise RuntimeError('TWENTY_READBACK_MISMATCH')
        return row

    def workflow_create(self, resource, payload):
        result = self.workflow_request('POST','/rest/'+resource,payload=payload)
        singular = {'people':'person','companies':'company','opportunities':'opportunity'}[resource]
        identity = self._extract_id(result,singular)
        if not identity or identity.startswith('dummy-'):
            raise RuntimeError('TWENTY_CREATE_UNCONFIRMED')
        return identity

    def workflow_patch(self, resource, identity, payload):
        self.workflow_request('PATCH','/rest/'+resource+'/'+identity,payload=payload)

    def workflow_delete(self, resource, identity):
        self.workflow_request('DELETE','/rest/'+resource+'/'+identity)

    def workflow_person_history(self, person_id):
        try:
            participants=self.workflow_list('messageParticipants')
        except RuntimeError:
            return None
        # Unknown relation shapes cannot prove an absence of communications.
        if any(not isinstance(row,dict) or 'personId' not in row for row in participants):
            return None
        return [row for row in participants if row.get('personId')==person_id]

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

        if contact.linkedin_url:
            payload["linkedinLink"] = {
                "primaryLinkLabel": "LinkedIn",
                "primaryLinkUrl": contact.linkedin_url,
                "secondaryLinks": []
            }

        endpoint = f"{self.base_url}/rest/people"
        res = requests.post(endpoint, json=payload, headers=self.headers)
        if res.status_code >= 400:
            import logging
            logging.getLogger(__name__).error("Twenty API Error %s: %s for payload: %s", res.status_code, res.text, payload)
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

    def find_company_by_name(self, name: str) -> Optional[str]:
        """Look up existing company by name in Twenty CRM."""
        if not name or not name.strip():
            return None

        norm_name = name.strip().lower()
        if norm_name in self._company_cache:
            return self._company_cache[norm_name]

        endpoint = f"{self.base_url}/rest/companies"
        try:
            res = requests.get(
                endpoint,
                params={"filter": json.dumps({"name": {"eq": name.strip()}})},
                headers=self.headers,
            )
            res.raise_for_status()
            data = res.json()
            companies = data.get("data", {}).get("companies", [])
            if companies:
                cid = str(companies[0].get("id"))
                self._company_cache[norm_name] = cid
                return cid
        except Exception as e:
            logger.error("Error looking up company '%s': %s", name, e)
        return None

    def find_person_by_id(self, person_id: str) -> Optional[Dict[str, Any]]:
        """Fetch person record from Twenty CRM."""
        endpoint = f"{self.base_url}/rest/people/{person_id}"
        try:
            res = requests.get(endpoint, headers=self.headers)
            res.raise_for_status()
            data = res.json()
            return data.get("data", {})
        except Exception as e:
            logger.error("Error fetching person %s: %s", person_id, e)
            return None

    def create_opportunity(
        self,
        name: str,
        stage: str,
        person_id: str,
        company_id: Optional[str] = None,
        amount: float = 0.0,
    ) -> str:
        """Create a new commercial Opportunity in Twenty CRM."""
        payload: Dict[str, Any] = {
            "name": name,
            "stage": stage,
            "pointOfContactId": person_id,
        }
        if company_id:
            payload["companyId"] = company_id
        if amount > 0:
            payload["amount"] = {"amountMicros": int(amount * 1_000_000), "currencyCode": "MAD"}

        endpoint = f"{self.base_url}/rest/opportunities"
        res = requests.post(endpoint, json=payload, headers=self.headers)
        res.raise_for_status()
        data = res.json() if res.content else {}
        return self._extract_id(data, "opportunity")

    def sync_career_change(
        self,
        person_id: str,
        harvest_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Proactively manage job/company change in Twenty CRM."""
        person = self.find_person_by_id(person_id) or {}
        existing_company_info = person.get("company") or {}
        old_company_name = existing_company_info.get("name") or ""
        new_company_name = harvest_data.get("current_company_name", "").strip()
        new_job_title = harvest_data.get("current_job_title", "").strip()

        if not new_company_name or new_company_name.lower() == old_company_name.lower():
            # No company change, simple metadata refresh
            return {
                "career_changed": False,
                "old_company": old_company_name,
                "new_company": old_company_name,
            }

        # Company changed: check if new company exists or create it
        new_comp_id = self.find_company_by_name(new_company_name)
        if not new_comp_id:
            co_record = CompanyRecord(
                name=new_company_name,
                domain_name=harvest_data.get("company_website"),
                activity_sector=harvest_data.get("company_industry"),
                source_file="harvest_direct",
            )
            new_comp_id = self.upsert_company(co_record)

        # Update person with new companyId, jobTitle, and enrichmentStatus
        patch_payload = {
            "companyId": new_comp_id,
            "enrichmentStatus": "ENRICHED",
        }
        if new_job_title:
            patch_payload["jobTitle"] = new_job_title

        endpoint = f"{self.base_url}/rest/people/{person_id}"
        patch_res = requests.patch(endpoint, json=patch_payload, headers=self.headers)
        patch_res.raise_for_status()

        return {
            "career_changed": True,
            "old_company": old_company_name,
            "new_company": new_company_name,
            "new_company_id": new_comp_id,
            "new_job_title": new_job_title,
        }
