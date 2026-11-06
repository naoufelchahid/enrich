from scripts.enrichment_clients import ProviderError
from scripts.tender_acquisition.contacts import Contact
from scripts.tender_acquisition.coordinates import enrich_contact, parse_fullenrich
from scripts.tender_acquisition.identity import CompanyIdentity
from scripts.tender_acquisition.state import OperationStore


PERSON = Contact('Amina', 'Alaoui', 'Directrice', 'LEADER',
                 'https://www.linkedin.com/in/amina-alaoui/', 'Atlas SARL')
COMPANY = CompanyIdentity('Atlas SARL', 'https://www.linkedin.com/company/atlas/',
                          'atlas.ma', None, None, None, None, None, '123')
EMAIL = 'amina.alaoui@atlas.ma'
PHONE = '+212600000000'


def completion(email=True, phone=True):
    info = {}
    if email:
        info['most_probable_work_email'] = {'email': EMAIL, 'status': 'DELIVERABLE'}
    if phone:
        info['most_probable_phone'] = {'number': PHONE, 'line_status': 'ACTIVE', 'line_type': 'MOBILE'}
    return {'status': 'FINISHED', 'data': [{'contact_info': info}]}


class FE:
    def __init__(self, data, fail=False): self.data = data; self.calls = 0; self.fail = fail
    def submit(self, payload):
        self.calls += 1
        assert payload['data'][0]['enrich_fields'] == ['contact.work_emails', 'contact.phones']
        if self.fail: raise ProviderError('NETWORK_ERROR', uncertain=True)
        return 'fe-1'
    def result(self, remote_id): return self.data


class N2B:
    def __init__(self, verdict='VALID'): self.verdict = verdict; self.calls = 0
    def submit(self, addresses): self.calls += 1; return 'n2b-1'
    def result(self, remote_id): return {'verdicts': {EMAIL: self.verdict}}


def test_email_only_and_phone_only(tmp_path):
    first = enrich_contact(PERSON, COMPANY, OperationStore(tmp_path / 'a.sqlite'),
                           fullenrich=FE(completion(phone=False)), no2bounce=N2B())
    second = enrich_contact(PERSON, COMPANY, OperationStore(tmp_path / 'b.sqlite'),
                            fullenrich=FE(completion(email=False)), no2bounce=N2B())
    assert (first.email, first.phone) == (EMAIL, None)
    assert (second.email, second.phone) == (None, PHONE)


def test_both_and_no_coordinates(tmp_path):
    both = enrich_contact(PERSON, COMPANY, OperationStore(tmp_path / 'a.sqlite'),
                          fullenrich=FE(completion()), no2bounce=N2B())
    none = enrich_contact(PERSON, COMPANY, OperationStore(tmp_path / 'b.sqlite'),
                          fullenrich=FE(completion(False, False)), no2bounce=N2B())
    assert (both.email, both.phone) == (EMAIL, PHONE)
    assert none is None


def test_only_most_probable_active_phone_accepted():
    data = completion(False, False)
    data['data'][0]['contact_info']['phones'] = [{'number': PHONE, 'line_status': 'ACTIVE'}]
    assert parse_fullenrich(data, PERSON, COMPANY) == (None, None)
    data['data'][0]['contact_info']['most_probable_phone'] = {
        'number': PHONE, 'line_status': 'UNKNOWN', 'line_type': 'MOBILE'}
    assert parse_fullenrich(data, PERSON, COMPANY) == (None, None)


def test_uncertain_submission_is_not_repeated(tmp_path):
    fe = FE(completion(), fail=True)
    store = OperationStore(tmp_path / 'ops.sqlite')
    assert enrich_contact(PERSON, COMPANY, store, fullenrich=fe, no2bounce=N2B()) is None
    assert enrich_contact(PERSON, COMPANY, store, fullenrich=fe, no2bounce=N2B()) is None
    assert fe.calls == 1
