"""Read-only readiness probes. No CRM or paid provider submission occurs here."""

from __future__ import annotations

import shutil

import fitz

from scripts.enrichment_clients import FullEnrichClient
from scripts.no2bounce_client import No2BounceClient
from scripts.twenty_client import TwentyClient

from .linkedin_direct import LinkedInMcp
from .portal import BASE, PortalClient, parse_listing


def probe_linkedin() -> bool:
    with LinkedInMcp() as mcp:
        result = mcp.call('search_companies', {'keywords': 'OpenAI'})
    return bool(result.get('sections', {}).get('search_results'))


def readiness(*, portal=None, crm=None, fullenrich=None, no2bounce=None,
              linkedin_probe=probe_linkedin, probe_vision=False, vision_probe=None) -> dict:
    checks = {}
    checks['codex_cli'] = bool(shutil.which('codex'))
    checks['pdf_renderer'] = bool(shutil.which('pdftoppm'))
    try:
        portal = portal or PortalClient()
        url = BASE + 'index.php5?page=entreprise.EntrepriseAdvancedSearch&AvisExtraitPV'
        response = portal._get(url)
        rows, _ = parse_listing(response.text, 'AvisExtraitPV')
        checks['portal'] = bool(rows)
    except Exception:
        checks['portal'] = False
    try:
        crm = crm or TwentyClient()
        schema = crm.inspect_workflow_schema()
        company_fields = crm.workflow_fields('companies', schema)
        person_fields = crm.workflow_fields('people', schema)
        checks['twenty'] = ('name' in company_fields
                            and ('domainName' in company_fields or 'linkedinLink' in company_fields)
                            and {'name', 'companyId', 'linkedinLink'} <= person_fields
                            and ('emails' in person_fields or 'phones' in person_fields))
    except Exception:
        checks['twenty'] = False
    try:
        fullenrich = fullenrich or FullEnrichClient()
        checks['fullenrich'] = bool(fullenrich.api_key and fullenrich.credits() > 0)
    except Exception:
        checks['fullenrich'] = False
    no2bounce = no2bounce or No2BounceClient()
    # The observed bulk API has no zero-cost token-check endpoint. This checks
    # configuration and response-contract support; authentication is proven on submit.
    checks['no2bounce_configured'] = bool(no2bounce.api_key and no2bounce.contract_ready)
    if checks['codex_cli']:
        try:
            checks['linkedin_mcp'] = bool(linkedin_probe())
        except Exception:
            checks['linkedin_mcp'] = False
    else:
        checks['linkedin_mcp'] = False
    if probe_vision:
        try:
            from .vision import extract_names
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), 'PROCES VERBAL\nSoumissionnaire : ATLAS SARL')
            sample = document.tobytes()
            document.close()
            names = (vision_probe or extract_names)(sample)
            checks['codex_vision'] = any(name.casefold() == 'atlas sarl' for name in names)
        except Exception:
            checks['codex_vision'] = False
    return {'ready': all(checks.values()), 'checks': checks,
            'no2bounce_authentication': 'verified_on_first_submission_only',
            'vision_probe': 'executed' if probe_vision else 'deferred'}
