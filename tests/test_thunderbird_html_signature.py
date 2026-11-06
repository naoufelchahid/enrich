from scripts.thunderbird_client import ThunderbirdClient


def test_direct_mail_uses_identity_html_signature_without_text_duplicate(monkeypatch):
    monkeypatch.delenv('WORKFLOW_STOP_AT_DRAFT', raising=False)
    mail = ThunderbirdClient(dry_run=True)
    result = mail.send_email('client@example.org', 'Objet', 'Bonjour,\n\nL’IA & vous ?',
                             include_signature=True)
    assert result['success']
    submitted = mail._sent_emails[0]
    assert submitted['includeSignature'] is True
    assert submitted['isHtml'] is True
    assert submitted['body'] == 'Bonjour,<br><br>L’IA &amp; vous ?'
    assert 'Naoufel Chahid | Contexte Lab Casablanca' not in submitted['body']
