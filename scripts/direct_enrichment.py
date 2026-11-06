"""Budgeted email enrichment and handoff to the LinkedIn MCP worker.

Submission and polling are separate invocations. An uncertain paid request is
never retried automatically. Budgets are in provider units, NOT guessed USD.
"""
import fcntl
import hashlib
import json
import math
import os
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.enrichment_clients import FullEnrichClient, HarvestClient, ProviderError, linkedin_url
from scripts.linkedin_mcp import pending_tasks, target_for
from scripts.email_hygiene import valid_email, fresh, inspect_email, email_identity, is_personal_email, personal_email_replacement
from scripts.no2bounce_client import No2BounceClient
from scripts.staging_db import calculate_priority_score, init_staging_db
from scripts.outreach_journal import install_outbox, flush_journal


def now():
    return datetime.now(timezone.utc).isoformat()


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def encoded(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def fingerprint(data):
    return hashlib.sha256(encoded(data).encode()).hexdigest()


def verified(lead):
    email = (lead.get('raw_email') or '').strip().lower()
    return bool(valid_email(email) and not is_personal_email(email) and lead.get('neverbounce_status') == 'VALID'
                and lead.get('email_check_status') not in ('INVALID', 'WAITING', 'UNKNOWN', 'FAILED', 'NEEDS_REVIEW', 'SUBMITTED', 'EXPIRED')
                and lead.get('email_validation_source') in ('fullenrich', 'no2bounce')
                and fresh(lead.get('email_validated_at'))
                and (lead.get('validated_email') or '').lower() == email
                and (lead.get('clean_email') or '').lower() == email)


def admissible(lead, *, at=None):
    email = (lead.get('raw_email') or '').strip().lower()
    return bool(email_identity(email, lead.get('first_name'), lead.get('last_name'))['accepted']
                and lead.get('email_check_status') in ('VALID', 'CATCHALL')
                and lead.get('email_validation_source') == 'no2bounce'
                and fresh(lead.get('email_validated_at'),at=at)
                and (lead.get('validated_email') or '').lower() == email
                and (lead.get('clean_email') or '').lower() == email)


def contact_input(lead):
    data = {'enrich_fields': ['contact.work_emails']}
    url = linkedin_url(lead.get('linkedin_url'))
    if url:
        data['linkedin_url'] = url
    for field in ('first_name', 'last_name', 'company_name'):
        if str(lead.get(field) or '').strip():
            data[field] = str(lead[field]).strip()
    # Explicitly reviewed domain only; never infer it from a company name, website
    # or mailbox automatically. An incomplete declaration blocks paid discovery.
    declared = lead.get('company_domain')
    if declared:
        source = lead.get('company_domain_source')
        if not isinstance(declared, str) or not isinstance(source, str) or not source.strip():
            return None
        domain = declared.strip().lower()
        if not valid_email('x@' + domain):
            return None
        data['domain'] = domain
    if not url and not (data.get('first_name') and data.get('last_name')
                        and (data.get('company_name') or data.get('domain'))):
        return None
    return data


def contact_key(data):
    if data.get('domain'):
        # Keep v2 keys untouched for existing domain-less jobs and cache entries.
        identity = {f: normalized(data.get(f)) for f in
                    ('first_name', 'last_name', 'company_name', 'domain', 'linkedin_url')}
        return fingerprint({'version': 3, 'identity': identity, 'fields': data['enrich_fields']})
    identity = {'linkedin_url': data['linkedin_url']} if data.get('linkedin_url') else {
        f: normalized(data.get(f)) for f in ('first_name', 'last_name', 'company_name')}
    return fingerprint({'version': 2, 'identity': identity, 'fields': data['enrich_fields']})


def confirmed_linkedin_input(c, lead):
    """Use only a URL bound to this contact by imported extraction evidence."""
    url = linkedin_url(lead.get('linkedin_url'))
    if (not url or lead.get('linkedin_data_source') not in ('linkedin_mcp', 'codex_chrome')
            or lead.get('linkedin_search_status') != 'CONFIRMED'
            or not lead.get('linkedin_observed_at')):
        return None
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='linkedin_browser_imports'").fetchone():
        return None
    proof = c.execute('''SELECT 1 FROM linkedin_browser_imports
        WHERE lead_id=? AND disposition='IMPORTED'
        AND json_extract(payload,'$.identity.status')='CONFIRMED'
        AND json_extract(payload,'$.linkedin_url')=?
        AND json_extract(payload,'$.target.input_sha256')=?
        AND json_extract(payload,'$.observed_at')=? LIMIT 1''',
        (lead['id'], url, target_for(lead)['input_sha256'], lead['linkedin_observed_at'])).fetchone()
    if not proof:
        return None
    return {'linkedin_url': url, 'enrich_fields': ['contact.work_emails']}


@contextmanager
def database(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
        flush_journal(path)
    finally:
        conn.close()


class DirectEnrichment:
    def __init__(self, *, fullenrich=None, harvest=None, no2bounce=None, dry_run=False,
                 no2bounce_daily_addresses=None, pilot=False, phase=None,
                 fullenrich_daily_credits=None, harvest_daily_requests=None,
                 email_credit_ceiling=None, posts_enabled=None, poll_seconds=30, workflow=False, lead_ids=None):
        self.fullenrich = fullenrich or FullEnrichClient()
        self.harvest = harvest or HarvestClient()
        self.workflow = workflow
        self.lead_ids = set(lead_ids) if lead_ids is not None else None
        self.deadline = None
        self.clock = lambda:datetime.now(timezone.utc)
        self.no2bounce = no2bounce or No2BounceClient(accept_catchall=workflow)
        self.nb_limit = int(no2bounce_daily_addresses if no2bounce_daily_addresses is not None
                            else os.getenv('NO2BOUNCE_DAILY_ADDRESSES', '0'))
        self.pilot = pilot
        self.phase = phase
        if phase not in (None, 'all', 'verify', 'enrich', 'profiles', 'linkedin-search', 'linkedin'):
            raise ValueError('Unknown enrichment phase')
        self.dry_run = dry_run
        self.fe_limit = float(fullenrich_daily_credits if fullenrich_daily_credits is not None
                              else os.getenv('FULLENRICH_DAILY_CREDITS', '0'))
        self.hv_limit = 0  # Retired, even if legacy environment/arguments provide a budget.
        self.email_ceiling = float(email_credit_ceiling if email_credit_ceiling is not None
                                   else os.getenv('FULLENRICH_EMAIL_CREDIT_CEILING', '1'))
        self.posts_enabled = posts_enabled if posts_enabled is not None else os.getenv('HARVEST_POSTS_ENABLED') == '1'
        self.poll_seconds = max(0, poll_seconds)
        if not all(math.isfinite(x) and x >= 0 for x in (self.fe_limit, self.hv_limit, self.nb_limit, self.email_ceiling)) or self.email_ceiling < 1:
            raise ValueError('Invalid provider budget or credit ceiling')
        if workflow:
            self.fe_limit = self.nb_limit = float('inf')

    def _acceptable(self, lead):
        return admissible(lead) if self.workflow else verified(lead)

    def _within_window(self,margin=0):
        return self.deadline is None or self.clock()+timedelta(seconds=margin)<self.deadline

    def _init(self, path):
        init_staging_db(path).close()
        with database(path) as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS enrichment_limits (provider TEXT PRIMARY KEY, total_items INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS enrichment_jobs (
                    id TEXT PRIMARY KEY, provider TEXT NOT NULL, kind TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, remote_id TEXT UNIQUE,
                    response TEXT, reserved REAL NOT NULL, cost REAL,
                    created_at TEXT NOT NULL, next_poll_at TEXT, error TEXT
                );
                CREATE TABLE IF NOT EXISTS enrichment_cache (
                    provider TEXT NOT NULL, kind TEXT NOT NULL, request_key TEXT NOT NULL,
                    job_id TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
                    PRIMARY KEY (provider, kind, request_key)
                );
                CREATE TABLE IF NOT EXISTS enrichment_targets (
                    job_id TEXT NOT NULL, request_key TEXT NOT NULL, lead_id INTEGER NOT NULL,
                    input_json TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(job_id, request_key, lead_id)
                );
            ''')
            columns={r[1] for r in c.execute('PRAGMA table_info(enrichment_jobs)')}
            if 'retry_attempts' not in columns:
                c.execute('ALTER TABLE enrichment_jobs ADD COLUMN retry_attempts INTEGER NOT NULL DEFAULT 0')
            install_outbox(c)
            if self.pilot:
                if c.execute('SELECT count(*) FROM staging_leads').fetchone()[0] > 3:
                    raise ValueError('Pilot requires an isolated database of at most three contacts')
                for provider, cap in [('no2bounce', 3), ('fullenrich', 3), ('harvest', 0)]:
                    c.execute('INSERT INTO enrichment_limits VALUES(?,?) ON CONFLICT(provider) DO UPDATE SET total_items=min(total_items,excluded.total_items)', (provider, cap))
            # Missing flags, too, normalize to zero without rewriting stable rows.
            # A legacy "enriched" flag without a personal URL cannot be genuine.
            c.execute("""UPDATE staging_leads SET is_enriched_linkedin=0
                WHERE coalesce(trim(linkedin_url),'')='' AND is_enriched_linkedin IS NOT 0""")

    @staticmethod
    def _remaining_items(c, provider):
        limit = c.execute('SELECT total_items FROM enrichment_limits WHERE provider=?', (provider,)).fetchone()
        if not limit:
            return float('inf')
        used = 0
        for (payload,) in c.execute('SELECT payload FROM enrichment_jobs WHERE provider=?', (provider,)):
            data = json.loads(payload)
            used += len(data['data']) if provider == 'fullenrich' else len(data['emailList']) if provider == 'no2bounce' else 1
        return max(0, limit[0] - used)

    @staticmethod
    def _suppressed(c, email):
        return bool(email and c.execute('SELECT 1 FROM email_suppressions WHERE email=?', (email,)).fetchone())

    def _collect_no2bounce(self, path):
        if not self.no2bounce.api_key:
            return
        with database(path) as c:
            jobs = c.execute("SELECT * FROM enrichment_jobs WHERE provider='no2bounce' AND status='SUBMITTED' AND (next_poll_at IS NULL OR next_poll_at<=?)", (now(),)).fetchall()
        for job in jobs[:10]:
            if not self._within_window():
                break
            with database(path) as c:
                c.execute('UPDATE enrichment_jobs SET next_poll_at=? WHERE id=?',
                          ((datetime.now(timezone.utc) + timedelta(seconds=self.poll_seconds)).isoformat(), job['id']))
            try:
                result = self.no2bounce.result(job['remote_id'])
            except ProviderError as exc:
                with database(path) as c:
                    self._record_read_failure(c,job,exc)
                continue
            if result is not None:
                with database(path) as c:
                    c.execute("UPDATE enrichment_jobs SET response=?,status='RECEIVED' WHERE id=?", (encoded(result), job['id']))

    @staticmethod
    def _decode_verification(c, job, result):
        expected = c.execute('SELECT DISTINCT request_key,input_json FROM enrichment_targets WHERE job_id=?', (job['id'],)).fetchall()
        emails = {json.loads(row['input_json'])['email'] for row in expected}
        verdicts = result.get('verdicts') if isinstance(result, dict) else None
        valid = (isinstance(verdicts, dict) and set(verdicts) == emails
                 and all(isinstance(v, str) and v in ('VALID', 'CATCHALL', 'INVALID', 'WAITING') for v in verdicts.values()))
        if not valid:
            c.execute("UPDATE enrichment_jobs SET status='NEEDS_REVIEW',error='VERIFICATION_RESULT_MISMATCH' WHERE id=?", (job['id'],))
            c.execute("UPDATE enrichment_cache SET status='NEEDS_REVIEW' WHERE job_id=?", (job['id'],))
            return
        for target in expected:
            email = json.loads(target['input_json'])['email']
            c.execute("UPDATE enrichment_cache SET status='READY',result=? WHERE job_id=? AND request_key=?",
                      (encoded({'email': email, 'verdict': verdicts[email]}), job['id'], target['request_key']))
        # 'cost' is addresses submitted, not a guessed number of billed credits.
        c.execute("UPDATE enrichment_jobs SET status='FINISHED',cost=reserved,error=NULL WHERE id=?", (job['id'],))

    @staticmethod
    def _apply_verification(sent, result, observed_at):
        email = sent['email']
        if is_personal_email(email):
            return {'email_check_status':'INVALID', 'email_checked_input':email,
                    'email_check_reason':'PERSONAL_DOMAIN', 'clean_email':None,
                    'validated_email':None, 'neverbounce_status':'UNKNOWN',
                    'enrichment_status':'PENDING', 'workflow_wait_reason':'REPLACEMENT_EMAIL_REQUIRED'}
        if result.get('email') != email:
            return {'email_check_status': 'NEEDS_REVIEW'}
        status = result['verdict']
        if status in ('VALID', 'CATCHALL') and not fresh(observed_at):
            status = 'EXPIRED'
        return {'email_check_status': status, 'email_checked_input': email,
                'email_check_reason': None, 'neverbounce_status': status if status in ('VALID', 'INVALID') else 'UNKNOWN',
                'clean_email': email if status in ('VALID', 'CATCHALL') else None,
                'validated_email': email if status in ('VALID', 'CATCHALL') else None,
                'email_validated_at': observed_at, 'email_validation_source': 'no2bounce',
                'enrichment_status': 'READY_FOR_CRM' if status in ('VALID', 'CATCHALL') else 'PENDING'}

    @staticmethod
    def _verification_key(c, email):
        previous = c.execute('''SELECT DISTINCT ca.request_key,ca.job_id,ca.status,ca.result,j.created_at
            FROM enrichment_cache ca JOIN enrichment_targets t ON t.job_id=ca.job_id AND t.request_key=ca.request_key
            JOIN enrichment_jobs j ON j.id=ca.job_id
            WHERE ca.provider='no2bounce' AND json_extract(t.input_json,'$.email')=?
            ORDER BY j.created_at DESC,j.rowid DESC LIMIT 1''', (email,)).fetchone()
        if previous:
            data = json.loads(previous['result']) if previous['result'] else {}
            if previous['status'] == 'READY' and data.get('verdict') in ('VALID', 'CATCHALL') and not fresh(previous['created_at']):
                return fingerprint({'email': email, 'after': previous['job_id']})
            return previous['request_key']
        return fingerprint({'email': email, 'version': 1})

    def _verify_emails(self, path, batch_size):
        if not self._within_window(30):
            return
        groups, dns_cache = {}, {}
        with database(path) as c:
            leads = [dict(row) for row in c.execute("SELECT * FROM staging_leads WHERE outreach_status='PENDING' ORDER BY priority_score DESC,id")]
            for lead in leads:
                email = (lead.get('raw_email') or '').strip().lower()
                if self.lead_ids is not None and lead['id'] not in self.lead_ids:
                    continue
                if is_personal_email(email):
                    updates=personal_email_replacement(lead)
                    c.execute('UPDATE staging_leads SET '+','.join(k+'=?' for k in updates)+' WHERE id=?',
                              (*updates.values(),lead['id']))
                    continue
                if not email or self._suppressed(c, email) or self._acceptable(lead):
                    continue
                if self.workflow:
                    identity = email_identity(email, lead.get('first_name'), lead.get('last_name'))
                    c.execute('UPDATE staging_leads SET identity_score_json=? WHERE id=?', (encoded(identity), lead['id']))
                    if not identity['accepted']:
                        c.execute("""UPDATE staging_leads SET email_check_status='INVALID',email_checked_input=?,
                            email_check_reason=?,clean_email=NULL,validated_email=NULL,workflow_wait_reason='REPLACEMENT_EMAIL_REQUIRED'
                            WHERE id=?""", (email, 'IDENTITY:' + identity['reason'], lead['id']))
                        continue
                if lead.get('email_checked_input') == email and lead.get('email_check_status') in ('INVALID', 'WAITING', 'UNKNOWN', 'FAILED', 'NEEDS_REVIEW'):
                    continue
                hygiene = inspect_email(email, domain_cache=dns_cache)
                if hygiene != 'PASS':
                    c.execute('''UPDATE staging_leads SET email_check_status=?,email_checked_input=?,
                        email_check_reason='LOCAL_HYGIENE',clean_email=NULL,validated_email=NULL,
                        neverbounce_status=?,email_validation_source=NULL,email_validated_at=NULL WHERE id=?''',
                        (hygiene, email, 'INVALID' if hygiene == 'INVALID' else 'UNKNOWN', lead['id']))
                    continue
                c.execute('UPDATE staging_leads SET email_checked_input=? WHERE id=?', (email, lead['id']))
                key = self._verification_key(c, email)
                groups.setdefault(key, []).append((lead, {'email': email}))
        groups = self._attach_cache(path, 'no2bounce', 'email', groups)
        if not groups or not self.no2bounce.api_key or self.nb_limit <= 0 or not self.no2bounce.contract_ready:
            return
        with database(path) as c:
            if c.execute("SELECT 1 FROM enrichment_jobs WHERE provider='no2bounce' AND status IN ('UNKNOWN','FAILED','SUBMITTED','NEEDS_REVIEW','RETRY_SCHEDULED')").fetchone():
                return
            count = min(batch_size, self.nb_limit - int(self._used(c, 'no2bounce')), self._remaining_items(c, 'no2bounce'))
        if count <= 0:
            return
        groups = dict(list(groups.items())[:int(count)])
        emails = [members[0][1]['email'] for members in groups.values()]
        reservation = self._reserve(path, 'no2bounce', 'email', {'emailList': emails}, groups, len(emails), self.nb_limit)
        if not reservation:
            return
        job_id, _ = reservation
        try:
            remote_id = self.no2bounce.submit(emails)
        except ProviderError as exc:
            self._failure(path, job_id, exc)
            return
        with database(path) as c:
            c.execute("UPDATE enrichment_jobs SET status='SUBMITTED',remote_id=? WHERE id=?", (remote_id, job_id))
            c.execute("UPDATE enrichment_cache SET status='SUBMITTED' WHERE job_id=?", (job_id,))
            c.execute("UPDATE staging_leads SET email_check_status='SUBMITTED' WHERE id IN (SELECT lead_id FROM enrichment_targets WHERE job_id=?)", (job_id,))

    @staticmethod
    def _used(c, provider):
        # Unreconciled requests retain their reservation across UTC day boundaries.
        return c.execute('''SELECT COALESCE(SUM(COALESCE(cost, reserved)),0)
                            FROM enrichment_jobs WHERE provider=?
                            AND (substr(created_at,1,10)=? OR cost IS NULL)''',
                         (provider, now()[:10])).fetchone()[0]

    @staticmethod
    def _column(provider, kind):
        if provider == 'no2bounce':
            return 'email_check_status'
        if provider == 'harvest' and kind == 'search':
            return 'linkedin_search_status'
        return 'contact_discovery_status' if provider == 'fullenrich' else ('posts_status' if kind == 'posts' else 'profile_status')

    def _reserve(self, path, provider, kind, payload, groups, amount, limit):
        if not self._within_window(30):
            return None
        job_id = str(uuid.uuid4())
        if provider == 'fullenrich':
            payload = {**payload, 'name': 'Enrich ' + job_id}
        with database(path) as c:
            c.execute('BEGIN IMMEDIATE')
            if self._used(c, provider) + amount > limit or self._remaining_items(c, provider) < len(groups):
                return None
            c.execute('INSERT INTO enrichment_jobs(id,provider,kind,status,payload,reserved,created_at) VALUES(?,?,?,?,?,?,?)',
                      (job_id, provider, kind, 'UNKNOWN', encoded(payload), amount, now()))
            for key, members in groups.items():
                c.execute('INSERT INTO enrichment_cache VALUES(?,?,?,?,?,NULL)',
                          (provider, kind, key, job_id, 'UNKNOWN'))
                for lead, data in members:
                    c.execute('INSERT INTO enrichment_targets(job_id,request_key,lead_id,input_json) VALUES(?,?,?,?)',
                              (job_id, key, lead['id'], encoded(data)))
                    c.execute(f'UPDATE staging_leads SET {self._column(provider, kind)}=? WHERE id=?', ('UNKNOWN', lead['id']))
        return job_id, payload

    def _failure(self, path, job_id, error):
        state = 'UNKNOWN' if error.uncertain else 'FAILED'
        with database(path) as c:
            job = c.execute('SELECT * FROM enrichment_jobs WHERE id=?', (job_id,)).fetchone()
            attempts=job['retry_attempts']+1
            if self.workflow and not error.uncertain and str(error)=='HTTP_429' and attempts<3:
                state='RETRY_SCHEDULED'
            c.execute('UPDATE enrichment_jobs SET retry_attempts=?,next_poll_at=? WHERE id=?',
                      (attempts,(datetime.now(timezone.utc)+timedelta(seconds=60*2**min(attempts-1,2))).isoformat(),job_id))
            c.execute('UPDATE enrichment_jobs SET status=?,error=? WHERE id=?', (state, str(error), job_id))
            c.execute('UPDATE enrichment_cache SET status=? WHERE job_id=?', (state, job_id))
            col = self._column(job['provider'], job['kind'])
            c.execute(f'UPDATE staging_leads SET {col}=? WHERE id IN (SELECT lead_id FROM enrichment_targets WHERE job_id=?)',
                      (state, job_id))

    def _attach_cache(self, path, provider, kind, groups):
        fresh = {}
        with database(path) as c:
            for key, members in groups.items():
                cached = c.execute('SELECT * FROM enrichment_cache WHERE provider=? AND kind=? AND request_key=?',
                                   (provider, kind, key)).fetchone()
                if not cached:
                    fresh[key] = members
                    continue
                for lead, data in members:
                    c.execute('INSERT OR IGNORE INTO enrichment_targets(job_id,request_key,lead_id,input_json) VALUES(?,?,?,?)',
                              (cached['job_id'], key, lead['id'], encoded(data)))
                    c.execute(f'UPDATE staging_leads SET {self._column(provider, kind)}=? WHERE id=?',
                              (cached['status'], lead['id']))
        return fresh

    def _collect_fullenrich(self, path):
        if not self.fullenrich.api_key:
            return
        with database(path) as c:
            jobs = c.execute("SELECT * FROM enrichment_jobs WHERE provider='fullenrich' AND status='SUBMITTED' AND (next_poll_at IS NULL OR next_poll_at<=?)", (now(),)).fetchall()
        # At most ten free result checks per invocation, no busy polling loop.
        for job in jobs[:10]:
            if not self._within_window():
                break
            with database(path) as c:
                c.execute('UPDATE enrichment_jobs SET next_poll_at=? WHERE id=?',
                          ((datetime.now(timezone.utc) + timedelta(seconds=self.poll_seconds)).isoformat(), job['id']))
            try:
                result = self.fullenrich.result(job['remote_id'])
            except ProviderError as exc:
                # Retrieval failure does not justify a second submission.
                with database(path) as c:
                    self._record_read_failure(c,job,exc)
                continue
            if result is None:
                continue
            with database(path) as c:
                c.execute("UPDATE enrichment_jobs SET response=?,status='RECEIVED' WHERE id=?", (encoded(result), job['id']))

    def _record_read_failure(self,c,job,error):
        if not self.workflow:
            c.execute('UPDATE enrichment_jobs SET error=? WHERE id=?',(str(error),job['id']))
            return
        attempts=job['retry_attempts']+1
        c.execute('UPDATE enrichment_jobs SET retry_attempts=?,error=?,status=?,next_poll_at=? WHERE id=?',
                  (attempts,str(error),'NEEDS_REVIEW' if attempts>=3 else 'SUBMITTED',
                   (datetime.now(timezone.utc)+timedelta(seconds=60*2**min(attempts-1,2))).isoformat(),job['id']))

    def _retry_rejected(self,path):
        if not self.workflow:
            return
        with database(path) as c:
            jobs=[dict(r) for r in c.execute("SELECT * FROM enrichment_jobs WHERE status='RETRY_SCHEDULED' AND retry_attempts<3 AND next_poll_at<=?",(now(),))]
        for job in jobs:
            if not self._within_window(30):
                break
            # Only a definite rejection (HTTP 429) reaches this state; mark UNKNOWN
            # before the new POST so a crash never enables blind resubmission.
            with database(path) as c:
                c.execute("UPDATE enrichment_jobs SET status='UNKNOWN' WHERE id=?",(job['id'],))
            payload=json.loads(job['payload'])
            client=self.no2bounce if job['provider']=='no2bounce' else self.fullenrich
            try:
                remote_id=client.submit(payload['emailList'] if job['provider']=='no2bounce' else payload)
            except ProviderError as error:
                self._failure(path,job['id'],error)
            else:
                with database(path) as c:
                    c.execute("UPDATE enrichment_jobs SET status='SUBMITTED',remote_id=?,retry_attempts=0,error=NULL WHERE id=?",(remote_id,job['id']))
                    c.execute("UPDATE enrichment_cache SET status='SUBMITTED' WHERE job_id=?",(job['id'],))

    def _decode_received(self, path):
        with database(path) as c:
            jobs = c.execute("SELECT * FROM enrichment_jobs WHERE status='RECEIVED'").fetchall()
            for job in jobs:
                result = json.loads(job['response'])
                if job['provider'] == 'no2bounce':
                    self._decode_verification(c, job, result)
                    continue
                if job['provider'] == 'harvest':
                    # Keep the complete response, not just three posts or a headline.
                    c.execute("UPDATE enrichment_cache SET status='READY',result=? WHERE job_id=?", (job['response'], job['id']))
                    c.execute("UPDATE enrichment_jobs SET status='FINISHED',cost=reserved WHERE id=?", (job['id'],))
                    continue
                if result.get('id') != job['remote_id'] or result.get('status') != 'FINISHED' or not isinstance(result.get('data'), list):
                    c.execute("UPDATE enrichment_jobs SET status='NEEDS_REVIEW',error='INCOMPLETE_OR_INVALID_RESULT' WHERE id=?", (job['id'],))
                    c.execute("UPDATE enrichment_cache SET status='NEEDS_REVIEW' WHERE job_id=?", (job['id'],))
                    c.execute("UPDATE staging_leads SET contact_discovery_status='NEEDS_REVIEW' WHERE id IN (SELECT lead_id FROM enrichment_targets WHERE job_id=?)", (job['id'],))
                    continue
                rows = {}
                for row in result['data']:
                    key = (row.get('custom') or {}).get('request_key') if isinstance(row, dict) else None
                    rows.setdefault(key, []).append(row)
                expected = c.execute('SELECT request_key FROM enrichment_cache WHERE job_id=?', (job['id'],)).fetchall()
                unexpected = set(rows) - {row[0] for row in expected}
                contract_error = bool(unexpected) or any(len(rows.get(row[0], [])) != 1 for row in expected)
                for (key,) in expected:
                    items = rows.get(key, [])
                    state = 'READY' if len(items) == 1 and not unexpected else 'NEEDS_REVIEW'
                    c.execute('UPDATE enrichment_cache SET status=?,result=? WHERE job_id=? AND request_key=?',
                              (state, encoded(items[0]) if len(items) == 1 else None, job['id'], key))
                cost = result.get('cost', {}).get('credits') if isinstance(result.get('cost'), dict) else None
                if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
                    cost = None  # Keep worst-case reservation until reconciled.
                error = 'COST_EXCEEDED_RESERVATION' if not self.workflow and cost is not None and cost > job['reserved'] else None
                if contract_error:
                    error = error or 'RESULT_KEYS_MISMATCH'
                c.execute('UPDATE enrichment_jobs SET status=?,cost=?,error=? WHERE id=?',
                          ('NEEDS_REVIEW' if contract_error else 'FINISHED', cost, error, job['id']))

    @staticmethod
    def _apply_contact(lead, sent, row):
        profile = row.get('profile') or {}
        info = row.get('contact_info') or {}
        if not isinstance(profile, dict) or not isinstance(info, dict):
            return {'contact_discovery_status': 'NEEDS_REVIEW'}
        company = ((profile.get('employment') or {}).get('current') or {}).get('company') or {}
        if (not sent.get('linkedin_url') and isinstance(company, dict) and company.get('name')
                and normalized(company['name']) != normalized(sent.get('company_name'))):
            return {'contact_discovery_status': 'NEEDS_REVIEW'}
        # Refuse a provider profile contradicting the supplied person's name.
        for field in ('first_name', 'last_name'):
            expected = sent.get(field) or lead.get(field)
            if expected and profile.get(field) and normalized(expected) != normalized(profile[field]):
                return {'contact_discovery_status': 'NEEDS_REVIEW'}
        updates = {}
        most = info.get('most_probable_work_email') or {}
        if not isinstance(most, dict):
            return {'contact_discovery_status': 'NEEDS_REVIEW'}
        email = most.get('email')
        if is_personal_email(email):
            return {'contact_discovery_status':'NO_MATCH', 'clean_email':None,
                    'validated_email':None, 'email_check_status':'INVALID',
                    'email_checked_input':lead.get('raw_email'), 'email_check_reason':'PERSONAL_DOMAIN',
                    'neverbounce_status':'UNKNOWN', 'enrichment_status':'PENDING',
                    'workflow_wait_reason':'NO_BUSINESS_EMAIL_FOUND'}
        if valid_email(email) and not verified(lead):
            email = email.strip().lower()
            deliverable = most.get('status') == 'DELIVERABLE' and inspect_email(email) == 'PASS'
            updates.update(raw_email=email, clean_email=email if deliverable else None,
                           neverbounce_status='VALID' if deliverable else 'UNKNOWN',
                           enrichment_status='READY_FOR_CRM' if deliverable else 'PENDING',
                           email_validation_source='fullenrich', validated_email=email if deliverable else None,
                           email_validated_at=row.get('_observed_at'),
                           email_check_status='VALID' if deliverable else 'WAITING',
                           email_checked_input=email)
        social = profile.get('social_profiles') or {}
        network = social.get('professional_network') or {} if isinstance(social, dict) else {}
        url = linkedin_url(network.get('url')) if isinstance(network, dict) else None
        if url:
            if sent.get('linkedin_url') and linkedin_url(sent['linkedin_url']) != url:
                return {'contact_discovery_status': 'NEEDS_REVIEW'}
            if not lead.get('linkedin_url'):
                updates['linkedin_url'] = url
        updates['contact_discovery_status'] = 'SUCCEEDED' if updates else 'NO_MATCH'
        return updates

    def _apply(self, path):
        with database(path) as c:
            targets = c.execute('''SELECT t.*, ca.provider, ca.kind, ca.status, ca.result, j.created_at AS observed_at
                FROM enrichment_targets t JOIN enrichment_cache ca
                ON ca.job_id=t.job_id AND ca.request_key=t.request_key
                JOIN enrichment_jobs j ON j.id=t.job_id
                WHERE t.applied=0 AND ca.provider!='harvest' AND ca.status IN ('READY','NEEDS_REVIEW')''').fetchall()
            for target in targets:
                row = c.execute('SELECT * FROM staging_leads WHERE id=?', (target['lead_id'],)).fetchone()
                if not row:
                    c.execute('UPDATE enrichment_targets SET applied=1 WHERE job_id=? AND request_key=? AND lead_id=?',
                              (target['job_id'], target['request_key'], target['lead_id']))
                    continue
                lead = dict(row)
                sent = json.loads(target['input_json'])
                col = self._column(target['provider'], target['kind'])
                if target['provider'] == 'no2bounce':
                    stale = (lead.get('raw_email') or '').strip().lower() != sent['email']
                else:
                    # Reconcile historical name/domain jobs with their original
                    # identity. New URL-only jobs require the current proof.
                    url_only = sent.get('linkedin_url') and not any(sent.get(k) for k in ('first_name','last_name','company_name','domain'))
                    current = (confirmed_linkedin_input(c, lead) if url_only else contact_input(lead)) if target['provider'] == 'fullenrich' else {'url': linkedin_url(lead.get('linkedin_url'))}
                    stale = not current or (contact_key(current) != contact_key(sent) if target['provider'] == 'fullenrich' else current['url'] != sent['url'])
                if stale or target['status'] == 'NEEDS_REVIEW':
                    updates = {col: 'NEEDS_REVIEW'}
                else:
                    result = json.loads(target['result'])
                    try:
                        if target['provider'] == 'no2bounce':
                            updates = self._apply_verification(sent, result, target['observed_at'])
                            if self.workflow:
                                updates['verification_verdict'] = result['verdict']
                        elif target['provider'] == 'fullenrich':
                            result['_observed_at'] = target['observed_at']
                            updates = self._apply_contact(lead, sent, result)
                            if self.workflow and updates.get('raw_email'):
                                # Discovery is never the verifier in the new workflow.
                                updates.update(clean_email=None, validated_email=None, email_validation_source=None,
                                               email_validated_at=None, email_check_status='PENDING', neverbounce_status='PENDING',
                                               enrichment_status='PENDING', workflow_wait_reason=None)
                    except (TypeError, ValueError, AttributeError):
                        updates = {col: 'NEEDS_REVIEW'}
                merged = {**lead, **updates}
                updates['priority_score'] = calculate_priority_score(merged)
                c.execute('UPDATE staging_leads SET ' + ','.join(f'{k}=?' for k in updates) + ' WHERE id=?',
                          (*updates.values(), lead['id']))
                c.execute('UPDATE enrichment_targets SET applied=1 WHERE job_id=? AND request_key=? AND lead_id=?',
                          (target['job_id'], target['request_key'], lead['id']))

    def _submit_contacts(self, path, batch_size):
        if not self._within_window(30):
            return
        with database(path) as c:
            leads = [dict(r) for r in c.execute("SELECT * FROM staging_leads WHERE contact_discovery_status IN ('PENDING','WAITING_LINKEDIN') ORDER BY priority_score DESC,id")]
        groups = {}
        with database(path) as c:
            for lead in leads:
                if self._acceptable(lead) or lead.get('outreach_status') != 'PENDING':
                    continue  # Do not pay to rediscover an already verified email.
                if self.lead_ids is not None and lead['id'] not in self.lead_ids:
                    continue
                email = (lead.get('raw_email') or '').strip().lower()
                if email and not is_personal_email(email) and not (lead.get('email_check_status') == 'INVALID' and lead.get('email_checked_input') == email):
                    continue
                if self._suppressed(c, email):
                    continue
                data = confirmed_linkedin_input(c, lead)
                if not data:
                    c.execute("UPDATE staging_leads SET contact_discovery_status='WAITING_LINKEDIN' WHERE id=?", (lead['id'],))
                    continue
                key = contact_key(data)
                groups.setdefault(key, []).append((lead, data))
        groups = self._attach_cache(path, 'fullenrich', 'contact', groups)
        if not groups or not self.fullenrich.api_key or self.fe_limit <= 0:
            return
        with database(path) as c:
            if c.execute("SELECT 1 FROM enrichment_jobs WHERE provider='fullenrich' AND (status IN ('UNKNOWN','FAILED','SUBMITTED','NEEDS_REVIEW','RETRY_SCHEDULED') OR error='COST_EXCEEDED_RESERVATION')").fetchone():
                return
            capacity = (batch_size if self.workflow else
                        min(int((self.fe_limit - self._used(c, 'fullenrich')) // self.email_ceiling), self._remaining_items(c, 'fullenrich')))
        if capacity <= 0:
            return
        try:
            balance = self.fullenrich.credits()
        except ProviderError:
            return
        count = min(batch_size, 100, capacity, int(balance // self.email_ceiling))
        if count <= 0:
            return
        groups = dict(list(groups.items())[:count])
        data = [{**members[0][1], 'custom': {'request_key': key}} for key, members in groups.items()]
        reservation = self._reserve(path, 'fullenrich', 'contact', {'data': data}, groups,
                                    len(groups) * self.email_ceiling, self.fe_limit)
        if not reservation:
            return
        job_id, payload = reservation
        try:
            remote_id = self.fullenrich.submit(payload)
        except ProviderError as exc:
            self._failure(path, job_id, exc)
            return
        with database(path) as c:
            c.execute("UPDATE enrichment_jobs SET status='SUBMITTED',remote_id=? WHERE id=?", (remote_id, job_id))
            c.execute("UPDATE enrichment_cache SET status='SUBMITTED' WHERE job_id=?", (job_id,))
            c.execute("UPDATE staging_leads SET contact_discovery_status='SUBMITTED' WHERE id IN (SELECT lead_id FROM enrichment_targets WHERE job_id=?)", (job_id,))

    def audit_staging_db(self, db_path, batch_size=10):
        if not isinstance(batch_size, int) or batch_size < 1 or batch_size > 100:
            raise ValueError('batch_size must be between 1 and 100')
        if self.dry_run:
            # Even migrations/locks must not modify the production DB in preview.
            with sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True) as c:
                count = c.execute('SELECT count(*) FROM staging_leads').fetchone()[0]
                has_limits = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='enrichment_limits'").fetchone()
                limits = dict(c.execute('SELECT provider,total_items FROM enrichment_limits')) if has_limits else {}
            return {'dry_run': True, 'leads': count, 'batch_limit': batch_size,
                    'fullenrich_credit_limit': self.fe_limit, 'harvest_request_limit': 0,
                    'no2bounce_address_limit': self.nb_limit, 'pilot': self.pilot or bool(limits),
                    'pilot_limits': limits, 'phase': self.phase or ('verify' if self.pilot or limits else 'all'),
                    'no2bounce_contract_ready': self.no2bounce.contract_ready,
                    'linkedin_provider': 'linkedin_mcp',
                    'workflow_order': ['verify_existing_email','linkedin_confirmed_url','fullenrich_work_email'],
                    'fullenrich_identity_method': 'CONFIRMED_LINKEDIN_URL_ONLY',
                    'linkedin_mcp_tasks': pending_tasks(db_path, batch_size) if self.phase in ('profiles','linkedin-search','linkedin','enrich','all') else []}
        if self.pilot:
            with sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True) as c:
                if c.execute('SELECT count(*) FROM staging_leads').fetchone()[0] > 3:
                    raise ValueError('Pilot requires an isolated database of at most three contacts')
        lock_path = str(Path(db_path).resolve()) + '.enrichment.lock'
        with open(lock_path, 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'status': 'BUSY'}
            self._init(db_path)
            with database(db_path) as c:
                is_pilot = bool(c.execute('SELECT 1 FROM enrichment_limits').fetchone())
            if self.workflow and is_pilot:
                raise ValueError('Workflow cannot consume a pilot database with legacy caps')
            self._retry_rejected(db_path)
            phase = self.phase or ('verify' if is_pilot else 'all')
            if is_pilot and phase == 'all':
                raise ValueError('Pilot requires a separate verify or enrich phase')
            if phase in ('all', 'verify'):
                self._collect_no2bounce(db_path)
            if phase in ('all', 'enrich'):
                self._collect_fullenrich(db_path)
            self._decode_received(db_path)
            self._apply(db_path)
            if phase in ('all', 'verify'):
                self._verify_emails(db_path, batch_size)
                self._apply(db_path)
            if phase in ('all', 'enrich'):
                # MCP acquisition is a separate phase. This handoff
                # is produced before submission; the URL proof gate below holds
                # contacts until their extraction has been imported.
                self._submit_contacts(db_path, batch_size)
                self._apply(db_path)
            with database(db_path) as c:
                return {'phase': phase, 'pilot_limits': dict(c.execute('SELECT provider,total_items FROM enrichment_limits')),
                        'email_states': dict(c.execute('SELECT email_check_status,count(*) FROM staging_leads GROUP BY 1')),
                        'no2bounce_addresses_committed': self._used(c, 'no2bounce'),
                        'no2bounce_contract_ready': self.no2bounce.contract_ready,
                        'no2bounce_configured': bool(self.no2bounce.api_key and self.nb_limit > 0),
                        'contact_states': dict(c.execute('SELECT contact_discovery_status,count(*) FROM staging_leads GROUP BY 1')),
                        'profile_states': dict(c.execute('SELECT profile_status,count(*) FROM staging_leads GROUP BY 1')),
                        'linkedin_search_states': dict(c.execute('SELECT linkedin_search_status,count(*) FROM staging_leads GROUP BY 1')),
                        'fullenrich_credits_committed': self._used(c, 'fullenrich'),
                        'harvest_requests_committed': self._used(c, 'harvest'),
                        'jobs_requiring_attention': [dict(r) for r in c.execute(
                            "SELECT id,provider,status,error FROM enrichment_jobs WHERE status IN ('UNKNOWN','FAILED','NEEDS_REVIEW') OR error IS NOT NULL")],
                        'fullenrich_configured': bool(self.fullenrich.api_key and self.fe_limit > 0),
                        'harvest_configured': False, 'linkedin_provider': 'linkedin_mcp',
                        'linkedin_acquisition_status': 'MCP_WORKER_REQUIRED',
                        'linkedin_mcp_tasks': pending_tasks(db_path, batch_size) if phase in ('all','enrich','profiles','linkedin-search','linkedin') else [],
                        'workflow_order': ['verify_existing_email','linkedin_confirmed_url','fullenrich_work_email'],
                        'fullenrich_identity_method': 'CONFIRMED_LINKEDIN_URL_ONLY'}

    def attach_fullenrich_job(self, db_path, local_id, remote_id):
        """Operator reconciliation: bind a dashboard ID, never resubmit paid work.

        Only apply completed results after all echoed request keys match the
        persisted submission. This also resolves a crash before remote_id save.
        """
        if self.dry_run:
            raise ValueError('Reconciliation modifies local state; dry-run cannot attach IDs')
        with open(str(Path(db_path).resolve()) + '.enrichment.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with database(db_path) as c:
                job = c.execute("SELECT * FROM enrichment_jobs WHERE id=? AND provider='fullenrich' AND status='UNKNOWN'", (local_id,)).fetchone()
                if not job:
                    raise ValueError('Expected an UNKNOWN local FullEnrich job')
                keys = {r[0] for r in c.execute('SELECT request_key FROM enrichment_cache WHERE job_id=?', (local_id,))}
            result = self.fullenrich.result(remote_id)
            if result is None:
                return {'status': 'WAITING_FOR_COMPLETE_RESULT'}
            rows = result.get('data')
            returned = [(r.get('custom') or {}).get('request_key') for r in rows] if isinstance(rows, list) and all(isinstance(r, dict) for r in rows) else []
            if (result.get('id') != remote_id or result.get('status') != 'FINISHED'
                    or len(returned) != len(keys) or set(returned) != keys):
                raise ValueError('Remote result does not match this saved batch')
            with database(db_path) as c:
                c.execute("UPDATE enrichment_jobs SET remote_id=?,response=?,status='RECEIVED',error=NULL WHERE id=?",
                          (remote_id, encoded(result), local_id))
            self._decode_received(db_path)
            self._apply(db_path)
            return {'status': 'RECONCILED', 'job_id': local_id, 'remote_id': remote_id}

    def attach_no2bounce_job(self,db_path,local_id,remote_id):
        if self.dry_run:
            raise ValueError('Reconciliation modifies local state')
        with open(str(Path(db_path).resolve())+'.enrichment.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with database(db_path) as c:
                job=c.execute("SELECT * FROM enrichment_jobs WHERE id=? AND provider='no2bounce' AND status='UNKNOWN'",(local_id,)).fetchone()
                if not job:
                    raise ValueError('Expected an UNKNOWN No2Bounce job')
                expected=set(json.loads(job['payload'])['emailList'])
            result=self.no2bounce.result(str(remote_id))
            if result is None:
                return {'status':'WAITING_FOR_COMPLETE_RESULT'}
            verdicts=result.get('verdicts')
            if not isinstance(verdicts,dict) or set(verdicts)!=expected:
                raise ValueError('No2Bounce result does not match saved addresses')
            with database(db_path) as c:
                c.execute("UPDATE enrichment_jobs SET remote_id=?,response=?,status='RECEIVED',error=NULL WHERE id=?",(str(remote_id),encoded(result),local_id))
            self._decode_received(db_path)
            self._apply(db_path)
            return {'status':'RECONCILED','job_id':local_id,'remote_id':str(remote_id)}
