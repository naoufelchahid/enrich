import os
import pandas as pd
import logging
import re
import argparse
from enricher.crm_client import TwentyCRMClient

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def is_valid_str(val):
    if pd.isna(val):
        return False
    val_str = str(val).strip()
    if not val_str or val_str.lower() == 'nan':
        return False
    return True

def sanitize_phone(phone):
    if not phone:
        return ""
    # Remove all non-digit characters except +
    cleaned = re.sub(r'[^\d+]', '', str(phone))
    if not cleaned:
        return ""
    if cleaned.startswith('+'):
        return cleaned
    if cleaned.startswith('212'):
        return '+' + cleaned
    if cleaned.startswith('0'):
        cleaned = cleaned[1:]
    if cleaned.startswith('5') or cleaned.startswith('6') or cleaned.startswith('7'):
        return '+212' + cleaned
    return cleaned

def load_env():
    env_file = ".env"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k] = v

def main():
    parser = argparse.ArgumentParser(description="Import BDD data into Twenty CRM")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of records to import (0 for unlimited)")
    args = parser.parse_args()
    
    limit = args.limit

    load_env()
    api_url = os.environ.get("TWENTY_API_BASE_URL", "http://localhost:3005/rest")
    api_key = os.environ.get("TWENTY_API_KEY")
    
    if not api_key:
        logger.error("TWENTY_API_KEY is not set in .env")
        return
        
    crm = TwentyCRMClient(api_base_url=api_url, api_key=api_key)
    
    file_path = "by_direction/bdd/BDD6.xls"
    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        return
        
    processed_count = 0
    for _, row in df.iterrows():
        if limit and processed_count >= limit:
            logger.info(f"Reached limit of {limit} entries. Exiting.")
            break
            
        societe = row.get('Société')
        if not is_valid_str(societe):
            continue
        societe = str(societe).strip()
            
        fonction = row.get('Fonction')
        if is_valid_str(fonction):
            fonction = str(fonction).strip()
            fonction_lower = fonction.lower()
            ignore_roles = ['etudiant', 'etudiante', 'étudiant', 'étudiante', 'eleve ingenieur', 'élève ingénieur', 'eleve-ingenieur', 'élève-ingénieur']
            if any(role in fonction_lower for role in ignore_roles):
                continue
        else:
            fonction = ''
            
        g = row.get('G')
        g = str(g).strip() if is_valid_str(g) else ''
            
        prenom_nom = row.get('Prénom et Nom')
        prenom_nom = str(prenom_nom).strip() if is_valid_str(prenom_nom) else ''
            
        tel = row.get('TEL')
        tel = str(tel).strip() if is_valid_str(tel) else ''
        tel = sanitize_phone(tel)
            
        email = row.get('E-mail')
        email = str(email).strip() if is_valid_str(email) else ''
            
        parts = prenom_nom.split(' ', 1)
        first_name = parts[0] if len(parts) > 0 else ''
        last_name = parts[1] if len(parts) > 1 else ''
        
        if g:
            first_name = f"{g} {first_name}".strip()
            
        # Create company
        company_data = {
            "name": societe,
            "enrichmentStatus": "pending"
        }
        company_res = crm.create_company(company_data)
        
        if not company_res:
            logger.warning(f"Skipping person creation because company creation failed for: {societe}")
            continue
            
        company_id = company_res.get('id')
        
        # Create person
        person_data = {
            "name": {
                "firstName": first_name,
                "lastName": last_name
            },
            "jobTitle": fonction,
            "enrichmentStatus": "pending"
        }
        
        if email:
            person_data["emails"] = {"primaryEmail": email}
        if tel:
            person_data["phones"] = {"primaryPhoneNumber": tel}
            
        if company_id:
            person_data["companyId"] = company_id
            
        crm.create_person(person_data)
        logger.info(f"Successfully processed {prenom_nom} at {societe}")
        
        processed_count += 1

if __name__ == "__main__":
    main()
