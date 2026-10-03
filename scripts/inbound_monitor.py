"""
Module Surveillance Inbound & Triage des Réponses — Contexte Lab

Surveille les messages entrants de la boîte de réception (IMAP),
classe automatiquement chaque message (BOUNCE, POSITIVE, UNSUBSCRIBE, NEUTRAL),
déclenche le coupe-circuit (Circuit Breaker) dès le premier rebond (NDR),
et synchronise les opportunités commerciales avec Twenty CRM.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from scripts.circuit_breaker import CircuitBreaker
from scripts.mcp_mail_server import MCPMailServer
from scripts.twenty_client import TwentyClient

logger = logging.getLogger(__name__)

BOUNCE_INDICATORS = [
    "mailer-daemon",
    "postmaster",
    "delivery status notification",
    "undelivered mail",
    "550 ",
    "user unknown",
    "mailbox unavailable",
    "failure notice",
    "delivery failure",
    "permanent error",
    "returned to sender",
]

POSITIVE_INDICATORS = [
    "intéressé",
    "interesse",
    "rendez-vous",
    "rdv",
    "call",
    "devis",
    "tarif",
    "disponible",
    "échange",
    "echange",
    "présentation",
    "revenir vers vous",
    "meeting",
]

UNSUBSCRIBE_INDICATORS = [
    "stop",
    "désinscrire",
    "desinscrire",
    "désabonner",
    "desabonner",
    "ne plus me contacter",
    "pas intéressé",
    "pas interesse",
    "supprimer",
    "opt-out",
    "unsubscribe",
]


def _matches_indicator(text: str, indicator: str) -> bool:
    """Check if indicator matches text, respecting word boundaries for short terms."""
    if indicator in ("stop", "rdv"):
        return bool(re.search(r"\b" + re.escape(indicator) + r"\b", text))
    return indicator in text


def classify_inbound_message(sender: str, subject: str, body: str) -> str:
    """Classify incoming email into BOUNCE, POSITIVE, UNSUBSCRIBE, or NEUTRAL.

    Order of priority:
    1. BOUNCE (NDR failure notices)
    2. UNSUBSCRIBE (Opt-out requests, preventing false positive 'pas intéressé')
    3. POSITIVE (Interest, meeting requests, quote requests)
    4. NEUTRAL (General inquiries, spam, newsletters)
    """
    combined = f"{sender.lower()} {subject.lower()} {body.lower()}"

    if any(_matches_indicator(combined, ind) for ind in BOUNCE_INDICATORS):
        return "BOUNCE"
    if any(_matches_indicator(combined, ind) for ind in UNSUBSCRIBE_INDICATORS):
        return "UNSUBSCRIBE"
    if any(_matches_indicator(combined, ind) for ind in POSITIVE_INDICATORS):
        return "POSITIVE"
    return "NEUTRAL"


def process_inbound_batch(
    mcp_server: MCPMailServer,
    twenty_client: TwentyClient,
    circuit_breaker: CircuitBreaker,
    folder: str = "INBOX",
) -> Dict[str, int]:
    """Scan inbox, classify messages, trigger circuit breaker on bounce, and update Twenty CRM."""
    messages = mcp_server.list_inbox_messages(folder=folder)
    summary = {
        "total": len(messages),
        "bounces": 0,
        "positives": 0,
        "unsubscribes": 0,
        "neutral": 0,
    }

    for msg_meta in messages:
        mid = str(msg_meta.get("id"))
        details = mcp_server.get_message_details(mid, folder=folder) or {}
        sender = str(details.get("from") or msg_meta.get("from") or "")
        subject = str(details.get("subject") or msg_meta.get("subject") or "")
        body = str(details.get("body") or "")

        typ = classify_inbound_message(sender, subject, body)

        if typ == "BOUNCE":
            summary["bounces"] += 1
            bounce_target = sender
            # Try to extract the failed recipient email from body or sender
            emails_in_body = re.findall(
                r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", body
            )
            for em in emails_in_body:
                if not any(
                    ign in em.lower()
                    for ign in ["mailer-daemon", "postmaster"]
                ):
                    bounce_target = em
                    break

            circuit_breaker.trip(
                reason=f"NDR received: {subject}", bounce_email=bounce_target
            )
            mcp_server.move_message(mid, folder, "Bounces_Archives")

        elif typ == "POSITIVE":
            summary["positives"] += 1
            opp_name = f"Opportunité - {sender}"
            twenty_client.create_opportunity(
                name=opp_name,
                stage="Qualified / RDV",
                person_id=f"contact-{mid}",
            )
            mcp_server.move_message(mid, folder, "Opportunités")

        elif typ == "UNSUBSCRIBE":
            summary["unsubscribes"] += 1
            mcp_server.move_message(mid, folder, "Désinscrits")

        else:
            summary["neutral"] += 1

    return summary
