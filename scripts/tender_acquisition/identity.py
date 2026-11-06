"""Resolve a tender company name to one supported company identity."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from scripts.kerix_client import KerixClient

from .linkedin_direct import LinkedInMcp, linkedin_scope, parse_observations


COMPANY_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['candidates'],
    'properties': {'candidates': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['name', 'linkedin_url', 'website', 'city', 'sector', 'description', 'phone', 'country', 'urn'],
        'properties': {key: {'type': ['string', 'null']} for key in
                       ('name', 'linkedin_url', 'website', 'city', 'sector', 'description', 'phone', 'country', 'urn')},
    }}},
}


def normalized(value: str | None) -> str:
    value = unicodedata.normalize('NFKD', (value or '').casefold())
    return ' '.join(re.findall(r'[^\W_]+', ''.join(c for c in value if not unicodedata.combining(c))))


def domain(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value if '://' in value else 'https://' + value
    host = urlparse(candidate).hostname
    if not host or '.' not in host or host.endswith('.linkedin.com'):
        return None
    return host.removeprefix('www.').casefold()


@dataclass(frozen=True)
class CompanyIdentity:
    name: str
    linkedin_url: str | None
    domain: str | None
    website: str | None
    city: str | None
    sector: str | None
    description: str | None
    phone: str | None
    urn: str | None
    registry_id: str | None = None


def choose_company(name: str, linkedin: list[dict], kerix: list[dict]) -> CompanyIdentity | None:
    exact = [row for row in linkedin if normalized(row.get('name')) == normalized(name)
             and isinstance(row.get('linkedin_url'), str)
             and '/company/' in row['linkedin_url']]
    kerix_named = [row for row in kerix if normalized(row.get('companyName')) == normalized(name)]
    if not exact:
        legal = [row for row in kerix_named if row.get('registryId')]
        if len(legal) != 1:
            return None
        row = legal[0]
        return CompanyIdentity(name=row['companyName'], linkedin_url=None,
                               domain=domain(row.get('website')), website=row.get('website'),
                               city=row.get('city'), sector=row.get('companyDescriptionTextShort'),
                               description=row.get('companyDescriptionTextShort'),
                               phone=row.get('phone'), urn=None, registry_id=row['registryId'])
    by_url = {row['linkedin_url'].rstrip('/').casefold(): row for row in exact}
    if len(by_url) != 1:
        corroborated = [row for row in by_url.values() if any(
            domain(row.get('website')) and domain(row.get('website')) == domain(k.get('website'))
            for k in kerix_named)]
        if len(corroborated) != 1:
            return None
        row = corroborated[0]
    else:
        row = next(iter(by_url.values()))
    site_domain = domain(row.get('website'))
    matching_kerix = [r for r in kerix_named
                      if ((site_domain and domain(r.get('website')) == site_domain)
                           or (r.get('registryId') and row.get('registry_id') == r.get('registryId')))]
    if len(matching_kerix) > 1:
        return None
    if kerix_named and not matching_kerix and site_domain:
        return None
    country = normalized(row.get('country'))
    morocco = any(word in country for word in ('maroc', 'morocco', 'المغرب'))
    if not matching_kerix and not (site_domain and (morocco or site_domain.endswith('.ma'))):
        return None
    kerix_row = matching_kerix[0] if matching_kerix else {}
    return CompanyIdentity(name=row['name'], linkedin_url=row['linkedin_url'], domain=site_domain,
                           website=row.get('website'), city=row.get('city') or kerix_row.get('city'),
                           sector=row.get('sector'), description=row.get('description'),
                           phone=row.get('phone') or kerix_row.get('phone'), urn=row.get('urn'),
                           registry_id=kerix_row.get('registryId'))


def search_linkedin_company(name: str) -> list[dict]:
    observations = []
    with linkedin_scope(LinkedInMcp) as mcp:
        search = mcp.call('search_companies', {'keywords': name})
        references = search.get('references', {}).get('search_results') or []
        plausible = [ref for ref in references if isinstance(ref, dict)
                     and ref.get('kind') == 'company'
                     and normalized(name) in normalized(ref.get('text'))
                     and isinstance(ref.get('url'), str)
                     and ref['url'].startswith('/company/')]
        if not plausible or len(plausible) > 6:
            return []
        for ref in plausible:
            url = urljoin('https://www.linkedin.com', ref['url']).rstrip('/') + '/'
            profile = mcp.call('get_company_profile', {'company_name': url})
            observations.append({'linkedin_url': url, 'profile': profile})
    prompt = ('Extract the exact observed company profile fields. Include all profiles; '
              'copy exact strings and use null for unavailable fields. '
              'linkedin_url must equal the observation URL. Search target: '
              + json.dumps(name, ensure_ascii=False))
    data = parse_observations(prompt, COMPANY_SCHEMA, observations)
    rows = data.get('candidates')
    if not isinstance(rows, list) or len(rows) > 30:
        raise ValueError('Invalid company candidates')
    by_url = {item['linkedin_url'].rstrip('/').casefold(): item['profile'] for item in observations}
    verified = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('linkedin_url'), str):
            continue
        profile = by_url.get(row['linkedin_url'].rstrip('/').casefold())
        if profile is None:
            continue
        observed = json.dumps(profile, ensure_ascii=False).casefold()
        for field in ('name', 'website', 'city', 'sector', 'description', 'phone', 'country', 'urn'):
            value = row.get(field)
            if value and (not isinstance(value, str) or value.casefold() not in observed):
                row[field] = None
        verified.append(row)
    return verified


def resolve_company(name: str, *, linkedin_search=search_linkedin_company,
                    kerix_client=None) -> CompanyIdentity | None:
    linkedin = linkedin_search(name)
    try:
        kerix = (kerix_client or KerixClient()).search(name)
    except RuntimeError:
        kerix = []
    return choose_company(name, linkedin, kerix)
