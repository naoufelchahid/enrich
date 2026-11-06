"""Find up to two current employees using read-only LinkedIn MCP calls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from .identity import CompanyIdentity, normalized
from .linkedin_direct import LinkedInMcp, linkedin_scope, parse_observations


CONTACT_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['candidates'],
    'properties': {'candidates': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['first_name', 'last_name', 'title', 'role', 'linkedin_url',
                     'current_employer_url', 'current_employer_urn', 'current_employer_name', 'current'],
        'properties': {
            **{key: {'type': ['string', 'null']} for key in
               ('first_name', 'last_name', 'title', 'linkedin_url', 'current_employer_url',
                'current_employer_urn', 'current_employer_name')},
            'role': {'type': 'string', 'enum': ['LEADER', 'COMMERCIAL']},
            'current': {'type': 'boolean'},
        },
    }}},
}


@dataclass(frozen=True)
class Contact:
    first_name: str
    last_name: str
    title: str | None
    role: str
    linkedin_url: str
    company_name: str


def personal_url(url: str | None) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return parsed.scheme == 'https' and parsed.hostname in ('linkedin.com', 'www.linkedin.com') \
        and parsed.path.startswith('/in/') and len(parsed.path.strip('/').split('/')) == 2


def choose_contacts(identity: CompanyIdentity, candidates: list[dict]) -> list[Contact]:
    selected = []
    for role in ('LEADER', 'COMMERCIAL'):
        valid = []
        for row in candidates:
            if not isinstance(row, dict) or row.get('role') != role or row.get('current') is not True:
                continue
            if not all(isinstance(row.get(key), str) and row[key].strip()
                       for key in ('first_name', 'last_name', 'linkedin_url')):
                continue
            if not personal_url(row['linkedin_url']):
                continue
            company_url = (row.get('current_employer_url') or '').rstrip('/').casefold()
            company_urn = str(row.get('current_employer_urn') or '')
            linked = ((identity.linkedin_url and company_url == identity.linkedin_url.rstrip('/').casefold())
                      or (identity.urn and company_urn == str(identity.urn)))
            if not linked or normalized(row.get('current_employer_name')) != normalized(identity.name):
                continue
            valid.append(row)
        urls = {row['linkedin_url'].rstrip('/').casefold(): row for row in valid}
        if len(urls) != 1:
            continue
        row = next(iter(urls.values()))
        if any(c.linkedin_url.rstrip('/').casefold() == row['linkedin_url'].rstrip('/').casefold() for c in selected):
            continue
        selected.append(Contact(row['first_name'].strip(), row['last_name'].strip(),
                                row.get('title'), role, row['linkedin_url'], identity.name))
    return selected


def search_contacts(identity: CompanyIdentity, *, agent=parse_observations,
                    mcp_factory=LinkedInMcp) -> list[Contact]:
    if not identity.linkedin_url:
        return []
    observations = []
    queries = (('LEADER', 'directeur général dirigeant CEO'),
               ('COMMERCIAL', 'directeur commercial appels offres marchés publics'))
    with linkedin_scope(mcp_factory) as mcp:
        for role, words in queries:
            arguments = {'keywords': words + ' ' + identity.name}
            if identity.urn and str(identity.urn).isdigit():
                arguments['current_company'] = str(identity.urn)
            search = mcp.call('search_people', arguments)
            references = search.get('references', {}).get('search_results') or []
            urls = []
            for ref in references:
                if isinstance(ref, dict) and ref.get('kind') == 'person' and isinstance(ref.get('url'), str):
                    url = urljoin('https://www.linkedin.com', ref['url'])
                    if personal_url(url) and url not in urls:
                        urls.append(url)
            for url in urls[:3]:
                profile = mcp.call('get_person_profile', {'linkedin_username': url,
                                                          'sections': 'experience', 'max_scrolls': 1})
                observations.append({'role_hint': role, 'linkedin_url': url, 'profile': profile})
    if not observations:
        return []
    prompt = ('Extract only observed personal profile facts. role must match role_hint only if '
              'the observed current title supports it. current=true only when the experience '
              'section explicitly shows ongoing employment. Copy exact names and company identity. '
              'Unknown fields are null. Company identity: '
              + json.dumps(identity.__dict__, ensure_ascii=False))
    data = agent(prompt, CONTACT_SCHEMA, observations)
    rows = data.get('candidates')
    if not isinstance(rows, list) or len(rows) > 30:
        raise ValueError('Invalid contact candidates')
    by_url = {item['linkedin_url'].rstrip('/').casefold(): item for item in observations}
    verified = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('linkedin_url'), str):
            continue
        item = by_url.get(row['linkedin_url'].rstrip('/').casefold())
        if not item or row.get('role') != item['role_hint']:
            continue
        observed = json.dumps(item['profile'], ensure_ascii=False).casefold()
        for field in ('first_name', 'last_name', 'title', 'current_employer_name',
                      'current_employer_url', 'current_employer_urn'):
            value = row.get(field)
            source_value = urlparse(value).path if field == 'current_employer_url' and isinstance(value, str) else value
            if value and (not isinstance(source_value, str) or source_value.casefold() not in observed):
                row[field] = None
        verified.append(row)
    return choose_contacts(identity, verified)
