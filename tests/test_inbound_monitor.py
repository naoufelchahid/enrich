import pytest
from unittest.mock import MagicMock
from scripts.circuit_breaker import CircuitBreaker
from scripts.mcp_mail_server import MCPMailServer
from scripts.twenty_client import TwentyClient
from scripts.inbound_monitor import (
    classify_inbound_message,
    process_inbound_batch,
)


def test_classify_inbound_message():
    assert classify_inbound_message("mailer-daemon@google.com", "Failure notice", "550 User unknown") == "BOUNCE"
    assert classify_inbound_message("prospect@ma.ma", "Re: question", "Très intéressé pour un devis et call") == "POSITIVE"
    assert classify_inbound_message("user@ma.ma", "Stop", "Veuillez me désinscrire de vos listes") == "UNSUBSCRIBE"
    assert classify_inbound_message("newsletter@spam.ma", "Promo", "Voici notre catalogue") == "NEUTRAL"


def test_process_inbound_batch_bounce_trips_breaker(tmp_path):
    state_path = str(tmp_path / "cb.json")
    cb = CircuitBreaker(state_file=state_path)

    mock_mcp = MagicMock(spec=MCPMailServer)
    mock_mcp.list_inbox_messages.return_value = [
        {"id": "101", "from": "MAILER-DAEMON@sales.contextelab.ma", "subject": "Delivery Failure", "folder": "INBOX"}
    ]
    mock_mcp.get_message_details.return_value = {
        "id": "101",
        "from": "MAILER-DAEMON@sales.contextelab.ma",
        "subject": "Delivery Failure",
        "body": "550 No such user: karim@old.ma",
        "folder": "INBOX",
    }

    mock_twenty = MagicMock(spec=TwentyClient)
    mock_twenty.purge_bounced_email.return_value = {"success": True}

    summary = process_inbound_batch(mock_mcp, mock_twenty, cb)
    assert summary["bounces"] == 1
    assert cb.is_tripped() is True
    mock_mcp.move_message.assert_called_with("101", "INBOX", "Bounces_Archives")


def test_process_inbound_batch_positive_creates_opportunity(tmp_path):
    state_path = str(tmp_path / "cb.json")
    cb = CircuitBreaker(state_file=state_path)

    mock_mcp = MagicMock(spec=MCPMailServer)
    mock_mcp.list_inbox_messages.return_value = [
        {"id": "202", "from": "ahmed@societe.ma", "subject": "Re: question sur societe", "folder": "INBOX"}
    ]
    mock_mcp.get_message_details.return_value = {
        "id": "202",
        "from": "ahmed@societe.ma",
        "subject": "Re: question sur societe",
        "body": "Bonjour Naoufel, pouvons-nous caler un rendez-vous demain ?",
        "folder": "INBOX",
    }

    mock_twenty = MagicMock(spec=TwentyClient)
    mock_twenty.create_opportunity.return_value = "opp-12345"

    summary = process_inbound_batch(mock_mcp, mock_twenty, cb)
    assert summary["positives"] == 1
    mock_twenty.create_opportunity.assert_called_once()
    mock_mcp.move_message.assert_called_with("202", "INBOX", "Opportunités")


def test_process_inbound_batch_unsubscribe(tmp_path):
    state_path = str(tmp_path / "cb.json")
    cb = CircuitBreaker(state_file=state_path)

    mock_mcp = MagicMock(spec=MCPMailServer)
    mock_mcp.list_inbox_messages.return_value = [
        {"id": "303", "from": "client@societe.ma", "subject": "Stop emails", "folder": "INBOX"}
    ]
    mock_mcp.get_message_details.return_value = {
        "id": "303",
        "from": "client@societe.ma",
        "subject": "Stop emails",
        "body": "Merci de me désinscrire de vos communications.",
        "folder": "INBOX",
    }

    mock_twenty = MagicMock(spec=TwentyClient)

    summary = process_inbound_batch(mock_mcp, mock_twenty, cb)
    assert summary["unsubscribes"] == 1
    assert cb.is_tripped() is False
    mock_mcp.move_message.assert_called_with("303", "INBOX", "Désinscrits")


def test_process_inbound_batch_neutral(tmp_path):
    state_path = str(tmp_path / "cb.json")
    cb = CircuitBreaker(state_file=state_path)

    mock_mcp = MagicMock(spec=MCPMailServer)
    mock_mcp.list_inbox_messages.return_value = [
        {"id": "404", "from": "info@news.ma", "subject": "Newsletter hebdo", "folder": "INBOX"}
    ]
    mock_mcp.get_message_details.return_value = {
        "id": "404",
        "from": "info@news.ma",
        "subject": "Newsletter hebdo",
        "body": "Voici notre sélection d'articles de la semaine.",
        "folder": "INBOX",
    }

    mock_twenty = MagicMock(spec=TwentyClient)

    summary = process_inbound_batch(mock_mcp, mock_twenty, cb)
    assert summary["neutral"] == 1
    assert summary["total"] == 1
    assert cb.is_tripped() is False
    mock_mcp.move_message.assert_not_called()


def test_process_inbound_batch_empty_folder(tmp_path):
    state_path = str(tmp_path / "cb.json")
    cb = CircuitBreaker(state_file=state_path)

    mock_mcp = MagicMock(spec=MCPMailServer)
    mock_mcp.list_inbox_messages.return_value = []

    mock_twenty = MagicMock(spec=TwentyClient)

    summary = process_inbound_batch(mock_mcp, mock_twenty, cb)
    assert summary == {"total": 0, "bounces": 0, "positives": 0, "unsubscribes": 0, "neutral": 0}
