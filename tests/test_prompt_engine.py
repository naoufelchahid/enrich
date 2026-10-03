# tests/test_prompt_engine.py
import pytest
from scripts.prompt_engine import (
    validate_email_copy,
    build_cognitive_prompt,
    compose_tailored_email,
)

def test_validate_email_copy_rules():
    # Valid email: 50-85 words, 0 HTML, 2-4 words subject, 1 CTA
    valid_subject = "question sur Atlas Tech"  # 4 words
    valid_body = (
        "Bonjour Karim,\n\n"
        "J'ai remarqué le développement récent d'Atlas Tech sur le marché marocain. "
        "À Contexte Lab à Casablanca, nous accompagnons les dirigeants dans l'optimisation "
        "de leurs processus opérationnels grâce à l'intelligence artificielle appliquée.\n\n"
        "Seriez-vous ouvert à un échange informel de 5 minutes cette semaine pour explorer "
        "si ces approches sont pertinentes pour vos équipes ?\n\n"
        "Bien à vous,\nNaoufel Chahid"
    )
    is_valid, errors = validate_email_copy(valid_subject, valid_body)
    assert is_valid is True
    assert len(errors) == 0

def test_validate_email_copy_rejects_html_and_bad_word_count():
    # Invalid: Contains HTML and subject too long
    bad_subject = "Proposition commerciale exclusive et urgente pour votre société"  # 8 words
    bad_body = "<p>Bonjour,</p><p>Cliquez <a href='https://spam.com'>ici</a>.</p>"
    is_valid, errors = validate_email_copy(bad_subject, bad_body)
    assert is_valid is False
    assert any("HTML" in e for e in errors)
    assert any("Subject" in e for e in errors)
    assert any("Word count" in e for e in errors)

def test_build_cognitive_prompt_contains_market_context():
    lead = {
        "first_name": "Taha",
        "last_name": "Lahlou",
        "company_name": "LogiMaroc",
        "job_title": "Directeur Général",
    }
    career_data = {
        "current_job_start_date": "2026-02-01",
        "past_companies": ["Bolloré Maroc"],
    }
    prompt = build_cognitive_prompt(lead, career_data)
    assert "LogiMaroc" in prompt
    assert "Taha" in prompt
    assert "50 à 85 mots" in prompt
    assert "Zero HTML" in prompt
    assert "Contexte Lab" in prompt

def test_compose_tailored_email_output():
    lead = {
        "first_name": "Youssef",
        "last_name": "Mansouri",
        "company_name": "Fintech Horizons",
        "job_title": "Fondateur",
    }
    msg = compose_tailored_email(lead)
    assert "subject" in msg
    assert "body" in msg
    is_valid, errors = validate_email_copy(msg["subject"], msg["body"])
    assert is_valid, f"Generated email failed validation: {errors}"


def test_validate_email_copy_boundary_cases():
    # 1 word subject
    is_valid, errors = validate_email_copy("Question", "word " * 60)
    assert not is_valid
    assert any("Subject" in e for e in errors)

    # 5 words subject
    is_valid, errors = validate_email_copy("Une question pour votre entreprise maintenant", "word " * 60)
    assert not is_valid
    assert any("Subject" in e for e in errors)

    # Body too short (49 words)
    is_valid, errors = validate_email_copy("Question sur entreprise", "mot " * 49)
    assert not is_valid
    assert any("Word count" in e for e in errors)

    # Body too long (86 words)
    is_valid, errors = validate_email_copy("Question sur entreprise", "mot " * 86)
    assert not is_valid
    assert any("Word count" in e for e in errors)


def test_build_cognitive_prompt_defaults():
    # When lead dictionary is empty and career_data is None
    prompt = build_cognitive_prompt({})
    assert "Bonjour" in prompt
    assert "votre société" in prompt
    assert "Dirigeant" in prompt
    assert "50 à 85 mots" in prompt


def test_compose_tailored_email_defaults():
    # When lead dictionary is empty
    msg = compose_tailored_email({})
    assert "subject" in msg
    assert "body" in msg
    is_valid, errors = validate_email_copy(msg["subject"], msg["body"])
    assert is_valid, f"Default generated email failed validation: {errors}"

