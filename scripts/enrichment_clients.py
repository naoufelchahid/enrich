"""Direct provider contracts. No automatic retries: even Harvest GETs can cost money.

FullEnrich v2: https://docs.fullenrich.com/api/v2/contact/enrich/bulk/post
Harvest: https://github.com/HarvestAPI/harvestapi-docs (OpenAPI snapshot 2026-10-06)
"""
import os
from urllib.parse import urlparse, unquote

import requests


class ProviderError(RuntimeError):
    def __init__(self, code, *, uncertain=False):
        super().__init__(code)  # Never include credentials, response bodies or request URLs.
        self.uncertain = uncertain


def linkedin_url(value):
    """Accept personal public URLs only; canonicalize without query or fragment."""
    if not isinstance(value, str):
        return None
    try:
        p = urlparse(value.strip())
        if (p.scheme not in ('http', 'https') or p.username or p.password or p.port
                or not (p.hostname == 'linkedin.com' or (p.hostname or '').endswith('.linkedin.com'))):
            return None
        parts = p.path.strip('/').split('/')
        if len(parts) != 2 or parts[0] != 'in' or not parts[1] or '/' in unquote(parts[1]):
            return None
        return 'https://www.linkedin.com/in/' + parts[1].lower()
    except ValueError:
        return None


def _json(response, *, paid=False):
    if not response.ok:
        raise ProviderError(f'HTTP_{response.status_code}', uncertain=paid and response.status_code >= 500)
    try:
        data = response.json()
    except ValueError:
        raise ProviderError('INVALID_JSON', uncertain=paid) from None
    if not isinstance(data, dict):
        raise ProviderError('INVALID_RESPONSE', uncertain=paid)
    return data


class FullEnrichClient:
    BASE = 'https://app.fullenrich.com/api/v2'

    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv('FULLENRICH_API_KEY')

    def _request(self, method, path, **kwargs):
        if not self.api_key:
            raise ProviderError('FULLENRICH_KEY_MISSING')
        try:
            return requests.request(method, self.BASE + path,
                                    headers={'Authorization': 'Bearer ' + self.api_key,
                                             'Content-Type': 'application/json'},
                                    timeout=(5, 30), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise ProviderError('NETWORK_ERROR', uncertain=method == 'POST') from None

    def credits(self):
        data = _json(self._request('GET', '/account/credits'))
        balance = data.get('balance')
        if isinstance(balance, bool) or not isinstance(balance, (int, float)) or balance < 0:
            raise ProviderError('INVALID_CREDIT_BALANCE')
        return balance

    def submit(self, payload):
        data = payload.get('data')
        if not payload.get('name') or not isinstance(data, list) or not 1 <= len(data) <= 100:
            raise ValueError('FullEnrich requires a named batch of 1..100 contacts')
        for item in data:
            if item.get('enrich_fields') != ['contact.work_emails']:
                raise ValueError('Only work email enrichment is enabled')
            if not linkedin_url(item.get('linkedin_url')):
                raise ValueError('A personal LinkedIn URL is required before email enrichment')
            if any(key in item for key in ('first_name', 'last_name', 'company_name', 'domain')):
                raise ValueError('New email enrichment uses LinkedIn URL identity only')
            if not all(isinstance(v, str) for v in item.get('custom', {}).values()):
                raise ValueError('FullEnrich custom values must be strings')
        result = _json(self._request('POST', '/contact/enrich/bulk', json=payload,
                                     params={'silentFail': 'false'}), paid=True)
        remote_id = result.get('enrichment_id')
        if not isinstance(remote_id, str) or not remote_id.strip():
            raise ProviderError('MISSING_ENRICHMENT_ID', uncertain=True)
        return remote_id

    def result(self, remote_id):
        from urllib.parse import quote
        response = self._request('GET', '/contact/enrich/bulk/' + quote(remote_id, safe=''))
        if response.status_code == 400:
            try:
                if response.json().get('code') == 'error.enrichment.in_progress':
                    return None
            except (ValueError, AttributeError):
                pass
        data = _json(response)
        if data.get('status') in ('CREATED', 'IN_PROGRESS', 'RATE_LIMIT'):
            return None
        return data


class HarvestClient:
    """Retired compatibility interface: never perform a Harvest network call."""
    def __init__(self, api_key=None):
        self.api_key = None

    @staticmethod
    def _retired(*args, **kwargs):
        raise ProviderError('HARVEST_RETIRED_USE_CODEX_CHROME')

    profile = _retired
    posts = _retired
    search_profiles = _retired
