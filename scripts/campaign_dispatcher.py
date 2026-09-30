#!/usr/bin/env python3
"""
Orchestrateur de Warm-up & Dispatcher de Campagne Mailing — Contexte Lab

Fonctionnalités :
- Consomme les contacts normalisés et validés (NormalizedContact).
- Répartit équitablement les 5 variantes de templates (A, B, C, D, E) sur le lot journalier.
- Respecte le protocole de warm-up :
  * Plafond journalier (par défaut 100 emails / jour).
  * Lissage horaire (pacing par défaut : 1 email toutes les 4 minutes / 240 s).
  * Expéditeur strict : "Naoufel Chahid <chahid.naoufel@sales.contextelab.ma>".
  * Adresse de réponse : "n.chahid@contextelab.ma".
- Circuit breaker : arrêt d'urgence immédiat si le taux d'erreur/rebond dépasse 2%.
- Support CLI complet avec simulation (--dry-run) et envoi direct (--send).
"""

import argparse
import logging
import os
import sys
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

import requests

from scripts.antibounce_verifier import process_antibounce_pass1
from scripts.etl_normalize import parse_all_sources
from scripts.models import CampaignStatusEnum, NormalizedContact
from scripts.templates import AVAILABLE_VARIANTS, render_email_template
from scripts.twenty_client import TwentyClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("campaign_dispatcher")

DEFAULT_FROM_EMAIL = "Naoufel Chahid <chahid.naoufel@sales.contextelab.ma>"
DEFAULT_REPLY_TO = "n.chahid@contextelab.ma"
DEFAULT_BATCH_SIZE = 100
DEFAULT_DELAY_SECONDS = 240.0  # 4 minutes between emails
DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 0.02  # 2% max error rate
DEFAULT_MIN_ATTEMPTS = 10
RESEND_API_URL = "https://api.resend.com/emails"


class CircuitBreakerError(Exception):
    """Raised when the circuit breaker is triggered due to high failure rate."""
    pass


class CampaignDispatcher:
    """
    Orchestrates cold email delivery via Resend API with warm-up batching,
    variant balancing, pacing, and circuit breaking.
    """

    def __init__(
        self,
        resend_api_key: Optional[str] = None,
        dry_run: bool = False,
        from_email: str = DEFAULT_FROM_EMAIL,
        reply_to: str = DEFAULT_REPLY_TO,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        circuit_breaker_threshold: float = DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
        min_attempts: int = DEFAULT_MIN_ATTEMPTS,
        sleeper: Optional[Callable[[float], None]] = None,
        twenty_client: Optional[TwentyClient] = None,
    ):
        self.dry_run = dry_run
        self.from_email = from_email
        self.reply_to = reply_to
        self.delay_seconds = delay_seconds
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.min_attempts = min_attempts
        self.sleeper = sleeper or time.sleep
        self.twenty_client = twenty_client

        api_key = resend_api_key or os.environ.get("RESEND_API_KEY", "")
        if not self.dry_run and not api_key:
            raise ValueError(
                "Resend API key is required when dry_run=False. "
                "Provide it via argument or RESEND_API_KEY env var."
            )
        self.resend_api_key = api_key

    def build_email_payload(
        self,
        contact: NormalizedContact,
        variant: str,
    ) -> Dict[str, Any]:
        """
        Builds the Resend API payload for a specific contact and template variant.
        """
        if not contact.email:
            raise ValueError("Contact does not have an active email address.")

        subject, html_body = render_email_template(variant, contact)

        payload: Dict[str, Any] = {
            "from": self.from_email,
            "to": [contact.email],
            "reply_to": self.reply_to,
            "subject": subject,
            "html": html_body,
            "tags": [
                {"name": "variant", "value": variant},
            ],
        }
        return payload

    def prepare_batch(
        self,
        contacts: List[NormalizedContact],
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> List[Dict[str, Any]]:
        """
        Filters deliverable contacts, slices the requested batch size,
        and assigns template variants evenly in rotation (A, B, C, D, E).
        """
        eligible = [c for c in contacts if c.email and c.email.strip()]
        selected = eligible[:batch_size]

        batch: List[Dict[str, Any]] = []
        num_variants = len(AVAILABLE_VARIANTS)

        for i, contact in enumerate(selected):
            variant = AVAILABLE_VARIANTS[i % num_variants]
            payload = self.build_email_payload(contact, variant)
            item = {
                "contact": contact,
                "variant": variant,
                **payload,
            }
            batch.append(item)

        logger.info(
            "Prepared batch of %d emails (Variants: %s)",
            len(batch),
            {v: sum(1 for b in batch if b["variant"] == v) for v in AVAILABLE_VARIANTS},
        )
        return batch

    def send_email(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sends a single email payload via Resend API, or simulates it in dry-run mode.
        """
        if self.dry_run:
            simulated_id = f"dry-run-{uuid.uuid4().hex[:12]}"
            logger.info(
                "[DRY-RUN] Simulating email to %s | Variant: %s | Subject: '%s'",
                payload.get("to"),
                (payload.get("tags") or [{}])[0].get("value", "N/A"),
                payload.get("subject"),
            )
            return {
                "id": simulated_id,
                "status": "simulated",
                "to": payload.get("to"),
            }

        headers = {
            "Authorization": f"Bearer {self.resend_api_key}",
            "Content-Type": "application/json",
        }
        api_payload = {
            "from": payload["from"],
            "to": payload["to"],
            "reply_to": payload["reply_to"],
            "subject": payload["subject"],
            "html": payload["html"],
            "tags": payload.get("tags", []),
        }

        res = requests.post(
            RESEND_API_URL,
            json=api_payload,
            headers=headers,
            timeout=15,
        )
        if res.status_code >= 400:
            error_msg = f"Resend API error {res.status_code}: {res.text}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        data = res.json() if res.content else {"id": "unknown"}
        logger.info(
            "Sent email to %s (Resend ID: %s)",
            payload.get("to"),
            data.get("id"),
        )
        return data

    def dispatch_batch(
        self,
        batch: List[Dict[str, Any]],
        raise_on_circuit_breaker: bool = False,
    ) -> Dict[str, Any]:
        """
        Dispatches all emails in the batch with delay pacing and circuit breaker monitoring.
        """
        attempted = 0
        sent = 0
        failed = 0
        errors: List[Dict[str, Any]] = []
        results: List[Dict[str, Any]] = []
        circuit_breaker_triggered = False

        total_batch = len(batch)
        logger.info(
            "Starting dispatch of %d emails (Mode: %s, Delay: %.1fs)",
            total_batch,
            "DRY-RUN" if self.dry_run else "LIVE SEND",
            self.delay_seconds,
        )

        for i, item in enumerate(batch):
            # Pacing: delay between consecutive emails
            if i > 0 and self.delay_seconds > 0:
                logger.debug("Pacing sleep for %.1f seconds...", self.delay_seconds)
                self.sleeper(self.delay_seconds)

            attempted += 1
            recipient = item.get("to", ["unknown"])[0]
            try:
                response = self.send_email(item)
                sent += 1
                results.append({
                    "status": "success",
                    "to": item.get("to"),
                    "variant": item.get("variant"),
                    "response": response,
                })
            except Exception as e:
                failed += 1
                errors.append({"to": recipient, "error": str(e)})
                results.append({
                    "status": "failed",
                    "to": item.get("to"),
                    "variant": item.get("variant"),
                    "error": str(e),
                })
                logger.warning(
                    "Failure sending email %d/%d to %s: %s",
                    attempted,
                    total_batch,
                    recipient,
                    e,
                )

            # Circuit breaker check
            failure_rate = failed / attempted
            if attempted >= self.min_attempts and failure_rate > self.circuit_breaker_threshold:
                circuit_breaker_triggered = True
                msg = (
                    f"CIRCUIT BREAKER TRIGGERED: Failure rate of {failure_rate:.1%} "
                    f"({failed}/{attempted}) exceeds threshold of "
                    f"{self.circuit_breaker_threshold:.1%}. Aborting dispatch immediately!"
                )
                logger.critical(msg)
                if raise_on_circuit_breaker:
                    raise CircuitBreakerError(msg)
                break

        summary = {
            "total": total_batch,
            "attempted": attempted,
            "sent": sent,
            "failed": failed,
            "dry_run": self.dry_run,
            "circuit_breaker_triggered": circuit_breaker_triggered,
            "errors": errors,
            "results": results,
        }
        logger.info(
            "Dispatch summary: %d/%d attempted, %d sent, %d failed (Circuit breaker: %s)",
            attempted,
            total_batch,
            sent,
            failed,
            "TRIGGERED" if circuit_breaker_triggered else "OK",
        )
        return summary


def load_eligible_contacts(data_dir: str = "bdd") -> List[NormalizedContact]:
    """
    Loads raw contacts, normalizes them, and applies anti-bounce hygiene
    to extract candidates eligible for cold email delivery.
    """
    logger.info("Reading raw data from %s...", data_dir)
    raw_contacts = parse_all_sources(data_dir)
    logger.info("Parsed %d contacts across sources. Applying anti-bounce hygiene...", len(raw_contacts))
    processed = process_antibounce_pass1(raw_contacts)
    # Filter for deliverable contacts with dirigente status prioritized
    eligible = [
        c for c in processed
        if c.email and c.is_dirigeant
    ]
    if not eligible:
        # Fallback to any deliverable contact
        eligible = [c for c in processed if c.email]

    logger.info("Identified %d eligible deliverable contacts.", len(eligible))
    return eligible


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Warm-up Campaign Dispatcher for Contexte Lab cold mailing.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simulate dispatch without sending real emails via Resend (Safe mode).",
    )
    group.add_argument(
        "--send",
        action="store_true",
        default=False,
        help="Trigger LIVE email delivery via Resend API.",
    )

    parser.add_argument(
        "--batch-size",
        "--batch",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        dest="batch_size",
        help="Number of emails in the daily batch (default: 100 for warm-up).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="Delay in seconds between emails (default: 240.0s = 4 minutes).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
        help="Circuit breaker failure rate threshold (default: 0.02 = 2%%).",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Resend API Key (defaults to RESEND_API_KEY environment variable).",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="bdd",
        help="Directory containing source datasets (default: bdd).",
    )
    parser.add_argument(
        "--from-email",
        type=str,
        default=DEFAULT_FROM_EMAIL,
        help=f"Sender identity (default: '{DEFAULT_FROM_EMAIL}').",
    )
    parser.add_argument(
        "--reply-to",
        type=str,
        default=DEFAULT_REPLY_TO,
        help=f"Reply-To address (default: '{DEFAULT_REPLY_TO}').",
    )

    args = parser.parse_args()

    # Safety: If neither is specified, default to dry-run
    dry_run = True
    if args.send:
        dry_run = False
        print("⚠️  LIVE SEND MODE ACTIVATED: Emails will be dispatched via Resend!")
    else:
        print("🛡️  SIMULATION MODE (DRY-RUN): No real emails will be dispatched.")

    try:
        dispatcher = CampaignDispatcher(
            resend_api_key=args.api_key,
            dry_run=dry_run,
            from_email=args.from_email,
            reply_to=args.reply_to,
            delay_seconds=args.delay if not dry_run else 0.0,
            circuit_breaker_threshold=args.threshold,
        )

        contacts = load_eligible_contacts(data_dir=args.data_dir)
        batch = dispatcher.prepare_batch(contacts, batch_size=args.batch_size)

        if not batch:
            print("❌ No deliverable contacts found to dispatch.")
            sys.exit(1)

        summary = dispatcher.dispatch_batch(batch)
        print("\n================ DISPATCH REPORT ================")
        print(f"Total in Batch:     {summary['total']}")
        print(f"Attempted:          {summary['attempted']}")
        print(f"Sent successfully:  {summary['sent']}")
        print(f"Failed:             {summary['failed']}")
        print(f"Circuit Breaker:    {'⚠️ TRIGGERED' if summary['circuit_breaker_triggered'] else '✅ OK'}")
        print("=================================================")

    except Exception as e:
        logger.exception("Dispatcher execution failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
