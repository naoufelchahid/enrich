from datetime import date

from scripts.tender_acquisition.__main__ import months_ago
from scripts.tender_acquisition.readiness import readiness
from scripts.tender_acquisition.schedule import BEGIN, END, block, merge
from scripts.tender_acquisition.simulate import simulate


def test_calendar_months_and_cron_preservation():
    assert months_ago(date(2024, 2, 29), 12) == date(2023, 2, 28)
    existing = '0 1 * * * existing-command\n'
    result = merge(existing, block('/project'))
    assert existing.strip() in result
    assert BEGIN in result and END in result
    assert merge(result, block('/project')) == result


def test_simulation_uses_no_external_services():
    result = simulate()
    assert result['services_called'] is False
    assert result['companies_in_crm'] == 1
    assert result['people_in_crm'] == 1


def test_readiness_is_read_only_and_explicit_about_no2bounce_auth():
    class Portal:
        def _get(self, url):
            return type('R', (), {'text': '''<table class="table-results"><tr><td></td>
                <td>15/10/2026</td><td><a href="?page=entreprise.EntrepriseDetailConsultation&refConsultation=1">x</a>
                </td></tr></table>'''})()

    class CRM:
        def inspect_workflow_schema(self): return {}
        def workflow_fields(self, resource, schema):
            return {'name', 'companyId', 'linkedinLink', 'domainName', 'emails'}

    class FE:
        api_key = 'test'
        def credits(self): return 2

    class N2B:
        api_key = 'test'
        contract_ready = True

    result = readiness(portal=Portal(), crm=CRM(), fullenrich=FE(), no2bounce=N2B(),
                       linkedin_probe=lambda: True)
    assert result['ready']
    assert result['no2bounce_authentication'] == 'verified_on_first_submission_only'
