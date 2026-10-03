# tests/test_outreach_dispatcher.py
import json
import subprocess
import pytest
from unittest.mock import MagicMock, patch
from scripts.circuit_breaker import CircuitBreaker
from scripts.mcp_mail_server import MCPMailServer
from scripts.outreach_dispatcher import OutreachDispatcher, main
from scripts.twenty_client import TwentyClient

@pytest.fixture
def mock_dispatcher(tmp_path):
    db_path = str(tmp_path / "staging.sqlite")
    state_path = str(tmp_path / "cb.json")
    queue_path = str(tmp_path / "daily_queue.json")

    dispatcher = OutreachDispatcher(
        db_path=db_path,
        circuit_breaker_file=state_path,
        queue_file=queue_path,
        dry_run=True,
    )
    return dispatcher

def test_prepare_daily_batch_populates_queue(mock_dispatcher):
    from scripts.staging_db import init_staging_db, insert_staging_leads, update_lead_neverbounce_result

    init_staging_db(mock_dispatcher.db_path)
    leads = [
        {"source_file": "BDD.xlsx", "first_name": "Tariq", "last_name": "Ibn", "company_name": "Co1", "raw_email": "t@co1.ma", "job_title": "DG"},
        {"source_file": "BDD.xlsx", "first_name": "Mouna", "last_name": "Ben", "company_name": "Co2", "raw_email": "m@co2.ma", "job_title": "PDG"},
    ]
    insert_staging_leads(mock_dispatcher.db_path, leads)
    update_lead_neverbounce_result(mock_dispatcher.db_path, 1, "VALID", "t@co1.ma")
    update_lead_neverbounce_result(mock_dispatcher.db_path, 2, "VALID", "m@co2.ma")

    stats = mock_dispatcher.prepare_daily_batch(limit=2)
    assert stats["queued"] == 2
    assert len(mock_dispatcher.get_pending_queue()) == 2

def test_send_next_candidate_respects_circuit_breaker(mock_dispatcher):
    mock_dispatcher.circuit_breaker.trip(reason="Previous bounce")
    result = mock_dispatcher.send_next_candidate()
    assert result["status"] == "CIRCUIT_BREAKER_ACTIVE"

def test_send_next_candidate_executes_single_send(mock_dispatcher):
    # Queue a candidate
    mock_dispatcher._save_queue([
        {
            "id": 1,
            "first_name": "Leila",
            "last_name": "Chraibi",
            "company_name": "Chraibi Group",
            "clean_email": "leila@chraibi.ma",
            "job_title": "Directrice Générale",
        }
    ])
    result = mock_dispatcher.send_next_candidate()
    assert result["status"] == "SENT"
    assert result["to"] == "leila@chraibi.ma"
    assert len(mock_dispatcher.get_pending_queue()) == 0

def test_send_next_candidate_queue_empty(mock_dispatcher):
    result = mock_dispatcher.send_next_candidate()
    assert result["status"] == "QUEUE_EMPTY"

def test_send_next_candidate_preflight_bounce_trips_breaker(mock_dispatcher):
    # Pre-flight inbound check detects a bounce and trips breaker
    mock_dispatcher._save_queue([
        {"id": 1, "first_name": "Ali", "company_name": "Tech", "clean_email": "ali@tech.ma"}
    ])

    with patch("scripts.outreach_dispatcher.process_inbound_batch") as mock_inbound:
        def side_effect(mcp, twenty, cb):
            cb.trip("NDR bounce received in pre-flight")
            return {"bounces": 1}
        mock_inbound.side_effect = side_effect

        result = mock_dispatcher.send_next_candidate()
        assert result["status"] == "CIRCUIT_BREAKER_ACTIVE"
        # Candidate should remain in queue
        assert len(mock_dispatcher.get_pending_queue()) == 1

def test_review_inbox_and_sync(mock_dispatcher):
    with patch("scripts.outreach_dispatcher.process_inbound_batch") as mock_inbound:
        mock_inbound.return_value = {"total": 5, "bounces": 0, "positives": 1, "unsubscribes": 0, "neutral": 4}
        stats = mock_dispatcher.review_inbox_and_sync()
        assert stats["total"] == 5
        assert stats["positives"] == 1
        mock_inbound.assert_called_once_with(
            mock_dispatcher.mcp_mail,
            mock_dispatcher.twenty_client,
            mock_dispatcher.circuit_breaker,
        )

def test_prepare_daily_batch_live_syncs_twenty(tmp_path):
    from scripts.staging_db import init_staging_db, insert_staging_leads, update_lead_neverbounce_result

    db_path = str(tmp_path / "staging_live.sqlite")
    queue_path = str(tmp_path / "queue_live.json")
    cb_path = str(tmp_path / "cb_live.json")

    init_staging_db(db_path)
    leads = [
        {"source_file": "BDD.xlsx", "first_name": "Karim", "last_name": "Alami", "company_name": "Atlas", "raw_email": "k@atlas.ma", "job_title": "DG"}
    ]
    insert_staging_leads(db_path, leads)
    update_lead_neverbounce_result(db_path, 1, "VALID", "k@atlas.ma")

    mock_twenty = MagicMock(spec=TwentyClient)
    mock_twenty.upsert_person.return_value = "crm-person-99"

    dispatcher = OutreachDispatcher(
        db_path=db_path,
        circuit_breaker_file=cb_path,
        queue_file=queue_path,
        dry_run=False,
        twenty_client=mock_twenty,
    )

    stats = dispatcher.prepare_daily_batch(limit=1, daily_send_target=1)
    assert stats["synced"] == 1
    assert stats["queued"] == 1
    mock_twenty.upsert_person.assert_called_once()

    # Now send candidate and verify campaign status updated
    send_res = dispatcher.send_next_candidate()
    assert send_res["status"] == "SENT"
    mock_twenty.update_campaign_status.assert_called_once_with("crm-person-99", "EMAIL_SENT")

def test_cli_main_dry_run_tasks(capsys):
    test_args = ["outreach_dispatcher.py", "--task", "review_inbox_and_sync", "--dry-run"]
    with patch("sys.argv", test_args):
        with patch.object(OutreachDispatcher, "review_inbox_and_sync", return_value={"total": 0, "bounces": 0}):
            main()
            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["total"] == 0

def test_cron_agent_dispatcher_script(tmp_path):
    # Test executing scripts/cron_agent_dispatcher.sh with --task review_inbox_and_sync --dry-run
    res = subprocess.run(
        ["bash", "scripts/cron_agent_dispatcher.sh", "--task", "review_inbox_and_sync", "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
