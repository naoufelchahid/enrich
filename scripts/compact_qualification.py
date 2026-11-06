"""Replay-safe imports and qualification in the compact journal, never the SAS.

Clients are lazy and injectable. A cost guard must reserve before every paid
submission; unresolved submissions keep their task and are never resubmitted.
"""
import json
from datetime import datetime, timezone

from scripts.direct_enrichment import admissible, normalized
from scripts.email_hygiene import email_identity, inspect_email, is_personal_email, personal_email_replacement
from scripts.enrichment_clients import FullEnrichClient, ProviderError, linkedin_url
from scripts.no2bounce_client import No2BounceClient
from scripts.workflow_store import encode, fingerprint

LANE = 'qualification'


def email_admissible(data, *, at=None):
    return data.get('email_check_status') == 'VALID' and admissible(data, at=at)


def _source_evidence(store, identity, source, sources):
    store.add_evidence(identity, encode(source).encode(), kind='source_row')
    for workbook in sources:
        if workbook['file'] == source.get('source_file'):
            store.add_evidence(identity, encode(workbook).encode(), path=workbook['file'], kind='source_workbook')


def import_compact(store, rows, *, sources=(), dry_run=False):
    """Import exact source rows with scoped replay identities, without trust flags.

An exact source-row occurrence is replayed. Names alone never merge people.
The workflow_import entrypoint supplies unchanged workbook archives as evidence.
"""
    summary = dict(accepted=0, duplicates=0, excluded=0, transferred=0, ambiguous=0,
                   sources=len(sources), inserted=0, status='COMPLETE')
    occurrences = {}
    with store.lock('compact-import'):
        if store.authority(LANE) != 'compact':
            raise RuntimeError('COMPACT_AUTHORITY_REQUIRED:' + LANE)
        by_email = {}
        for prior in store.candidates():
            value = prior['data']
            address = (value.get('raw_email') or '').strip().lower()
            if email_identity(address, value.get('first_name'), value.get('last_name'))['accepted']:
                by_email.setdefault(address, []).append(prior)
        for source in rows:
            source = dict(source)
            row_hash = fingerprint(source)
            occurrence = occurrences.get(row_hash, 0)
            occurrences[row_hash] = occurrence + 1
            source_id = 'import:' + str(source.get('source_file') or 'provided')
            legacy_id = fingerprint({'row': row_hash, 'occurrence': occurrence})
            from scripts.workflow_import import digest
            legacy_import_key = digest({'source_row':digest(source),'occurrence':occurrence})
            previous = (store.resolve_legacy(source_id, legacy_id)
                        or store.resolve_legacy('legacy-import-key',legacy_import_key))
            if previous:
                if not dry_run:
                    store.bind_legacy(source_id,legacy_id,previous)
                    _source_evidence(store, previous, source, sources)
                summary['duplicates'] += 1
                continue
            email = (source.get('raw_email') or '').strip().lower()
            if email and store.excluded(email):
                summary['excluded'] += 1
                continue
            matches = by_email.get(email, []) if email_identity(email, source.get('first_name'), source.get('last_name'))['accepted'] else []
            same = [p for p in matches if all(normalized(p['data'].get(k)) == normalized(source.get(k))
                                             for k in ('first_name', 'last_name'))]
            if len(same) == 1:
                summary['duplicates'] += 1
                if not dry_run:
                    identity = same[0]['id']
                    store.bind_legacy(source_id, legacy_id, identity)
                    _source_evidence(store, identity, source, sources)
                continue
            lead = {**source, 'original_email': source.get('original_email') or source.get('raw_email'),
                    'raw_email': email or None, 'clean_email': None, 'validated_email': None,
                    'email_validation_source': None, 'email_validated_at': None,
                    'email_check_status': 'PENDING', 'email_checked_input': None,
                    'neverbounce_status': 'PENDING', 'contact_discovery_status': 'PENDING',
                    'linkedin_data_source': None, 'linkedin_search_status': 'PENDING',
                    'linkedin_observed_at': None, 'is_enriched_linkedin': 0,
                    'linkedin_proof_uri': None, 'linkedin_task_id': None,
                    'linkedin_profile_json': None, 'linkedin_comments_json': None,
                    'recent_posts': None, 'profile_status': 'PENDING', 'posts_status': 'PENDING',
                    'acquisition_brief_id':None,'acquisition_brief_version':None,
                    'discovery_identity_required':False,'discovery_task_id':None,'discovery_company_id':None,
                    'workflow_wait_reason': None, 'verification_verdict': None,
                    'crm_person_id': None, 'crm_synced_at': None, 'outreach_status': 'PENDING'}
            # Durable identities and operational claims are never accepted from a spreadsheet.
            lead.pop('journal_contact_id', None)
            lead.pop('qualification_applied_tasks', None)
            if is_personal_email(email):
                lead.update(personal_email_replacement(lead))
            if matches:
                summary['ambiguous'] += 1
                lead['workflow_wait_reason'] = 'IDENTITY_CONFLICT'
            summary['accepted'] += 1
            if dry_run:
                continue
            identity = store.put_candidate(lead, lane=LANE, source_id=source_id, legacy_id=legacy_id,
                status='NEEDS_REVIEW' if matches else 'PENDING', wait_reason='IDENTITY_CONFLICT' if matches else None)
            if email_identity(email, lead.get('first_name'), lead.get('last_name'))['accepted']:
                by_email.setdefault(email, []).append(store.candidate(identity))
            _source_evidence(store, identity, source, sources)
            summary['inserted'] += 1
    if dry_run:
        summary['dry_run'] = True
    return summary


class CompactQualification:
    def __init__(self, store, *, no2bounce=None, fullenrich=None, cost_guard=None,
                 hygiene=inspect_email, clock=None):
        self.store = store
        self._nb, self._fe = no2bounce, fullenrich
        self.cost_guard = cost_guard
        self.hygiene = hygiene
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _client(self, provider):
        if provider == 'no2bounce':
            if self._nb is None:
                self._nb = No2BounceClient(accept_catchall=False)
            return self._nb
        if self._fe is None:
            self._fe = FullEnrichClient()
        return self._fe

    def _wait(self, identity, reason, changes=None):
        self.store.update_candidate(identity, changes or {}, lane=LANE, status='WAITING', wait_reason=reason)
        return {'status': 'WAITING', 'reason': reason, 'candidate_id': identity}

    def _tasks(self, identity):
        with self.store.connection() as c:
            return [self.store._task(r) for r in c.execute(
                'SELECT * FROM workflow_tasks WHERE lane=? AND candidate_id=? ORDER BY created_at,id', (LANE, identity))]

    def tick(self, identity):
        # Serialize one candidate through journal + transport, independently of other workflows.
        with self.store.lock('qualification-' + identity):
            candidate = self.store.candidate(identity)
            if candidate is None:
                raise ValueError('CANDIDATE_NOT_FOUND')
            data = candidate['data']
            if data.get('pending_legacy_jobs'):
                return self._wait(identity, 'LEGACY_PROVIDER_RECONCILIATION_REQUIRED')
            if data.get('discovery_identity_required') or data.get('discovery_task_id'):
                from scripts.compact_linkedin import CompactLinkedIn
                if not CompactLinkedIn(self.store).confirmed_input(candidate):
                    return self._wait(identity,'IDENTITY_CONFIRMATION_REQUIRED')
            if candidate['wait_reason'] == 'IDENTITY_CONFLICT':
                return self._wait(identity, 'IDENTITY_CONFLICT')
            email = (data.get('raw_email') or '').strip().lower()
            if email and self.store.excluded(email):
                return self._wait(identity, 'EXCLUDED')
            applied = data.get('qualification_applied_tasks', [])
            for task in self._tasks(identity):
                if task['id'] in applied:
                    # Candidate commit may precede the terminal task commit.
                    if task['status'] == 'SUBMITTED' and (task['result'] or {}).get('response_uri'):
                        self.store.finish_task(task['id'], 'SUCCEEDED', task['result'])
                    continue
                if task['status'] == 'RUNNING':
                    self.store.finish_task(task['id'], 'UNKNOWN', task['result'], wait_reason='INTERRUPTED_SUBMISSION')
                    if hasattr(self.cost_guard, 'unknown'):
                        self.cost_guard.unknown(operation_id=task['id'])
                    return self._wait(identity, 'PROVIDER_RECONCILIATION_REQUIRED')
                if task['status'] == 'UNKNOWN':
                    return self._wait(identity, 'PROVIDER_RECONCILIATION_REQUIRED')
                if task['status'] == 'SUBMITTED':
                    return self._collect(task)
                if task['status'] in ('FAILED', 'NEEDS_REVIEW', 'WAITING'):
                    return self._wait(identity, task['wait_reason'] or task['status'])
            if email_admissible(data, at=self.clock()):
                self.store.update_candidate(identity, {}, lane=LANE, status='QUALIFIED', wait_reason=None)
                return {'status': 'QUALIFIED', 'candidate_id': identity}
            if is_personal_email(email):
                data = self.store.update_candidate(identity, personal_email_replacement(data), lane=LANE)['data']
                email = ''
            source_usable = bool(email and email_identity(email, data.get('first_name'), data.get('last_name'))['accepted'])
            if source_usable and data.get('email_checked_input') == email and data.get('email_check_status') in ('WAITING', 'CATCHALL', 'UNKNOWN', 'NEEDS_REVIEW'):
                return self._wait(identity, 'VERIFICATION_' + data['email_check_status'])
            if source_usable and not (data.get('email_checked_input') == email and data.get('email_check_status') == 'INVALID'):
                hygiene = self.hygiene(email)
                if hygiene != 'PASS':
                    return self._wait(identity, 'LOCAL_HYGIENE_' + hygiene,
                                      {'email_check_status': hygiene, 'email_checked_input': email})
                return self._submit(identity, 'no2bounce', {'emailList': [email], 'after': data.get('email_validated_at')})
            # Only a target-bound, persisted LinkedIn confirmation can enable URL-only enrichment.
            from scripts.compact_linkedin import CompactLinkedIn
            confirmed = CompactLinkedIn(self.store).confirmed_input(self.store.candidate(identity))
            if not confirmed:
                return self._wait(identity, 'CONFIRMED_LINKEDIN_REQUIRED')
            url = confirmed['linkedin_url']
            if data.get('contact_discovery_status') == 'NO_MATCH':
                return self._wait(identity, 'NO_BUSINESS_EMAIL_FOUND')
            request_key = fingerprint({'candidate': identity, 'url': url})
            return self._submit(identity, 'fullenrich', {'name': 'compact-' + request_key,
                'data': [{'linkedin_url': url, 'enrich_fields': ['contact.work_emails'],
                          'custom': {'request_key': request_key}}]})

    def _submit(self, identity, provider, payload):
        task = self.store.create_task(LANE, provider, payload, candidate_id=identity)
        if task['status'] != 'PENDING':
            return self._wait(identity, task['wait_reason'] or 'PROVIDER_RECONCILIATION_REQUIRED')
        if self.cost_guard is None:
            return self._wait(identity, 'BUDGET_GUARD_REQUIRED')
        try:
            self.cost_guard(provider=provider, operation_id=task['id'], quantity=1, payload=payload)
        except (RuntimeError, ValueError) as exc:
            return self._wait(identity, str(exc))
        claimed = self.store.claim_task(LANE, 'qualification', task_id=task['id'])
        if claimed is None:
            return self._wait(identity, 'TASK_ALREADY_CLAIMED')
        self.store.add_evidence(identity, encode(payload).encode(), kind=provider + '_request')
        try:
            client = self._client(provider)
            remote = client.submit(payload['emailList'] if provider == 'no2bounce' else payload)
            if not isinstance(remote, str) or not remote:
                raise ProviderError('MISSING_REMOTE_ID', uncertain=True)
        except Exception as exc:
            uncertain = not isinstance(exc, (ProviderError, ValueError)) or getattr(exc, 'uncertain', False)
            state = 'UNKNOWN' if uncertain else 'FAILED'
            reason = str(exc) if isinstance(exc, (ProviderError, ValueError)) else 'SUBMISSION_INTERRUPTED'
            self.store.finish_task(task['id'], state, wait_reason=reason)
            if not uncertain and hasattr(self.cost_guard, 'failed'):
                self.cost_guard.failed(operation_id=task['id'], certain=True)
            elif uncertain and hasattr(self.cost_guard, 'unknown'):
                self.cost_guard.unknown(operation_id=task['id'])
            return self._wait(identity, 'PROVIDER_RECONCILIATION_REQUIRED' if uncertain else reason)
        self.store.finish_task(task['id'], 'SUBMITTED', {'remote_id': remote})
        if hasattr(self.cost_guard, 'submitted'):
            self.cost_guard.submitted(operation_id=task['id'], remote_id=remote)
        return {'status': 'SUBMITTED', 'task_id': task['id'], 'remote_id': remote}

    def reconcile_remote(self, task_id, remote_id):
        """Explicit operator/provider reconciliation, never an automatic resubmit."""
        task = self.store.task(task_id)
        if not task or task['lane'] != LANE or task['status'] not in ('UNKNOWN', 'RUNNING'):
            raise ValueError('UNCERTAIN_TASK_REQUIRED')
        if not isinstance(remote_id, str) or not remote_id.strip():
            raise ValueError('REMOTE_ID_REQUIRED')
        result = self.store.finish_task(task_id, 'SUBMITTED', {'remote_id': remote_id})
        if hasattr(self.cost_guard, 'submitted'):
            self.cost_guard.submitted(operation_id=task_id, remote_id=remote_id)
        return result

    def _collect(self, task):
        identity, provider = task['candidate_id'], task['kind']
        state = task['result']
        if state.get('response_uri'):
            response = json.loads(self.store.get_blob(state['response_uri']))
        else:
            try:
                response = self._client(provider).result(state['remote_id'])
            except ProviderError as exc:
                return self._wait(identity, str(exc))
            if response is None:
                return {'status': 'SUBMITTED', 'task_id': task['id']}
            uri = self.store.add_evidence(identity, encode(response).encode(), kind=provider + '_response')
            state = {**state, 'response_uri': uri, 'observed_at': self.clock().isoformat()}
            self.store.finish_task(task['id'], 'SUBMITTED', state)
        data = self.store.candidate(identity)['data']
        changes = {}
        try:
            if provider == 'no2bounce':
                email = task['payload']['emailList'][0]
                verdicts = response.get('verdicts')
                if not isinstance(verdicts, dict) or set(verdicts) != {email} or verdicts[email] not in ('VALID', 'INVALID', 'WAITING', 'CATCHALL'):
                    raise ValueError('VERIFICATION_RESULT_MISMATCH')
                if (data.get('raw_email') or '').strip().lower() != email:
                    raise ValueError('VERIFICATION_TARGET_CHANGED')
                verdict = verdicts[email]
                changes = {'email_checked_input': email, 'email_check_status': verdict,
                    'email_validation_source': 'no2bounce', 'email_validated_at': state['observed_at'],
                    'validated_email': email if verdict == 'VALID' else None,
                    'clean_email': email if verdict == 'VALID' else None,
                    'neverbounce_status': 'VALID' if verdict == 'VALID' else 'UNKNOWN'}
            else:
                request = task['payload']['data'][0]
                from scripts.compact_linkedin import CompactLinkedIn
                confirmed = CompactLinkedIn(self.store).confirmed_input(self.store.candidate(identity))
                if not confirmed or confirmed['linkedin_url'] != request['linkedin_url']:
                    raise ValueError('FULLENRICH_TARGET_CHANGED')
                rows = response.get('data')
                if response.get('id') != state['remote_id'] or response.get('status') != 'FINISHED' or not isinstance(rows, list) or len(rows) != 1:
                    raise ValueError('FULLENRICH_RESULT_MISMATCH')
                row = rows[0]
                if row.get('custom') != request['custom']:
                    raise ValueError('FULLENRICH_TARGET_MISMATCH')
                profile = row.get('profile') or {}
                network = ((profile.get('social_profiles') or {}).get('professional_network') or {})
                if network.get('url') and linkedin_url(network['url']) != request['linkedin_url']:
                    raise ValueError('FULLENRICH_PROFILE_CONFLICT')
                for field in ('first_name', 'last_name'):
                    if profile.get(field) and normalized(profile[field]) != normalized(data.get(field)):
                        raise ValueError('FULLENRICH_IDENTITY_CONFLICT')
                most = (row.get('contact_info') or {}).get('most_probable_work_email') or {}
                email = most.get('email')
                if is_personal_email(email):
                    changes = {'contact_discovery_status': 'NO_MATCH',
                               'email_check_reason': 'PERSONAL_DOMAIN',
                               'workflow_wait_reason': 'NO_BUSINESS_EMAIL_FOUND'}
                elif not email or not email_identity(email, data.get('first_name'), data.get('last_name'))['accepted']:
                    changes = {'contact_discovery_status': 'NO_MATCH'}
                else:
                    changes = {'raw_email': email.strip().lower(), 'clean_email': None,
                        'validated_email': None, 'email_validation_source': None, 'email_validated_at': None,
                        'email_checked_input': None, 'email_check_status': 'PENDING', 'neverbounce_status': 'PENDING',
                        'contact_discovery_status': 'SUCCEEDED'}
        except (ValueError, TypeError, AttributeError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else 'PROVIDER_SCHEMA_INVALID'
            self.store.finish_task(task['id'], 'NEEDS_REVIEW', state, wait_reason=reason)
            return self._wait(identity, reason)
        changes['qualification_applied_tasks'] = list(dict.fromkeys(data.get('qualification_applied_tasks', []) + [task['id']]))
        updated = self.store.update_candidate(identity, changes, lane=LANE, status='PENDING', wait_reason=None)['data']
        self.store.finish_task(task['id'], 'SUCCEEDED', state)
        if email_admissible(updated, at=self.clock()):
            self.store.update_candidate(identity, {}, lane=LANE, status='QUALIFIED', wait_reason=None)
            return {'status': 'QUALIFIED', 'candidate_id': identity}
        return {'status': 'RESULT_APPLIED', 'task_id': task['id']}
