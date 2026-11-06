import json
import html
import re
from pathlib import Path
import urllib.request
import urllib.error
import logging
import os
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from typing import Any, Dict, List, Optional
from scripts.email_hygiene import is_personal_email

logger = logging.getLogger(__name__)

class ThunderbirdResponseError(RuntimeError):
    """Keep a provider receipt for reconciliation without connection credentials."""
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = receipt

class ThunderbirdClient:
    """
    Client HTTP pour interagir avec le serveur MCP natif de l'extension Thunderbird.
    Remplace l'ancienne implémentation IMAP/SMTP factice.
    """
    def __init__(
        self,
        connection_file: str = "/home/naoufel/Downloads/thunderbird.tmp/thunderbird-mcp/connection.json",
        dry_run: bool = False
    ):
        self.connection_file = connection_file
        self.dry_run = dry_run
        self.port = None
        self.token = None
        self._sent_emails = []
        self._mock_inbox = []
        self._load_connection()

    def _load_connection(self):
        if self.dry_run:
            return
        try:
            with open(self.connection_file, "r") as f:
                data = json.load(f)
                self.port = data.get("port")
                self.token = data.get("token")
        except Exception as e:
            logger.error(f"Erreur lors du chargement de {self.connection_file}: {e}")

    def _assert_html_signature_available(self):
        """Fail closed if this account no longer points to the campaign signature."""
        signature = Path(__file__).resolve().parents[1] / 'outputs/signature-contextelab/signature.html'
        profile_root = Path.home() / 'snap/thunderbird/current/.config/thunderbird'
        if not signature.is_file() or not signature.read_text().strip():
            raise RuntimeError('THUNDERBIRD_HTML_SIGNATURE_MISSING')
        configured = False
        for prefs in profile_root.glob('*/prefs.js'):
            settings = prefs.read_text(errors='replace')
            attached = re.search(r'user_pref\("mail\.identity\.id1\.attach_signature", true\);', settings)
            path = re.search(r'user_pref\("mail\.identity\.id1\.sig_file", "([^"]+)"\);', settings)
            if attached and path and Path(path.group(1)).resolve() == signature.resolve():
                configured = True
                break
        if not configured:
            raise RuntimeError('THUNDERBIRD_HTML_SIGNATURE_NOT_CONFIGURED')

    def _call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any], *, rpc_method='tools/call') -> Any:
        if self.dry_run:
            logger.info(f"[DRY RUN] Call MCP tool {tool_name} with args {arguments}")
            return {}

        if not self.port or not self.token:
            self._load_connection()
            if not self.port or not self.token:
                raise RuntimeError("Thunderbird MCP non connecté")

        url = f"http://127.0.0.1:{self.port}/"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": rpc_method,
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }
        if rpc_method=='tools/list':
            payload['params']={}

        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.token}")

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8")
                res = json.loads(body)
                if "error" in res:
                    raise ThunderbirdResponseError('Thunderbird RPC error',res['error'])
                if not isinstance(res.get('result'), dict) or res['result'].get('isError'):
                    raise ThunderbirdResponseError('Invalid Thunderbird response',res.get('result'))
                content = res.get("result", {}).get("content", [])
                if content and content[0].get("type") == "text":
                    try:
                        return json.loads(content[0]["text"])
                    except:
                        return content[0]["text"]
                return res.get("result", {})
        except ThunderbirdResponseError:
            raise
        except Exception as e:
            raise RuntimeError('Thunderbird request failed') from None

    def send_email(self, to: str, subject: str, body_text: str, reply_to: str = None,
                   *, include_signature: bool = False) -> Dict[str, Any]:
        if any(is_personal_email(address) for _, address in getaddresses([to])):
            raise ValueError('PERSONAL_EMAIL_FORBIDDEN')
        if os.getenv('WORKFLOW_STOP_AT_DRAFT') == '1':
            raise RuntimeError('WORKFLOW_STOP_AT_DRAFT')
        if include_signature and not self.dry_run:
            self._assert_html_signature_available()
        body = html.escape(body_text).replace('\n', '<br>') if include_signature else body_text
        args = {
            "to": to,
            "subject": subject,
            "body": body,
            "includeSignature": include_signature,
            "isHtml": include_signature,
            "skipReview": True # Envoi direct sans fenêtre de confirmation !
        }
        if self.dry_run:
            self._sent_emails.append(args)
        result = self._call_mcp_tool("sendMail", args)
        if not self.dry_run and (not result or (isinstance(result, dict) and result.get('success') is False)):
            raise ThunderbirdResponseError('Thunderbird did not confirm mail submission',result)
        return {"success": True, "result": result, "dry_run": self.dry_run}

    def supports_exact_reply(self):
        # The inspected extension appends a generated quotation to native replies.
        # Until its API exposes an exact-body mode, do not silently alter the
        # operator-approved body or send a new unthreaded message instead.
        if self.dry_run:
            return True
        result=self._call_mcp_tool('',{},rpc_method='tools/list')
        for tool in result.get('tools',[]):
            if tool.get('name')=='replyToMessage':
                properties=(tool.get('inputSchema') or {}).get('properties',{})
                return {'subject','includeQuoted'}<=set(properties)
        return False

    def send_approved_email(self, to, subject, body_text, reply_to=None, *, include_signature=False):
        if any(is_personal_email(address) for _, address in getaddresses([to])):
            raise ValueError('PERSONAL_EMAIL_FORBIDDEN')
        if os.getenv('WORKFLOW_STOP_AT_DRAFT') == '1':
            raise RuntimeError('WORKFLOW_STOP_AT_DRAFT')
        if reply_to and not self.supports_exact_reply():
            raise RuntimeError('THUNDERBIRD_EXACT_REPLY_CAPABILITY_REQUIRED')
        if reply_to and not self.dry_run:
            reference=json.loads(reply_to)
            if not reference.get('id') or not reference.get('folder_path'):
                raise ValueError('REPLY_REFERENCE_REQUIRED')
            receipt=self._call_mcp_tool('replyToMessage',{'messageId':reference['id'],'folderPath':reference['folder_path'],
                'to':to,'subject':subject,'body':body_text,'isHtml':False,'skipReview':True,'includeQuoted':False})
            result={'success':True,'result':receipt,'dry_run':False}
        else:
            result=self.send_email(to=to,subject=subject,body_text=body_text,reply_to=reply_to,
                                   include_signature=include_signature)
        if not self.dry_run:
            receipt=result.get('result')
            if (not isinstance(receipt,dict) or receipt.get('success') is not True
                    or not str(receipt.get('message') or '').startswith(('Message sent','Reply sent'))):
                raise ThunderbirdResponseError('THUNDERBIRD_SEND_NOT_CONFIRMED',receipt)
        return result

    def workflow_folders(self, kind):
        folders = self._call_mcp_tool('listFolders',{})
        if not isinstance(folders,list):
            raise RuntimeError('THUNDERBIRD_FOLDER_CONTRACT_MISMATCH')
        def walk(rows):
            for row in rows:
                if not isinstance(row,dict):
                    continue
                yield row
                yield from walk(row.get('subFolders') or row.get('children') or [])
        selected=[]
        for row in walk(folders):
            flags = row.get('type') or row.get('specialUse') or row.get('flags') or []
            if isinstance(flags,str):
                flags=[flags]
            path = row.get('folderPath') or row.get('uri') or row.get('path')
            if path and (kind.lower() in [str(x).lower() for x in flags]
                         or (row.get('name') or '').lower() in ({'inbox','courrier entrant'} if kind=='inbox' else {'sent','sent mail','envoyés','sent messages'})):
                selected.append(path)
        if not selected:
            raise RuntimeError('THUNDERBIRD_FOLDER_REQUIRED')
        return selected

    def workflow_search(self, folder_path, *, start_date=None, query=''):
        rows,offset=[],0
        while True:
            args={'query':query,'folderPath':folder_path,'includeSubfolders':False,
                  'offset':offset,'maxResults':200,'sortOrder':'asc'}
            if start_date:
                args['startDate']=start_date
            page=self._call_mcp_tool('searchMessages',args)
            if not isinstance(page,dict) or not isinstance(page.get('messages'),list) or page.get('truncated'):
                raise RuntimeError('THUNDERBIRD_SEARCH_INCOMPLETE')
            rows.extend(page['messages'])
            if not page.get('hasMore'):
                return rows
            if not page['messages']:
                raise RuntimeError('THUNDERBIRD_PAGINATION_INVALID')
            offset+=len(page['messages'])

    def workflow_message(self, message_id, folder_path):
        result=self._call_mcp_tool('getMessage',{'messageId':str(message_id),'folderPath':folder_path,'bodyFormat':'text'})
        if not isinstance(result,dict) or not isinstance(result.get('body'),str):
            raise RuntimeError('THUNDERBIRD_MESSAGE_UNAVAILABLE')
        return result

    def workflow_raw_message(self,message_id,folder_path):
        result=self._call_mcp_tool('getMessage',{'messageId':str(message_id),'folderPath':folder_path,'rawSource':True})
        raw=result.get('rawSource') if isinstance(result,dict) else None
        if not isinstance(raw,str):
            raise RuntimeError('THUNDERBIRD_RAW_MESSAGE_UNAVAILABLE')
        return raw

    def find_sent_exact(self, recipient, subject, body, submitted_at):
        after=datetime.fromisoformat(submitted_at).replace(microsecond=0)
        matches=[]
        for folder in self.workflow_folders('sent'):
            for row in self.workflow_search(folder,start_date=after.isoformat(),query='to:'+recipient):
                message=self.workflow_message(row['messageId'],folder)
                addresses=[address.lower() for _,address in getaddresses([str(message.get('recipients') or message.get('to') or '')])]
                if recipient.lower() in addresses and message.get('subject')==subject and message['body']==body:
                    matches.append({'message_id':row['messageId'],'folder_path':folder})
        return matches

    def list_inbox_messages(self, unread_only: bool = False, folder: str = "INBOX") -> List[Dict[str, Any]]:
        if self.dry_run:
            return [m for m in self._mock_inbox if m.get('folder', 'INBOX') == folder]
        # Utilisation de searchMessages
        args = {}
        if unread_only:
            args["unread"] = True

        result = self._call_mcp_tool("searchMessages", args)
        messages = []
        if not isinstance(result, list) or not all(isinstance(m, dict) and m.get('messageId') is not None for m in result):
            raise RuntimeError('Invalid inbox response')
        if isinstance(result, list):
            for msg in result:
                messages.append({
                    "id": msg.get("messageId"),
                    "from": msg.get("author"),
                    "subject": msg.get("subject"),
                    "date": msg.get("date"),
                    "folder": folder
                })
        return messages

    def get_message_details(self, message_id: str, folder: str = "INBOX") -> Dict[str, Any]:
        if self.dry_run and self._mock_inbox:
            for item in self._mock_inbox:
                if str(item.get("id")) == str(message_id):
                    return dict(item)
        result = self._call_mcp_tool("getMessage", {"messageId": message_id, "bodyFormat": "text"})
        if isinstance(result, dict) and "body" in result:
            return {
                "id": message_id,
                "from": result.get("author"),
                "to": result.get("recipients", {}).get("to", ""),
                "subject": result.get("subject"),
                "date": result.get("date"),
                "body": result.get("body"),
                "folder": folder,
            }
        raise RuntimeError('Message details unavailable')

    def move_message(self, message_id: str, source_folder: str, target_folder: str) -> bool:
        if self.dry_run and self._mock_inbox:
            for item in self._mock_inbox:
                if str(item.get("id")) == str(message_id):
                    item["folder"] = target_folder
                    return True
        # Dans un vrai cas, il faut récupérer le folder URI avec listFolders
        # Pour simplifier, on envoie directement le nom du dossier, le MCP essaiera de le résoudre
        result = self._call_mcp_tool("updateMessage", {
            "messageIds": [message_id],
            "moveTo": target_folder
        })
        if not result or (isinstance(result, dict) and result.get('success') is False):
            raise RuntimeError('Message move not confirmed')
        return True
