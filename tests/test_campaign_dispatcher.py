from unittest.mock import MagicMock
import pytest
import responses
from scripts.campaign_dispatcher import CampaignDispatcher, CircuitBreakerError
from scripts.models import CampaignStatusEnum, NormalizedContact


def create_mock_contacts(n: int, valid_emails: bool = True):
    return [
        NormalizedContact(
            company_name=f"Societe {i}",
            activity_sector="Technologie",
            first_name=f"Prenom{i}",
            last_name=f"Nom{i}",
            email=f"contact{i}@societe{i}.ma" if valid_emails else None,
            source_file="BDD3",
            is_dirigeant=True,
        )
        for i in range(n)
    ]


def test_dispatcher_dry_run_allocation():
    dispatcher = CampaignDispatcher(resend_api_key="mock-key", dry_run=True)
    mock_contacts = create_mock_contacts(10)
    results = dispatcher.prepare_batch(mock_contacts, batch_size=5)
    assert len(results) == 5
    # Rotation des 5 variantes A, B, C, D, E
    variants_used = [r["variant"] for r in results]
    assert variants_used == ["A", "B", "C", "D", "E"]


def test_dispatcher_variant_rotation_100_batch():
    dispatcher = CampaignDispatcher(resend_api_key="mock-key", dry_run=True)
    mock_contacts = create_mock_contacts(120)
    results = dispatcher.prepare_batch(mock_contacts, batch_size=100)
    assert len(results) == 100
    variants_used = [r["variant"] for r in results]
    # Check that each variant A, B, C, D, E appears exactly 20 times in a batch of 100
    for v in ["A", "B", "C", "D", "E"]:
        assert variants_used.count(v) == 20


def test_dispatcher_filters_contacts_without_email():
    dispatcher = CampaignDispatcher(resend_api_key="mock-key", dry_run=True)
    contacts = [
        NormalizedContact(
            company_name="Valid Inc",
            first_name="Amine",
            last_name="Chraibi",
            email="amine@valid.ma",
            source_file="BDD1",
        ),
        NormalizedContact(
            company_name="NoEmail Inc",
            first_name="Karim",
            last_name="Bennani",
            email=None,  # Should be skipped
            source_file="BDD1",
        ),
        NormalizedContact(
            company_name="Valid2 Inc",
            first_name="Sara",
            last_name="Idrissi",
            email="sara@valid2.ma",
            source_file="BDD1",
        ),
    ]
    batch = dispatcher.prepare_batch(contacts, batch_size=5)
    assert len(batch) == 2
    assert batch[0]["to"] == ["amine@valid.ma"]
    assert batch[1]["to"] == ["sara@valid2.ma"]


def test_dispatcher_payload_construction():
    dispatcher = CampaignDispatcher(
        resend_api_key="test-api-key",
        from_email="Naoufel Chahid <chahid.naoufel@sales.contextelab.ma>",
        reply_to="n.chahid@contextelab.ma",
        dry_run=True,
    )
    contact = NormalizedContact(
        company_name="Atlas Finance",
        first_name="Rachid",
        last_name="El Amrani",
        email="r.amrani@atlasfinance.ma",
        source_file="BDD3",
        is_dirigeant=True,
    )
    payload = dispatcher.build_email_payload(contact, variant="A")
    assert payload["from"] == "Naoufel Chahid <chahid.naoufel@sales.contextelab.ma>"
    assert payload["reply_to"] == "n.chahid@contextelab.ma"
    assert payload["to"] == ["r.amrani@atlasfinance.ma"]
    assert "Atlas Finance" in payload["subject"]
    assert "Rachid" in payload["html"]
    assert payload["tags"] == [{"name": "variant", "value": "A"}]


def test_dispatcher_missing_api_key_in_live_mode():
    with pytest.raises(ValueError, match="API key"):
        CampaignDispatcher(resend_api_key="", dry_run=False)


def test_dispatcher_send_email_dry_run():
    dispatcher = CampaignDispatcher(resend_api_key="dummy", dry_run=True)
    contact = create_mock_contacts(1)[0]
    payload = dispatcher.build_email_payload(contact, variant="B")
    response = dispatcher.send_email(payload)
    assert response["status"] == "simulated"
    assert "dry-run" in response["id"]


@responses.activate
def test_dispatcher_send_email_live_success():
    responses.add(
        responses.POST,
        "https://api.resend.com/emails",
        json={"id": "resend-msg-12345"},
        status=200,
    )
    dispatcher = CampaignDispatcher(resend_api_key="resend-secret-123", dry_run=False)
    contact = create_mock_contacts(1)[0]
    payload = dispatcher.build_email_payload(contact, variant="C")
    response = dispatcher.send_email(payload)
    assert response["id"] == "resend-msg-12345"
    assert responses.calls[0].request.headers["Authorization"] == "Bearer resend-secret-123"


@responses.activate
def test_dispatcher_send_email_live_failure():
    responses.add(
        responses.POST,
        "https://api.resend.com/emails",
        json={"message": "Invalid API key"},
        status=401,
    )
    dispatcher = CampaignDispatcher(resend_api_key="invalid-key", dry_run=False)
    contact = create_mock_contacts(1)[0]
    payload = dispatcher.build_email_payload(contact, variant="A")
    with pytest.raises(Exception):
        dispatcher.send_email(payload)


def test_dispatcher_pacing_delay_called():
    mock_sleep = MagicMock()
    dispatcher = CampaignDispatcher(
        resend_api_key="dummy",
        dry_run=True,
        delay_seconds=240.0,
        sleeper=mock_sleep,
    )
    contacts = create_mock_contacts(3)
    batch = dispatcher.prepare_batch(contacts, batch_size=3)
    summary = dispatcher.dispatch_batch(batch)
    assert summary["sent"] == 3
    # 3 emails dispatched -> 2 sleeps between them
    assert mock_sleep.call_count == 2
    mock_sleep.assert_called_with(240.0)


@responses.activate
def test_dispatcher_circuit_breaker_aborts_when_failure_rate_exceeds_threshold():
    """
    Test that when failure rate exceeds 2% (after min attempts),
    the circuit breaker triggers and halts sending the rest of the batch.
    """
    dispatcher = CampaignDispatcher(
        resend_api_key="secret",
        dry_run=False,
        delay_seconds=0.0,
        sleeper=lambda _: None,
        circuit_breaker_threshold=0.02,
        min_attempts=5,
    )
    # Total batch of 15 contacts
    contacts = create_mock_contacts(15)
    batch = dispatcher.prepare_batch(contacts, batch_size=15)

    # Responses: first 4 succeed, 5th fails
    # At attempt 5: 1 failure / 5 attempts = 20% > 2% -> Circuit breaker trips!
    responses.add(responses.POST, "https://api.resend.com/emails", json={"id": "1"}, status=200)
    responses.add(responses.POST, "https://api.resend.com/emails", json={"id": "2"}, status=200)
    responses.add(responses.POST, "https://api.resend.com/emails", json={"id": "3"}, status=200)
    responses.add(responses.POST, "https://api.resend.com/emails", json={"id": "4"}, status=200)
    responses.add(responses.POST, "https://api.resend.com/emails", json={"error": "Rate limit / bounce"}, status=429)

    summary = dispatcher.dispatch_batch(batch)
    assert summary["circuit_breaker_triggered"] is True
    assert summary["attempted"] == 5
    assert summary["sent"] == 4
    assert summary["failed"] == 1
    # Only 5 HTTP requests were made out of 15 contacts in the batch
    assert len(responses.calls) == 5


def test_dispatcher_circuit_breaker_not_tripped_when_all_succeed():
    dispatcher = CampaignDispatcher(
        resend_api_key="mock",
        dry_run=True,
        delay_seconds=0.0,
        sleeper=lambda _: None,
        circuit_breaker_threshold=0.02,
        min_attempts=5,
    )
    contacts = create_mock_contacts(10)
    batch = dispatcher.prepare_batch(contacts, batch_size=10)
    summary = dispatcher.dispatch_batch(batch)
    assert summary["circuit_breaker_triggered"] is False
    assert summary["sent"] == 10
    assert summary["failed"] == 0


@responses.activate
def test_dispatcher_circuit_breaker_raises_exception():
    dispatcher = CampaignDispatcher(
        resend_api_key="secret",
        dry_run=False,
        delay_seconds=0.0,
        sleeper=lambda _: None,
        circuit_breaker_threshold=0.02,
        min_attempts=2,
    )
    contacts = create_mock_contacts(5)
    batch = dispatcher.prepare_batch(contacts, batch_size=5)

    responses.add(responses.POST, "https://api.resend.com/emails", json={"id": "1"}, status=200)
    responses.add(responses.POST, "https://api.resend.com/emails", json={"error": "fatal"}, status=500)

    with pytest.raises(CircuitBreakerError, match="CIRCUIT BREAKER TRIGGERED"):
        dispatcher.dispatch_batch(batch, raise_on_circuit_breaker=True)


def test_build_email_payload_raises_on_missing_email():
    dispatcher = CampaignDispatcher(resend_api_key="dummy", dry_run=True)
    contact = NormalizedContact(
        company_name="No Email Co",
        email=None,
        source_file="BDD1",
    )
    with pytest.raises(ValueError, match="active email"):
        dispatcher.build_email_payload(contact, variant="A")


def test_cli_execution_dry_run(monkeypatch, capsys):
    from scripts.campaign_dispatcher import main
    # Mock parse_args and load_eligible_contacts
    contacts = create_mock_contacts(5)
    monkeypatch.setattr("scripts.campaign_dispatcher.load_eligible_contacts", lambda **kw: contacts)
    monkeypatch.setattr("sys.argv", ["campaign_dispatcher.py", "--dry-run", "--batch-size", "5"])

    main()
    captured = capsys.readouterr()
    assert "SIMULATION MODE (DRY-RUN)" in captured.out
    assert "Total in Batch:     5" in captured.out
    assert "Sent successfully:  5" in captured.out
    assert "Circuit Breaker:    ✅ OK" in captured.out

