import sqlite3
from pathlib import Path
from typing import Any, Dict, List
from scripts.outreach_journal import install_outbox, flush_journal

EXECUTIVE_KEYWORDS = ["pdg", "dg", "fondateur", "founder", "ceo", "gérant", "gerant", "président", "president", "managing director"]
DIRECTOR_KEYWORDS = ["directeur", "director", "responsable", "vp", "head", "lead"]

def install_operator_view(conn: sqlite3.Connection) -> None:
    """A readable, read-only projection; technical evidence stays in its tables."""
    conn.execute('DROP VIEW IF EXISTS vue_contacts')
    conn.execute('''CREATE VIEW vue_contacts AS
        SELECT id, first_name AS prenom, last_name AS nom,
            company_name AS entreprise, job_title AS fonction,
            coalesce(clean_email,raw_email) AS email,
            CASE email_check_status
                WHEN 'VALID' THEN 'Valide'
                WHEN 'CATCHALL' THEN 'Catch-all'
                WHEN 'INVALID' THEN 'Rejetée'
                WHEN 'SUBMITTED' THEN 'Vérification en cours'
                WHEN 'PENDING' THEN 'À vérifier'
                ELSE email_check_status END AS statut_email,
            email_validation_source AS verificateur,
            email_validated_at AS date_verification,
            CASE linkedin_search_status
                WHEN 'CONFIRMED' THEN 'Confirmé'
                WHEN 'PENDING' THEN 'À rechercher'
                ELSE linkedin_search_status END AS statut_linkedin,
            linkedin_url,
            CASE
                WHEN workflow_wait_reason='REPLACEMENT_EMAIL_REQUIRED' THEN 'Enrichissement email'
                WHEN workflow_wait_reason='NO_BUSINESS_EMAIL_FOUND' THEN 'Adresse professionnelle introuvable'
                WHEN workflow_wait_reason IS NOT NULL THEN 'À revoir'
                WHEN email_check_status IS NOT 'VALID'
                    OR email_check_status IS NULL THEN 'Qualification email'
                WHEN crm_person_id IS NULL THEN 'Transfert Twenty'
                ELSE 'Transféré au CRM' END AS prochaine_etape,
            workflow_wait_reason AS motif_attente,
            source_file AS source
        FROM staging_leads''')

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

def init_staging_db(db_path: str = "archives/essais/ancien-sas/staging_leads.sqlite") -> sqlite3.Connection:
    from scripts.backend_guard import require_legacy
    require_legacy(db_path, lane='qualification')
    conn = sqlite3.connect(db_path)
    old_columns = {r[1] for r in conn.execute('PRAGMA table_info(staging_leads)')}
    if old_columns and 'journal_contact_id' not in old_columns and db_path != ':memory:':
        backup_path = str(Path(db_path).resolve()) + '.before-outreach-journal.bak'
        if not Path(backup_path).exists():
            backup = sqlite3.connect(backup_path)
            try:
                conn.backup(backup)
            finally:
                backup.close()
    if old_columns and 'email_validation_source' not in old_columns and db_path != ':memory:':
        backup_path = str(Path(db_path).resolve()) + '.before-direct-enrichment.bak'
        if not Path(backup_path).exists():
            backup = sqlite3.connect(backup_path)
            try:
                conn.backup(backup)
            finally:
                backup.close()
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
            is_enriched_linkedin BOOLEAN DEFAULT 0,
            priority_score INTEGER DEFAULT 0,
            crm_person_id TEXT,
            crm_synced_at TIMESTAMP,
            recent_posts TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    try:
        conn.execute("ALTER TABLE staging_leads ADD COLUMN recent_posts TEXT;")
    except sqlite3.OperationalError:
        pass

    # Additive, idempotent migration. Existing VALID rows get no invented proof.
    columns = {row[1] for row in conn.execute('PRAGMA table_info(staging_leads)')}
    additions = {
        'website': 'TEXT',
        'company_domain': 'TEXT',
        'company_domain_source': 'TEXT',
        'original_email': 'TEXT',
        'email_check_status': "TEXT NOT NULL DEFAULT 'PENDING'",
        'email_checked_input': 'TEXT',
        'email_check_reason': 'TEXT',
        'contact_discovery_status': "TEXT NOT NULL DEFAULT 'PENDING'",
        'profile_status': "TEXT NOT NULL DEFAULT 'PENDING'",
        'linkedin_profile_json': 'TEXT',
        'linkedin_comments_json': 'TEXT',
        'linkedin_data_source': 'TEXT',
        'linkedin_observed_at': 'TEXT',
        'linkedin_search_status': "TEXT NOT NULL DEFAULT 'PENDING'",
        'linkedin_search_candidates': 'TEXT',
        'linkedin_search_observed_at': 'TEXT',
        'posts_status': "TEXT NOT NULL DEFAULT 'PENDING'",
        'email_validation_source': 'TEXT',
        'validated_email': 'TEXT',
        'email_validated_at': 'TEXT',
        'outreach_status': "TEXT NOT NULL DEFAULT 'PENDING'",
        'activity_sector': 'TEXT',
        'city': 'TEXT',
        'address': 'TEXT',
        'civility': 'TEXT',
        'is_dirigeant': 'INTEGER NOT NULL DEFAULT 0',
        'source_data_json': 'TEXT',
        'import_key': 'TEXT',
        'identity_score_json': 'TEXT',
        'verification_verdict': 'TEXT',
        'workflow_wait_reason': 'TEXT',
    }
    for name, declaration in additions.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE staging_leads ADD COLUMN {name} {declaration}')
    conn.execute('CREATE INDEX IF NOT EXISTS staging_import_key ON staging_leads(import_key)')

    if 'original_email' not in columns:
        conn.execute('UPDATE staging_leads SET original_email=raw_email')
    conn.execute("""CREATE TABLE IF NOT EXISTS email_suppressions (
        email TEXT PRIMARY KEY, reason TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    install_outbox(conn)
    install_operator_view(conn)
    conn.commit()
    try:
        flush_journal(db_path)
    except Exception:
        conn.close()
        raise
    return conn

def insert_staging_leads(db_path: str, leads: List[Dict[str, Any]]) -> int:
    from scripts.backend_guard import require_legacy
    require_legacy(db_path, lane='qualification')
    conn = sqlite3.connect(db_path)
    inserted = 0
    for lead in leads:
        initial_score = calculate_priority_score(lead)
        conn.execute(
            """
            INSERT INTO staging_leads (
                source_file, first_name, last_name, company_name, job_title,
                phone_standard, phone_mobile, raw_email, clean_email,
                linkedin_url, neverbounce_status, enrichment_status, priority_score, website, original_email,
                company_domain, company_domain_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                lead.get('website'),
                lead.get('original_email', lead.get('raw_email')),
                lead.get('company_domain'),
                lead.get('company_domain_source'),
            ),
        )
        inserted += 1
        import json
        conn.execute('''UPDATE staging_leads SET activity_sector=?,city=?,address=?,civility=?,
            is_dirigeant=?,source_data_json=?,import_key=? WHERE id=last_insert_rowid()''',
            (lead.get('activity_sector'), lead.get('city'), lead.get('address'), lead.get('civility'),
             bool(lead.get('is_dirigeant')), json.dumps(lead, ensure_ascii=False, default=str), lead.get('import_key')))
    conn.commit()
    conn.close()
    flush_journal(db_path)
    return inserted

def mark_lead_synced(db_path: str, lead_id: int, crm_person_id: str) -> None:
    from scripts.backend_guard import require_legacy
    require_legacy(db_path, lane='crm')
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
    flush_journal(db_path)
