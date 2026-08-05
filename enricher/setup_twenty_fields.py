import os
import requests
import logging

logger = logging.getLogger(__name__)

COMPANY_FIELDS_TO_CREATE = [
    {"name": "ice", "label": "ICE", "type": "TEXT"},
    {"name": "rc", "label": "RC", "type": "TEXT"},
    {"name": "creation", "label": "Création", "type": "TEXT"},
    {"name": "capital", "label": "Capital", "type": "TEXT"},
    {"name": "chiffreDAffaires", "label": "Chiffre D'affaires", "type": "TEXT"},
    {"name": "effectif", "label": "Effectif", "type": "TEXT"},
    {"name": "activity", "label": "Activités", "type": "TEXT"},
    {"name": "phone", "label": "Téléphone", "type": "TEXT"}
]

STATUS_FIELDS = [
    {"name": "enrichmentStatus", "label": "Enrichment Status", "type": "TEXT"},
    {"name": "contactStatus", "label": "Contact Status", "type": "TEXT"}
]

def ensure_twenty_company_fields(api_base_url: str, api_key: str):
    """Ensures all extracted fields exist as custom columns on the company and person objects in Twenty CRM."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    meta_url = f"{api_base_url.rstrip('/')}/metadata/objects"
    try:
        meta_resp = requests.get(meta_url, headers=headers)
        if meta_resp.status_code != 200:
            logger.error(f"Failed to fetch metadata [{meta_resp.status_code}]: {meta_resp.text}")
            return
            
        data = meta_resp.json()
        objects = data.get("data", [])
        
        objects_by_name = {obj.get("nameSingular"): obj for obj in objects}
        
        company_obj = objects_by_name.get("company")
        if company_obj:
            _ensure_fields_for_object(
                api_base_url, headers, company_obj, 
                COMPANY_FIELDS_TO_CREATE + STATUS_FIELDS
            )
        else:
            logger.error("Company object metadata not found in Twenty CRM")
            
        person_obj = objects_by_name.get("person")
        if person_obj:
            _ensure_fields_for_object(
                api_base_url, headers, person_obj, 
                STATUS_FIELDS
            )
        else:
            logger.error("Person object metadata not found in Twenty CRM")

    except Exception as e:
        logger.error(f"Error ensuring Twenty CRM company fields: {e}")

def _ensure_fields_for_object(api_base_url, headers, obj_meta, fields_to_create):
    object_id = obj_meta.get("id")
    object_name = obj_meta.get("nameSingular")
    existing_field_names = {f.get("name") for f in obj_meta.get("fields", [])}
    
    post_field_url = f"{api_base_url.rstrip('/')}/metadata/fields"
    
    for field in fields_to_create:
        fname = field["name"]
        flabel = field["label"]
        ftype = field["type"]
        
        if fname in existing_field_names:
            logger.info(f"Field '{fname}' ({flabel}) already exists on Twenty CRM {object_name} object.")
            continue
            
        payload = {
            "objectMetadataId": object_id,
            "name": fname,
            "label": flabel,
            "type": ftype
        }
        
        resp = requests.post(post_field_url, headers=headers, json=payload)
        if resp.status_code in (200, 201):
            logger.info(f"Created custom field '{fname}' ({flabel}) on Twenty CRM {object_name} object.")
            existing_field_names.add(fname)
        else:
            logger.warning(f"Failed to create field '{fname}' on {object_name}: [{resp.status_code}] {resp.text}")

if __name__ == "__main__":
    from enrich_companies import load_env
    load_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    api_url = os.environ.get("TWENTY_API_BASE_URL", "http://localhost:3005/rest")
    api_key = os.environ.get("TWENTY_API_KEY")
    if api_key:
        ensure_twenty_company_fields(api_url, api_key)
