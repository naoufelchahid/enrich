from scripts.tender_acquisition.contacts import Contact
from scripts.tender_acquisition.coordinates import QualifiedContact
from scripts.tender_acquisition.person_crm import upsert_person
from scripts.tender_acquisition.state import OperationStore


PERSON = Contact('Amina', 'Alaoui', 'Directrice', 'LEADER',
                 'https://www.linkedin.com/in/amina-alaoui/', 'Atlas SARL')


class FakeCRM:
    def __init__(self, rows=None):
        self.rows = {r['id']: r for r in (rows or [])}
        self.created = []
        self.patches = []
    def inspect_workflow_schema(self): return {}
    def workflow_fields(self, *args):
        return {'name', 'companyId', 'linkedinLink', 'jobTitle', 'emails', 'phones', 'campaignStatus'}
    def workflow_list(self, resource): return list(self.rows.values())
    def workflow_get(self, resource, record_id): return self.rows.get(record_id)
    def workflow_create(self, resource, payload):
        self.created.append(payload)
        self.rows['p1'] = {'id': 'p1', **payload}
        return 'p1'
    def workflow_patch(self, resource, record_id, payload):
        self.patches.append(payload)
        self.rows[record_id].update(payload)


def test_email_only_and_phone_only_admitted_without_outreach(tmp_path):
    for index, qualified in enumerate((QualifiedContact(PERSON, 'amina.alaoui@atlas.ma', None),
                                       QualifiedContact(PERSON, None, '+212600000000'))):
        crm = FakeCRM()
        result = upsert_person(crm, OperationStore(tmp_path / f'{index}.sqlite'), qualified, 'c1')
        assert result == 'p1'
        assert 'campaignStatus' not in crm.created[0]
        assert 'sourceFile' not in crm.created[0]


def test_existing_person_fills_missing_fields(tmp_path):
    row = {'id': 'p1', 'name': {'firstName': 'Amina', 'lastName': 'Alaoui'},
           'companyId': 'c1', 'emails': {'primaryEmail': 'amina.alaoui@atlas.ma'},
           'jobTitle': 'Existing title'}
    crm = FakeCRM([row])
    qualified = QualifiedContact(PERSON, 'amina.alaoui@atlas.ma', '+212600000000')
    assert upsert_person(crm, OperationStore(tmp_path / 'ops.sqlite'), qualified, 'c1') == 'p1'
    assert crm.rows['p1']['jobTitle'] == 'Existing title'
    assert crm.rows['p1']['phones']['primaryPhoneNumber'] == '+212600000000'


def test_homonymous_person_is_skipped(tmp_path):
    row = {'id': 'p1', 'name': {'firstName': 'Amina', 'lastName': 'Alaoui'},
           'companyId': 'c1', 'linkedinLink': {'primaryLinkUrl': 'https://www.linkedin.com/in/another/'}}
    crm = FakeCRM([row])
    assert upsert_person(crm, OperationStore(tmp_path / 'ops.sqlite'),
                         QualifiedContact(PERSON, None, '+212600000000'), 'c1') is None
    assert crm.created == []
