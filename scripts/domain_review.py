"""Read-only WHOIS/RDAP evidence for known domains; never authorizes enrichment."""
import argparse
import hashlib
import ipaddress
import json
import re
import socket
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import requests

from scripts.email_hygiene import valid_email

BOOTSTRAP = 'https://data.iana.org/rdap/dns.json'
MAX_BYTES = 2_000_000


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def domain_name(value):
    if not isinstance(value, str):
        raise ValueError('A bare domain is required')
    value = value.strip().lower()
    if not valid_email('x@' + value):
        raise ValueError('Use a bare domain, not a URL or email')
    return value


def recent(value, days):
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(value)
        return timedelta(0) <= age <= timedelta(days=days)
    except (TypeError, ValueError):
        return False


def https_json(url):
    # URLs come only from IANA discovery, never from contact data or RDAP links.
    p = urlsplit(url)
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or not p.hostname or '.' not in p.hostname:
        raise ValueError('Unsupported RDAP endpoint')
    try:
        ipaddress.ip_address(p.hostname)
    except ValueError:
        pass
    else:
        raise ValueError('Literal IP endpoints are not supported')
    with requests.get(url, timeout=(5, 15), allow_redirects=False, stream=True) as r:
        if r.status_code != 200:
            raise ValueError('HTTP_' + str(r.status_code))
        chunks, size = [], 0
        for chunk in r.iter_content(65536):
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError('Response too large')
            chunks.append(chunk)
        return json.loads(b''.join(chunks))


def whois_ma(domain):
    # Fixed authoritative server; do not follow referrals from the response.
    with socket.create_connection(('whois.registre.ma', 43), timeout=8) as s:
        s.settimeout(8)
        s.sendall((domain + '\r\n').encode('ascii'))
        chunks, size = [], 0
        while True:
            chunk = s.recv(16384)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError('Response too large')
            chunks.append(chunk)
    return b''.join(chunks).decode('utf-8', errors='replace')


def parse_whois(domain, text):
    fields = {}
    for line in text.splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            fields.setdefault(key.strip().lower(), []).append(value.strip())
    if fields.get('domain name', []) != [domain]:
        # Allow provider case differences, but reject missing/conflicting identities.
        if [v.lower() for v in fields.get('domain name', [])] != [domain]:
            raise ValueError('Domain identity not confirmed')
    names = fields.get('registrant organization') or fields.get('registrant name', [])
    return {'domain': domain, 'status': 'FOUND', 'registrant_names': names,
            'protocol': 'WHOIS', 'source': 'whois.registre.ma:43',
            'evidence_sha256': hashlib.sha256(text.encode()).hexdigest()}


def parse_rdap(domain, body, source):
    if not isinstance(body, dict) or body.get('objectClassName') != 'domain' or str(body.get('ldhName', '')).lower() != domain:
        raise ValueError('Domain identity not confirmed')
    names = []
    for entity in body.get('entities', []):
        if not isinstance(entity, dict) or 'registrant' not in (entity.get('roles') or []):
            continue
        card = entity.get('vcardArray')
        if not isinstance(card, list) or len(card) != 2 or not isinstance(card[1], list):
            continue
        for row in card[1]:
            if isinstance(row, list) and len(row) == 4 and row[0] in ('org', 'fn') and isinstance(row[3], str) and row[3].strip():
                names.append(row[3].strip())
    return {'domain': domain, 'status': 'FOUND', 'registrant_names': sorted(set(names)),
            'protocol': 'RDAP', 'source': source,
            'evidence_sha256': hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()}


class DomainReview:
    def __init__(self, cache_path):
        self.cache_path = str(cache_path)

    @contextmanager
    def _connect(self):
        c = sqlite3.connect(self.cache_path, timeout=10)
        try:
            with c:
                c.execute('CREATE TABLE IF NOT EXISTS domain_review_cache (key TEXT PRIMARY KEY, observed_at TEXT NOT NULL, payload TEXT NOT NULL)')
                yield c
        finally:
            c.close()

    def _cached(self, key):
        with self._connect() as c:
            row = c.execute('SELECT observed_at,payload FROM domain_review_cache WHERE key=?', (key,)).fetchone()
        if not row:
            return None
        value = json.loads(row[1])
        # Failures are cached briefly, not mistaken for durable negative evidence.
        ttl = 7 if value.get('status') in ('FOUND', 'BOOTSTRAP') else 5 / 1440
        return value if recent(row[0], ttl) else None

    def _save(self, key, value):
        with self._connect() as c:
            c.execute('INSERT OR REPLACE INTO domain_review_cache VALUES(?,?,?)', (key, value['observed_at'], json.dumps(value)))

    def lookup(self, value):
        domain = domain_name(value)
        hit = self._cached(domain)
        if hit:
            return dict(hit, cache_hit=True)
        observed = timestamp()
        try:
            if domain.endswith('.ma'):
                result = parse_whois(domain, whois_ma(domain))
            else:
                bootstrap = self._cached('__iana__')
                if not bootstrap:
                    body = https_json(BOOTSTRAP)
                    if not isinstance(body, dict) or not isinstance(body.get('services'), list):
                        raise ValueError('Invalid IANA discovery response')
                    bootstrap = {'status': 'BOOTSTRAP', 'observed_at': observed, 'services': body['services']}
                    self._save('__iana__', bootstrap)
                tld = domain.rsplit('.', 1)[-1]
                endpoints = [url for entry in bootstrap['services']
                             if isinstance(entry, list) and len(entry) == 2
                             and isinstance(entry[0], list) and isinstance(entry[1], list)
                             and tld in entry[0] for url in entry[1]
                             if isinstance(url, str) and url.startswith('https://')]
                if not endpoints:
                    result = {'domain': domain, 'status': 'UNSUPPORTED', 'registrant_names': [], 'source': BOOTSTRAP}
                else:
                    endpoint = endpoints[0]
                    source = endpoint.rstrip('/') + '/domain/' + domain
                    result = parse_rdap(domain, https_json(source), source)
        except (requests.RequestException, OSError, ValueError, TypeError, KeyError):
            # No raw exception text: it can include response data or URLs.
            result = {'domain': domain, 'status': 'UNKNOWN', 'registrant_names': [],
                      'reason': 'LOOKUP_FAILED_OR_UNRECOGNIZED_RESPONSE'}
        result['observed_at'] = observed
        self._save(domain, result)
        return dict(result, cache_hit=False)

    def review(self, domain, company):
        result = self.lookup(domain)
        norm = lambda s: ' '.join(re.findall(r'\w+', s.casefold()))
        matches = bool(company.strip()) and any(norm(company) == norm(n) for n in result['registrant_names'])
        return {**result, 'company': company,
                'assessment': 'NAME_MATCH' if matches else 'NEEDS_REVIEW',
                'requires_human_review': True, 'email_deliverability_proven': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--domain', required=True)
    p.add_argument('--company', required=True)
    p.add_argument('--cache', default='bdd/auxiliary/domain-review.sqlite')
    p.add_argument('--dry-run', action='store_true', help='Validate inputs only; no network or cache writes')
    a = p.parse_args()
    domain = domain_name(a.domain)
    if a.dry_run:
        print(json.dumps({'domain': domain, 'company': a.company, 'dry_run': True}))
        return
    print(json.dumps(DomainReview(a.cache).review(domain, a.company), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
