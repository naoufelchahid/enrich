"""Contract-driven Apify acquisition with durable bounded dataset ingestion.

The transport is injected: inspect_contract, submit, get_run, dataset_page and
reconcile. It must enforce maximum_charge on the actual account/Actor. No HTTP
shape, Actor input field or account price is guessed in this module. The account
contract supplies current schema, field mapping and an attested upper cost bound.
"""
import json
from datetime import datetime, timedelta, timezone

from jsonschema import validate as validate_schema, ValidationError, SchemaError

from scripts.compact_briefs import decimal, multiply
from scripts.enrichment_clients import ProviderError
from scripts.workflow_store import encode, fingerprint

ACTOR = 'compass/crawler-google-places'
LANE = 'apify'


class CompactApify:
    def __init__(self, store, budgets, client, qualifier, *, discovery=None, clock=None):
        self.store, self.budgets, self.client, self.qualifier = store, budgets, client, qualifier
        self.discovery = discovery
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with store.connection() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS workflow_apify_records (
                    place_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, content_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_apify_ingested (
                    task_id TEXT NOT NULL, place_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
                    PRIMARY KEY(task_id,place_id));
            ''')

    def enqueue(self, brief_id, version, *, limits_by_search, run_limit, maximum_charge):
        brief = self.budgets.brief(brief_id, version)
        if brief is None:
            raise ValueError('BRIEF_NOT_FOUND')
        if not isinstance(limits_by_search, dict) or not limits_by_search or type(run_limit) is not int or run_limit < 1:
            raise ValueError('FINITE_SEARCH_LIMITS_REQUIRED')
        decimal(maximum_charge)
        tasks = []
        for query, limit in limits_by_search.items():
            if query not in brief['definition'].get('searches', []):
                raise ValueError('SEARCH_NOT_IN_REVIEWED_BRIEF')
            if type(limit) is not int or limit < 1:
                raise ValueError('SEARCH_LIMIT_REQUIRED')
        for query, limit in limits_by_search.items():
            payload = {'actor': ACTOR, 'brief_id': brief_id, 'version': version, 'query': query,
                       'zone': brief['definition'].get('zone'), 'search_limit': limit, 'run_limit': run_limit,
                       'effective_limit': min(limit, run_limit), 'maximum_charge': str(decimal(maximum_charge))}
            tasks.append(self.store.create_task(LANE, 'APIFY_ACQUIRE', payload))
        return tasks

    def _proof(self, value):
        return 'journal://artifacts/' + self.store.put_blob(value if isinstance(value, bytes) else encode(value).encode())

    def _wait(self, task, reason, *, state='WAITING', result=None):
        self.store.finish_task(task['id'], state, result if result is not None else task.get('result'), wait_reason=reason)
        return {'status': state, 'reason': reason, 'task_id': task['id']}

    def _contract(self, task, brief):
        contract = self.client.inspect_contract(ACTOR)
        if not isinstance(contract, dict) or contract.get('actor') != ACTOR or not contract.get('version'):
            raise ValueError('APIFY_CONTRACT_REQUIRED')
        schema, fields, pricing = contract.get('input_schema'), contract.get('input_fields'), contract.get('pricing')
        if not isinstance(schema, dict) or not isinstance(fields, dict) or set(fields) != {'query', 'location', 'limit'} or not isinstance(pricing, dict):
            raise ValueError('APIFY_CONTRACT_REQUIRED')
        if any(not isinstance(v, str) or v not in schema.get('properties', {}) for v in fields.values()):
            raise ValueError('APIFY_INPUT_CAPABILITY_ABSENT')
        if len(set(fields.values())) != 3:
            raise ValueError('APIFY_INPUT_CAPABILITY_ABSENT')
        if pricing.get('currency') != brief['definition']['budgets']['currency'] or not pricing.get('source') or not pricing.get('account_scope') or pricing.get('maximum_charge_enforced') is not True:
            raise ValueError('APIFY_ACCOUNT_PRICING_REQUIRED')
        try:
            verified = datetime.fromisoformat(pricing['verified_at'].replace('Z', '+00:00'))
        except (KeyError, ValueError, TypeError):
            raise ValueError('APIFY_ACCOUNT_PRICING_REQUIRED') from None
        if verified.tzinfo is None or not timedelta(minutes=-5) <= self.clock() - verified <= timedelta(hours=24):
            raise ValueError('APIFY_ACCOUNT_PRICING_EXPIRED')
        upper = multiply(pricing.get('upper_bound_per_result'), task['payload']['effective_limit'])
        if upper > decimal(task['payload']['maximum_charge']):
            raise ValueError('APIFY_MAXIMUM_CHARGE_BELOW_KNOWN_BOUND')
        values = {fields['query']: [task['payload']['query']], fields['location']: task['payload']['zone'],
                  fields['limit']: task['payload']['effective_limit']}
        try:
            validate_schema(values, schema)
        except (ValidationError, SchemaError):
            raise ValueError('APIFY_INPUT_SCHEMA_MISMATCH') from None
        return contract, values

    def tick(self, task_id):
        with self.store.lock('apify-' + task_id):
            task = self.store.task(task_id)
            if not task or task['lane'] != LANE or task['kind'] != 'APIFY_ACQUIRE':
                raise ValueError('APIFY_TASK_NOT_FOUND')
            if task['status'] == 'SUCCEEDED':
                return {'status': 'UNCHANGED', **task['result']}
            if task['status'] in ('UNKNOWN', 'RUNNING'):
                if task['status'] == 'RUNNING':
                    if self.budgets.reservation(task['id']):
                        self.budgets.reconcile(task['id'], 'UNKNOWN')
                    return self._wait(task, 'INTERRUPTED_SUBMISSION', state='UNKNOWN')
                return {'status': 'RECONCILIATION_REQUIRED', 'task_id': task_id}
            if task['status'] != 'PENDING' and task['status'] != 'SUBMITTED':
                return {'status': task['status'], 'reason': task['wait_reason'], 'task_id': task_id}
            if task['status'] == 'PENDING':
                return self._submit(task)
            return self._collect(task)

    def _submit(self, task):
        p = task['payload']
        brief = self.budgets.brief(p['brief_id'])
        if brief is None or brief['version'] != p['version'] or brief['state'] != 'LAUNCHED':
            return self._wait(task, 'BRIEF_VERSION_NOT_LAUNCHED')
        blockers = self.budgets.blockers(p['brief_id'], p['version'])
        if blockers:
            return self._wait(task, ';'.join(blockers))
        if self.client is None:
            return self._wait(task, 'APIFY_TRANSPORT_NOT_CONFIGURED')
        try:
            contract, actor_input = self._contract(task, brief)
            self.budgets.reserve(p['brief_id'], p['version'], 'apify', key=task['id'],
                amount=p['maximum_charge'], quantity=p['effective_limit'], quantity_unit='companies',
                payload={'input': actor_input, 'contract_hash': fingerprint(contract)})
        except (RuntimeError, ValueError) as exc:
            return self._wait(task, str(exc))
        except Exception:
            return self._wait(task, 'APIFY_CONTRACT_UNAVAILABLE')
        state = {'contract_uri': self._proof(contract), 'input_uri': self._proof(actor_input),
                 'input_version': contract['version'], 'offset': 0, 'pages': [], 'ingested': 0}
        claimed = self.store.claim_task(LANE, 'compact-apify', task_id=task['id'])
        if claimed is None:
            return {'status': 'ALREADY_CLAIMED', 'task_id': task['id']}
        # Submission intent, exact request/contract and its reservation are all
        # durable before transport. A process exit now can only reconcile.
        self.store.finish_task(task['id'], 'UNKNOWN', state, wait_reason='APIFY_SUBMISSION_IN_PROGRESS')
        self.budgets.reconcile(task['id'], 'UNKNOWN')
        try:
            response = self.client.submit(ACTOR, actor_input, maximum_charge=p['maximum_charge'],
                currency=brief['definition']['budgets']['currency'], operation_id=task['id'])
            if not isinstance(response, dict) or not isinstance(response.get('id'), str) or not response['id']:
                raise ProviderError('APIFY_MISSING_RUN_ID', uncertain=True)
        except Exception as exc:
            certain = isinstance(exc, ProviderError) and not exc.uncertain
            self.budgets.reconcile(task['id'], 'FAILED_CERTAIN' if certain else 'UNKNOWN')
            return self._wait(task, str(exc) if isinstance(exc, ProviderError) else 'APIFY_SUBMISSION_UNKNOWN',
                              state='FAILED' if certain else 'UNKNOWN', result=state)
        state.update(run_id=response['id'], dataset_id=response.get('defaultDatasetId'), submit_uri=self._proof(response))
        self.store.finish_task(task['id'], 'SUBMITTED', state)
        self.budgets.mark_submitted(task['id'])
        return {'status': 'SUBMITTED', 'task_id': task['id'], **state}

    def reconcile(self, task_id, *, run_id=None):
        task = self.store.task(task_id)
        if not task or task['lane'] != LANE or task['status'] not in ('UNKNOWN', 'RUNNING'):
            raise ValueError('UNCERTAIN_APIFY_TASK_REQUIRED')
        found = {'id': run_id} if run_id else self.client.reconcile(task['id'])
        if not isinstance(found, dict) or not isinstance(found.get('id'), str) or not found['id']:
            return {'status': 'RECONCILIATION_REQUIRED', 'task_id': task_id}
        state = {**(task['result'] or {}), 'run_id': found['id'], 'dataset_id': found.get('defaultDatasetId'),
                 'offset': (task['result'] or {}).get('offset', 0), 'pages': (task['result'] or {}).get('pages', []),
                 'ingested': (task['result'] or {}).get('ingested', 0)}
        self.store.finish_task(task_id, 'SUBMITTED', state)
        self.budgets.mark_submitted(task_id)
        return {'status': 'SUBMITTED', **state}

    def _collect(self, task):
        state = dict(task['result'])
        if state.get('pending_page_uri'):
            return self._apply_page(task, state, json.loads(self.store.get_blob(state['pending_page_uri'])))
        try:
            run = self.client.get_run(state['run_id'])
        except Exception:
            return {'status': 'SUBMITTED', 'reason': 'APIFY_RUN_STATUS_UNAVAILABLE', 'task_id': task['id']}
        if not isinstance(run, dict) or run.get('id') != state['run_id'] or not isinstance(run.get('status'), str):
            return self._wait(task, 'APIFY_RUN_IDENTITY_INVALID', state='NEEDS_REVIEW')
        state.update(remote_status=run['status'], run_uri=self._proof(run))
        dataset = run.get('defaultDatasetId') or state.get('dataset_id')
        if dataset and state.get('dataset_id') and dataset != state['dataset_id']:
            return self._wait(task, 'APIFY_DATASET_CHANGED', state='NEEDS_REVIEW', result=state)
        state['dataset_id'] = dataset
        self.store.finish_task(task['id'], 'SUBMITTED', state)
        if not dataset:
            if run['status'] in ('FAILED','ABORTED','TIMED-OUT'):
                return self._wait(task, 'APIFY_FAILED_WITHOUT_DATASET', result=state)
            return {'status': 'SUBMITTED', 'task_id': task['id']}
        limit = task['payload']['effective_limit']
        try:
            page = self.client.dataset_page(dataset, offset=state['offset'], limit=min(100, max(1, limit - state['ingested'])))
        except Exception:
            return {'status': 'SUBMITTED', 'reason': 'APIFY_DATASET_UNAVAILABLE', 'task_id': task['id']}
        if (not isinstance(page, dict) or page.get('offset') != state['offset'] or not isinstance(page.get('items'), list)
                or type(page.get('total')) is not int or page['total'] < 0):
            return self._wait(task, 'APIFY_PAGE_INVALID', state='NEEDS_REVIEW', result=state)
        uri = self._proof(page)
        # Persist page proof before ingestion; a replay uses stable place IDs.
        state['pending_page_uri'] = uri
        self.store.finish_task(task['id'], 'SUBMITTED', state)
        return self._apply_page(task, state, page)

    def _apply_page(self, task, state, page):
        limit = task['payload']['effective_limit']
        with self.store.connection() as c:
            state['ingested'] = c.execute('SELECT count(*) FROM workflow_apify_ingested WHERE task_id=?', (task['id'],)).fetchone()[0]
        for record in page['items']:
            if state['ingested'] >= limit:
                break
            if not isinstance(record, dict) or not isinstance(record.get('placeId'), str) or not record['placeId'] or not isinstance(record.get('title'), str) or not record['title']:
                return self._wait(task, 'APIFY_COMPANY_IDENTITY_REQUIRED', state='NEEDS_REVIEW', result=state)
            place_id = record['placeId']
            with self.store.connection() as c:
                applied = c.execute('SELECT 1 FROM workflow_apify_ingested WHERE task_id=? AND place_id=?', (task['id'], place_id)).fetchone()
            if applied:
                continue
            identity = 'apify-place:' + fingerprint(place_id)
            # The local existing importer also uses placeId for durable company
            # identity; we reuse that source contract without its direct CRM writes.
            self.store.put_candidate({'company_name': record['title'], 'place_id': place_id,
                'website': record.get('website'), 'address': record.get('address'), 'city': record.get('city'),
                'phone': record.get('phone'), 'source_url': record.get('url'), 'source_file': 'apify:' + state['run_id'],
                'company_only': True}, lane=LANE, candidate_id=identity, source_id='apify-google-places', legacy_id=place_id,
                status='WAITING', wait_reason='CONTACT_DISCOVERY_REQUIRED')
            self.store.add_evidence(identity, encode(record).encode(), kind='apify_company_record')
            qualification_task = self.store.create_task(LANE, 'COMPANY_QUALIFY', {'record': record},
                candidate_id=identity, key='company-qualify:' + fingerprint({'place_id': place_id, 'record': record}))
            if qualification_task['status'] == 'SUCCEEDED':
                proposal = qualification_task['result']
            else:
                if qualification_task['status'] != 'PENDING':
                    return self._wait(task, 'COMPANY_QUALIFICATION_RECONCILIATION_REQUIRED', state='NEEDS_REVIEW', result=state)
                self.store.claim_task(LANE, 'company-qualifier', task_id=qualification_task['id'])
                try:
                    proposal = self.qualifier({'company': record, 'query': task['payload']['query'], 'zone': task['payload']['zone']})
                    if (not isinstance(proposal, dict) or set(proposal) != {'relevant','facts','uncertainties','reason'}
                            or type(proposal['relevant']) is not bool or not isinstance(proposal['reason'], str)
                            or not isinstance(proposal['facts'], list) or not isinstance(proposal['uncertainties'], list)
                            or any(not isinstance(x, str) for x in proposal['uncertainties'])
                            or any(not isinstance(x, str) or x not in encode(record) for x in proposal['facts'])):
                        raise ValueError('INVALID_COMPANY_QUALIFICATION_SCHEMA')
                except Exception:
                    self.store.finish_task(qualification_task['id'], 'NEEDS_REVIEW', wait_reason='INVALID_COMPANY_QUALIFICATION')
                    return self._wait(task, 'COMPANY_QUALIFICATION_REVIEW', state='NEEDS_REVIEW', result=state)
                self.store.finish_task(qualification_task['id'], 'SUCCEEDED', proposal)
            self.store.add_evidence(identity, encode(proposal).encode(), kind='company_qualification')
            reason = 'CONTACT_DISCOVERY_REQUIRED' if proposal['relevant'] and not proposal['uncertainties'] else 'COMPANY_RELEVANCE_REVIEW'
            self.store.update_candidate(identity, {'qualification_reason': proposal['reason']}, lane=LANE, status='WAITING', wait_reason=reason)
            if reason == 'CONTACT_DISCOVERY_REQUIRED':
                if self.discovery is not None:
                    self.discovery.enqueue_company(identity, task['payload']['brief_id'], task['payload']['version'])
                else:
                    # This is an explicit handoff waiting for a reviewed search,
                    # not a runnable named-confirmation task or an email target.
                    handoff = self.store.create_task(LANE, 'COMPANY_CONTACT_NEEDED',
                        {'company_id': identity, 'brief_id': task['payload']['brief_id'], 'version': task['payload']['version']},
                        candidate_id=identity, key='company-contact-needed:' + fingerprint({'company': identity, 'brief': task['payload']['brief_id'], 'version': task['payload']['version']}))
                    if handoff['status'] == 'PENDING':
                        self.store.finish_task(handoff['id'], 'WAITING', wait_reason='DISCOVERY_SERVICE_OR_REVIEWED_QUERY_REQUIRED')
            # Commit the replay marker only AFTER the discovery handoff exists.
            with self.store.connection() as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute('INSERT OR IGNORE INTO workflow_apify_records VALUES(?,?,?)', (place_id, identity, fingerprint(record)))
                c.execute('INSERT OR IGNORE INTO workflow_apify_ingested VALUES(?,?,?)', (task['id'], place_id, identity))
                state['ingested'] = c.execute('SELECT count(*) FROM workflow_apify_ingested WHERE task_id=?', (task['id'],)).fetchone()[0]
        state['pages'].append({'offset': page['offset'], 'proof_uri': state.pop('pending_page_uri')})
        next_offset = page['offset'] + len(page['items'])
        done = state['ingested'] >= limit or next_offset >= page['total']
        if not done and next_offset <= state['offset']:
            return self._wait(task, 'APIFY_PAGE_NO_PROGRESS', state='NEEDS_REVIEW', result=state)
        state['offset'] = next_offset
        terminal = state.get('remote_status') in ('SUCCEEDED','FAILED','ABORTED','TIMED-OUT')
        if done and terminal:
            run = json.loads(self.store.get_blob(state['run_uri']))
            attested = run.get('attested_cost')
            currency = self.budgets.reservation(task['id'])['currency']
            if (isinstance(attested, dict) and attested.get('attested') is True and attested.get('source')
                    and attested.get('currency') == currency and attested.get('amount') is not None):
                try:
                    reconciled = self.budgets.reconcile(task['id'], 'CONFIRMED',
                        actual_amount=attested['amount'], actual_credits=attested.get('credits'))
                    state['cost_reconciliation'] = reconciled['state']
                except (ValueError, RuntimeError):
                    state['cost_reconciliation'] = 'REQUIRED_INVALID_ATTESTED_COST'
            else:
                state['cost_reconciliation'] = 'REQUIRED_BOUND_RETAINED'
            succeeded = state['remote_status'] == 'SUCCEEDED'
            # The provider's attested real costs may be absent: retain the bound.
            self.store.finish_task(task['id'], 'SUCCEEDED' if succeeded else 'WAITING', state,
                wait_reason=None if succeeded else 'APIFY_PARTIAL_RESULT')
            return {'status': 'COMPLETE' if succeeded else 'PARTIAL', **state}
        self.store.finish_task(task['id'], 'SUBMITTED', state)
        return {'status': 'SUBMITTED', **state}
