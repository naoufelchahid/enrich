import pytest
from unittest.mock import MagicMock, patch
from scripts.mcp_mail_server import MCPMailServer


@pytest.fixture
def mock_mail_server():
    server = MCPMailServer(
        smtp_host="localhost",
        smtp_port=587,
        imap_host="localhost",
        imap_port=993,
        username="chahid.naoufel@sales.contextelab.ma",
        password="secret_password",
        dry_run=True,
    )
    return server


def test_send_email_dry_run(mock_mail_server):
    res = mock_mail_server.send_email(
        to="prospect@company.ma",
        subject="question sur Company",
        body_text="Bonjour,\nUn court message.\n\nNaoufel",
        reply_to="n.chahid@contextelab.ma",
    )
    assert res["success"] is True
    assert res["to"] == "prospect@company.ma"
    assert "Message-ID" in res["headers"]
    assert "Mozilla Thunderbird" in res["headers"]["User-Agent"]


def test_list_and_get_message_mock(mock_mail_server):
    mock_mail_server._mock_inbox = [
        {
            "id": "1",
            "from": "Mailer-Daemon <mailer-daemon@mx.google.com>",
            "subject": "Delivery Status Notification (Failure)",
            "body": "550 5.1.1 The email account that you tried to reach does not exist.",
            "folder": "INBOX",
            "unread": True,
        },
        {
            "id": "2",
            "from": "prospect@lead.ma",
            "subject": "Re: question sur Lead",
            "body": "Bonjour Naoufel, je suis intéressé pour un call jeudi à 14h.",
            "folder": "INBOX",
            "unread": False,
        },
    ]

    inbox = mock_mail_server.list_inbox_messages()
    assert len(inbox) == 2
    assert inbox[0]["subject"] == "Delivery Status Notification (Failure)"

    unread_inbox = mock_mail_server.list_inbox_messages(unread_only=True)
    assert len(unread_inbox) == 1
    assert unread_inbox[0]["id"] == "1"

    details = mock_mail_server.get_message_details("2")
    assert "intéressé" in details["body"]


def test_move_message_mock(mock_mail_server):
    mock_mail_server._mock_inbox = [
        {"id": "1", "folder": "INBOX"},
    ]
    moved = mock_mail_server.move_message("1", "INBOX", "Bounces_Archives")
    assert moved is True
    assert mock_mail_server._mock_inbox[0]["folder"] == "Bounces_Archives"


def test_handle_jsonrpc_protocol(mock_mail_server):
    req = {
        "jsonrpc": "2.0",
        "id": 42,
        "method": "tools/call",
        "params": {
            "name": "send_email",
            "arguments": {
                "to": "test@domain.ma",
                "subject": "test",
                "body_text": "hello",
            },
        },
    }
    resp = mock_mail_server.handle_jsonrpc(req)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 42
    assert "result" in resp
    assert resp["result"]["success"] is True


def test_handle_jsonrpc_tools_list(mock_mail_server):
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
    }
    resp = mock_mail_server.handle_jsonrpc(req)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert "tools" in resp["result"]
    tool_names = [t["name"] for t in resp["result"]["tools"]]
    assert "send_email" in tool_names
    assert "list_inbox_messages" in tool_names
    assert "get_message_details" in tool_names
    assert "move_message" in tool_names


def test_handle_jsonrpc_all_tools(mock_mail_server):
    # Test list_inbox_messages via RPC
    mock_mail_server._mock_inbox = [
        {"id": "1", "folder": "INBOX", "subject": "Test Subj", "unread": True}
    ]
    req_list = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {"name": "list_inbox_messages", "arguments": {"folder": "INBOX"}},
    }
    resp_list = mock_mail_server.handle_jsonrpc(req_list)
    assert resp_list["result"][0]["subject"] == "Test Subj"

    # Test get_message_details via RPC
    req_details = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {"name": "get_message_details", "arguments": {"message_id": "1"}},
    }
    resp_details = mock_mail_server.handle_jsonrpc(req_details)
    assert resp_details["result"]["id"] == "1"

    # Test move_message via RPC
    req_move = {
        "jsonrpc": "2.0",
        "id": 12,
        "method": "tools/call",
        "params": {
            "name": "move_message",
            "arguments": {
                "message_id": "1",
                "source_folder": "INBOX",
                "target_folder": "Processed",
            },
        },
    }
    resp_move = mock_mail_server.handle_jsonrpc(req_move)
    assert resp_move["result"]["success"] is True

    # Test unknown tool
    req_unknown = {
        "jsonrpc": "2.0",
        "id": 13,
        "method": "tools/call",
        "params": {"name": "non_existent_tool", "arguments": {}},
    }
    resp_unknown = mock_mail_server.handle_jsonrpc(req_unknown)
    assert resp_unknown["error"]["code"] == -32602


def test_handle_jsonrpc_invalid_method(mock_mail_server):
    req = {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "unknown/method",
        "params": {},
    }
    resp = mock_mail_server.handle_jsonrpc(req)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 99
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_live_send_email_with_smtp_mock():
    server = MCPMailServer(
        smtp_host="smtp.example.com",
        smtp_port=587,
        imap_host="imap.example.com",
        imap_port=993,
        username="test@example.com",
        password="secret",
        dry_run=False,
    )
    with patch("smtplib.SMTP") as mock_smtp, patch.object(server, "archive_sent_email") as mock_archive:
        mock_instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_instance

        res = server.send_email(
            to="prospect@company.ma",
            subject="Question",
            body_text="Hello",
        )
        assert res["success"] is True
        mock_instance.starttls.assert_called_once()
        mock_instance.login.assert_called_once_with("test@example.com", "secret")
        mock_instance.send_message.assert_called_once()
        mock_archive.assert_called_once()


def test_handle_jsonrpc_initialize_and_ping(mock_mail_server):
    init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    init_resp = mock_mail_server.handle_jsonrpc(init_req)
    assert init_resp["result"]["serverInfo"]["name"] == "mcp-mail-server"

    ping_req = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    ping_resp = mock_mail_server.handle_jsonrpc(ping_req)
    assert ping_resp["result"] == {}



def test_archive_sent_email_dry_run_and_live():
    server = MCPMailServer(dry_run=True)
    assert server.archive_sent_email(b"dummy") is True

    live_server = MCPMailServer(
        smtp_host="smtp.example.com",
        imap_host="imap.example.com",
        dry_run=False,
    )
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_instance = MagicMock()
        mock_imap.return_value.__enter__.return_value = mock_instance
        mock_instance.append.return_value = ("OK", [b"Append completed"])

        res = live_server.archive_sent_email(b"dummy message bytes", folder="Sent")
        assert res is True
        mock_instance.login.assert_called_once()
        mock_instance.append.assert_called_once()


def test_live_send_email_with_smtp_error():
    server = MCPMailServer(dry_run=False)
    with patch("smtplib.SMTP", side_effect=Exception("Connection refused")):
        res = server.send_email(
            to="prospect@company.ma",
            subject="Test",
            body_text="Test body",
        )
        assert res["success"] is False
        assert "Connection refused" in res["error"]


def test_live_imap_list_and_details():
    server = MCPMailServer(dry_run=False)
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_instance = MagicMock()
        mock_imap.return_value.__enter__.return_value = mock_instance
        # Mock search
        mock_instance.search.return_value = ("OK", [b"1 2"])
        # Mock fetch headers
        header_raw = (
            b"From: test@sender.com\r\nSubject: Test Subject\r\nDate: Mon, 1 Jan 2026 10:00:00 +0000\r\nMessage-ID: <abc@123>\r\n\r\n"
        )
        mock_instance.fetch.return_value = ("OK", [(b"1 (RFC822 {100})", header_raw)])

        msgs = server.list_inbox_messages(unread_only=True)
        assert len(msgs) == 2
        assert msgs[0]["subject"] == "Test Subject"

        # Mock fetch full details (multipart)
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        multipart_msg = MIMEMultipart()
        multipart_msg["From"] = "test@sender.com"
        multipart_msg["To"] = "test@recipient.com"
        multipart_msg["Subject"] = "Full Message"
        multipart_msg.attach(MIMEText("This is plain text body", "plain", "utf-8"))
        raw_full = multipart_msg.as_bytes()

        mock_instance.fetch.return_value = ("OK", [(b"1 (RFC822)", raw_full)])
        details = server.get_message_details("1")
        assert details["subject"] == "Full Message"
        assert "This is plain text body" in details["body"]


def test_live_imap_move_message():
    server = MCPMailServer(dry_run=False)
    with patch("imaplib.IMAP4_SSL") as mock_imap:
        mock_instance = MagicMock()
        mock_imap.return_value.__enter__.return_value = mock_instance
        mock_instance.copy.return_value = ("OK", [b"Success"])

        res = server.move_message("10", "INBOX", "Archive")
        assert res is True
        mock_instance.select.assert_called_with("INBOX")
        mock_instance.copy.assert_called_with("10", "Archive")
        mock_instance.store.assert_called_with("10", "+FLAGS", r"(\Deleted)")
        mock_instance.expunge.assert_called_once()


def test_run_stdio_server():
    import io
    import json
    from scripts.mcp_mail_server import run_stdio_server

    input_data = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        + "\n"
        + "invalid json\n"
    )
    mock_stdin = io.StringIO(input_data)
    mock_stdout = io.StringIO()

    server = MCPMailServer(dry_run=True)
    with patch("sys.stdin", mock_stdin), patch("sys.stdout", mock_stdout):
        run_stdio_server(server)

    output = mock_stdout.getvalue().strip().split("\n")
    assert len(output) == 2
    res_1 = json.loads(output[0])
    assert res_1["id"] == 1
    assert res_1["result"] == {}

    res_2 = json.loads(output[1])
    assert res_2["error"]["code"] == -32700

