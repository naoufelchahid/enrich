"""The two approved initial messages, V2 and V5."""
import re


SIGNATURE = 'Naoufel Chahid | Contexte Lab Casablanca'
SIGN_OFF = 'Cordialement,'
HTML_SIGNATURE_MODE = 'thunderbird_html'
CLOSING_PHRASE = (
    "Chez Contexte Lab, nous accompagnons les entreprises pour faire de l'IA "
    "un vrai levier de croissance."
)

def formal_salutation(*, last_name, civility=None, job_title=None):
    """Use a recorded civility or an unambiguous French job-title form."""
    last_name = str(last_name or '').strip()
    if not last_name or any(c in last_name for c in '\r\n<>'):
        raise ValueError('LAST_NAME_REQUIRED')
    title = str(civility or '').strip().casefold().rstrip('.')
    if title in ('m', 'mr', 'monsieur'):
        honorific = 'Monsieur'
    elif title in ('mme', 'mlle', 'madame', 'mrs', 'ms'):
        honorific = 'Madame'
    elif not title:
        role = str(job_title or '').casefold()
        feminine = bool(re.search(r'\b(directrice|gérante|présidente|fondatrice|administratrice|associée)\b', role))
        masculine = bool(re.search(r'\b(directeur|gérant|président|fondateur|administrateur|associé)\b', role))
        if feminine and masculine:
            raise ValueError('CIVILITY_REVIEW_REQUIRED')
        if feminine:
            honorific = 'Madame'
        elif masculine:
            honorific = 'Monsieur'
        else:
            raise ValueError('CIVILITY_REVIEW_REQUIRED')
    else:
        raise ValueError('CIVILITY_REVIEW_REQUIRED')
    return f'Bonjour {honorific} {last_name},'


PILOT_REVISION = '2026-11-05-v2-v5'
BOOKING_URL = ('https://cal.com/chahid-naoufel-hd6ljk/30min?utm_source=contextelab'
               '&utm_medium=site&utm_campaign=autre&utm_content=%2Fcontact&overlayCalendar=true')
PILOT_COMMON_ENDING = (
    'Pour découvrir notre méthode, vous pouvez réserver un échange de 30 minutes sur Cal Video ('
    + BOOKING_URL + ') ou m’appeler directement. Je serai heureux de vous détailler notre démarche.'
    + '\n\n' + CLOSING_PHRASE + '\n\n' + SIGN_OFF
)
PILOT_PATTERNS = {
    'V2': {
        'subject':'construire votre avance',
        'paragraphs': (
            'Avec l’IA, l’avance concurrentielle dépend aussi de la manière dont chaque entreprise apprend à s’en servir.',
            'ChatGPT ou Claude peuvent constituer un premier pas. Pour aller plus loin, leurs usages doivent tenir compte du métier, des données et des contraintes de l’organisation.',
            'Une adoption sans méthode risque de multiplier les dépendances plutôt que les bénéfices.',
            'J’ai accompagné plusieurs entreprises dans cette transition. Je vous propose d’échanger sur la façon dont {company} pourrait structurer sa transformation digitale avec l’IA.',
        ),
    },
    'V5': {
        'subject':'au-delà des chatbots',
        'paragraphs': (
            'ChatGPT ou Claude peuvent aider vos équipes. Mais leur donner accès à ces outils ne suffit pas à organiser une transformation digitale.',
            'L’IA doit disposer du bon contexte : vos données, vos règles métier, vos processus et vos contraintes. Les équipes doivent aussi savoir quand lui faire confiance et quand vérifier.',
            'J’ai accompagné plusieurs entreprises pour intégrer ces usages à leur fonctionnement. Je serais heureux de vous présenter notre méthode et de regarder avec vous où elle pourrait être utile à {company}.',
        ),
    },
}


def editorial_question_count(body):
    """A URL query string is not an editorial question."""
    return re.sub(r'https?://[^\s)]+', '', body).count('?')


def render_pilot_pattern(variant_id, *, last_name, company_name, civility):
    if variant_id not in PILOT_PATTERNS:
        raise ValueError('UNKNOWN_PILOT_VARIANT')
    if not str(civility or '').strip():
        raise ValueError('CIVILITY_REVIEW_REQUIRED')
    greeting=formal_salutation(last_name=last_name,civility=civility)
    company_name=str(company_name or '').strip()
    if not company_name or any(ch in company_name for ch in '\r\n<>'):
        raise ValueError('INVALID_EMAIL_IDENTITY')
    pattern=PILOT_PATTERNS[variant_id]
    body='\n\n'.join([greeting,*[p.format(company=company_name) for p in pattern['paragraphs']],PILOT_COMMON_ENDING])
    return {'subject':pattern['subject'],'body':body,'variant_id':variant_id,
            'pattern_revision':PILOT_REVISION,'signature_mode':HTML_SIGNATURE_MODE}


def validate_pilot_copy(variant_id, subject, body, *, last_name, company_name, civility,
                        revision=PILOT_REVISION):
    """Validate the exact approved subject, body, booking link and signature mode."""
    if revision != PILOT_REVISION:
        return False,['PILOT_REVISION_MISMATCH']
    try:
        expected=render_pilot_pattern(variant_id,last_name=last_name,company_name=company_name,civility=civility)
    except ValueError as exc:
        return False,[str(exc)]
    errors=[]
    if subject != expected['subject']:
        errors.append('PILOT_SUBJECT_CHANGED')
    if body != expected['body']:
        errors.append('PILOT_BODY_CHANGED')
    if body.count(BOOKING_URL)!=1:
        errors.append('PILOT_BOOKING_LINK_CHANGED')
    if SIGNATURE in body or not body.endswith(SIGN_OFF):
        errors.append('PILOT_SIGNATURE_INVALID')
    return not errors,errors
