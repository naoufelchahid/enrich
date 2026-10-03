import pytest
import responses
from scripts.staging_db import init_staging_db, insert_staging_leads
from scripts.neverbounce_client import NeverBounceClient

@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_nb.sqlite")
    conn = init_staging_db(db_file)
    conn.close()
    return db_file

@responses.activate
def test_neverbounce_check_email_valid():
    responses.add(
        responses.GET,
        "https://api.neverbounce.com/v4/single/check",
        json={"status": "success", "result": "valid", "execution_time": 120},
        status=200,
    )
    client = NeverBounceClient(api_key="nb_test_key_123")
    result = client.check_email("ceo@test.ma")
    assert result["status"] == "VALID"
    assert result["raw_result"] == "valid"

@responses.activate
def test_neverbounce_check_email_invalid():
    responses.add(
        responses.GET,
        "https://api.neverbounce.com/v4/single/check",
        json={"status": "success", "result": "invalid"},
        status=200,
    )
    client = NeverBounceClient(api_key="nb_test_key_123")
    result = client.check_email("fake@nonexistentdomain.ma")
    assert result["status"] == "INVALID"

def test_audit_staging_db(temp_db):
    leads = [
        {"source_file": "BDD.xlsx", "raw_email": "  good@valid.ma  ", "job_title": "DG"},
        {"source_file": "BDD.xlsx", "raw_email": "bad@invalid.ma", "job_title": "Manager"},
        {"source_file": "BDD.xlsx", "raw_email": None, "job_title": "Commercial"},
    ]
    insert_staging_leads(temp_db, leads)

    def mock_check(email):
        if "good" in email:
            return {"status": "VALID", "raw_result": "valid"}
        return {"status": "INVALID", "raw_result": "invalid"}

    client = NeverBounceClient(dry_run=True)
    client.check_email = mock_check

    stats = client.audit_staging_db(temp_db, batch_size=10)
    assert stats["processed"] == 2
    assert stats["valid"] == 1
    assert stats["invalid"] == 1

    import sqlite3
    conn = sqlite3.connect(temp_db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM staging_leads ORDER BY id").fetchall()]
    conn.close()
    assert rows[0]["clean_email"] == "good@valid.ma"
    assert rows[0]["neverbounce_status"] == "VALID"
    assert rows[0]["enrichment_status"] == "READY_FOR_CRM"
    assert rows[1]["clean_email"] is None
    assert rows[1]["neverbounce_status"] == "INVALID"
    assert rows[1]["enrichment_status"] == "NEEDS_ENRICHMENT"
    assert rows[2]["clean_email"] is None
    assert rows[2]["neverbounce_status"] == "PENDING"

def test_check_email_dry_run():
    client = NeverBounceClient(dry_run=True)
    result = client.check_email("test@example.com")
    assert result["status"] == "VALID"
    assert result["raw_result"] == "valid"

def test_check_email_malformed():
    client = NeverBounceClient(api_key="key")
    result_empty = client.check_email("")
    assert result_empty["status"] == "INVALID"
    assert "error" in result_empty

    result_none = client.check_email(None)
    assert result_none["status"] == "INVALID"

    result_no_at = client.check_email("invalidemail.com")
    assert result_no_at["status"] == "INVALID"

@responses.activate
def test_check_email_api_error():
    responses.add(
        responses.GET,
        "https://api.neverbounce.com/v4/single/check",
        status=500,
    )
    client = NeverBounceClient(api_key="key")
    result = client.check_email("test@example.com")
    assert result["status"] == "UNKNOWN"
    assert result["raw_result"] == "error"
    assert "error" in result

@responses.activate
def test_check_email_api_auth_failure():
    responses.add(
        responses.GET,
        "https://api.neverbounce.com/v4/single/check",
        json={"status": "auth_failure", "message": "Authentication failed"},
        status=200,
    )
    client = NeverBounceClient(api_key="bad_key")
    result = client.check_email("test@example.com")
    assert result["status"] == "UNKNOWN"
    assert result["raw_result"] == "error"
    assert result["error"] == "Authentication failed"

@responses.activate
def test_check_email_api_insufficient_credits():
    responses.add(
        responses.GET,
        "https://api.neverbounce.com/v4/single/check",
        json={"status": "insufficient_credits", "message": "Insufficient credits"},
        status=200,
    )
    client = NeverBounceClient(api_key="key_no_credits")
    result = client.check_email("test@example.com")
    assert result["status"] == "UNKNOWN"
    assert result["raw_result"] == "error"
    assert result["error"] == "Insufficient credits"
