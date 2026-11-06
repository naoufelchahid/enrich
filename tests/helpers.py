"""Synthetic validation evidence for isolated test databases only."""
import sqlite3
from typing import Optional
from scripts.staging_db import calculate_priority_score

def set_test_validation(
    db_path: str,
    lead_id: int,
    nb_status: str,
    clean_email: Optional[str] = None,
    validation_source: str = 'no2bounce',
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
        SET neverbounce_status = ?, clean_email = ?, enrichment_status = ?, priority_score = ?,
            email_validation_source = ?, validated_email = ?, email_validated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (norm_status, email_to_set, enrich_status, new_score, validation_source,
         email_to_set if norm_status == 'VALID' else None, lead_id),
    )
    conn.commit()
    conn.close()





def set_test_linkedin_confirmation(db_path, lead_id=1, url='https://www.linkedin.com/in/alice-example'):
    """Import synthetic MCP evidence through the production offline validator."""
    import json
    import uuid
    from copy import deepcopy
    from pathlib import Path
    from datetime import datetime, timezone
    from scripts.linkedin_mcp import export_task, import_extraction
    task=export_task(db_path,lead_id)
    section={'status':'NOT_OBSERVED','source_url':None,'text':None,'items':[],'limitation':'Synthetic test fixture'}
    profile={k:deepcopy(section) for k in ('about','experience','education','skills','certifications','languages','services','featured','projects','publications','volunteering','honors','recommendations')}
    profile.update(source_url=url,full_name=task['target']['first_name']+' '+task['target']['last_name'],headline=None,location=None)
    record={'schema_version':'1.0','extraction_id':str(uuid.uuid4()),'acquisition_method':'linkedin_mcp','purpose':'CONTACT',
        'target':task['target'],'observed_at':datetime.now(timezone.utc).isoformat(),
        'identity':{'status':'CONFIRMED','basis':['Synthetic name match','Synthetic company match'],'alternative_urls':[]},
        'linkedin_url':url,'profile':profile,'activity':{'status':'NOT_OBSERVED','source_url':None,'sorted_by':'UNKNOWN',
        'exhaustive':False,'posts':[],'limitation':'Synthetic fixture'},'limitations':['Synthetic, offline test only']}
    path=Path(db_path).parent/(record['extraction_id']+'.json');path.write_text(json.dumps(record))
    try:import_extraction(db_path,path)
    finally:path.unlink()
