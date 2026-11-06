"""Bounded company-to-person discovery, separate from named confirmation.

Only the injected search_people capability is called here, under the same global
MCP lock as CompactLinkedIn. The research specialist receives exact result data
and returns a proposal; the orchestrator checks observed names, roles, links and
evidence before creating a candidate. Search results never confirm identity.
"""
import json
import re

from scripts.compact_linkedin import CompactLinkedIn
from scripts.enrichment_clients import linkedin_url
from scripts.email_hygiene import is_personal_email
from scripts.linkedin_mcp import name_key
from scripts.linkedin_queue import mcp_lock
from scripts.workflow_store import encode, fingerprint

LANE = 'discovery'


def inspect_contract(result):
    """Inspect the actual tools/list snapshot; absent capabilities mean waiting."""
    if not isinstance(result, dict) or not isinstance(result.get('tools'), list):
        return {'usable': False, 'reason': 'LINKEDIN_TOOLS_LIST_REQUIRED'}
    allowed = {}
    for tool in result['tools']:
        name = tool.get('name', '').removeprefix('mcp__linkedin__')
        if name in ('search_people', 'get_person_profile'):
            allowed[name] = tool.get('inputSchema', {})
    search, profile = allowed.get('search_people', {}), allowed.get('get_person_profile', {})
    if (search.get('properties', {}).get('keywords', {}).get('type') != 'string'
            or 'keywords' not in search.get('required', [])
            or profile.get('properties', {}).get('linkedin_username', {}).get('type') != 'string'
            or 'linkedin_username' not in profile.get('required', [])):
        return {'usable': False, 'reason': 'LINKEDIN_DISCOVERY_CAPABILITY_ABSENT'}
    return {'usable': True, 'schemas': allowed, 'sha256': fingerprint(result)}


class CompactDiscovery:
    def __init__(self, store, budgets, *, list_tools, search_people, researcher,
                 cost_guard=None, confirmation=None, qualification=None):
        self.store, self.budgets = store, budgets
        self.list_tools, self.search_people, self.researcher = list_tools, search_people, researcher
        self.cost_guard = cost_guard
        self.confirmation = confirmation or CompactLinkedIn(store)
        self.qualification = qualification

    def enqueue_company(self, company_id, brief_id, version, *, queries=None):
        company = self.store.candidate(company_id)
        if company is None or not company['data'].get('company_name'):
            raise ValueError('COMPANY_DOSSIER_REQUIRED')
        brief = self.budgets.brief(brief_id, version)
        if brief is None:
            raise ValueError('BRIEF_NOT_FOUND')
        allowed = brief['definition'].get('searches', [])
        # Terms come from the reviewed finite brief. A person name is never
        # synthesized to fit the older named-person search contract.
        queries = queries if queries is not None else [{'keywords': text} for text in allowed]
        if not isinstance(queries, list) or not queries:
            raise ValueError('FINITE_DISCOVERY_QUERIES_REQUIRED')
        for query in queries:
            if not isinstance(query, dict) or set(query) - {'keywords', 'current_company', 'location', 'network'}:
                raise ValueError('INVALID_SEARCH_QUERY')
            if query.get('keywords') not in allowed:
                raise ValueError('SEARCH_NOT_IN_REVIEWED_BRIEF')
            for field in ('current_company', 'location'):
                if query.get(field) is not None and (not isinstance(query[field], str) or not query[field].isdigit()):
                    raise ValueError('NUMERIC_URN_REQUIRED:' + field)
            if query.get('network') is not None and (not isinstance(query['network'], list) or any(x not in ('F','S','O') for x in query['network'])):
                raise ValueError('INVALID_NETWORK_FILTER')
        payload = {'company_id': company_id, 'company_name': company['data']['company_name'],
                   'brief_id': brief_id, 'version': version, 'queries': queries}
        return self.store.create_task(LANE, 'LINKEDIN_DISCOVER', payload, candidate_id=company_id)

    def _waiting(self, task, reason, *, state='WAITING', result=None):
        self.store.finish_task(task['id'], state, result, wait_reason=reason)
        return {'status': state, 'reason': reason, 'task_id': task['id']}

    def run_one(self, *, worker='compact-discovery'):
        try:
            with mcp_lock():
                task = self.store.claim_task(LANE, worker)
                if task is None:
                    return {'status': 'EMPTY'}
                if task['kind'] != 'LINKEDIN_DISCOVER':
                    return self._waiting(task, 'UNEXPECTED_TASK_KIND', state='NEEDS_REVIEW')
                company = self.store.candidate(task['candidate_id'])
                if company is None or company['data'].get('company_name') != task['payload']['company_name']:
                    return self._waiting(task, 'COMPANY_CHANGED', state='NEEDS_REVIEW')
                brief = self.budgets.brief(task['payload']['brief_id'])
                if brief is None or brief['version'] != task['payload']['version'] or brief['state'] != 'LAUNCHED':
                    return self._waiting(task, 'BRIEF_VERSION_NOT_LAUNCHED')
                reasons = self.budgets.blockers(brief['brief_id'], brief['version'])
                if reasons:
                    return self._waiting(task, ';'.join(reasons))
                try:
                    snapshot = self.list_tools()
                except Exception:
                    return self._waiting(task, 'LINKEDIN_ACCESS_REQUIRED')
                contract = inspect_contract(snapshot)
                if not contract['usable']:
                    return self._waiting(task, contract['reason'])
                self.store.add_evidence(company['id'], encode(snapshot).encode(), kind='linkedin_tools_contract')
                if self.cost_guard is None:
                    return self._waiting(task, 'BUDGET_GUARD_REQUIRED')
                collected = []
                for index, query in enumerate(task['payload']['queries']):
                    schema = contract['schemas']['search_people']
                    if any(key not in schema.get('properties', {}) for key in query):
                        return self._waiting(task, 'LINKEDIN_QUERY_CAPABILITY_ABSENT', result={'results': collected})
                    op_id = task['id'] + ':search:' + str(index)
                    try:
                        self.cost_guard(provider='linkedin_mcp', operation_id=op_id, quantity=1, payload=query)
                    except (RuntimeError, ValueError) as exc:
                        return self._waiting(task, str(exc), result={'results': collected})
                    try:
                        raw = self.search_people(**query)
                    except Exception:
                        if hasattr(self.cost_guard, 'unknown'):
                            self.cost_guard.unknown(operation_id=op_id)
                        return self._waiting(task, 'LINKEDIN_RESULT_UNKNOWN', state='UNKNOWN', result={'results': collected})
                    if hasattr(self.cost_guard, 'submitted'):
                        self.cost_guard.submitted(operation_id=op_id)
                    raw = raw if isinstance(raw, bytes) else encode(raw).encode()
                    if len(raw) > 2_000_000:
                        return self._waiting(task, 'LINKEDIN_RESULT_TOO_LARGE', state='NEEDS_REVIEW', result={'results': collected})
                    uri = self.store.add_evidence(company['id'], raw, kind='linkedin_discovery_result')
                    collected.append({'query': query, 'proof_uri': uri})
                    self.store.finish_task(task['id'], 'SUBMITTED', {'results': collected, 'contract_sha256': contract['sha256']})
                return self._select(self.store.task(task['id']))
        except RuntimeError as exc:
            if str(exc) == 'MCP_BUSY':
                return {'status': 'MCP_BUSY'}
            raise

    def reconcile(self, task_id):
        """Re-use observed results only; never resubmit an uncertain search."""
        task = self.store.task(task_id)
        if not task or task['lane'] != LANE:
            raise ValueError('DISCOVERY_TASK_NOT_FOUND')
        if task['status'] in ('SUCCEEDED', 'NO_MATCH'):
            return {'status': 'UNCHANGED', **task['result']}
        if task['status'] == 'UNKNOWN' or len((task.get('result') or {}).get('results', [])) < len(task['payload']['queries']):
            return {'status': 'RECONCILIATION_REQUIRED', 'task_id': task_id, 'reason': 'PARTIAL_OR_UNKNOWN_SEARCH', 'results': (task.get('result') or {}).get('results', [])}
        if not (task.get('result') or {}).get('results'):
            return {'status': 'RECONCILIATION_REQUIRED', 'task_id': task_id}
        return self._select(task)

    def _select(self, task):
        company = self.store.candidate(task['candidate_id'])
        if company is None or company['data'].get('company_name') != task['payload']['company_name']:
            return self._waiting(task, 'COMPANY_CHANGED', state='NEEDS_REVIEW', result=task['result'])
        try:
            observed = [self.store.get_blob(item['proof_uri']).decode('utf-8') for item in task['result']['results']]
        except (UnicodeError, FileNotFoundError, RuntimeError, KeyError):
            return self._waiting(task, 'DISCOVERY_PROOF_UNREADABLE', state='NEEDS_REVIEW', result=task['result'])
        dossier = {'company_name': task['payload']['company_name'], 'observed_results': observed,
                   'purpose': 'Choose the most relevant observed professional; no social, CRM or mail rights'}
        try:
            proposal = self.researcher(dossier)
            selected = self._validate_proposal(proposal, observed, task['payload']['company_name'])
        except (ValueError, TypeError, KeyError):
            return self._waiting(task, 'INVALID_RESEARCH_SCHEMA_OR_UNSUPPORTED_FACT', state='NEEDS_REVIEW', result=task['result'])
        except Exception:
            return self._waiting(task, 'RESEARCH_UNAVAILABLE', result=task['result'])
        if selected is None:
            state = 'NO_MATCH' if proposal['status'] == 'NOT_FOUND' else 'NEEDS_REVIEW'
            return self._waiting(task, 'NO_OBSERVED_CONTACT' if state == 'NO_MATCH' else 'IDENTITY_OR_PROFESSIONAL_LINK_UNCERTAIN', state=state, result=task['result'])
        # Search proves provenance, never identity. Confirmation has its own
        # immutable named target, task, profile read and professional-link checks.
        identity = 'discovered:' + fingerprint({'company_id': company['id'],
            'url': selected['linkedin_url'], 'first_name': selected['first_name'], 'last_name': selected['last_name']})
        email = selected.get('email')
        if email and self.store.excluded(email):
            return self._waiting(task, 'EXCLUDED', state='NEEDS_REVIEW', result=task['result'])
        for prior in self.store.candidates():
            data = prior['data']
            if (all(name_key(data.get(field) or '') == name_key(selected[field]) for field in ('first_name','last_name','company_name'))
                    and linkedin_url(data.get('linkedin_url')) == selected['linkedin_url']
                    and self.confirmation.confirmed_input(prior)):
                identity = prior['id']
                break
        acquisition = {'acquisition_brief_id':task['payload']['brief_id'],
                       'acquisition_brief_version':task['payload']['version'],
                       'discovery_identity_required':True}
        prior_candidate = self.store.candidate(identity)
        if prior_candidate is not None and not prior_candidate['data'].get('acquisition_brief_id'):
            admitted = self.store.contact(identity)
            if not admitted or not admitted.get('admitted_at'):
                self.store.update_candidate(identity,acquisition,lane=LANE,
                    expected_revision=prior_candidate['revision'])
        self.store.put_candidate({**acquisition, 'first_name': selected['first_name'], 'last_name': selected['last_name'],
            'company_name': company['data']['company_name'], 'job_title': selected['role'],
            'linkedin_url': selected['linkedin_url'], 'raw_email': email if email and not is_personal_email(email) else None,
            'discovery_company_id': company['id'], 'discovery_task_id': task['id'],
            'discovery_reason': proposal['reason'], 'source_file': 'compact-discovery:' + task['id']},
            lane=LANE, candidate_id=identity, source_id='company-discovery:' + company['id'], legacy_id=selected['linkedin_url'])
        uri = self.store.add_evidence(identity, encode(proposal).encode(), kind='contact_selection')
        for item in task['result']['results']:
            self.store.add_evidence(identity, self.store.get_blob(item['proof_uri']), kind='linkedin_discovery_result')
        confirmation = self.confirmation.enqueue(identity)
        result = {**task['result'], 'candidate_id': identity, 'selection_proof_uri': uri,
                  'confirmation_task_id': confirmation.get('id'), 'reason': proposal['reason']}
        self.store.finish_task(task['id'], 'SUCCEEDED', result)
        return {'status': 'CONFIRMATION_PENDING', **result}

    @staticmethod
    def _validate_proposal(proposal, observed, company):
        if not isinstance(proposal, dict) or set(proposal) != {'status','people','selected_url','reason'}:
            raise ValueError('INVALID_RESEARCH_SCHEMA')
        if proposal['status'] not in ('SELECTED','NEEDS_REVIEW','NOT_FOUND') or not isinstance(proposal['reason'], str) or not proposal['reason'].strip() or not isinstance(proposal['people'], list):
            raise ValueError('INVALID_RESEARCH_SCHEMA')
        corpus = '\n'.join(observed)
        urls = set()
        for person in proposal['people']:
            if not isinstance(person, dict) or set(person) != {'first_name','last_name','company_name','role','linkedin_url','source_url','basis','professional_link_certain','email'}:
                raise ValueError('INVALID_PERSON_SCHEMA')
            for field in ('first_name','last_name','company_name','role','linkedin_url','source_url'):
                if not isinstance(person[field], str) or not person[field].strip() or person[field] not in corpus:
                    raise ValueError('UNOBSERVED_PERSON_FACT')
            if name_key(person['company_name']) != name_key(company) or linkedin_url(person['linkedin_url']) != person['linkedin_url']:
                raise ValueError('UNCONFIRMED_COMPANY_OR_URL')
            if type(person['professional_link_certain']) is not bool:
                raise ValueError('PROFESSIONAL_LINK_STATE_REQUIRED')
            if not isinstance(person['basis'], list) or len(person['basis']) < 2 or any(not isinstance(text, str) or not text.strip() or text not in corpus for text in person['basis']):
                raise ValueError('EXACT_OBSERVED_BASIS_REQUIRED')
            if person['email'] is not None and (not isinstance(person['email'], str) or person['email'] not in corpus):
                raise ValueError('UNOBSERVED_EMAIL')
            if person['linkedin_url'] in urls:
                raise ValueError('DUPLICATE_PROFILE')
            urls.add(person['linkedin_url'])
        if proposal['status'] != 'SELECTED':
            if proposal['selected_url'] is not None:
                raise ValueError('UNCERTAIN_SELECTION')
            if proposal['status'] == 'NOT_FOUND' and proposal['people']:
                raise ValueError('CONTRADICTORY_NOT_FOUND')
            return None
        matches = [p for p in proposal['people'] if p['linkedin_url'] == proposal['selected_url']]
        if len(matches) != 1:
            raise ValueError('SELECTION_NOT_OBSERVED')
        chosen = matches[0]
        if not CompactDiscovery._person_bound(chosen, observed):
            raise ValueError('PERSON_FACTS_NOT_BOUND_TO_OBSERVED_RECORD')
        if chosen['role'] not in proposal['reason']:
            raise ValueError('ROLE_BASED_REASON_REQUIRED')
        same_names = [p for p in proposal['people'] if name_key(p['first_name'] + ' ' + p['last_name']) == name_key(chosen['first_name'] + ' ' + chosen['last_name'])]
        if len(same_names) > 1 or not chosen['professional_link_certain']:
            return None
        return chosen

    @staticmethod
    def _person_bound(person, observed):
        full_name = person['first_name'] + ' ' + person['last_name']
        facts = (full_name, person['company_name'], person['role'], person['linkedin_url'], person['source_url'])
        if person.get('email'):
            facts += (person['email'],)
        def bound_record(value):
            if isinstance(value, dict):
                scalars = [v for v in value.values() if isinstance(v, str)]
                if value.get('first_name') and value.get('last_name'):
                    scalars.append(str(value['first_name']) + ' ' + str(value['last_name']))
                if all(fact in scalars for fact in facts):
                    return True
                return any(bound_record(v) for v in value.values() if isinstance(v, (dict,list)))
            if isinstance(value, list):
                return any(bound_record(v) for v in value)
            return False
        for raw in observed:
            try:
                if bound_record(json.loads(raw)):
                    return True
            except (ValueError, TypeError):
                pass
        # For text responses, require an exact single-profile block, not facts
        # scattered across different results. Structured responses use records.
        for snippet in person['basis']:
            if all(fact in snippet for fact in facts) and '\n\n' not in snippet:
                urls = set(re.findall(r'https?://(?:www\.)?linkedin\.com/in/[^\s"<>]+', snippet))
                if urls == {person['linkedin_url']} and any(snippet in raw for raw in observed):
                    return True
        return False

    def qualify_confirmed(self, candidate_id):
        candidate = self.store.candidate(candidate_id)
        if candidate is None or not self.confirmation.confirmed_input(candidate):
            return {'status': 'WAITING', 'reason': 'IDENTITY_CONFIRMATION_REQUIRED'}
        if any(self.store.excluded(str(candidate['data'].get(k) or '')) for k in ('raw_email','clean_email','validated_email','original_email')):
            return {'status': 'WAITING', 'reason': 'EXCLUDED'}
        if self.qualification is None:
            return {'status': 'WAITING', 'reason': 'QUALIFICATION_SERVICE_REQUIRED'}
        return self.qualification.tick(candidate_id)
