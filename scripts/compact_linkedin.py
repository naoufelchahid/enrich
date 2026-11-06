"""Named-person LinkedIn confirmation backed by the compact journal only.

The injected runner receives a task and returns exact extraction bytes (or a
structured fixture dictionary). Only the legacy global MCP lock and Extraction
schema are reused; no staging table, browser, social action, CRM or mail client
is created. Discovery from a company is a separate service.
"""
import json

from scripts.enrichment_clients import linkedin_url
from scripts.linkedin_contract import Extraction
from scripts.linkedin_mcp import name_key, target_for
from scripts.linkedin_queue import mcp_lock
from scripts.workflow_store import encode, fingerprint


class CompactLinkedIn:
    def __init__(self, store, *, lane='linkedin'):
        self.store = store
        self.lane = lane
        with store.connection() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS workflow_linkedin_targets (
                lead_id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT UNIQUE NOT NULL)''')

    def _target(self, candidate, *, create=False):
        data = candidate['data']
        if not all(isinstance(data.get(key), str) and data[key].strip()
                   for key in ('first_name', 'last_name', 'company_name')):
            raise ValueError('NAMED_PERSON_REQUIRED')
        with self.store.connection() as c:
            if create:
                c.execute('INSERT OR IGNORE INTO workflow_linkedin_targets(candidate_id) VALUES (?)', (candidate['id'],))
            row = c.execute('SELECT lead_id FROM workflow_linkedin_targets WHERE candidate_id=?', (candidate['id'],)).fetchone()
        if row is None:
            raise ValueError('LINKEDIN_TARGET_NOT_RESERVED')
        return target_for({**data, 'id': row[0], 'source_file': data.get('source_file') or 'compact:' + candidate['id']})

    def enqueue(self, candidate_id):
        candidate = self.store.candidate(candidate_id)
        if candidate is None:
            raise ValueError('CANDIDATE_NOT_FOUND')
        data = candidate['data']
        if data.get('pending_legacy_jobs'):
            return {'status':'WAITING','reason':'LEGACY_PROVIDER_RECONCILIATION_REQUIRED','candidate_id':candidate_id}
        if self._excluded(data):
            return {'status':'EXCLUDED', 'candidate_id':candidate_id}
        if self.confirmed_input(candidate):
            return {'status':'ALREADY_CONFIRMED', 'candidate_id':candidate_id}
        # Validate the lane before creating even the compatibility target mapping.
        with self.store.connection() as c:
            self.store._require_authority(c, self.lane)
        target = self._target(candidate, create=True)
        payload = {'schema_version':'1.0', 'purpose':'CONTACT', 'target':target,
                   'known_linkedin_url':linkedin_url(data.get('linkedin_url')), 'max_posts':3,
                   'provider':'linkedin_mcp', 'instructions':'docs/LINKEDIN_AGENT_INSTRUCTIONS.md',
                   'output_schema':'schemas/linkedin-extraction-v1.schema.json'}
        key = 'compact-linkedin:' + fingerprint({'candidate_id':candidate_id, 'target':target})
        # extraction_id binds the output to the immutable task, not a worker run.
        payload['extraction_id'] = key
        return self.store.create_task(self.lane, 'LINKEDIN_CONFIRM', payload,
                                      candidate_id=candidate_id, key=key)

    def _excluded(self, data):
        return any(self.store.excluded(str(data.get(key) or ''))
                   for key in ('raw_email','clean_email','validated_email','original_email'))

    def run_one(self, *, runner, worker='compact-linkedin'):
        try:
            with mcp_lock():
                task = self.store.claim_task(self.lane, worker)
                if task is None:
                    return {'status':'EMPTY'}
                if task['kind'] != 'LINKEDIN_CONFIRM':
                    self.store.finish_task(task['id'],'NEEDS_REVIEW',wait_reason='UNEXPECTED_TASK_KIND')
                    return {'status':'NEEDS_REVIEW', 'task_id':task['id']}
                candidate = self.store.candidate(task['candidate_id'])
                if candidate and candidate['data'].get('pending_legacy_jobs'):
                    self.store.finish_task(task['id'],'WAITING',wait_reason='LEGACY_PROVIDER_RECONCILIATION_REQUIRED')
                    return {'status':'WAITING','reason':'LEGACY_PROVIDER_RECONCILIATION_REQUIRED','task_id':task['id']}
                if (candidate is None or self._target(candidate) != task['payload']['target']
                        or self._excluded(candidate['data'])):
                    self.store.finish_task(task['id'],'NEEDS_REVIEW',wait_reason='TARGET_CHANGED_OR_EXCLUDED')
                    return {'status':'STALE', 'task_id':task['id']}
                try:
                    raw = runner(task['payload'])
                except Exception:
                    # A timeout may have consumed a provider operation. Never auto retry.
                    self.store.finish_task(task['id'],'UNKNOWN',wait_reason='LINKEDIN_ACCESS_OR_RESULT_UNKNOWN')
                    return {'status':'UNKNOWN', 'task_id':task['id']}
                try:
                    return self.complete(task['id'], raw)
                except (ValueError, TypeError, RuntimeError, KeyError):
                    self.store.finish_task(task['id'],'NEEDS_REVIEW',wait_reason='LINKEDIN_RESULT_INVALID')
                    return {'status':'NEEDS_REVIEW', 'task_id':task['id']}
        except RuntimeError as exc:
            if str(exc) == 'MCP_BUSY':
                return {'status':'MCP_BUSY'}
            raise

    @staticmethod
    def _raw(value):
        if isinstance(value, bytes):
            return value
        if isinstance(value, dict):
            return encode(value).encode('utf-8')
        raise TypeError('EXTRACTION_BYTES_REQUIRED')

    def complete(self, task_id, value):
        raw = self._raw(value)
        if len(raw) > 2_000_000:
            raise ValueError('RESULT_TOO_LARGE')
        task = self.store.task(task_id)
        if not task or task['lane'] != self.lane or task['kind'] != 'LINKEDIN_CONFIRM':
            raise ValueError('LINKEDIN_TASK_NOT_FOUND')
        if task['status'] == 'PENDING':
            raise ValueError('LINKEDIN_TASK_NOT_CLAIMED')
        record = Extraction.model_validate_json(raw)
        if (record.acquisition_method != 'linkedin_mcp' or record.purpose != 'CONTACT'
                or record.target is None or record.target.model_dump() != task['payload']['target']
                or record.extraction_id != task_id):
            raise ValueError('RESULT_DOES_NOT_BELONG_TO_RESERVED_TASK')
        candidate = self.store.candidate(task['candidate_id'])
        if candidate is None or self._target(candidate) != task['payload']['target']:
            raise ValueError('TARGET_CHANGED')
        if self._excluded(candidate['data']):
            raise ValueError('EXCLUDED')
        if task['status'] in ('SUCCEEDED','NO_MATCH'):
            expected = (task['result'] or {}).get('proof_uri')
            if not expected or self.store.get_blob(expected) != raw:
                raise ValueError('EXTRACTION_ID_REUSED_WITH_DIFFERENT_CONTENT')
            return {'status':'UNCHANGED', 'task_id':task_id, 'proof_uri':expected}
        # Exact proof is committed before attribution. A crash can reconcile from it.
        uri = self.store.add_evidence(candidate['id'], raw, kind='linkedin_confirmation')
        self.store.finish_task(task_id, 'SUBMITTED', {'proof_uri':uri})
        state = record.identity.status
        data = candidate['data']
        changes = {}
        if state == 'CONFIRMED':
            if name_key(record.profile.full_name) != name_key(data['first_name'] + ' ' + data['last_name']):
                raise ValueError('PROFILE_NAME_MISMATCH')
            existing = linkedin_url(data.get('linkedin_url'))
            if existing and existing != record.linkedin_url:
                raise ValueError('CONFLICTING_LINKEDIN_URL')
            # A confirmation must retain the observed professional relationship.
            company = name_key(data['company_name'])
            professional_link = (name_key(record.profile.company_displayed or '') == company
                or any(item.is_current is True and name_key(item.organization or '') == company
                       for item in record.profile.experience.items))
            if not professional_link:
                state = 'NEEDS_REVIEW'
            else:
                changes = {'linkedin_url':record.linkedin_url, 'linkedin_data_source':'linkedin_mcp',
                           'linkedin_observed_at':record.observed_at, 'linkedin_search_status':'CONFIRMED',
                           'profile_status':'SUCCEEDED', 'linkedin_proof_uri':uri, 'linkedin_task_id':task_id,
                           'is_enriched_linkedin':1}
        if state == 'CONFIRMED':
            reason = None if candidate['wait_reason'] in ('LINKEDIN_REQUIRED','LINKEDIN_REVIEW','LINKEDIN_ACCESS_REQUIRED') else candidate['wait_reason']
            task_state, result_status = 'SUCCEEDED', 'IMPORTED'
        else:
            reason = {'NOT_FOUND':'LINKEDIN_NOT_FOUND', 'BLOCKED':'LINKEDIN_ACCESS_REQUIRED'}.get(state,'LINKEDIN_REVIEW')
            # Keep an earlier confirmed attribution when a later review is inconclusive.
            if data.get('linkedin_search_status') != 'CONFIRMED':
                changes['linkedin_search_status'] = state
            task_state = 'NO_MATCH' if state == 'NOT_FOUND' else 'WAITING' if state == 'BLOCKED' else 'NEEDS_REVIEW'
            result_status = state
        self.store.update_candidate(candidate['id'], changes, lane=self.lane,
                                    wait_reason=reason, expected_revision=candidate['revision'])
        self.store.finish_task(task_id, task_state, {'proof_uri':uri, 'identity_status':state}, wait_reason=reason)
        return {'status':result_status, 'task_id':task_id, 'proof_uri':uri}

    def reconcile(self, task_id):
        task = self.store.task(task_id)
        uri = (task or {}).get('result', {}).get('proof_uri') if (task or {}).get('result') else None
        if not uri:
            return {'status':'RECONCILIATION_REQUIRED', 'task_id':task_id}
        return self.complete(task_id, self.store.get_blob(uri))

    def confirmed_input(self, candidate):
        """Recheck the exact linked proof before URL-only enrichment is allowed."""
        data = candidate['data']
        if (data.get('linkedin_search_status') != 'CONFIRMED' or
                data.get('linkedin_data_source') != 'linkedin_mcp' or not data.get('linkedin_proof_uri')):
            return None
        try:
            record = Extraction.model_validate_json(self.store.get_blob(data['linkedin_proof_uri']))
            task = self.store.task(data.get('linkedin_task_id'))
            company = name_key(data.get('company_name') or '')
            professional_link = (record.profile is not None and (
                name_key(record.profile.company_displayed or '') == company
                or any(item.is_current is True and name_key(item.organization or '') == company
                       for item in record.profile.experience.items)))
            if (not task or task['candidate_id'] != candidate['id'] or not professional_link
                    or record.profile is None
                    or name_key(record.profile.full_name) != name_key(data['first_name'] + ' ' + data['last_name'])
                    or record.identity.status != 'CONFIRMED' or record.extraction_id != data.get('linkedin_task_id')
                    or record.target.model_dump() != self._target(candidate)
                    or record.linkedin_url != linkedin_url(data.get('linkedin_url'))
                    or record.observed_at != data.get('linkedin_observed_at')):
                return None
        except (ValueError, RuntimeError, FileNotFoundError, AttributeError, KeyError):
            return None
        return {'linkedin_url':record.linkedin_url, 'enrich_fields':['contact.work_emails']}
