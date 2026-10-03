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
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.circuit_breaker import CircuitBreaker
from scripts.inbound_monitor import process_inbound_batch
from scripts.mcp_mail_server import MCPMailServer
from scripts.models import NormalizedContact
from scripts.prompt_engine import compose_tailored_email
from scripts.staging_db import get_top_priority_leads, mark_lead_synced
from scripts.twenty_client import TwentyClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("outreach_dispatcher")


class OutreachDispatcher:
    """Main orchestrator coordinating staging leads, Apify enrichment, Twenty CRM, and MCP mail sending."""

    def __init__(
        self,
        db_path: str = "staging_leads.sqlite",
        circuit_breaker_file: str = ".circuit_breaker_state.json",
        queue_file: str = ".daily_send_queue.json",
        twenty_base_url: str = "http://localhost:3005",
        dry_run: bool = False,
        circuit_breaker: Optional[CircuitBreaker] = None,
        twenty_client: Optional[TwentyClient] = None,
        mcp_mail: Optional[MCPMailServer] = None,
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
        self.mcp_mail = mcp_mail or MCPMailServer(dry_run=dry_run)

    def _load_queue(self) -> List[Dict[str, Any]]:
        """Load queued leads from disk."""
        if not os.path.exists(self.queue_file):
            return []
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                return []
        except Exception as e:
            logger.warning("Could not load queue from %s: %s", self.queue_file, e)
            return []

    def _save_queue(self, queue: List[Dict[str, Any]]) -> None:
        """Persist queued leads to disk."""
        parent_dir = os.path.dirname(os.path.abspath(self.queue_file))
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(self.queue_file, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=2)

    def get_pending_queue(self) -> List[Dict[str, Any]]:
        """Return the current active queue."""
        return self._load_queue()

    def prepare_daily_batch(self, limit: int = 25, daily_send_target: int = 5) -> Dict[str, Any]:
        """09:00 task: check inbox, select top priority leads, sync to Twenty, and queue daily sends."""
        # 1. Review inbox for overnight bounces or responses
        process_inbound_batch(self.mcp_mail, self.twenty_client, self.circuit_breaker)

        # 2. Select top priority leads from staging db
        leads = get_top_priority_leads(self.db_path, limit=limit)
        queued_candidates: List[Dict[str, Any]] = []

        for lead in leads:
            if not self.dry_run:
                try:
                    c = NormalizedContact(
                        company_name=lead.get("company_name") or "Entreprise",
                        first_name=lead.get("first_name"),
                        last_name=lead.get("last_name"),
                        job_title=lead.get("job_title"),
                        phone_mobile=lead.get("phone_mobile"),
                        email=lead.get("clean_email") or lead.get("raw_email"),
                        raw_email=lead.get("raw_email"),
                        source_file=lead.get("source_file") or "staging",
                    )
                    crm_id = self.twenty_client.upsert_person(c)
                    mark_lead_synced(self.db_path, lead["id"], crm_id)
                    lead["crm_person_id"] = crm_id
                except Exception as e:
                    logger.error("Failed to sync lead %s to Twenty CRM: %s", lead["id"], e)
            else:
                crm_id = f"mock-person-{lead['id']}"
                lead["crm_person_id"] = crm_id
                mark_lead_synced(self.db_path, lead["id"], crm_id)

            queued_candidates.append(lead)

        active_queue = queued_candidates[:daily_send_target]
        self._save_queue(active_queue)

        return {"synced": len(queued_candidates), "queued": len(active_queue)}

    def send_next_candidate(self, jitter_min: int = 0, jitter_max: int = 0) -> Dict[str, Any]:
        """Hourly send task: checks circuit breaker, drafts customized email, and sends via MCP Mail."""
        if self.circuit_breaker.is_tripped():
            logger.warning("Send cancelled: Circuit Breaker is ACTIVE.")
            return {"status": "CIRCUIT_BREAKER_ACTIVE"}

        # Quick inbound pre-flight check for fresh bounce
        process_inbound_batch(self.mcp_mail, self.twenty_client, self.circuit_breaker)
        if self.circuit_breaker.is_tripped():
            return {"status": "CIRCUIT_BREAKER_ACTIVE"}

        queue = self._load_queue()
        if not queue:
            logger.info("No candidates pending in today's send queue.")
            return {"status": "QUEUE_EMPTY"}

        candidate = queue.pop(0)

        # Apply random jitter if specified
        if jitter_max > jitter_min:
            sleep_sec = random.randint(jitter_min, jitter_max)
            logger.info("Applying random jitter of %ds before dispatching email.", sleep_sec)
            time.sleep(sleep_sec)

        # Draft tailored email
        email_content = compose_tailored_email(candidate)
        to_email = candidate.get("clean_email") or candidate.get("raw_email")

        # Send via MCP mail bridge
        self.mcp_mail.send_email(
            to=to_email,
            subject=email_content["subject"],
            body_text=email_content["body"],
        )

        # Update status in Twenty CRM
        if candidate.get("crm_person_id") and not self.dry_run:
            try:
                self.twenty_client.update_campaign_status(candidate["crm_person_id"], "EMAIL_SENT")
            except Exception as e:
                logger.error("Failed to update campaign status in Twenty CRM: %s", e)

        self._save_queue(queue)
        return {
            "status": "SENT",
            "to": to_email,
            "subject": email_content["subject"],
            "remaining_queue": len(queue),
        }

    def review_inbox_and_sync(self) -> Dict[str, Any]:
        """18:00 task: audit inbound responses, log reputation status, and close daily loop."""
        stats = process_inbound_batch(self.mcp_mail, self.twenty_client, self.circuit_breaker)
        logger.info("Daily Inbound Review Summary: %s", stats)
        return stats


def main():
    parser = argparse.ArgumentParser(description="Outreach Autonomous Dispatcher")
    parser.add_argument(
        "--task",
        required=True,
        choices=["prepare_daily_batch", "send_next_candidate", "review_inbox_and_sync"],
        help="Scheduled task to execute",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute without sending live emails or modifying live CRM",
    )
    args = parser.parse_args()

    dispatcher = OutreachDispatcher(dry_run=args.dry_run)

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
