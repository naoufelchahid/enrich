"""
End-to-End Integration Test for Autonomous Outreach Pipeline — Contexte Lab.

Validates the full autonomous lifecycle across Tasks 1 through 7:
1. Ingest raw lead into staging SQLite with `DG`, phone, email, and linkedin.
2. Run audit updating NeverBounce status to `VALID`.
3. Assert priority score equals 200 (100 DG + 50 VALID + 30 mobile + 20 linkedin).
4. Test proactive career change sync with Twenty CRM (`sync_career_change`).
5. Initialize `OutreachDispatcher` in `dry_run=True` (and/or with mocked Twenty client).
6. Call `prepare_daily_batch(limit=1, daily_send_target=1)`.
7. Assert `synced == 1` and `queued == 1`.
8. Call `send_next_candidate()`.
9. Assert `status == "SENT"`, recipient matches, subject is 2-4 words, body adheres to 50-85 words,
   0 HTML, single CTA (`validate_email_copy`).
10. Assert queue count drops to 0.
11. Inject a bounce email into `dispatcher.mcp_mail._mock_inbox` (`from: MAILER-DAEMON@...`, `body: 550 User unknown`).
12. Call `dispatcher.review_inbox_and_sync()`.
13. Assert `inbox_summary["bounces"] == 1`.
14. Assert `dispatcher.circuit_breaker.is_tripped() is True`.
15. Attempt another `send_next_candidate()`.
16. Assert `status == "CIRCUIT_BREAKER_ACTIVE"`.
"""

import json
import pytest
import responses
from unittest.mock import MagicMock

from scripts.circuit_breaker import CircuitBreaker
from scripts.mcp_mail_server import MCPMailServer
from scripts.outreach_dispatcher import OutreachDispatcher
from scripts.prompt_engine import validate_email_copy
from scripts.staging_db import (
    get_top_priority_leads,
    init_staging_db,
    insert_staging_leads,
    update_lead_neverbounce_result,
)
from scripts.twenty_client import TwentyClient


@responses.activate
def test_full_autonomous_outreach_pipeline_lifecycle(tmp_path):
    """Verify the entire autonomous outreach pipeline lifecycle and zero-bounce circuit breaker."""
    db_file = str(tmp_path / "staging_integration.sqlite")
    cb_file = str(tmp_path / "circuit_breaker.json")
    queue_file = str(tmp_path / "daily_queue.json")

    # 1. Ingest raw lead into staging SQLite with `DG`, phone, email, and linkedin
    init_staging_db(db_file)
    raw_lead = {
        "source_file": "leads_casa.xlsx",
        "first_name": "Khadija",
        "last_name": "Idrissi",
        "company_name": "Idrissi Logistique",
        "job_title": "DG",
        "phone_mobile": "0661122334",
        "raw_email": "k.idrissi@idrissilogistique.ma",
        "linkedin_url": "https://www.linkedin.com/in/khadija-idrissi",
    }
    inserted = insert_staging_leads(db_file, [raw_lead])
    assert inserted == 1

    # 2. Run audit updating NeverBounce status to `VALID`
    lead_id = 1
    update_lead_neverbounce_result(
        db_file,
        lead_id=lead_id,
        nb_status="VALID",
        clean_email="k.idrissi@idrissilogistique.ma",
    )

    # Assert priority score equals 200 (100 DG + 50 VALID + 30 mobile + 20 linkedin)
    top_leads = get_top_priority_leads(db_file, limit=1)
    assert len(top_leads) == 1
    lead = top_leads[0]
    assert lead["priority_score"] == 200
    assert lead["neverbounce_status"] == "VALID"
    assert lead["enrichment_status"] == "READY_FOR_CRM"
    assert lead["clean_email"] == "k.idrissi@idrissilogistique.ma"

    # 3. Test proactive career change sync with Twenty CRM (`sync_career_change`)
    twenty_client = TwentyClient(base_url="http://localhost:3005", api_key="test_api_key")

    # Mock Twenty REST API responses for career change sync
    responses.add(
        responses.GET,
        "http://localhost:3005/rest/people/person-idrissi",
        json={
            "data": {
                "id": "person-idrissi",
                "jobTitle": "Directrice d'Exploitation",
                "companyId": "comp-old",
                "company": {"name": "Atlas Fret"},
            }
        },
        status=200,
    )
    # Search for new company "Idrissi Logistique"
    responses.add(
        responses.GET,
        "http://localhost:3005/rest/companies?filter=%7B%22name%22%3A+%7B%22eq%22%3A+%22Idrissi+Logistique%22%7D%7D",
        json={"data": {"companies": []}},
        status=200,
    )
    # Create new company
    responses.add(
        responses.POST,
        "http://localhost:3005/rest/companies",
        json={"data": {"id": "comp-new-123", "name": "Idrissi Logistique"}},
        status=201,
    )
    # Patch person
    responses.add(
        responses.PATCH,
        "http://localhost:3005/rest/people/person-idrissi",
        json={"data": {"id": "person-idrissi", "companyId": "comp-new-123", "jobTitle": "DG"}},
        status=200,
    )
    # Create Opportunity
    responses.add(
        responses.POST,
        "http://localhost:3005/rest/opportunities",
        json={"data": {"id": "opp-proactive-1", "name": "Opportunité Proactive - Idrissi Logistique"}},
        status=201,
    )

    harvest_data = {
        "current_company_name": "Idrissi Logistique",
        "current_job_title": "DG",
        "company_website": "https://idrissilogistique.ma",
        "company_industry": "Logistique & Supply Chain",
    }
    career_sync_result = twenty_client.sync_career_change("person-idrissi", harvest_data)
    assert career_sync_result["career_changed"] is True
    assert career_sync_result["old_company"] == "Atlas Fret"
    assert career_sync_result["new_company"] == "Idrissi Logistique"
    assert career_sync_result["new_company_id"] == "comp-new-123"
    assert career_sync_result["new_job_title"] == "DG"

    opp_id = twenty_client.create_opportunity(
        name="Opportunité Proactive - Idrissi Logistique",
        stage="Prospect",
        person_id="person-idrissi",
        company_id=career_sync_result["new_company_id"],
    )
    assert opp_id == "opp-proactive-1"

    # 4. Initialize OutreachDispatcher in dry_run=True (and with mocked Twenty client)
    dispatcher = OutreachDispatcher(
        db_path=db_file,
        circuit_breaker_file=cb_file,
        queue_file=queue_file,
        dry_run=True,
        twenty_client=twenty_client,
    )
    assert dispatcher.circuit_breaker.is_tripped() is False

    # 5. Call prepare_daily_batch(limit=1, daily_send_target=1)
    batch_res = dispatcher.prepare_daily_batch(limit=1, daily_send_target=1)
    # Assert synced == 1 and queued == 1
    assert batch_res["synced"] == 1
    assert batch_res["queued"] == 1
    assert len(dispatcher.get_pending_queue()) == 1
    assert dispatcher.get_pending_queue()[0]["id"] == 1

    # 6. Call send_next_candidate()
    send_res = dispatcher.send_next_candidate()

    # Assert status == "SENT", recipient matches
    assert send_res["status"] == "SENT"
    assert send_res["to"] == "k.idrissi@idrissilogistique.ma"

    # Assert subject is 2-4 words, body adheres to 50-85 words, 0 HTML, single CTA (validate_email_copy)
    subject = send_res["subject"]
    body = send_res["body"]

    subject_words = [w for w in subject.strip().split() if w]
    assert 2 <= len(subject_words) <= 4, f"Subject word count invalid: '{subject}' ({len(subject_words)} words)"

    body_words = [w for w in body.strip().split() if w]
    assert 50 <= len(body_words) <= 85, f"Body word count invalid: {len(body_words)} words"

    # 0 HTML
    assert "<" not in subject and ">" not in subject
    assert "<" not in body and ">" not in body

    # Single CTA
    assert "échange" in body.lower() or "5 minutes" in body

    # Strict copywriting check
    is_valid, copy_errors = validate_email_copy(subject, body)
    assert is_valid is True, f"Copy validation failed: {copy_errors}"

    # Assert queue count drops to 0
    assert len(dispatcher.get_pending_queue()) == 0
    assert send_res["remaining_queue"] == 0

    # Verify MCP Mail Server sent email details in dry-run
    assert len(dispatcher.mcp_mail._sent_emails) == 1
    sent_record = dispatcher.mcp_mail._sent_emails[0]
    assert sent_record["to"] == "k.idrissi@idrissilogistique.ma"
    assert sent_record["subject"] == subject
    assert "User-Agent" in sent_record["headers"]

    # 7. Inject a bounce email into dispatcher.mcp_mail._mock_inbox (from: MAILER-DAEMON@..., body: 550 User unknown)
    dispatcher.mcp_mail._mock_inbox.append(
        {
            "id": "bounce-ndr-001",
            "from": "MAILER-DAEMON@sales.contextelab.ma",
            "to": "chahid.naoufel@sales.contextelab.ma",
            "subject": "Undelivered Mail Returned to Sender",
            "body": "550 User unknown: recipient k.idrissi@idrissilogistique.ma not found",
            "folder": "INBOX",
            "unread": True,
        }
    )

    # 8. Call dispatcher.review_inbox_and_sync()
    inbox_summary = dispatcher.review_inbox_and_sync()
    # Assert inbox_summary["bounces"] == 1
    assert inbox_summary["bounces"] == 1
    # Assert dispatcher.circuit_breaker.is_tripped() is True
    assert dispatcher.circuit_breaker.is_tripped() is True

    # 9. Attempt another send_next_candidate()
    # Enqueue a candidate to guarantee breaker stops even with active queue
    dispatcher._save_queue([
        {
            "id": 2,
            "first_name": "Tariq",
            "last_name": "Alami",
            "company_name": "Alami Tech",
            "clean_email": "tariq@alamitech.ma",
            "job_title": "DG",
        }
    ])
    blocked_send = dispatcher.send_next_candidate()
    # Assert status == "CIRCUIT_BREAKER_ACTIVE"
    assert blocked_send["status"] == "CIRCUIT_BREAKER_ACTIVE"
    # Candidate remains in queue, unsent
    assert len(dispatcher.get_pending_queue()) == 1


def test_outreach_pipeline_with_mocked_twenty_live_sync(tmp_path):
    """Verify live CRM sync flow (dry_run=False) with mock TwentyClient and instant breaker trip."""
    db_file = str(tmp_path / "staging_live.sqlite")
    cb_file = str(tmp_path / "circuit_breaker_live.json")
    queue_file = str(tmp_path / "daily_queue_live.json")

    init_staging_db(db_file)
    raw_lead = {
        "source_file": "leads_rabat.xlsx",
        "first_name": "Yassine",
        "last_name": "El Fassi",
        "company_name": "Fassi Consulting",
        "job_title": "CEO",
        "phone_mobile": "0700112233",
        "raw_email": "yassine@fassiconsulting.ma",
        "linkedin_url": "https://www.linkedin.com/in/yassine-elfassi",
    }
    insert_staging_leads(db_file, [raw_lead])
    update_lead_neverbounce_result(db_file, 1, "VALID", "yassine@fassiconsulting.ma")

    # Priority score: CEO(100) + VALID(50) + mobile(30) + linkedin(20) = 200
    top_leads = get_top_priority_leads(db_file, limit=1)
    assert top_leads[0]["priority_score"] == 200

    mock_twenty = MagicMock(spec=TwentyClient)
    mock_twenty.upsert_person.return_value = "crm-person-777"

    mock_mcp = MCPMailServer(dry_run=True)

    dispatcher = OutreachDispatcher(
        db_path=db_file,
        circuit_breaker_file=cb_file,
        queue_file=queue_file,
        dry_run=False,
        twenty_client=mock_twenty,
        mcp_mail=mock_mcp,
    )

    # 1. Batch preparation syncs to CRM
    batch_res = dispatcher.prepare_daily_batch(limit=1, daily_send_target=1)
    assert batch_res["synced"] == 1
    assert batch_res["queued"] == 1
    mock_twenty.upsert_person.assert_called_once()

    # 2. Dispatch next candidate
    send_res = dispatcher.send_next_candidate()
    assert send_res["status"] == "SENT"
    assert send_res["to"] == "yassine@fassiconsulting.ma"
    mock_twenty.update_campaign_status.assert_called_once_with("crm-person-777", "EMAIL_SENT")
    assert len(dispatcher.get_pending_queue()) == 0

    # 3. Simulate NDR bounce
    mock_mcp._mock_inbox.append({
        "id": "bounce-live-1",
        "from": "MAILER-DAEMON@mail.server.net",
        "subject": "Delivery Failure",
        "body": "550 User unknown",
        "folder": "INBOX",
    })

    review_res = dispatcher.review_inbox_and_sync()
    assert review_res["bounces"] == 1
    assert dispatcher.circuit_breaker.is_tripped() is True

    # 4. Next send blocked
    blocked = dispatcher.send_next_candidate()
    assert blocked["status"] == "CIRCUIT_BREAKER_ACTIVE"
