"""No2Bounce transport plus the completed CSV contract observed on 2026-10-06.

See archives/essais/no2bounce-contract-2026-10-06/INVESTIGATION.md. Submission and
progress envelopes are documented; the completed CSV contract is account evidence.
"""
import csv
import hashlib
import io
import os

from urllib.parse import urlsplit

import requests

from scripts.enrichment_clients import ProviderError, _json
from scripts.email_hygiene import is_personal_email


_OBSERVED_CSV = object()


class No2BounceClient:
    URL = 'https://connect.no2bounce.com/v2/n2b_validate_bulk'

    def __init__(self, api_key=None, *, result_decoder=_OBSERVED_CSV, accept_catchall=False):
        self.api_key = api_key or os.getenv('NO2BOUNCE_API_KEY')
        self.result_decoder = result_decoder
        self.accept_catchall = accept_catchall

    @property
    def contract_ready(self):
        return self.result_decoder is _OBSERVED_CSV or callable(self.result_decoder)

    def _request(self, method, **kwargs):
        if not self.api_key:
            raise ProviderError('NO2BOUNCE_KEY_MISSING')
        try:
            response = requests.request(method, self.URL,
                headers={'apitoken': self.api_key, 'Content-Type': 'application/json'},
                timeout=(5, 30), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise ProviderError('NETWORK_ERROR', uncertain=method == 'POST') from None
        body = _json(response, paid=method == 'POST')
        if method == 'GET' and 'trackingId' in body and 'overallStatus' in body:
            return body
        if body.get('statusCode') != 200 or not isinstance(body.get('data'), dict):
            raise ProviderError('NO2BOUNCE_INVALID_RESPONSE', uncertain=method == 'POST')
        return body

    def submit(self, emails):
        if not self.contract_ready:
            raise ProviderError('NO2BOUNCE_RESULT_CONTRACT_UNCONFIRMED')
        if not emails or len(emails) > 100 or len(set(emails)) != len(emails):
            raise ValueError('Expected 1..100 distinct email addresses')
        if any(is_personal_email(email) for email in emails):
            raise ValueError('PERSONAL_EMAIL_FORBIDDEN')
        result = self._request('POST', json={'emailList': emails})
        tracking = result['data'].get('trackingId')
        if isinstance(tracking, bool) or not isinstance(tracking, (str, int)) or not str(tracking).strip():
            raise ProviderError('NO2BOUNCE_MISSING_TRACKING_ID', uncertain=True)
        return str(tracking)

    def result(self, tracking_id):
        body = self._request('GET', params={'trackingId': tracking_id})
        if 'trackingId' in body and 'overallStatus' in body:
            return self._csv_result(body, str(tracking_id))
        data = body['data']
        if data.get('taskId') is not None and str(data['taskId']) != tracking_id:
            raise ProviderError('NO2BOUNCE_WRONG_TRACKING_ID')
        if 'apiResponse' not in data:
            if data.get('signedUrl'):
                raise ProviderError('NO2BOUNCE_DOWNLOAD_REQUIRES_REVIEW')
            total, done = data.get('totalEmails'), data.get('completedEmails')
            if type(total) is int and type(done) is int and 0 <= done < total:
                return None
            raise ProviderError('NO2BOUNCE_RESULT_INCOMPLETE')
        if not callable(self.result_decoder):
            raise ProviderError('NO2BOUNCE_RESULT_CONTRACT_UNCONFIRMED')
        # Adapter must return {email: VALID|INVALID|WAITING}. Engine checks keys.
        try:
            verdicts = self.result_decoder(body)
        except (TypeError, ValueError, KeyError):
            verdicts = None
        return {'verdicts': verdicts, 'raw': body}

    def _csv_result(self, body, tracking_id):
        if str(body.get('trackingId')) != tracking_id:
            raise ProviderError('NO2BOUNCE_WRONG_TRACKING_ID')
        if not self.contract_ready:
            raise ProviderError('NO2BOUNCE_RESULT_CONTRACT_UNCONFIRMED')
        # No unobserved status spelling is interpreted as successful completion.
        if body.get('overallStatus') != 'Completed':
            raise ProviderError('NO2BOUNCE_RESULT_INCOMPLETE')
        total = body.get('totalRecord')
        if (type(total) is not int or not 1 <= total <= 100
                or type(body.get('percent')) is not int or body['percent'] != 100):
            raise ProviderError('NO2BOUNCE_RESULT_INCOMPLETE')
        result = body.get('result')
        url = result.get('downloadFile') if isinstance(result, dict) else None
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            safe = (parsed is not None and parsed.scheme == 'https'
                    and parsed.hostname == 'no2bounce.s3.eu-west-1.amazonaws.com'
                    and not parsed.username and not parsed.password
                    and parsed.port in (None, 443) and not parsed.fragment
                    and parsed.path.endswith('.csv'))
        except ValueError:
            safe = False
        if not safe:
            raise ProviderError('NO2BOUNCE_DOWNLOAD_REQUIRES_REVIEW')
        # The S3 URL authenticates itself. Never forward the provider API key.
        try:
            with requests.get(url, timeout=(5, 30), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise ProviderError(f'NO2BOUNCE_DOWNLOAD_HTTP_{response.status_code}')
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 2_000_000:
                        raise ProviderError('NO2BOUNCE_DOWNLOAD_TOO_LARGE')
                    chunks.append(chunk)
                content = b''.join(chunks)
        except requests.RequestException:
            raise ProviderError('NO2BOUNCE_DOWNLOAD_NETWORK_ERROR') from None
        try:
            reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')), strict=True)
            if reader.fieldnames != ['email', 'finalScore', 'finalScoreValue', 'catchall']:
                raise ValueError('Unexpected columns')
            rows = list(reader)
            if len(rows) != total:
                raise ValueError('Wrong record count')
            verdicts = {}
            for row in rows:
                if set(row) != set(reader.fieldnames) or any(v is None for v in row.values()):
                    raise ValueError('Malformed row')
                email = row['email'].strip().lower()
                if not email or email in verdicts:
                    raise ValueError('Missing or duplicate email')
                # AcceptAll always waits, including positive and negative subtypes.
                pair = (row['finalScoreValue'], row['catchall'])
                verdicts[email] = {('Deliverable', 'false'): 'VALID',
                                   ('UnDeliverable', 'false'): 'INVALID'}.get(pair, 'WAITING')
                if self.accept_catchall and pair in (('Deliverable/AcceptAll', 'true'),
                                                     ('UnDeliverable/AcceptAll', 'true'),
                                                     ('Risky/AcceptAll', 'true')):
                    verdicts[email] = 'CATCHALL'
        except (UnicodeError, csv.Error, ValueError, TypeError):
            raise ProviderError('NO2BOUNCE_INVALID_CSV') from None
        # Keep reproducible evidence without storing the temporary signed credential.
        raw = dict(body)
        raw['result'] = {'downloadFile': '[SIGNED_URL_REDACTED]'}
        raw['csv_rows'] = rows
        raw['csv_sha256'] = hashlib.sha256(content).hexdigest()
        return {'verdicts': verdicts, 'raw': raw}
