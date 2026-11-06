"""Phone and work-email qualification for confirmed LinkedIn contacts."""

from __future__ import annotations

from dataclasses import dataclass

from scripts.email_hygiene import email_identity, valid_email
from scripts.enrichment_clients import FullEnrichClient, ProviderError, _json, linkedin_url
from scripts.no2bounce_client import No2BounceClient

from .contacts import Contact
from .identity import CompanyIdentity, domain, normalized
from .state import OperationStore


class FullEnrichTenderClient(FullEnrichClient):
    """New contract isolated from the existing email-only client."""

    def submit(self, payload):
        data = payload.get('data')
        if not payload.get('name') or not isinstance(data, list) or len(data) != 1:
            raise ValueError('Expected one confirmed contact')
        item = data[0]
        if item.get('enrich_fields') != ['contact.work_emails', 'contact.phones']:
            raise ValueError('Expected work email and phone enrichment')
        if not linkedin_url(item.get('linkedin_url')) or set(item) != {'linkedin_url', 'enrich_fields'}:
            raise ValueError('Confirmed personal LinkedIn URL required')
        result = _json(self._request('POST', '/contact/enrich/bulk', json=payload,
                                     params={'silentFail': 'false'}), paid=True)
        remote_id = result.get('enrichment_id')
        if not isinstance(remote_id, str) or not remote_id.strip():
            raise ProviderError('MISSING_ENRICHMENT_ID', uncertain=True)
        return remote_id


@dataclass(frozen=True)
class QualifiedContact:
    person: Contact
    email: str | None
    phone: str | None


def parse_fullenrich(result: dict, contact: Contact, company: CompanyIdentity) -> tuple[str | None, str | None]:
    if result.get('status') != 'FINISHED' or not isinstance(result.get('data'), list) or len(result['data']) != 1:
        raise ValueError('FullEnrich result incomplete')
    record = result['data'][0]
    profile = record.get('profile') or {}
    if profile.get('first_name') and normalized(profile['first_name']) != normalized(contact.first_name):
        raise ValueError('FullEnrich person mismatch')
    if profile.get('last_name') and normalized(profile['last_name']) != normalized(contact.last_name):
        raise ValueError('FullEnrich person mismatch')
    current = ((profile.get('employment') or {}).get('current') or {}).get('company') or {}
    if current.get('domain') and company.domain and domain(current['domain']) != company.domain:
        raise ValueError('FullEnrich company mismatch')
    info = record.get('contact_info') or {}
    likely_email = info.get('most_probable_work_email') or {}
    raw_email = likely_email.get('email')
    email = raw_email.strip().lower() if (likely_email.get('status') == 'DELIVERABLE'
            and valid_email(raw_email) and email_identity(raw_email, contact.first_name,
                                                           contact.last_name)['accepted']) else None
    likely_phone = info.get('most_probable_phone') or {}
    number = likely_phone.get('number')
    phone = number.strip() if (isinstance(number, str) and number.strip().startswith('+')
            and likely_phone.get('line_status') == 'ACTIVE'
            and likely_phone.get('line_type') in ('MOBILE', 'LANDLINE')
            and likely_phone.get('ownership_match') != 'MISMATCH') else None
    return email, phone


def _submit_once(store: OperationStore, kind: str, subject_kind: str, subject: str, submit):
    op = store.reserve(kind, subject_kind, subject)
    if op['state'] == 'SUCCEEDED' and op['remote_id']:
        return op['remote_id']
    if op['state'] not in ('PENDING', 'RETRYABLE'):
        return None
    store.begin(op['id'])
    try:
        remote_id = submit()
    except ProviderError as exc:
        store.finish(op['id'], 'UNKNOWN' if exc.uncertain else 'RETRYABLE', outcome=str(exc))
        return None
    except Exception:
        store.finish(op['id'], 'UNKNOWN')
        return None
    store.finish(op['id'], 'SUCCEEDED', remote_id=str(remote_id), outcome='submitted')
    return str(remote_id)


def enrich_contact(contact: Contact, company: CompanyIdentity, store: OperationStore, *,
                   fullenrich=None, no2bounce=None) -> QualifiedContact | None:
    fullenrich = fullenrich or FullEnrichTenderClient()
    no2bounce = no2bounce or No2BounceClient()
    url = linkedin_url(contact.linkedin_url)
    if not url:
        return None
    payload = {'name': 'tender-company-contact', 'data': [{
        'linkedin_url': url, 'enrich_fields': ['contact.work_emails', 'contact.phones']}]}
    remote_id = _submit_once(store, 'FULLENRICH', 'person_linkedin', url,
                             lambda: fullenrich.submit(payload))
    if not remote_id:
        return None
    try:
        result = fullenrich.result(remote_id)
    except ProviderError:
        return None
    if result is None:
        return None
    try:
        email, phone = parse_fullenrich(result, contact, company)
    except (ValueError, AttributeError, TypeError):
        return None
    verified_email = None
    if email:
        tracking_id = _submit_once(store, 'NO2BOUNCE', 'email', email,
                                   lambda: no2bounce.submit([email]))
        if tracking_id:
            try:
                verdict = no2bounce.result(tracking_id)
                if verdict and verdict.get('verdicts', {}).get(email) == 'VALID':
                    verified_email = email
            except ProviderError:
                pass
    if not verified_email and not phone:
        return None
    return QualifiedContact(contact, verified_email, phone)
