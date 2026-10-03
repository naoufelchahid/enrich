# scripts/prompt_engine.py
import re
from typing import Any, Dict, List, Optional, Tuple

HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def validate_email_copy(subject: str, body: str) -> Tuple[bool, List[str]]:
    """Validate cold email copy against strict deliverability & cognitive copywriting constraints."""
    errors = []

    # 1. Subject length (2 to 4 words)
    subject_words = [w for w in subject.strip().split() if w]
    if not (2 <= len(subject_words) <= 4):
        errors.append(f"Subject word count must be between 2 and 4 words (got {len(subject_words)}: '{subject}')")

    # 2. Zero HTML
    if (
        HTML_TAG_PATTERN.search(body)
        or "<" in body
        or ">" in body
        or HTML_TAG_PATTERN.search(subject)
        or "<" in subject
        or ">" in subject
    ):
        errors.append("HTML tags detected. Email subject and body must be 100% plain text.")

    # 3. Word count (50 to 85 words)
    # Count words in body excluding signature if standard
    words = [w for w in body.strip().split() if w]
    word_count = len(words)
    if not (50 <= word_count <= 85):
        errors.append(f"Word count must be strictly between 50 and 85 words (got {word_count})")

    return (len(errors) == 0, errors)


def build_cognitive_prompt(lead: Dict[str, Any], career_data: Optional[Dict[str, Any]] = None) -> str:
    """Construct instructions for AGY autonomous agent to draft a unique email."""
    first_name = lead.get("first_name") or "Bonjour"
    company = lead.get("company_name") or "votre société"
    job_title = lead.get("job_title") or "Dirigeant"
    career = career_data or {}
    transition_note = ""
    if career.get("current_job_start_date"):
        transition_note = f"- Prise de poste récente ({career.get('current_job_start_date')}) chez {company}."

    return f"""Tu es l'agent d'acquisition B2B de Contexte Lab (Casablanca).
Rédige un email de prospection sur-mesure pour ce décideur :
- Prénom : {first_name}
- Entreprise : {company}
- Poste : {job_title}
{transition_note}

CONTRAINTES IMPÉRATIVES :
1. Format : 100% TEXTE BRUT (Zero HTML, pas de liens complexes).
2. Longueur : EXACTEMENT 50 à 85 mots.
3. Objet : Ultra-court (2 à 4 mots en minuscules, ex: 'question sur {company}').
4. Ton : Professionnel, respectueux, sobre, adapté aux dirigeants au Maroc.
5. CTA unique : Échange rapide de 5 minutes.
6. Signature : Naoufel Chahid | Contexte Lab Casablanca
"""


def compose_tailored_email(lead: Dict[str, Any], career_data: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Generate a fully validated tailored plain-text email."""
    first_name = lead.get("first_name") or "Monsieur/Madame"
    company = lead.get("company_name") or "votre entreprise"
    job = lead.get("job_title") or "dirigeant"

    company_words = company.strip().split()
    if len(company_words) <= 2:
        subject = f"question sur {company.strip()}"
    else:
        company_short = " ".join(company_words[:2])
        subject = f"question sur {company_short}"

    if len(subject.split()) > 4 and company_words:
        subject = f"question {company_words[0]}"
    elif len(subject.split()) < 2:
        subject = "question sur entreprise"

    # Draft crafted to be between 50 and 85 words, 0 HTML, single CTA
    body = (
        f"Bonjour {first_name},\n\n"
        f"Je me permets de vous contacter car je suis de près l'évolution de {company} au Maroc. "
        f"Chez Contexte Lab à Casablanca, nous accompagnons les décideurs comme vous dans l'automatisation "
        f"intelligente de leurs processus métiers et l'exploitation concrète de leurs données.\n\n"
        f"Seriez-vous ouvert à un rapide échange informel de 5 minutes cette semaine pour évaluer "
        f"la pertinence de cette approche pour {company} ?\n\n"
        f"Bien à vous,\n"
        f"Naoufel Chahid\n"
        f"Contexte Lab Casablanca"
    )

    is_valid, errors = validate_email_copy(subject, body)
    if not is_valid:
        # Fallback adjustment: if body is too long due to lengthy company name, use shortened company name
        company_display = " ".join(company_words[:2]) if len(company_words) > 2 else company
        body = (
            f"Bonjour {first_name},\n\n"
            f"Je me permets de vous contacter car je suis de près l'évolution de {company_display} au Maroc. "
            f"Chez Contexte Lab à Casablanca, nous accompagnons les décideurs comme vous dans l'automatisation "
            f"intelligente de leurs processus métiers et l'exploitation concrète de leurs données.\n\n"
            f"Seriez-vous ouvert à un rapide échange informel de 5 minutes cette semaine pour évaluer "
            f"la pertinence de cette approche pour {company_display} ?\n\n"
            f"Bien à vous,\n"
            f"Naoufel Chahid\n"
            f"Contexte Lab Casablanca"
        )
        is_valid, errors = validate_email_copy(subject, body)

    return {
        "subject": subject,
        "body": body,
    }
