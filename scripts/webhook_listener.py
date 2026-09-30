#!/usr/bin/env python3
"""
Récepteur Webhook Resend & Gestion des Rebonds — Contexte Lab

Fonctionnalités :
- Écoute les webhooks signés ou standards émis par Resend (Svix signature support).
- Traite les événements :
  * `email.bounced` (Rebonds durs / soft) :
    1. Purge l'email dans Twenty CRM via TwentyClient.purge_bounced_email(person_id).
    2. Ajoute l'email à la liste d'exclusion locale (suppression_list.json).
    3. Renvoie un résultat structuré {"status": "PURGED", "person_id": ..., "email": ...}.
  * `email.complained` (Plaintes pour spam) :
    1. Purge l'email dans Twenty CRM.
    2. Ajoute l'email à la liste d'exclusion locale (suppression_list.json).
    3. Renvoie {"status": "PURGED", "person_id": ..., "email": ...}.
  * `email.delivered` :
    1. Met à jour le statut du contact dans Twenty CRM (campaignStatus = EMAIL_DELIVERED).
    2. Renvoie {"status": "DELIVERED", "person_id": ..., "email": ...}.
- Serveur HTTP léger standard zéro-dépendance (http.server).
- CLI complet pour démarrage en production ou démon.
"""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import http.server
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Set, Union

import requests

from scripts.models import CampaignStatusEnum
from scripts.twenty_client import TwentyClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("webhook_listener")

DEFAULT_SUPPRESSION_FILE = "suppression_list.json"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080


def extract_person_id(data: Dict[str, Any]) -> Optional[str]:
    """
    Extract person_id from Resend event data payload.
    Supports tag lists, tag dictionaries, direct fields, and metadata.
    """
    if not isinstance(data, dict):
        return None

    if "person_id" in data and data["person_id"]:
        return str(data["person_id"])

    tags = data.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict):
                name = tag.get("name") or tag.get("key")
                if name == "person_id" and tag.get("value"):
                    return str(tag.get("value"))
    elif isinstance(tags, dict):
        if "person_id" in tags and tags["person_id"]:
            return str(tags["person_id"])

    metadata = data.get("metadata")
    if isinstance(metadata, dict) and "person_id" in metadata:
        return str(metadata["person_id"])

    return None


def extract_email(data: Dict[str, Any]) -> Optional[str]:
    """
    Extract recipient email address from Resend event data payload.
    """
    if not isinstance(data, dict):
        return None

    to_field = data.get("to")
    if isinstance(to_field, list) and len(to_field) > 0:
        return str(to_field[0]).strip().lower()
    elif isinstance(to_field, str) and to_field.strip():
        return to_field.strip().lower()

    if "email" in data and data["email"]:
        return str(data["email"]).strip().lower()
    if "recipient" in data and data["recipient"]:
        return str(data["recipient"]).strip().lower()

    return None


def extract_reason(data: Dict[str, Any], event_type: str) -> str:
    """
    Extract bounce or complaint reason from Resend event data payload.
    """
    if not isinstance(data, dict):
        return event_type

    if event_type == "email.bounced":
        bounce_info = data.get("bounce")
        if isinstance(bounce_info, dict):
            return str(
                bounce_info.get("message")
                or bounce_info.get("type")
                or "mailbox unavailable"
            )
        return "bounced"

    if event_type == "email.complained":
        complaint_info = data.get("complaint")
        if isinstance(complaint_info, dict):
            return str(complaint_info.get("message") or "spam complaint")
        return "complained"

    return event_type


def load_suppression_list(
    filepath: Union[str, Path] = DEFAULT_SUPPRESSION_FILE,
) -> List[Dict[str, Any]]:
    """
    Load suppressed email entries from the local suppression JSON file.
    """
    path = Path(filepath)
    if not path.is_file():
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                entries = []
                for item in data:
                    if isinstance(item, str):
                        entries.append({"email": item.strip().lower()})
                    elif isinstance(item, dict) and "email" in item:
                        entries.append(item)
                return entries
            return []
    except Exception as exc:
        logger.error("Failed to read suppression list %s: %s", path, exc)
        return []


def is_suppressed(
    email: Optional[str],
    filepath: Union[str, Path] = DEFAULT_SUPPRESSION_FILE,
) -> bool:
    """
    Check if an email is present in the suppression list (case-insensitive).
    """
    if not email:
        return False
    clean_email = email.strip().lower()
    entries = load_suppression_list(filepath)
    return any(
        str(entry.get("email", "")).strip().lower() == clean_email
        for entry in entries
    )


def add_to_suppression_list(
    email: str,
    person_id: Optional[str] = None,
    reason: Optional[str] = None,
    event_type: Optional[str] = None,
    filepath: Union[str, Path] = DEFAULT_SUPPRESSION_FILE,
) -> bool:
    """
    Atomically add an email to the suppression list if not already present.
    Returns True if added, False if already present.
    """
    if not email or not email.strip():
        return False

    clean_email = email.strip().lower()
    path = Path(filepath)

    entries = load_suppression_list(path)
    if any(
        str(entry.get("email", "")).strip().lower() == clean_email
        for entry in entries
    ):
        logger.debug("Email %s is already in suppression list.", clean_email)
        return False

    new_entry = {
        "email": clean_email,
        "person_id": person_id,
        "reason": reason or event_type or "unspecified",
        "event_type": event_type or "unknown",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    entries.append(new_entry)

    # Atomic write via temporary file
    parent_dir = path.parent
    parent_dir.mkdir(parents=True, exist_ok=True)
    temp_path = parent_dir / f"{path.name}.tmp.{os.getpid()}"

    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        temp_path.replace(path)
        logger.info(
            "Added %s to suppression list %s (reason: %s)",
            clean_email,
            path,
            new_entry["reason"],
        )
        return True
    except Exception as exc:
        logger.error(
            "Failed to save suppression list %s: %s",
            path,
            exc,
        )
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        return False


def verify_resend_signature(
    payload: bytes,
    headers: Dict[str, str],
    secret: Optional[str] = None,
) -> bool:
    """
    Verify Svix / Resend webhook signature using HMAC-SHA256.
    If secret is not set, verification is skipped and returns True.
    """
    if not secret:
        return True

    clean_headers = {k.lower(): v for k, v in headers.items()}
    msg_id = clean_headers.get("svix-id")
    timestamp = clean_headers.get("svix-timestamp")
    signature_header = clean_headers.get("svix-signature")

    if not msg_id or not timestamp or not signature_header:
        logger.warning("Missing required Svix signature headers.")
        return False

    # Extract raw secret key
    secret_clean = secret.strip()
    if secret_clean.startswith("whsec_"):
        secret_clean = secret_clean[6:]

    try:
        secret_bytes = base64.b64decode(secret_clean)
    except Exception as exc:
        logger.error("Invalid base64 webhook secret: %s", exc)
        return False

    to_sign = f"{msg_id}.{timestamp}.".encode("utf-8") + payload
    computed_digest = hmac.new(secret_bytes, to_sign, hashlib.sha256).digest()
    computed_sig = base64.b64encode(computed_digest).decode("utf-8")

    # svix-signature header format: "v1,signature v1,signature2" or "v1,signature"
    signatures = [
        sig.strip()
        for item in signature_header.split(" ")
        for sig in item.split(",")
    ]
    for sig in signatures:
        if sig.startswith("v1="):
            sig_val = sig[3:]
        elif sig.startswith("v1,"):
            sig_val = sig[3:]
        else:
            sig_val = sig

        if hmac.compare_digest(sig_val, computed_sig):
            return True

    logger.warning("Resend webhook signature verification failed.")
    return False


def handle_resend_event(
    event: Union[str, bytes, Dict[str, Any]],
    twenty_client: Optional[TwentyClient] = None,
    suppression_file: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """
    Processes incoming Resend webhook events:
    - email.bounced: purges email in Twenty CRM, adds to suppression list.
    - email.complained: purges email in Twenty CRM, adds to suppression list.
    - email.delivered: updates status in Twenty CRM.
    - Other events: logs and returns status IGNORED.
    """
    if isinstance(event, (str, bytes)):
        try:
            event = json.loads(event)
        except Exception as exc:
            logger.error("Failed to parse event JSON: %s", exc)
            return {"status": "ERROR", "error": f"Invalid JSON: {exc}"}

    if not isinstance(event, dict):
        return {"status": "ERROR", "error": "Event must be a JSON object"}

    event_type = event.get("type", "")
    data = event.get("data", {})
    if not isinstance(data, dict):
        data = {}

    email = extract_email(data)
    person_id = extract_person_id(data)
    reason = extract_reason(data, event_type)
    supp_path = suppression_file or os.environ.get(
        "SUPPRESSION_LIST_PATH",
        DEFAULT_SUPPRESSION_FILE,
    )

    if twenty_client is None:
        twenty_url = os.environ.get("TWENTY_API_URL", "http://localhost:3005")
        twenty_key = os.environ.get("TWENTY_API_KEY", None)
        twenty_client = TwentyClient(base_url=twenty_url, api_key=twenty_key)

    logger.info(
        "Processing Resend event: type='%s', email='%s', person_id='%s', reason='%s'",
        event_type,
        email,
        person_id,
        reason,
    )

    if event_type in ("email.bounced", "email.complained"):
        crm_purged = False
        if person_id:
            try:
                twenty_client.purge_bounced_email(person_id)
                crm_purged = True
                logger.info(
                    "Successfully purged bounced email for person_id='%s' in Twenty CRM",
                    person_id,
                )
            except Exception as exc:
                logger.warning(
                    "Could not purge person_id='%s' in Twenty CRM (service unreachable or error): %s",
                    person_id,
                    exc,
                )

        if email:
            add_to_suppression_list(
                email=email,
                person_id=person_id,
                reason=reason,
                event_type=event_type,
                filepath=supp_path,
            )

        return {
            "status": "PURGED",
            "person_id": person_id,
            "email": email or "",
            "event_type": event_type,
            "reason": reason,
            "twenty_crm_purged": crm_purged,
        }

    elif event_type == "email.delivered":
        crm_updated = False
        if person_id:
            try:
                twenty_client.update_campaign_status(
                    person_id,
                    CampaignStatusEnum.EMAIL_DELIVERED.value,
                )
                crm_updated = True
                logger.info(
                    "Updated delivery status for person_id='%s' in Twenty CRM",
                    person_id,
                )
            except Exception as exc:
                logger.warning(
                    "Could not update delivery status for person_id='%s' in Twenty CRM: %s",
                    person_id,
                    exc,
                )

        return {
            "status": "DELIVERED",
            "person_id": person_id,
            "email": email or "",
            "event_type": event_type,
            "twenty_crm_updated": crm_updated,
        }

    else:
        logger.info("Ignored unhandled Resend event type: %s", event_type)
        return {
            "status": "IGNORED",
            "person_id": person_id,
            "email": email or "",
            "event_type": event_type,
        }


class ResendWebhookHTTPHandler(http.server.BaseHTTPRequestHandler):
    """
    HTTP Request Handler for receiving Resend webhooks.
    """

    twenty_client: Optional[TwentyClient] = None
    suppression_file: Optional[Union[str, Path]] = None
    webhook_secret: Optional[str] = None

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _send_json_response(self, status_code: int, data: Dict[str, Any]) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/health", "/", "/ping"):
            self._send_json_response(200, {"status": "healthy", "service": "webhook_listener"})
        else:
            self._send_json_response(404, {"error": "Not Found"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        if path not in ("/webhook", "/", "/resend"):
            self._send_json_response(404, {"error": "Not Found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self._send_json_response(400, {"error": "Empty request body"})
                return

            body = self.rfile.read(content_length)

            # Signature verification if secret configured
            if self.webhook_secret:
                header_dict = {k: v for k, v in self.headers.items()}
                if not verify_resend_signature(body, header_dict, secret=self.webhook_secret):
                    self._send_json_response(401, {"error": "Invalid webhook signature"})
                    return

            try:
                event = json.loads(body.decode("utf-8"))
            except Exception as exc:
                self._send_json_response(400, {"error": f"Invalid JSON payload: {exc}"})
                return

            action = handle_resend_event(
                event,
                twenty_client=self.twenty_client,
                suppression_file=self.suppression_file,
            )

            self._send_json_response(200, {"status": "ok", "action": action})

        except Exception as exc:
            logger.exception("Error processing webhook POST request: %s", exc)
            self._send_json_response(500, {"error": f"Internal server error: {exc}"})


def create_webhook_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    twenty_client: Optional[TwentyClient] = None,
    suppression_file: Optional[Union[str, Path]] = None,
    webhook_secret: Optional[str] = None,
) -> http.server.HTTPServer:
    """
    Factory creating a configured HTTPServer instance.
    """
    handler_class = type(
        "ConfiguredResendWebhookHandler",
        (ResendWebhookHTTPHandler,),
        {
            "twenty_client": twenty_client,
            "suppression_file": suppression_file,
            "webhook_secret": webhook_secret,
        },
    )
    server = http.server.HTTPServer((host, port), handler_class)
    return server


def run_webhook_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    twenty_client: Optional[TwentyClient] = None,
    suppression_file: Optional[Union[str, Path]] = None,
    webhook_secret: Optional[str] = None,
) -> None:
    """
    Runs the webhook server until interrupted.
    """
    server = create_webhook_server(
        host=host,
        port=port,
        twenty_client=twenty_client,
        suppression_file=suppression_file,
        webhook_secret=webhook_secret,
    )
    actual_port = server.server_address[1]
    logger.info("Resend Webhook Server listening on http://%s:%s/webhook", host, actual_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Webhook server stopped by user.")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resend Webhook Receiver & Bounce Manager — Contexte Lab",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=DEFAULT_HOST,
        help=f"Host address to bind to (default: {DEFAULT_HOST}).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port number to listen on (default: {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--suppression-file",
        type=str,
        default=DEFAULT_SUPPRESSION_FILE,
        help=f"Path to suppression JSON file (default: {DEFAULT_SUPPRESSION_FILE}).",
    )
    parser.add_argument(
        "--secret",
        type=str,
        default=os.environ.get("RESEND_WEBHOOK_SECRET"),
        help="Svix / Resend Webhook signing secret (defaults to RESEND_WEBHOOK_SECRET env var).",
    )
    parser.add_argument(
        "--twenty-url",
        type=str,
        default=os.environ.get("TWENTY_API_URL", "http://localhost:3005"),
        help="Twenty CRM API base URL (default: http://localhost:3005).",
    )
    parser.add_argument(
        "--twenty-key",
        type=str,
        default=os.environ.get("TWENTY_API_KEY"),
        help="Twenty CRM API Key (defaults to TWENTY_API_KEY env var).",
    )

    args = parser.parse_args()

    twenty_client = TwentyClient(base_url=args.twenty_url, api_key=args.twenty_key)
    run_webhook_server(
        host=args.host,
        port=args.port,
        twenty_client=twenty_client,
        suppression_file=args.suppression_file,
        webhook_secret=args.secret,
    )


if __name__ == "__main__":
    main()
