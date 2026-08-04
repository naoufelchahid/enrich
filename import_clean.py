import os
import re
import logging
import pandas as pd
from enricher.crm_client import TwentyCRMClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def sanitize_phone(phone):
    if pd.isna(phone) or not str(phone).strip():
        return ""
    cleaned = re.sub(r'[^\d+]', '', str(phone))
    if not cleaned:
        return ""
    if cleaned.startswith('+'):
        return cleaned
    if cleaned.startswith('00212'):
        return '+' + cleaned[2:]
    if cleaned.startswith('212'):
        return '+' + cleaned
    if cleaned.startswith('0'):
        cleaned = cleaned[1:]
    if cleaned.startswith('5') or cleaned.startswith('6') or cleaned.startswith('7'):
        return '+212' + cleaned
    return cleaned

def is_valid_str(val):
    if pd.isna(val):
        return False
    val_str = str(val).strip()
    return bool(val_str and val_str.lower() != 'nan')

def group_company_data(filepath: str) -> dict:
    df = pd.read_excel(filepath)
    grouped = {}
    
    ignore_roles = ['etudiant', 'etudiante', 'étudiant', 'étudiante', 'eleve', 'élève']

    for _, row in df.iterrows():
        societe = row.get('Société')
        if not is_valid_str(societe):
            continue
        
        orig_name = str(societe).strip()
        norm_name = orig_name.lower().strip()
        
        if norm_name not in grouped:
            grouped[norm_name] = {"original_name": orig_name, "people": []}
            
        fonction = str(row.get('Fonction', '')).strip() if is_valid_str(row.get('Fonction')) else ""
        if any(role in fonction.lower() for role in ignore_roles):
            continue
            
        g = str(row.get('G', '')).strip() if is_valid_str(row.get('G')) else ""
        prenom_nom = str(row.get('Prénom et Nom', '')).strip() if is_valid_str(row.get('Prénom et Nom')) else ""
        tel = sanitize_phone(row.get('TEL'))
        email = str(row.get('E-mail', '')).strip() if is_valid_str(row.get('E-mail')) else ""
        
        parts = prenom_nom.split(' ', 1)
        first_name = parts[0].strip() if len(parts) > 0 else ""
        last_name = parts[1].strip() if len(parts) > 1 else ""
        if g:
            first_name = f"{g} {first_name}".strip()
        
        grouped[norm_name]["people"].append({
            "firstName": first_name,
            "lastName": last_name,
            "jobTitle": fonction,
            "phone": tel,
            "email": email
        })
    return grouped

process_excel_data = group_company_data

def main():
    api_url = os.environ.get("TWENTY_API_BASE_URL", "http://localhost:3005/rest")
    api_key = os.environ.get("TWENTY_API_KEY")
    if not api_key:
        logger.error("Missing TWENTY_API_KEY")
        return
        
    crm = TwentyCRMClient(api_url, api_key)
    grouped_data = group_company_data("by_direction/bdd/BDD6.xls")
    
    for norm_name, data in grouped_data.items():
        company_res = crm.create_company({"name": data["original_name"]})
        if not company_res:
            continue
        
        comp_id = company_res.get('id')
        if not comp_id:
            logger.error(f"No company ID returned for {data['original_name']}")
            continue

        for p in data["people"]:
            payload = {
                "name": {"firstName": p["firstName"], "lastName": p["lastName"]},
                "jobTitle": p["jobTitle"],
                "companyId": comp_id
            }
            if p["email"]:
                payload["emails"] = {"primaryEmail": p["email"]}
            if p["phone"]:
                payload["phones"] = {"primaryPhoneNumber": p["phone"]}
            crm.create_person(payload)

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    main()
