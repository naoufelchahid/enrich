import pytest
from unittest.mock import MagicMock, patch
from outreach import get_pending_outreach, send_email, send_whatsapp, process_outreach

def test_get_pending_outreach():
    mock_crm = MagicMock()
    mock_crm.list_people.return_value = [
        {"id": "p1"}, {"id": "p2"}
    ]
    
    people = get_pending_outreach(mock_crm, limit=10)
    
    mock_crm.list_people.assert_called_once_with(
        limit=10, 
        filters={"contactStatus": "pending", "enrichmentStatus": "person_enriched"}
    )
    assert len(people) == 2

@patch("outreach.smtplib.SMTP")
def test_send_email_success(mock_smtp_class):
    mock_smtp = MagicMock()
    mock_smtp_class.return_value.__enter__.return_value = mock_smtp
    
    # We will pass a config dictionary
    config = {
        "SMTP_SERVER": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "user",
        "SMTP_PASS": "pass",
        "SMTP_SENDER": "sender@example.com"
    }
    
    result = send_email("target@example.com", "Test Subject", "Test Body", config)
    
    assert result is True
    mock_smtp_class.assert_called_once_with("smtp.example.com", 587)
    mock_smtp.starttls.assert_called_once()
    mock_smtp.login.assert_called_once_with("user", "pass")
    mock_smtp.send_message.assert_called_once()

@patch("outreach.pywhatkit")
def test_send_whatsapp_success(mock_pywhatkit):
    result = send_whatsapp("+1234567890", "Test Message")
    
    assert result is True
    mock_pywhatkit.sendwhatmsg_instantly.assert_called_once_with("+1234567890", "Test Message", wait_time=15, tab_close=True, close_time=3)

@patch("outreach.send_email")
@patch("outreach.send_whatsapp")
def test_process_outreach_success(mock_send_whatsapp, mock_send_email):
    mock_crm = MagicMock()
    mock_send_email.return_value = True
    mock_send_whatsapp.return_value = True
    
    config = {
        "SMTP_SERVER": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "user",
        "SMTP_PASS": "pass",
        "SMTP_SENDER": "sender@example.com"
    }
    
    person = {
        "id": "p1",
        "name": {"firstName": "John", "lastName": "Doe"},
        "emails": [{"primaryEmail": "john@example.com"}],
        "phones": [{"primaryPhone": "+1234567890"}]
    }
    
    result = process_outreach(mock_crm, person, config)
    
    assert result is True
    mock_send_email.assert_called_once()
    mock_send_whatsapp.assert_called_once()
    mock_crm.update_person.assert_called_once_with("p1", {"contactStatus": "contacted"})

@patch("outreach.send_email")
@patch("outreach.send_whatsapp")
def test_process_outreach_missing_contact_info(mock_send_whatsapp, mock_send_email):
    mock_crm = MagicMock()
    
    config = {} # Not used because it should fail early or skip
    
    person = {
        "id": "p1",
        "name": {"firstName": "John", "lastName": "Doe"},
        "emails": [],
        "phones": []
    }
    
    result = process_outreach(mock_crm, person, config)
    
    assert result is False
    mock_send_email.assert_not_called()
    mock_send_whatsapp.assert_not_called()
    mock_crm.update_person.assert_called_once_with("p1", {"contactStatus": "failed"})

@patch("outreach.send_email")
@patch("outreach.send_whatsapp")
def test_process_outreach_email_fails(mock_send_whatsapp, mock_send_email):
    mock_crm = MagicMock()
    mock_send_email.return_value = False
    mock_send_whatsapp.return_value = True
    
    config = {
        "SMTP_SERVER": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "user",
        "SMTP_PASS": "pass",
        "SMTP_SENDER": "sender@example.com"
    }
    
    person = {
        "id": "p1",
        "name": {"firstName": "John", "lastName": "Doe"},
        "emails": [{"primaryEmail": "john@example.com"}],
        "phones": [{"primaryPhone": "+1234567890"}]
    }
    
    result = process_outreach(mock_crm, person, config)
    
    # If both are attempted, and one fails, how should we handle it?
    # Let's say if either succeeds it's a success? Or if all fail? 
    # Usually, if at least one works, it's a success. Or we can mandate both.
    # The requirement is "Set contactStatus = 'contacted'. On failure, set contactStatus to 'failed'."
    # Let's say if AT LEAST one succeeds, it's contacted. If ALL attempted methods fail, it's failed.
    # In this test, email fails but whatsapp succeeds, so it should be True.
    assert result is True
    mock_crm.update_person.assert_called_once_with("p1", {"contactStatus": "contacted"})

@patch("outreach.send_email")
@patch("outreach.send_whatsapp")
def test_process_outreach_all_fail(mock_send_whatsapp, mock_send_email):
    mock_crm = MagicMock()
    mock_send_email.return_value = False
    mock_send_whatsapp.return_value = False
    
    config = {
        "SMTP_SERVER": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "user",
        "SMTP_PASS": "pass",
        "SMTP_SENDER": "sender@example.com"
    }
    
    person = {
        "id": "p1",
        "name": {"firstName": "John", "lastName": "Doe"},
        "emails": [{"primaryEmail": "john@example.com"}],
        "phones": [{"primaryPhone": "+1234567890"}]
    }
    
    result = process_outreach(mock_crm, person, config)
    
    assert result is False
    mock_crm.update_person.assert_called_once_with("p1", {"contactStatus": "failed"})
