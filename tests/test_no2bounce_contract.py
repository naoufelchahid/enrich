import json
from pathlib import Path

import pytest
import requests
import responses

from scripts.no2bounce_client import No2BounceClient
from scripts.enrichment_clients import ProviderError

FIXTURES = Path(__file__).parent / 'fixtures' / 'no2bounce'


def fixture():
    return json.loads((FIXTURES / 'completed.json').read_text()), (FIXTURES / 'completed.csv').read_text()


@responses.activate
def test_derived_distinct_result_and_no_token_forwarding():
    body, csv = fixture()
    client = No2BounceClient('private-token')
    responses.get(client.URL, json=body)
    responses.get(body['result']['downloadFile'], body=csv)
    result = client.result('observed-job')
    assert list(result['verdicts'].values()).count('VALID') == 5
    assert list(result['verdicts'].values()).count('INVALID') == 1
    assert list(result['verdicts'].values()).count('WAITING') == 10
    assert 'apitoken' not in responses.calls[1].request.headers
    assert 'signature=fixture' not in json.dumps(result)
    assert len(result['raw']['csv_rows']) == 16
    assert client.contract_ready


@pytest.mark.parametrize('change,error', [
    ({'trackingId': 'other'}, 'WRONG_TRACKING_ID'),
    ({'overallStatus': 'Unexpected'}, 'INCOMPLETE'),
    ({'percent': 99}, 'INCOMPLETE'),
    ({'totalRecord': True}, 'INCOMPLETE'),
    ({'totalRecord': 101}, 'INCOMPLETE'),
    ({'result': {'downloadFile': 'https://evil.example/report.csv'}}, 'DOWNLOAD_REQUIRES_REVIEW'),
    ({'result': {'downloadFile': 'https://no2bounce.s3.eu-west-1.amazonaws.com.evil.example/report.csv'}}, 'DOWNLOAD_REQUIRES_REVIEW'),
    ({'result': {'downloadFile': 'http://no2bounce.s3.eu-west-1.amazonaws.com/report.csv'}}, 'DOWNLOAD_REQUIRES_REVIEW'),
])
@responses.activate
def test_rejects_untrusted_or_incomplete_response_before_download(change, error):
    body, _ = fixture(); body.update(change)
    responses.get(No2BounceClient.URL, json=body)
    with pytest.raises(ProviderError, match=error):
        No2BounceClient('token').result('observed-job')
    assert len(responses.calls) == 1


@pytest.mark.parametrize('kind', ['duplicate', 'missing', 'extra', 'columns', 'truncated'])
@responses.activate
def test_bad_csv_never_produces_verdicts(kind):
    body, csv = fixture()
    lines = csv.splitlines()
    if kind == 'duplicate': lines[-1] = lines[1]
    if kind == 'missing': lines.pop()
    if kind == 'extra': lines.append(lines[1])
    if kind == 'columns': lines[0] = 'email,status,score,catchall'
    if kind == 'truncated': lines[-1] = 'broken@example.org'
    responses.get(No2BounceClient.URL, json=body)
    responses.get(body['result']['downloadFile'], body='\n'.join(lines))
    with pytest.raises(ProviderError, match='INVALID_CSV'):
        No2BounceClient('token').result('observed-job')


@responses.activate
def test_unknown_verdict_and_conflicting_catchall_are_waiting():
    body, _ = fixture(); body['totalRecord'] = 3
    csv = 'email,finalScore,finalScoreValue,catchall\na@example.org,99,NewStatus,false\nb@example.org,99,Deliverable,true\nc@example.org,99,Deliverable,missing\n'
    responses.get(No2BounceClient.URL, json=body)
    responses.get(body['result']['downloadFile'], body=csv)
    assert set(No2BounceClient('token').result('observed-job')['verdicts'].values()) == {'WAITING'}


@pytest.mark.parametrize('failure', [302, 403, 'timeout'])
@responses.activate
def test_download_failure_no_redirect_no_retry_no_secret(failure):
    body, _ = fixture()
    responses.get(No2BounceClient.URL, json=body)
    if failure == 'timeout':
        responses.get(body['result']['downloadFile'], body=requests.Timeout('signature=fixture'))
    else:
        responses.get(body['result']['downloadFile'], status=failure, headers={'Location': 'https://evil.example/'})
    with pytest.raises(ProviderError) as exc:
        No2BounceClient('token').result('observed-job')
    assert 'signature=fixture' not in str(exc.value)
    assert len(responses.calls) == 2


@responses.activate
def test_authentic_anonymized_report_rejects_historical_duplicate_inputs():
    body = json.loads((FIXTURES / 'observed-duplicates.json').read_text())
    responses.get(No2BounceClient.URL, json=body)
    responses.get(body['result']['downloadFile'], body=(FIXTURES / 'observed-duplicates.csv').read_text())
    with pytest.raises(ProviderError, match='INVALID_CSV'):
        No2BounceClient('token').result('observed-job')
