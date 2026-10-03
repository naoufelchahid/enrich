import os
import sqlite3
import pytest
from scripts.staging_db import (
    init_staging_db,
    calculate_priority_score,
    insert_staging_leads,
    update_lead_neverbounce_result,
    get_top_priority_leads,
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

    # Initialement PENDING -> Non retournés par get_top_priority_leads
    priority_leads = get_top_priority_leads(temp_db, limit=10)
    assert len(priority_leads) == 0

    # Valider Karim via NeverBounce
    update_lead_neverbounce_result(temp_db, lead_id=1, nb_status="VALID", clean_email="karim@atlastech.ma")

    priority_leads = get_top_priority_leads(temp_db, limit=10)
    assert len(priority_leads) == 1
    assert priority_leads[0]["first_name"] == "Karim"
    assert priority_leads[0]["neverbounce_status"] == "VALID"
    assert priority_leads[0]["clean_email"] == "karim@atlastech.ma"
    assert priority_leads[0]["enrichment_status"] == "READY_FOR_CRM"
    assert priority_leads[0]["priority_score"] == 200

    # Marquer comme synchronisé dans Twenty
    mark_lead_synced(temp_db, lead_id=1, crm_person_id="twenty-person-uuid-123")
    assert len(get_top_priority_leads(temp_db, limit=10)) == 0

def test_neverbounce_invalid_purges_email(temp_db):
    leads = [
        {
            "source_file": "BDD2.xlsx",
            "first_name": "Yassine",
            "last_name": "Berrada",
            "company_name": "Berrada Immo",
            "job_title": "Gérant",
            "raw_email": "fake@berradaimmo.ma",
        }
    ]
    insert_staging_leads(temp_db, leads)
    update_lead_neverbounce_result(temp_db, lead_id=1, nb_status="INVALID")

    conn = sqlite3.connect(temp_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM staging_leads WHERE id = 1").fetchone()
    conn.close()

    assert row["neverbounce_status"] == "INVALID"
    assert row["clean_email"] is None
    assert row["enrichment_status"] == "NEEDS_ENRICHMENT"
