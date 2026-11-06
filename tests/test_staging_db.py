import os
import sqlite3
import pytest
from scripts.staging_db import (
    init_staging_db,
    calculate_priority_score,
    insert_staging_leads,
    mark_lead_synced,
)

@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_staging.sqlite")
    conn = init_staging_db(db_file)
    conn.close()
    return db_file

def test_priority_score_calculation():
    # Dirigeant exécutif (+100), Valid (+50), Mobile GSM (+30), LinkedIn (+20) -> 200
    lead_ceo = {
        "job_title": "Président Directeur Général",
        "neverbounce_status": "VALID",
        "phone_mobile": "+212661234567",
        "linkedin_url": "https://linkedin.com/in/pdg-test",
    }
    assert calculate_priority_score(lead_ceo) == 200

    # Directeur (+70), Valid (+50), Sans mobile ni linkedin -> 120
    lead_dir = {
        "job_title": "Directeur Commercial",
        "neverbounce_status": "VALID",
        "phone_mobile": None,
        "linkedin_url": "",
    }
    assert calculate_priority_score(lead_dir) == 120

    # Simple contact sans rôle clé (0), PENDING (0), fixe uniquement -> 0
    lead_basic = {
        "job_title": "Assistant",
        "neverbounce_status": "PENDING",
        "phone_mobile": "0522000000",
        "linkedin_url": None,
    }
    assert calculate_priority_score(lead_basic) == 0

def test_insert_and_query_staging_leads(temp_db):
    leads = [
        {
            "source_file": "BDD1.xlsx",
            "first_name": "Karim",
            "last_name": "Alami",
            "company_name": "Atlas Tech",
            "job_title": "Fondateur & DG",
            "phone_standard": "0522112233",
            "phone_mobile": "0661998877",
            "raw_email": "karim@atlastech.ma",
            "linkedin_url": "https://linkedin.com/in/karim-alami",
        },
        {
            "source_file": "BDD1.xlsx",
            "first_name": "Sara",
            "last_name": "Tazi",
            "company_name": "Maroc Conseil",
            "job_title": "Responsable RH",
            "phone_standard": "0522445566",
            "phone_mobile": "0770112233",
            "raw_email": "sara@marocconseil.ma",
            "linkedin_url": "",
        },
    ]
    count = insert_staging_leads(temp_db, leads)
    assert count == 2

    with sqlite3.connect(temp_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM staging_leads ORDER BY id").fetchall()
        assert [row['first_name'] for row in rows] == ['Karim', 'Sara']
        assert all(row['neverbounce_status'] == 'PENDING' for row in rows)
        assert all(row['email_validation_source'] is None for row in rows)
    mark_lead_synced(temp_db, lead_id=1, crm_person_id="twenty-person-uuid-123")
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT crm_person_id, crm_synced_at FROM staging_leads WHERE id=1").fetchone()
        assert row[0] == "twenty-person-uuid-123"
        assert row[1]
