import pytest
from scripts.models import NormalizedContact
from scripts.templates import render_email_template, AVAILABLE_VARIANTS


def test_all_variants_exist():
    assert set(AVAILABLE_VARIANTS) == {"A", "B", "C", "D", "E"}
    assert len(AVAILABLE_VARIANTS) == 5


def test_template_existential_doubt_hook():
    contact = NormalizedContact(
        company_name="Maroc Industrie",
        activity_sector="l'industrie manufacturière",
        first_name="Adil",
        last_name="Bennani",
        job_title="Directeur Général",
        email="a.bennani@marocindustrie.ma",
        source_file="BDD3",
        is_dirigeant=True,
    )
    for v in AVAILABLE_VARIANTS:
        subject, body = render_email_template(v, contact)
        assert len(subject) > 10
        assert "Adil" in body or "Bennani" in body
        assert "Contexte Lab" in body
        assert "Formation" in body or "formation" in body
        # Verification of unsubscribe link
        assert "{{{RESEND_UNSUBSCRIBE_URL}}}" in body
        assert "désinscri" in body.lower()
        # Size limit < 100 KB
        assert len(body.encode("utf-8")) < 100 * 1024


def test_salutation_with_civility_m():
    contact = NormalizedContact(
        company_name="Atlas Finance",
        civility="M.",
        first_name="Karim",
        last_name="Tazi",
        job_title="PDG",
        email="k.tazi@atlasfinance.ma",
        source_file="BDD1",
        is_dirigeant=True,
    )
    subject, body = render_email_template("A", contact)
    assert "Bonjour M. Tazi" in body


def test_salutation_with_civility_mme():
    contact = NormalizedContact(
        company_name="Atlas Finance",
        civility="Mme",
        first_name="Leila",
        last_name="Mansouri",
        job_title="Présidente",
        email="l.mansouri@atlasfinance.ma",
        source_file="BDD1",
        is_dirigeant=True,
    )
    subject, body = render_email_template("B", contact)
    assert "Bonjour Mme Mansouri" in body


def test_salutation_without_civility_fallback_firstname():
    contact = NormalizedContact(
        company_name="Tech Solutions",
        first_name="Yassine",
        last_name="Alami",
        job_title="Directeur",
        email="y.alami@tech.ma",
        source_file="BDD2",
        is_dirigeant=True,
    )
    subject, body = render_email_template("C", contact)
    assert "Bonjour Yassine" in body


def test_salutation_without_names():
    contact = NormalizedContact(
        company_name="Tech Solutions",
        source_file="BDD2",
    )
    subject, body = render_email_template("D", contact)
    assert "Bonjour," in body or "Bonjour <" in body or "<p>Bonjour,</p>" in body or "<p>Bonjour</p>" in body


def test_defaults_when_company_and_sector_missing():
    contact = NormalizedContact(
        company_name="",
        activity_sector=None,
        first_name="Omar",
        source_file="BDD1",
    )
    subject, body = render_email_template("A", contact)
    assert "votre entreprise" in subject or "votre entreprise" in body
    assert "votre secteur" in body


def test_all_variants_contain_signature_and_casablanca():
    contact = NormalizedContact(
        company_name="Maroc Conseil",
        first_name="Mehdi",
        last_name="Amrani",
        source_file="BDD1",
    )
    for v in AVAILABLE_VARIANTS:
        _, body = render_email_template(v, contact)
        assert "Naoufel Chahid" in body
        assert "Contexte Lab" in body
        assert "Casablanca" in body
        assert "www.contextelab.ma" in body


def test_invalid_variant_raises_value_error():
    contact = NormalizedContact(
        company_name="Maroc Conseil",
        source_file="BDD1",
    )
    with pytest.raises(ValueError, match="variant"):
        render_email_template("UNKNOWN", contact)
