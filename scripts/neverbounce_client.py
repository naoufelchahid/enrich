import logging
import os
import sqlite3
from typing import Any, Dict, Optional
import requests
from scripts.staging_db import update_lead_neverbounce_result

logger = logging.getLogger(__name__)
NEVERBOUNCE_API_URL = "https://api.neverbounce.com/v4/single/check"

class NeverBounceClient:
    """Client for NeverBounce API v4 single check and staging database batch auditing."""

    def __init__(self, api_key: Optional[str] = None, dry_run: bool = False):
        self.api_key = api_key or os.getenv("NEVERBOUNCE_API_KEY")
        self.dry_run = dry_run or (self.api_key is None)

    def check_email(self, email: str) -> Dict[str, Any]:
        """Verify a single email address with NeverBounce."""
        if not email or "@" not in email:
            return {"status": "INVALID", "raw_result": "invalid", "error": "Missing or malformed email"}

        if self.dry_run:
            logger.info("[DRY-RUN] Checking email with mock NeverBounce: %s", email)
            return {"status": "VALID", "raw_result": "valid", "execution_time": 0}

        params = {
            "key": self.api_key,
            "email": email.strip(),
        }
        try:
            res = requests.get(NEVERBOUNCE_API_URL, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()
            if data.get("status") != "success":
                msg = data.get("message", "NeverBounce API error")
                logger.error("NeverBounce API error response for %s: %s", email, msg)
                return {"status": "UNKNOWN", "raw_result": "error", "error": msg}

            raw_result = (data.get("result") or "unknown").lower()
            status_map = {
                "valid": "VALID",
                "invalid": "INVALID",
                "disposable": "DISPOSABLE",
                "catchall": "CATCHALL",
                "unknown": "UNKNOWN",
            }
            return {
                "status": status_map.get(raw_result, "UNKNOWN"),
                "raw_result": raw_result,
                "execution_time": data.get("execution_time"),
            }
        except Exception as e:
            logger.error("NeverBounce verification error for %s: %s", email, e)
            return {"status": "UNKNOWN", "raw_result": "error", "error": str(e)}

    def audit_staging_db(self, db_path: str, batch_size: int = 50) -> Dict[str, int]:
        """Audit leads with PENDING NeverBounce status and update clean_email and enrichment_status."""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            SELECT id, raw_email FROM staging_leads
            WHERE neverbounce_status = 'PENDING'
              AND raw_email IS NOT NULL
              AND raw_email != ''
            LIMIT ?
            """,
            (batch_size,),
        )
        leads_to_check = [dict(row) for row in cursor.fetchall()]
        conn.close()

        stats = {"processed": 0, "valid": 0, "invalid": 0, "disposable": 0, "catchall": 0, "unknown": 0}

        for lead in leads_to_check:
            lead_id = lead["id"]
            email = lead["raw_email"]
            result = self.check_email(email)
            st = result["status"]
            clean_email = email.strip() if st == "VALID" else None
            update_lead_neverbounce_result(db_path, lead_id, st, clean_email=clean_email)
            stats["processed"] += 1
            key = st.lower()
            if key in stats:
                stats[key] += 1

        return stats
