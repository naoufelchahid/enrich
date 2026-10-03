#!/usr/bin/env python3
"""
Serveur MCP Mail Local (Bridge IMAP / SMTP Thunderbird) — Contexte Lab

Expose un serveur MCP (JSON-RPC 2.0 sur stdio) permettant d'interagir
avec le client mail Thunderbird ou tout serveur SMTP/IMAP standard :
- Envoi d'emails via SMTP avec en-têtes réalistes (User-Agent Thunderbird, Message-ID).
- Archivage automatique dans le dossier Sent (IMAP append).
- Consultation des messages et détails (IMAP fetch / search).
- Déplacement des messages (dossier rebonds / réponses).
- Support complet du mode dry-run et mocks pour tests unitaires.
"""

import email
import imaplib
import json
import logging
import os
import smtplib
import sys
import time
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mcp_mail_server")

DEFAULT_FROM_DISPLAY = "Naoufel Chahid"
DEFAULT_USERNAME = "chahid.naoufel@sales.contextelab.ma"
DEFAULT_REPLY_TO = "n.chahid@contextelab.ma"
USER_AGENT_THUNDERBIRD = "Mozilla Thunderbird / Contexte Lab Bridge"

MCP_TOOLS = [
    {
        "name": "send_email",
        "description": "Send an email via SMTP (or dry-run) and archive to Sent folder in Thunderbird.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body_text": {"type": "string", "description": "Plain text email body"},
                "reply_to": {
                    "type": "string",
                    "description": "Reply-To email address",
                    "default": DEFAULT_REPLY_TO,
                },
            },
            "required": ["to", "subject", "body_text"],
        },
    },
    {
        "name": "list_inbox_messages",
        "description": "List email messages from an IMAP folder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "unread_only": {
                    "type": "boolean",
                    "description": "Only list unread messages",
                    "default": False,
                },
                "folder": {
                    "type": "string",
                    "description": "IMAP folder name",
                    "default": "INBOX",
                },
            },
        },
    },
    {
        "name": "get_message_details",
        "description": "Get detailed email content and headers by message ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "IMAP message ID",
                },
                "folder": {
                    "type": "string",
                    "description": "IMAP folder name",
                    "default": "INBOX",
                },
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "move_message",
        "description": "Move an email from one IMAP folder to another.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "IMAP message ID",
                },
                "source_folder": {
                    "type": "string",
                    "description": "Source IMAP folder",
                },
                "target_folder": {
                    "type": "string",
                    "description": "Target IMAP folder",
                },
            },
            "required": ["message_id", "source_folder", "target_folder"],
        },
    },
]


class MCPMailServer:
    """
    Bridge IMAP / SMTP pour l'agent IA, exposant une interface MCP.
    """

    def __init__(
        self,
        smtp_host: str = "localhost",
        smtp_port: int = 587,
        imap_host: str = "localhost",
        imap_port: int = 993,
        username: str = DEFAULT_USERNAME,
        password: str = "",
        from_display: str = DEFAULT_FROM_DISPLAY,
        dry_run: bool = False,
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.username = username
        self.password = password
        self.from_display = from_display
        self.dry_run = dry_run

        self._mock_inbox: List[Dict[str, Any]] = []
        self._sent_emails: List[Dict[str, Any]] = []

    def send_email(
        self,
        to: str,
        subject: str,
        body_text: str,
        reply_to: Optional[str] = DEFAULT_REPLY_TO,
    ) -> Dict[str, Any]:
        """
        Envoie un email via SMTP avec les en-têtes Thunderbird.
        Si dry_run=True, simule l'envoi et renvoie les en-têtes générés.
        """
        msg = MIMEText(body_text, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = formataddr((self.from_display, self.username))
        msg["To"] = to
        msg["Date"] = formatdate(localtime=True)
        domain = self.username.split("@")[-1] if "@" in self.username else None
        msg["Message-ID"] = make_msgid(domain=domain)
        msg["User-Agent"] = USER_AGENT_THUNDERBIRD

        if reply_to:
            msg["Reply-To"] = reply_to

        headers = {k: v for k, v in msg.items()}

        if self.dry_run:
            logger.info(f"[DRY RUN] Envoi d'email à {to}: '{subject}'")
            result = {
                "success": True,
                "to": to,
                "subject": subject,
                "message_id": msg["Message-ID"],
                "headers": headers,
                "dry_run": True,
            }
            self._sent_emails.append(result)
            return result

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.send_message(msg)

            archived = self.archive_sent_email(msg.as_bytes())

            return {
                "success": True,
                "to": to,
                "subject": subject,
                "message_id": msg["Message-ID"],
                "headers": headers,
                "archived": archived,
                "dry_run": False,
            }
        except Exception as e:
            logger.error(f"Échec de l'envoi d'email à {to}: {e}")
            return {
                "success": False,
                "to": to,
                "subject": subject,
                "error": str(e),
                "dry_run": False,
            }

    def archive_sent_email(self, raw_msg_bytes: bytes, folder: str = "Sent") -> bool:
        """
        Archive un message envoyé dans le dossier Sent via IMAP append.
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Archivage du message dans {folder} ({len(raw_msg_bytes)} octets)")
            return True

        try:
            with imaplib.IMAP4_SSL(self.imap_host, self.imap_port) as imap:
                imap.login(self.username, self.password)
                date_time = imaplib.Time2Internaldate(time.time())
                status, _ = imap.append(folder, r"(\Seen)", date_time, raw_msg_bytes)
                return status == "OK"
        except Exception as e:
            logger.error(f"Échec d'archivage dans {folder}: {e}")
            return False

    def list_inbox_messages(
        self, unread_only: bool = False, folder: str = "INBOX"
    ) -> List[Dict[str, Any]]:
        """
        Liste les messages d'un dossier IMAP.
        """
        if self.dry_run:
            results = []
            for item in self._mock_inbox:
                if item.get("folder", "INBOX") != folder:
                    continue
                if unread_only and not item.get("unread", False):
                    continue
                results.append(dict(item))
            return results

        try:
            with imaplib.IMAP4_SSL(self.imap_host, self.imap_port) as imap:
                imap.login(self.username, self.password)
                imap.select(folder)
                search_crit = "UNSEEN" if unread_only else "ALL"
                status, data = imap.search(None, search_crit)
                if status != "OK" or not data or not data[0]:
                    return []
                msg_ids = data[0].split()
                results = []
                for mid in msg_ids:
                    mid_str = mid.decode()
                    res, msg_data = imap.fetch(
                        mid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)])"
                    )
                    if res == "OK" and msg_data and msg_data[0]:
                        raw_header = msg_data[0][1]
                        parsed = email.message_from_bytes(raw_header)
                        results.append({
                            "id": mid_str,
                            "from": parsed.get("From", ""),
                            "subject": parsed.get("Subject", ""),
                            "date": parsed.get("Date", ""),
                            "message_id": parsed.get("Message-ID", ""),
                            "folder": folder,
                        })
                return results
        except Exception as e:
            logger.error(f"Échec de lecture des messages dans {folder}: {e}")
            return []

    def get_message_details(
        self, message_id: str, folder: str = "INBOX"
    ) -> Dict[str, Any]:
        """
        Récupère les détails et le corps d'un email par son ID.
        """
        if self.dry_run:
            for item in self._mock_inbox:
                if str(item.get("id")) == str(message_id):
                    return dict(item)
            return {}

        try:
            with imaplib.IMAP4_SSL(self.imap_host, self.imap_port) as imap:
                imap.login(self.username, self.password)
                imap.select(folder)
                res, msg_data = imap.fetch(str(message_id), "(RFC822)")
                if res != "OK" or not msg_data or not msg_data[0]:
                    return {}
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                body_text = ""
                body_html = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        content_disposition = str(part.get("Content-Disposition", ""))
                        if "attachment" in content_disposition:
                            continue
                        payload = part.get_payload(decode=True)
                        if not payload:
                            continue
                        charset = part.get_content_charset() or "utf-8"
                        text = payload.decode(charset, errors="replace")
                        if content_type == "text/plain":
                            body_text += text
                        elif content_type == "text/html":
                            body_html += text
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        charset = msg.get_content_charset() or "utf-8"
                        body_text = payload.decode(charset, errors="replace")

                body = body_text if body_text else body_html
                headers = {k: v for k, v in msg.items()}
                return {
                    "id": str(message_id),
                    "from": msg.get("From", ""),
                    "to": msg.get("To", ""),
                    "subject": msg.get("Subject", ""),
                    "date": msg.get("Date", ""),
                    "message_id": msg.get("Message-ID", ""),
                    "body": body,
                    "folder": folder,
                    "headers": headers,
                }
        except Exception as e:
            logger.error(f"Échec de récupération du message {message_id}: {e}")
            return {}

    def move_message(
        self, message_id: str, source_folder: str, target_folder: str
    ) -> bool:
        """
        Déplace un email d'un dossier vers un autre.
        """
        if self.dry_run:
            for item in self._mock_inbox:
                if str(item.get("id")) == str(message_id) and item.get("folder", "INBOX") == source_folder:
                    item["folder"] = target_folder
                    return True
            for item in self._mock_inbox:
                if str(item.get("id")) == str(message_id):
                    item["folder"] = target_folder
                    return True
            return False

        try:
            with imaplib.IMAP4_SSL(self.imap_host, self.imap_port) as imap:
                imap.login(self.username, self.password)
                imap.select(source_folder)
                res, _ = imap.copy(str(message_id), target_folder)
                if res != "OK":
                    return False
                imap.store(str(message_id), "+FLAGS", r"(\Deleted)")
                imap.expunge()
                return True
        except Exception as e:
            logger.error(
                f"Échec de déplacement du message {message_id} de {source_folder} vers {target_folder}: {e}"
            )
            return False

    def handle_jsonrpc(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite une requête MCP JSON-RPC 2.0.
        """
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": MCP_TOOLS,
                },
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})

            if tool_name == "send_email":
                try:
                    res = self.send_email(
                        to=arguments.get("to", ""),
                        subject=arguments.get("subject", ""),
                        body_text=arguments.get("body_text", ""),
                        reply_to=arguments.get("reply_to", DEFAULT_REPLY_TO),
                    )
                    return {"jsonrpc": "2.0", "id": req_id, "result": res}
                except Exception as e:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": str(e)},
                    }

            elif tool_name == "list_inbox_messages":
                try:
                    res = self.list_inbox_messages(
                        unread_only=arguments.get("unread_only", False),
                        folder=arguments.get("folder", "INBOX"),
                    )
                    return {"jsonrpc": "2.0", "id": req_id, "result": res}
                except Exception as e:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": str(e)},
                    }

            elif tool_name == "get_message_details":
                try:
                    res = self.get_message_details(
                        message_id=arguments.get("message_id", ""),
                        folder=arguments.get("folder", "INBOX"),
                    )
                    return {"jsonrpc": "2.0", "id": req_id, "result": res}
                except Exception as e:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": str(e)},
                    }

            elif tool_name == "move_message":
                try:
                    moved = self.move_message(
                        message_id=arguments.get("message_id", ""),
                        source_folder=arguments.get("source_folder", "INBOX"),
                        target_folder=arguments.get("target_folder", ""),
                    )
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"success": moved},
                    }
                except Exception as e:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": str(e)},
                    }
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"},
                }

        elif method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "mcp-mail-server",
                        "version": "1.0.0",
                    },
                },
            }

        elif method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                },
            }


def run_stdio_server(server: Optional[MCPMailServer] = None) -> None:
    """
    Boucle stdio MCP JSON-RPC.
    """
    if server is None:
        smtp_host = os.getenv("SMTP_HOST", "localhost")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        imap_host = os.getenv("IMAP_HOST", "localhost")
        imap_port = int(os.getenv("IMAP_PORT", "993"))
        username = os.getenv("MAIL_USERNAME", DEFAULT_USERNAME)
        password = os.getenv("MAIL_PASSWORD", "")
        from_display = os.getenv("MAIL_FROM_DISPLAY", DEFAULT_FROM_DISPLAY)
        dry_run = os.getenv("MAIL_DRY_RUN", "false").lower() in ("true", "1", "yes")

        server = MCPMailServer(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            imap_host=imap_host,
            imap_port=imap_port,
            username=username,
            password=password,
            from_display=from_display,
            dry_run=dry_run,
        )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = server.handle_jsonrpc(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError as e:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio_server()
