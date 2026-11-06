from scripts.tender_acquisition.company_crm import upsert_company
from scripts.tender_acquisition.identity import CompanyIdentity
from scripts.tender_acquisition.state import OperationStore


def identity():
    return CompanyIdentity('Atlas SARL', 'https://www.linkedin.com/company/atlas/',
                           'atlas.ma', 'https://atlas.ma', 'Rabat', 'Construction',
                           None, None, '123')


class FakeCRM:
    def __init__(self, rows=None, fail_create=False):
        self.rows = {r['id']: r for r in (rows or [])}
        self.create_calls = 0
        self.patches = []
        self.fail_create = fail_create

    def inspect_workflow_schema(self): return {}
    def workflow_fields(self, resource, schema):
        return {'name', 'domainName', 'linkedinLink', 'address', 'activitySector'}
    def workflow_list(self, resource): return list(self.rows.values())
    def workflow_get(self, resource, record_id): return self.rows.get(record_id)
    def workflow_create(self, resource, payload):
        self.create_calls += 1
        if self.fail_create:
            self.rows['c1'] = {'id': 'c1', **payload}
            raise RuntimeError('response lost')
        self.rows['c1'] = {'id': 'c1', **payload}
        return 'c1'
    def workflow_patch(self, resource, record_id, payload):
        self.patches.append(payload)
        self.rows[record_id].update(payload)


def test_new_company_created_once_and_read_back(tmp_path):
    store = OperationStore(tmp_path / 'ops.sqlite')
    crm = FakeCRM()
    assert upsert_company(crm, store, identity()) == 'c1'
    assert upsert_company(crm, store, identity()) == 'c1'
    assert crm.create_calls == 1


def test_existing_company_only_fills_empty_fields(tmp_path):
    row = {'id': 'c1', 'name': 'Atlas SARL',
           'domainName': {'primaryLinkUrl': 'https://atlas.ma'},
           'activitySector': 'Existing trusted sector'}
    crm = FakeCRM([row])
    assert upsert_company(crm, OperationStore(tmp_path / 'ops.sqlite'), identity()) == 'c1'
    assert crm.rows['c1']['activitySector'] == 'Existing trusted sector'
    assert crm.rows['c1']['address']['addressCity'] == 'Rabat'


def test_homonym_with_other_domain_is_skipped(tmp_path):
    row = {'id': 'other', 'name': 'Atlas SARL',
           'domainName': {'primaryLinkUrl': 'https://other.ma'}}
    crm = FakeCRM([row])
    assert upsert_company(crm, OperationStore(tmp_path / 'ops.sqlite'), identity()) is None
    assert crm.create_calls == 0


def test_unknown_create_reconciles_from_crm_list(tmp_path):
    store = OperationStore(tmp_path / 'ops.sqlite')
    crm = FakeCRM(fail_create=True)
    assert upsert_company(crm, store, identity()) is None
    assert upsert_company(crm, store, identity()) == 'c1'
    assert crm.create_calls == 1
