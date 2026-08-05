import os
import sys
import argparse
import logging
import smtplib
from email.message import EmailMessage
from typing import List, Dict, Any

from enricher.crm_client import TwentyCRMClient

# Try to import pywhatkit, but it might fail or block in headless environments
try:
    import pywhatkit
except ImportError:
    pywhatkit = None

OUTREACH_LIMIT = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def get_pending_outreach(crm: TwentyCRMClient, limit: int) -> List[Dict[str, Any]]:
    """Fetch people ready for outreach."""
    filters = {
        "contactStatus": "pending",
        "enrichmentStatus": "person_enriched"
    }
    return crm.list_people(limit=limit, filters=filters)

def send_email(to_email: str, subject: str, body: str, config: Dict[str, str]) -> bool:
    """Send an email using SMTP."""
    try:
        msg = EmailMessage()
        msg.set_content(body)
        msg['Subject'] = subject
        msg['From'] = config['SMTP_SENDER']
        msg['To'] = to_email

        server = config['SMTP_SERVER']
        port = int(config.get('SMTP_PORT', 587))
        user = config['SMTP_USER']
        password = config['SMTP_PASS']

        with smtplib.SMTP(server, port) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
            
        logger.info(f"Email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        return False

def send_whatsapp(phone_number: str, message: str) -> bool:
    """Send a WhatsApp message using pywhatkit."""
    if os.getenv("MOCK_WHATSAPP") == "true":
        logger.info(f"[MOCK] WhatsApp message sent to {phone_number}: {message}")
        return True

    if pywhatkit is None:
        logger.warning("pywhatkit is not installed. Skipping WhatsApp message.")
        return False

    try:
        # sendwhatmsg_instantly(phone_no, message, wait_time=15, tab_close=False, close_time=3)
        pywhatkit.sendwhatmsg_instantly(phone_number, message, wait_time=15, tab_close=True, close_time=3)
        logger.info(f"WhatsApp message sent to {phone_number}")
        return True
    except Exception as e:
        logger.error(f"Failed to send WhatsApp message to {phone_number}: {e}")
        return False

def process_outreach(crm: TwentyCRMClient, person: Dict[str, Any], config: Dict[str, str]) -> bool:
    """Process outreach for a single person."""
    person_id = person.get("id")
    first_name = person.get("name", {}).get("firstName", "")
    full_name = f"{first_name} {person.get('name', {}).get('lastName', '')}".strip()
    
    emails = person.get("emails", {})
    primary_email = emails.get("primaryEmail")
    
    phones = person.get("phones", {})
    primary_phone = phones.get("primaryPhoneNumber")
    
    if not primary_email and not primary_phone:
        logger.warning(f"No email or phone for {full_name}. Failing outreach.")
        crm.update_person(person_id, {"contactStatus": "failed"})
        return False

    email_success = False
    if primary_email:
        subject = f"Hello {first_name}, let's connect!"
        body = f"Hi {first_name},\n\nI noticed your profile and wanted to reach out.\n\nBest,\nThe Team"
        email_success = send_email(primary_email, subject, body, config)

    wa_success = False
    if primary_phone:
        message = f"Hi {first_name}, I wanted to reach out regarding a potential collaboration. Let me know if you have a moment to chat!"
        wa_success = send_whatsapp(primary_phone, message)

    if email_success or wa_success:
        logger.info(f"Outreach successful for {full_name}")
        crm.update_person(person_id, {"contactStatus": "contacted"})
        return True
    else:
        logger.error(f"Outreach failed completely for {full_name}")
        crm.update_person(person_id, {"contactStatus": "failed"})
        return False

def main():
    parser = argparse.ArgumentParser(description="Enrichment Pipeline: Outreach Automation")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of people to process")
    args = parser.parse_args()

    api_url = os.getenv("TWENTY_API_BASE_URL")
    api_key = os.getenv("TWENTY_API_KEY")
    
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = os.getenv("SMTP_PORT", "587")
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    smtp_sender = os.getenv("SMTP_SENDER")

    if not all([api_url, api_key]):
        logger.error("Missing CRM credentials (TWENTY_API_BASE_URL, TWENTY_API_KEY)")
        sys.exit(1)

    if not all([smtp_server, smtp_user, smtp_pass, smtp_sender]):
        logger.error("Missing SMTP credentials (SMTP_SERVER, SMTP_USER, SMTP_PASS, SMTP_SENDER)")
        sys.exit(1)

    smtp_config = {
        "SMTP_SERVER": smtp_server,
        "SMTP_PORT": smtp_port,
        "SMTP_USER": smtp_user,
        "SMTP_PASS": smtp_pass,
        "SMTP_SENDER": smtp_sender
    }

    crm = TwentyCRMClient(api_url, api_key)
    
    limit = min(args.limit, OUTREACH_LIMIT)
    people_to_process = get_pending_outreach(crm, limit=limit)
    logger.info(f"Found {len(people_to_process)} people for outreach (capped at {limit}).")

    for person in people_to_process:
        process_outreach(crm, person, smtp_config)

if __name__ == "__main__":
    main()
