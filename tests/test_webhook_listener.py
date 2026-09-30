import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import requests

from scripts.webhook_listener import (
    handle_resend_event,
    add_to_suppression_list,
    load_suppression_list,
    is_suppressed,
    verify_resend_signature,
    create_webhook_server,
    extract_person_id,
    extract_email,
)
from scripts.models import CampaignStatusEnum


def test_handle_bounced_event(tmp_path):
    suppression_file = tmp_path / "suppression_list.json"
    event = {
        "type": "email.bounced",
        "data": {
            "to": ["bounced-user@example.com"],
            "bounce": {"message": "mailbox unavailable"},
            "tags": [{"name": "person_id", "value": "person-999"}],
        },
    }
    action = handle_resend_event(event, suppression_file=suppression_file)
    assert action["status"] == "PURGED"
    assert action["person_id"] == "person-999"
    assert "bounced-user@example.com" in action["email"]

    # Verify suppression file was written
    assert is_suppressed("bounced-user@example.com", filepath=suppression_file)


def test_handle_bounced_event_twenty_crm_called(tmp_path):
    mock_twenty = MagicMock()
    mock_twenty.purge_bounced_email.return_value = {"id": "person-999"}
    suppression_file = tmp_path / "suppression_list.json"

    event = {
        "type": "email.bounced",
        "data": {
            "to": ["bounced-user@example.com"],
            "bounce": {"message": "mailbox unavailable"},
            "tags": [{"name": "person_id", "value": "person-999"}],
        },
    }

    action = handle_resend_event(
        event,
        twenty_client=mock_twenty,
        suppression_file=suppression_file,
    )

    assert action["status"] == "PURGED"
    assert action["person_id"] == "person-999"
    assert action["email"] == "bounced-user@example.com"
    mock_twenty.purge_bounced_email.assert_called_once_with("person-999")
    assert is_suppressed("bounced-user@example.com", filepath=suppression_file)


def test_handle_complaint_event(tmp_path):
    mock_twenty = MagicMock()
    mock_twenty.purge_bounced_email.return_value = {"id": "person-888"}
    suppression_file = tmp_path / "suppression_list.json"

    event = {
        "type": "email.complained",
        "data": {
            "to": ["complainer@example.com"],
            "complaint": {"message": "marked as spam"},
            "tags": [{"name": "person_id", "value": "person-888"}],
        },
    }

    action = handle_resend_event(
        event,
        twenty_client=mock_twenty,
        suppression_file=suppression_file,
    )

    assert action["status"] == "PURGED"
    assert action["person_id"] == "person-888"
    assert action["email"] == "complainer@example.com"
    mock_twenty.purge_bounced_email.assert_called_once_with("person-888")
    assert is_suppressed("complainer@example.com", filepath=suppression_file)


def test_handle_delivered_event(tmp_path):
    mock_twenty = MagicMock()
    mock_twenty.update_campaign_status.return_value = {"id": "person-777"}
    suppression_file = tmp_path / "suppression_list.json"

    event = {
        "type": "email.delivered",
        "data": {
            "to": ["delivered@example.com"],
            "tags": [{"name": "person_id", "value": "person-777"}],
        },
    }

    action = handle_resend_event(
        event,
        twenty_client=mock_twenty,
        suppression_file=suppression_file,
    )

    assert action["status"] == "DELIVERED"
    assert action["person_id"] == "person-777"
    assert action["email"] == "delivered@example.com"
    mock_twenty.update_campaign_status.assert_called_once_with(
        "person-777",
        CampaignStatusEnum.EMAIL_DELIVERED.value,
    )
    # Delivered should NOT be added to suppression list
    assert not is_suppressed("delivered@example.com", filepath=suppression_file)


def test_handle_unhandled_event(tmp_path):
    suppression_file = tmp_path / "suppression_list.json"
    event = {
        "type": "email.opened",
        "data": {
            "to": ["reader@example.com"],
        },
    }
    action = handle_resend_event(event, suppression_file=suppression_file)
    assert action["status"] == "IGNORED"
    assert action["event_type"] == "email.opened"


def test_handle_bounced_without_person_id(tmp_path):
    suppression_file = tmp_path / "suppression_list.json"
    event = {
        "type": "email.bounced",
        "data": {
            "to": ["anonymous-bounce@example.com"],
            "bounce": {"message": "user unknown"},
        },
    }
    action = handle_resend_event(event, suppression_file=suppression_file)
    assert action["status"] == "PURGED"
    assert action["person_id"] is None
    assert action["email"] == "anonymous-bounce@example.com"
    assert is_suppressed("anonymous-bounce@example.com", filepath=suppression_file)


def test_handle_bounced_crm_failure_is_resilient(tmp_path):
    mock_twenty = MagicMock()
    mock_twenty.purge_bounced_email.side_effect = requests.ConnectionError("CRM is down")
    suppression_file = tmp_path / "suppression_list.json"

    event = {
        "type": "email.bounced",
        "data": {
            "to": ["bounced-safe@example.com"],
            "tags": [{"name": "person_id", "value": "person-111"}],
        },
    }

    # Should not raise exception
    action = handle_resend_event(
        event,
        twenty_client=mock_twenty,
        suppression_file=suppression_file,
    )
    assert action["status"] == "PURGED"
    assert is_suppressed("bounced-safe@example.com", filepath=suppression_file)


def test_suppression_list_operations(tmp_path):
    suppression_file = tmp_path / "suppression_list.json"

    # Initially empty
    assert load_suppression_list(suppression_file) == []
    assert not is_suppressed("test@example.com", filepath=suppression_file)

    # Add first email
    added = add_to_suppression_list(
        "test@example.com",
        person_id="person-1",
        reason="hard bounce",
        event_type="email.bounced",
        filepath=suppression_file,
    )
    assert added is True
    assert is_suppressed("test@example.com", filepath=suppression_file)
    assert is_suppressed("TEST@EXAMPLE.COM", filepath=suppression_file)  # Case insensitive

    # Duplicate add returns False (already exists)
    added_duplicate = add_to_suppression_list(
        "test@example.com",
        reason="second bounce",
        filepath=suppression_file,
    )
    assert added_duplicate is False

    entries = load_suppression_list(suppression_file)
    assert len(entries) == 1
    assert entries[0]["email"] == "test@example.com"
    assert entries[0]["person_id"] == "person-1"
    assert entries[0]["reason"] == "hard bounce"


def test_extract_person_id_variations():
    # Tag list format
    assert extract_person_id({"tags": [{"name": "person_id", "value": "pid-1"}]}) == "pid-1"
    assert extract_person_id({"tags": [{"key": "person_id", "value": "pid-2"}]}) == "pid-2"
    # Tag dict format
    assert extract_person_id({"tags": {"person_id": "pid-3"}}) == "pid-3"
    # Direct field
    assert extract_person_id({"person_id": "pid-4"}) == "pid-4"
    # Missing
    assert extract_person_id({}) is None


def test_extract_email_variations():
    assert extract_email({"to": ["user@example.com"]}) == "user@example.com"
    assert extract_email({"to": "user2@example.com"}) == "user2@example.com"
    assert extract_email({"email": "user3@example.com"}) == "user3@example.com"
    assert extract_email({"recipient": "user4@example.com"}) == "user4@example.com"
    assert extract_email({}) is None


def test_verify_resend_signature():
    # Empty secret disables check
    assert verify_resend_signature(b"payload", {}, secret=None) is True
    assert verify_resend_signature(b"payload", {}, secret="") is True

    # Missing headers with secret
    assert verify_resend_signature(b"payload", {}, secret="whsec_dGVzdHNlY3JldA==") is False

    # Valid Svix signature test
    import hmac
    import hashlib
    import base64

    secret_raw = b"testsecret123456"
    secret_b64 = base64.b64encode(secret_raw).decode("utf-8")
    secret = f"whsec_{secret_b64}"

    msg_id = "msg_123"
    timestamp = str(int(time.time()))
    payload = b'{"hello": "world"}'

    to_sign = f"{msg_id}.{timestamp}.".encode("utf-8") + payload
    signature = base64.b64encode(hmac.new(secret_raw, to_sign, hashlib.sha256).digest()).decode("utf-8")

    headers = {
        "svix-id": msg_id,
        "svix-timestamp": timestamp,
        "svix-signature": f"v1,{signature}",
    }

    assert verify_resend_signature(payload, headers, secret=secret) is True

    # Invalid signature
    bad_headers = dict(headers)
    bad_headers["svix-signature"] = "v1,invalid_signature"
    assert verify_resend_signature(payload, bad_headers, secret=secret) is False


def test_http_webhook_server(tmp_path):
    mock_twenty = MagicMock()
    mock_twenty.purge_bounced_email.return_value = {"id": "person-999"}
    suppression_file = tmp_path / "suppression_list.json"

    # Start server on an ephemeral port
    server = create_webhook_server(
        host="127.0.0.1",
        port=0,
        twenty_client=mock_twenty,
        suppression_file=suppression_file,
    )
    port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base_url = f"http://127.0.0.1:{port}"

        # 1. Health check
        res_health = requests.get(f"{base_url}/health", timeout=5)
        assert res_health.status_code == 200
        assert res_health.json()["status"] == "healthy"

        # 2. Bounce POST
        payload = {
            "type": "email.bounced",
            "data": {
                "to": ["bounced-http@example.com"],
                "bounce": {"message": "550 User unknown"},
                "tags": [{"name": "person_id", "value": "person-999"}],
            },
        }
        res_post = requests.post(f"{base_url}/webhook", json=payload, timeout=5)
        assert res_post.status_code == 200
        data = res_post.json()
        assert data["status"] == "ok"
        assert data["action"]["status"] == "PURGED"
        assert data["action"]["person_id"] == "person-999"

        # Check suppression
        assert is_suppressed("bounced-http@example.com", filepath=suppression_file)
        mock_twenty.purge_bounced_email.assert_called_once_with("person-999")

        # 3. Invalid JSON POST
        res_invalid = requests.post(
            f"{base_url}/webhook",
            data=b"not-json",
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        assert res_invalid.status_code == 400

    finally:
        server.shutdown()
        server.server_close()
