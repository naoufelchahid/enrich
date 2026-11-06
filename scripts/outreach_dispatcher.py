#!/usr/bin/env python3
"""
Orchestrateur Autonome d'Outreach & Runner Journalier — Contexte Lab

Coordonne :
1. Revue de la boîte de réception et détection de rebonds / réponses via MCP Mail.
2. Coupe-circuit Zero-Bounce garantissant l'arrêt immédiat si rebond détecté.
3. Sélection et synchronisation des meilleurs leads dans Twenty CRM.
4. File d'attente journalière lissée (pacing / jitter) et envoi via MCP Mail bridge.
"""

import argparse
import json
import logging
import os
import random
import sys
import time
import sqlite3
import tempfile
import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.circuit_breaker import CircuitBreaker
from scripts.inbound_monitor import process_inbound_batch
from scripts.thunderbird_client import ThunderbirdClient
from scripts.models import NormalizedContact
from scripts.staging_db import init_staging_db, mark_lead_synced
from scripts.outreach_journal import flush_journal
from scripts.twenty_client import TwentyClient
from scripts.direct_enrichment import DirectEnrichment, verified
from scripts.email_hygiene import inspect_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("outreach_dispatcher")


class OutreachDispatcher:
    """Coordinate direct enrichment, proven email validation, CRM and outbound queue."""

    def __init__(
        self,
        db_path: str = "archives/essais/ancien-sas/staging_leads.sqlite",
        circuit_breaker_file: str = ".circuit_breaker_state.json",
        queue_file: str = "archives/essais/ancien-sas/.daily_send_queue.json",
        twenty_base_url: str = "http://localhost:3005",
        dry_run: bool = False,
        circuit_breaker: Optional[CircuitBreaker] = None,
        twenty_client: Optional[TwentyClient] = None,
        mcp_mail: Optional[ThunderbirdClient] = None,
        enrichment_client=None,
    ):
        self.db_path = db_path
        self.circuit_breaker_file = circuit_breaker_file
        self.queue_file = queue_file
        resolved_url = twenty_base_url
        if twenty_base_url == "http://localhost:3005" and os.environ.get("TWENTY_BASE_URL"):
            resolved_url = os.environ.get("TWENTY_BASE_URL", twenty_base_url)
        self.twenty_base_url = resolved_url
        self.dry_run = dry_run

        self.circuit_breaker = circuit_breaker or CircuitBreaker(state_file=circuit_breaker_file)
        self.twenty_client = twenty_client or TwentyClient(base_url=self.twenty_base_url)
        self.mcp_mail = mcp_mail or ThunderbirdClient(dry_run=dry_run)
        self.enrichment_client = enrichment_client or DirectEnrichment(dry_run=dry_run)

    def _load_queue(self) -> List[Dict[str, Any]]:
        """Load queued leads from disk."""
        if not os.path.exists(self.queue_file):
            return []
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                raise ValueError('Queue must be an array')
        except (ValueError, OSError) as e:
            raise RuntimeError('Queue unreadable; refusing to replace it') from e

    def _save_queue(self, queue: List[Dict[str, Any]]) -> None:
        """Persist queued leads to disk."""
        parent_dir = os.path.dirname(os.path.abspath(self.queue_file))
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=parent_dir, delete=False) as f:
            temporary = f.name
            json.dump(queue, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, self.queue_file)

    @contextmanager
    def _queue_lock(self):
        from scripts.backend_guard import require_legacy
        require_legacy(self.db_path)
        lock_file = str(Path(self.db_path).resolve()) + '.outreach.lock'
        with open(lock_file, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _ready_leads(self, limit):
        with sqlite3.connect(Path(self.db_path).resolve().as_uri() + '?mode=ro', uri=True) as c:
            c.row_factory = sqlite3.Row
            columns = {r[1] for r in c.execute('PRAGMA table_info(staging_leads)')}
            if 'email_validation_source' not in columns:
                return []  # Preview of a legacy DB does not migrate or trust old flags.
            rows = c.execute("SELECT * FROM staging_leads s WHERE outreach_status='PENDING' AND NOT EXISTS (SELECT 1 FROM email_suppressions x WHERE x.email=lower(trim(s.raw_email))) ORDER BY priority_score DESC,id").fetchall()
        return [dict(r) for r in rows if verified(dict(r))][:limit]

    def get_pending_queue(self) -> List[Dict[str, Any]]:
        """Return the current active queue."""
        return self._load_queue()

    def _suppress(self, email, reason):
        from scripts.backend_guard import require_legacy
        require_legacy(self.db_path,lane='campaigns')
        email = email.strip().lower()
        with sqlite3.connect(self.db_path) as c:
            c.execute('INSERT OR IGNORE INTO email_suppressions(email,reason) VALUES(?,?)', (email, reason))
            c.execute("UPDATE staging_leads SET outreach_status='SUPPRESSED' WHERE lower(trim(raw_email))=? OR lower(trim(clean_email))=?", (email, email))
        flush_journal(self.db_path)

    def _review_live(self):
        try:
            stats = process_inbound_batch(self.mcp_mail, self.twenty_client, self.circuit_breaker,
                                          on_suppression=self._suppress)
            for email in stats.get('suppressed_emails', []):
                self._suppress(email, 'UNSUBSCRIBE')
            for email in stats.get('bounced_emails', []):
                self._suppress(email, 'BOUNCE')
            return stats
        except Exception:
            self.circuit_breaker.trip('INBOUND_REVIEW_FAILED')
            return {'status': 'INBOUND_REVIEW_FAILED', 'blocked': True}

    def prepare_daily_batch(self, limit: int = 25, daily_send_target: int = 5) -> Dict[str, Any]:
        """Prepare only validated candidates. Preview has no persistent side effects."""
        if limit < 1 or daily_send_target < 1:
            raise ValueError('Batch and daily target must be positive')
        if self.dry_run:
            preview = self.enrichment_client.audit_staging_db(self.db_path, batch_size=min(limit, daily_send_target, 100))
            return {'dry_run': True, 'synced': 0, 'queued': 0,
                    'eligible': len(self._ready_leads(limit)), 'enrichment': preview}
        with self._queue_lock():
            init_staging_db(self.db_path).close()
            self._review_live()
            if self.circuit_breaker.is_tripped():
                return {'status': 'CIRCUIT_BREAKER_ACTIVE', 'synced': 0, 'queued': 0}
            stats = self.enrichment_client.audit_staging_db(self.db_path, batch_size=min(limit, daily_send_target, 100))
            queue = self._load_queue()
            queued_ids = {r['id'] for r in queue}
            queued_emails = {(r.get('clean_email') or '').lower() for r in queue}
            with sqlite3.connect(self.db_path) as c:
                queued_emails.update((r[0] or '').lower() for r in c.execute(
                    "SELECT clean_email FROM staging_leads WHERE outreach_status IN ('SENT','SENDING','SEND_UNKNOWN','SUPPRESSED')"))
            synced, added = 0, 0
            for lead in self._ready_leads(limit):
                if lead['id'] in queued_ids or lead['clean_email'].lower() in queued_emails or len(queue) >= daily_send_target:
                    continue
                try:
                    c = NormalizedContact(
                        company_name=lead.get("company_name") or "Entreprise",
                        first_name=lead.get("first_name"),
                        last_name=lead.get("last_name"),
                        job_title=lead.get("job_title"),
                        phone_mobile=lead.get("phone_mobile"),
                        email=lead.get("clean_email") or lead.get("raw_email"),
                        raw_email=lead.get("raw_email"),
                        linkedin_url=lead.get("linkedin_url"),
                        recent_posts=lead.get("recent_posts"),
                        source_file=lead.get("source_file") or "staging",
                    )
                    crm_id = lead.get('crm_person_id')
                    if not crm_id:
                        crm_id = self.twenty_client.upsert_person(c)
                        if not isinstance(crm_id, str) or not crm_id.strip():
                            raise ValueError('CRM did not return an ID')
                        mark_lead_synced(self.db_path, lead['id'], crm_id)
                        synced += 1
                    lead["crm_person_id"] = crm_id
                except Exception as e:
                    logger.error('CRM sync failed for lead %s (%s)', lead['id'], type(e).__name__)
                    continue
                queue.append(lead)
                queued_ids.add(lead['id'])
                queued_emails.add(lead['clean_email'].lower())
                added += 1
            self._save_queue(queue)
            return {'synced': synced, 'queued': len(queue), 'added': added, 'enrichment': stats}

    def send_next_candidate(self, jitter_min=0, jitter_max=0):
        return {'status':'LEGACY_SEND_DISABLED_USE_APPROVED_WORKFLOW'}

    def _send_locked(self, jitter_min=0, jitter_max=0):
        return {'status':'LEGACY_SEND_DISABLED_USE_APPROVED_WORKFLOW'}

    def review_inbox_and_sync(self) -> Dict[str, Any]:
        """18:00 task: audit inbound responses, log reputation status, and close daily loop."""
        if self.dry_run:
            return {'dry_run': True, 'total': 0, 'bounces': 0}
        init_staging_db(self.db_path).close()
        stats = self._review_live()
        logger.info("Daily Inbound Review Summary: %s", stats)
        return stats


def main():
    parser = argparse.ArgumentParser(description="Outreach Autonomous Dispatcher")
    parser.add_argument(
        "--task",
        required=True,
        choices=["prepare_daily_batch", "send_next_candidate", "review_inbox_and_sync", "enrich", "linkedin_enqueue", "linkedin_worker", "linkedin_status", "linkedin_cycle"],
        help="Scheduled task to execute",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute without sending live emails or modifying live CRM",
    )
    parser.add_argument('--pilot', action='store_true', help='Persist cumulative 3-contact caps; disable Harvest')
    parser.add_argument('--phase', choices=['verify', 'enrich', 'profiles', 'linkedin-search', 'linkedin', 'all'])
    parser.add_argument('--harvest-request-limit', type=int, help='Deprecated compatibility argument; Harvest is retired and no requests are made')
    parser.add_argument('--db-path', default='archives/essais/ancien-sas/staging_leads.sqlite')
    parser.add_argument('--batch-size', type=int, default=10)
    parser.add_argument('--linkedin-daily-limit', type=int, default=0)
    parser.add_argument('--linkedin-output-dir', default='bdd/linkedin-worker')
    parser.add_argument('--resume-job-id', help='Local UNKNOWN FullEnrich job ID to reconcile')
    parser.add_argument('--fullenrich-id', help='Existing completed enrichment ID from the FullEnrich dashboard')
    args = parser.parse_args()

    if args.task.startswith('linkedin_'):
        from scripts.linkedin_queue import enqueue, run_one, connection
        if args.task == 'linkedin_cycle':
            if args.linkedin_daily_limit == 0 and not args.dry_run:
                result = {'status': 'DISABLED', 'launches': 0}
            else:
                queued = enqueue(args.db_path, limit=min(args.batch_size, 3), dry_run=args.dry_run)
                result = {'queue': queued, 'worker': run_one(args.db_path, daily_limit=args.linkedin_daily_limit,
                    output_dir=args.linkedin_output_dir, dry_run=args.dry_run)}
        elif args.task == 'linkedin_enqueue':
            result = enqueue(args.db_path, limit=min(args.batch_size, 3), dry_run=args.dry_run)
        elif args.task == 'linkedin_worker':
            result = run_one(args.db_path, daily_limit=args.linkedin_daily_limit,
                             output_dir=args.linkedin_output_dir, dry_run=args.dry_run)
        else:
            with connection(args.db_path) as c:
                exists = c.execute("SELECT 1 FROM sqlite_master WHERE name='linkedin_browser_queue'").fetchone()
                result = {'states': dict(c.execute('SELECT status,count(*) FROM linkedin_browser_queue GROUP BY status')) if exists else {}}
        print(json.dumps(result, indent=2))
        return

    if args.task == 'enrich':
        client = DirectEnrichment(dry_run=args.dry_run, pilot=args.pilot, phase=args.phase,
                                  harvest_daily_requests=args.harvest_request_limit)
        if args.resume_job_id or args.fullenrich_id:
            if not (args.resume_job_id and args.fullenrich_id):
                parser.error('Both reconciliation IDs are required')
            result = client.attach_fullenrich_job(args.db_path, args.resume_job_id, args.fullenrich_id)
        else:
            result = client.audit_staging_db(args.db_path, args.batch_size)
        print(json.dumps(result, indent=2))
        return
    dispatcher = OutreachDispatcher(dry_run=args.dry_run, db_path=args.db_path)

    if args.task == "prepare_daily_batch":
        res = dispatcher.prepare_daily_batch()
    elif args.task == "send_next_candidate":
        res = dispatcher.send_next_candidate(jitter_min=5, jitter_max=25)
    elif args.task == "review_inbox_and_sync":
        res = dispatcher.review_inbox_and_sync()
    else:
        res = {"error": f"Unknown task: {args.task}"}

    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
