import sqlite3
from typing import Any, Dict, List, Optional

EXECUTIVE_KEYWORDS = ["pdg", "dg", "fondateur", "founder", "ceo", "gérant", "gerant", "président", "president", "managing director"]
DIRECTOR_KEYWORDS = ["directeur", "director", "responsable", "vp", "head", "lead"]

def calculate_priority_score(lead: Dict[str, Any]) -> int:
    score = 0
    job = (lead.get("job_title") or "").strip().lower()

    if any(kw in job for kw in EXECUTIVE_KEYWORDS):
        score += 100
    elif any(kw in job for kw in DIRECTOR_KEYWORDS):
        score += 70

    if lead.get("neverbounce_status") == "VALID":
        score += 50

    mobile = (lead.get("phone_mobile") or "").strip()
    if mobile.startswith("06") or mobile.startswith("07") or mobile.startswith("+2126") or mobile.startswith("+2127"):
        score += 30

    linkedin = (lead.get("linkedin_url") or "").strip()
    if linkedin and "linkedin.com" in linkedin:
        score += 20

    return score

def init_staging_db(db_path: str = "staging_leads.sqlite") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS staging_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            company_name TEXT,
            job_title TEXT,
            phone_standard TEXT,
            phone_mobile TEXT,
            raw_email TEXT,
            clean_email TEXT,
            linkedin_url TEXT,
            neverbounce_status TEXT DEFAULT 'PENDING',
            enrichment_status TEXT DEFAULT 'PENDING',
            priority_score INTEGER DEFAULT 0,
            crm_person_id TEXT,
            crm_synced_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()
    return conn

def insert_staging_leads(db_path: str, leads: List[Dict[str, Any]]) -> int:
    conn = sqlite3.connect(db_path)
    inserted = 0
    for lead in leads:
        initial_score = calculate_priority_score(lead)
        conn.execute(
            """
            INSERT INTO staging_leads (
                source_file, first_name, last_name, company_name, job_title,
                phone_standard, phone_mobile, raw_email, clean_email,
                linkedin_url, neverbounce_status, enrichment_status, priority_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lead.get("source_file", "unknown"),
                lead.get("first_name"),
                lead.get("last_name"),
                lead.get("company_name"),
                lead.get("job_title"),
                lead.get("phone_standard"),
                lead.get("phone_mobile"),
                lead.get("raw_email"),
                lead.get("clean_email"),
                lead.get("linkedin_url"),
                lead.get("neverbounce_status", "PENDING"),
                lead.get("enrichment_status", "PENDING"),
                initial_score,
            ),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return inserted

def update_lead_neverbounce_result(
    db_path: str,
    lead_id: int,
    nb_status: str,
    clean_email: Optional[str] = None,
) -> None:
    norm_status = nb_status.upper().strip()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    lead = dict(conn.execute("SELECT * FROM staging_leads WHERE id = ?", (lead_id,)).fetchone() or {})
    if not lead:
        conn.close()
        return

    if norm_status == "VALID":
        email_to_set = clean_email or lead.get("raw_email")
        enrich_status = "READY_FOR_CRM"
    elif norm_status in ("INVALID", "DISPOSABLE"):
        email_to_set = None
        enrich_status = "NEEDS_ENRICHMENT"
    else:  # CATCHALL or UNKNOWN
        email_to_set = lead.get("raw_email")
        enrich_status = "PENDING"

    lead["neverbounce_status"] = norm_status
    lead["clean_email"] = email_to_set
    lead["enrichment_status"] = enrich_status
    new_score = calculate_priority_score(lead)

    conn.execute(
        """
        UPDATE staging_leads
        SET neverbounce_status = ?, clean_email = ?, enrichment_status = ?, priority_score = ?
        WHERE id = ?
        """,
        (norm_status, email_to_set, enrich_status, new_score, lead_id),
    )
    conn.commit()
    conn.close()

def get_top_priority_leads(db_path: str, limit: int = 25) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        """
        SELECT * FROM staging_leads
        WHERE crm_person_id IS NULL
          AND neverbounce_status = 'VALID'
          AND enrichment_status IN ('READY_FOR_CRM', 'ENRICHED')
        ORDER BY priority_score DESC, id ASC
        LIMIT ?
        """,
        (limit,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def mark_lead_synced(db_path: str, lead_id: int, crm_person_id: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        UPDATE staging_leads
        SET crm_person_id = ?, crm_synced_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (crm_person_id, lead_id),
    )
    conn.commit()
    conn.close()
