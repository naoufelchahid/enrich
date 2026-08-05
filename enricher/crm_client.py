import requests
import json
import logging
import time
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class TwentyCRMClient:
    def __init__(self, api_base_url: str, api_key: str):
        self.api_base_url = api_base_url.rstrip('/')
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    def _request_with_retry(self, method: str, url: str, **kwargs):
        req_func = getattr(requests, method.lower())
        resp = req_func(url, **kwargs)
        if resp.status_code == 429:
            logger.warning(f"Rate limited (429) on {method} {url}. Please wait for next cron run.")
            resp.raise_for_status()
        return resp

    def search_company(self, name: str) -> Optional[Dict[str, Any]]:
        """Search company by exact or partial name in Twenty CRM."""
        url = f"{self.api_base_url}/companies"
        params = {
            "filter[name][eq]": name,
            "limit": 1
        }
        try:
            resp = self._request_with_retry("get", url, headers=self.headers, params=params)
            if resp.status_code == 200:
                data = resp.json().get("data", {}).get("companies", [])
                if data:
                    return data[0]
            else:
                logger.warning(f"CRM search failed [{resp.status_code}]: {resp.text}")
        except Exception as e:
            logger.error(f"Error searching company {name} in Twenty CRM: {e}")
        return None

    def upsert_company(self, company_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Creates or updates a company record in Twenty CRM."""
        name = company_data.get("name")
        if not name:
            return None

        existing = self.search_company(name)
        if existing:
            company_id = existing["id"]
            url = f"{self.api_base_url}/companies/{company_id}"
            try:
                resp = self._request_with_retry("patch", url, headers=self.headers, json=company_data)
                if resp.status_code in (200, 201):
                    logger.info(f"Updated company {name} ({company_id}) in Twenty CRM")
                    return resp.json().get("data", {}).get("updateCompany", existing)
                else:
                    logger.error(f"Failed to update company {name}: {resp.text}")
            except Exception as e:
                logger.error(f"Exception updating company {name}: {e}")
            return existing
        else:
            return self.create_company(company_data)

    def create_company(self, company_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Creates a new company record in Twenty CRM without checking if it exists."""
        name = company_data.get("name")
        if not name:
            return None
        url = f"{self.api_base_url}/companies"
        try:
            resp = self._request_with_retry("post", url, headers=self.headers, json=company_data)
            if resp.status_code in (200, 201):
                created = resp.json().get("data", {}).get("createCompany", {})
                logger.info(f"Created company {name} in Twenty CRM")
                return created
            else:
                logger.error(f"Failed to create company {name}: {resp.text}")
        except Exception as e:
            logger.error(f"Exception creating company {name}: {e}")
        return None


    def create_person(self, person_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Creates a person record in Twenty CRM."""
        url = f"{self.api_base_url}/people"
        try:
            resp = self._request_with_retry("post", url, headers=self.headers, json=person_data)
            if resp.status_code in (200, 201):
                created = resp.json().get("data", {}).get("createPerson", {})
                first_name = person_data.get('name', {}).get('firstName', '')
                logger.info(f"Created person {first_name} in Twenty CRM")
                return created
            else:
                logger.error(f"Failed to create person: {resp.text}")
        except Exception as e:
            logger.error(f"Exception creating person: {e}")
        return None

    def add_note(self, company_id: str, title: str, note_body: str) -> bool:
        """Attaches a note to a company in Twenty CRM."""
        # First create the note
        note_url = f"{self.api_base_url}/notes"
        blocknote_json = json.dumps([
            {
                "id": "block-1",
                "type": "paragraph",
                "props": {},
                "content": [{"type": "text", "text": note_body, "styles": {}}],
                "children": []
            }
        ])
        payload = {
            "title": title,
            "bodyV2": {
                "blocknote": blocknote_json,
                "markdown": note_body
            }
        }
        try:
            resp = self._request_with_retry("post", note_url, headers=self.headers, json=payload)
            if resp.status_code in (200, 201):
                note_data = resp.json().get("data", {}).get("createNote", {})
                note_id = note_data.get("id")
                if note_id:
                    # Link note to company via noteTargets
                    target_url = f"{self.api_base_url}/noteTargets"
                    target_payload = {
                        "noteId": note_id,
                        "targetCompanyId": company_id
                    }
                    self._request_with_retry("post", target_url, headers=self.headers, json=target_payload)
                    logger.info(f"Attached note '{title}' to company ID {company_id}")
                    return True
            else:
                logger.warning(f"Failed to create note: {resp.text}")
        except Exception as e:
            logger.error(f"Error adding note to company {company_id}: {e}")
        return False

    def add_person_note(self, person_id: str, title: str, note_body: str) -> bool:
        """Attaches a note to a person in Twenty CRM."""
        note_url = f"{self.api_base_url}/notes"
        blocknote_json = json.dumps([
            {
                "id": "block-1",
                "type": "paragraph",
                "props": {},
                "content": [{"type": "text", "text": note_body, "styles": {}}],
                "children": []
            }
        ])
        payload = {
            "title": title,
            "bodyV2": {
                "blocknote": blocknote_json,
                "markdown": note_body
            }
        }
        try:
            resp = self._request_with_retry("post", note_url, headers=self.headers, json=payload)
            if resp.status_code in (200, 201):
                note_data = resp.json().get("data", {}).get("createNote", {})
                note_id = note_data.get("id")
                if note_id:
                    target_url = f"{self.api_base_url}/noteTargets"
                    target_payload = {
                        "noteId": note_id,
                        "targetPersonId": person_id
                    }
                    self._request_with_retry("post", target_url, headers=self.headers, json=target_payload)
                    logger.info(f"Attached note '{title}' to person ID {person_id}")
                    return True
            else:
                logger.warning(f"Failed to create note: {resp.text}")
        except Exception as e:
            logger.error(f"Error adding note to person {person_id}: {e}")
        return False

    def list_companies(self, limit: int = 50, filters: Dict[str, str] = None) -> list:
        """Fetch a list of companies from Twenty CRM."""
        url = f"{self.api_base_url}/companies"
        params = {"limit": limit}
        if filters:
            for k, v in filters.items():
                params[f"filter[{k}][eq]"] = v
        try:
            resp = self._request_with_retry("get", url, headers=self.headers, params=params)
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("companies", [])
            else:
                logger.warning(f"Failed to list companies [{resp.status_code}]: {resp.text}")
        except Exception as e:
            logger.error(f"Error listing companies: {e}")
        return []

    def update_company(self, company_id: str, company_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Updates an existing company record in Twenty CRM by ID."""
        url = f"{self.api_base_url}/companies/{company_id}"
        try:
            resp = self._request_with_retry("patch", url, headers=self.headers, json=company_data)
            if resp.status_code in (200, 201):
                logger.info(f"Updated company ID {company_id} in Twenty CRM")
                return resp.json().get("data", {}).get("updateCompany", {})
            else:
                logger.error(f"Failed to update company ID {company_id}: {resp.text}")
        except Exception as e:
            logger.error(f"Exception updating company ID {company_id}: {e}")
        return None

    def list_people(self, limit: int = 50, filters: Dict[str, str] = None) -> list:
        """Fetch a list of people from Twenty CRM."""
        url = f"{self.api_base_url}/people"
        params = {"limit": limit}
        if filters:
            for k, v in filters.items():
                params[f"filter[{k}][eq]"] = v
        try:
            resp = self._request_with_retry("get", url, headers=self.headers, params=params)
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("people", [])
            else:
                logger.warning(f"Failed to list people [{resp.status_code}]: {resp.text}")
        except Exception as e:
            logger.error(f"Error listing people: {e}")
        return []

    def update_person(self, person_id: str, person_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Updates an existing person record in Twenty CRM by ID."""
        url = f"{self.api_base_url}/people/{person_id}"
        try:
            resp = self._request_with_retry("patch", url, headers=self.headers, json=person_data)
            if resp.status_code in (200, 201):
                logger.info(f"Updated person ID {person_id} in Twenty CRM")
                return resp.json().get("data", {}).get("updatePerson", {})
            else:
                logger.error(f"Failed to update person ID {person_id}: {resp.text}")
        except Exception as e:
            logger.error(f"Exception updating person ID {person_id}: {e}")
        return None

