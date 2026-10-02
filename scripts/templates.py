from typing import Tuple
from scripts.models import NormalizedContact

AVAILABLE_VARIANTS = ["A", "B", "C", "D", "E"]


def render_email_template(variant: str, contact: NormalizedContact) -> Tuple[str, str]:
    """
    Renders an executive cold email template in 100% PLAIN TEXT format for maximum deliverability.
    
    Variants:
    - A: Le Décalage de Compétitivité (risk of lagging competitors)
    - B: Subir le Rythme ou Fixer la Trajectoire (control vs chaos)
    - C: L'Illusion du Temps (urgency of exponential pace)
    - D: De Dirigeant à Dirigeant (peer-to-peer executive dialogue)
    - E: Le Cadrage Décisionnel 30 min (immediate decision audit)
    """
    if variant not in AVAILABLE_VARIANTS:
        raise ValueError(f"Unknown variant '{variant}'. Available variants are: {', '.join(AVAILABLE_VARIANTS)}")

    if contact.civility in ("M.", "Mr", "Monsieur") and contact.last_name:
        salutation = f"Bonjour M. {contact.last_name},"
    elif contact.civility in ("Mme", "Mme.", "Madame") and contact.last_name:
        salutation = f"Bonjour Mme {contact.last_name},"
    elif contact.first_name:
        salutation = f"Bonjour {contact.first_name},"
    elif contact.last_name:
        salutation = f"Bonjour M./Mme {contact.last_name},"
    else:
        salutation = "Bonjour,"

    entreprise = (contact.company_name or "").strip() or "votre entreprise"
    secteur = (contact.activity_sector or "").strip() or "votre secteur"

    if variant == "A":
        # Variante A : Le Décalage de Compétitivité
        subject = f"{entreprise} face à l'accélération de l'IA au Maroc"
        content = f"""{salutation}

L’intelligence artificielle n’est plus un sujet de prospective pour 2030 : elle commence déjà à modifier les marges, la productivité et la vitesse d’exécution dans {secteur}.

Face à ce rythme qui s'accélère, une question stratégique se pose pour tout dirigeant : est-ce que votre entreprise prépare activement cette transition, ou risquez-vous de voir des concurrents creuser l'écart de compétitivité ?

Le piège actuel est de vouloir acheter des outils au hasard. Chez Contexte Lab, nous pensons que le seul point de départ sain est la formation de la direction : acquérir les repères concrets pour distinguer le bruit médiatique des vraies opportunités de rentabilité.

Une fois ces repères posés, vous pouvez décider avec clarté de vos prochaines étapes.

Seriez-vous ouvert à échanger 10 minutes cette semaine ? Répondez simplement à cet email pour me dire si le sujet fait écho à vos priorités actuelles."""
    elif variant == "B":
        # Variante B : Subir le Rythme ou Fixer la Trajectoire
        subject = "IA : subir le rythme ou fixer la trajectoire ?"
        content = f"""{salutation}

L'accélération des outils IA au Maroc est désormais une réalité de terrain. Dans la plupart des entreprises, les équipes commencent à tester des solutions dans leur coin, souvent sans méthode ni contrôle.

Pour un dirigeant, le risque n'est pas seulement technologique : c'est la perte de maîtrise et de compétitivité face à ceux qui structurent leur démarche.

Notre parcours Formation IA pour Dirigeants a été conçu pour cela : vous donner, sans jargon, les clés pour arbitrer, cadrer les risques et décider où investir utilement dans vos processus.

Quel est votre sentiment actuel sur la maturité de {entreprise} face à cette transition ?"""
    elif variant == "C":
        # Variante C : L'Illusion du Temps
        subject = "Avons-nous encore le temps d'attendre sur l'IA ?"
        content = f"""{salutation}

Dans chaque tournant technologique, l'erreur classique est de croire qu'on a le temps de voir venir. Mais la courbe de l'IA est exponentielle, et l'inaction est aujourd'hui le choix le plus coûteux.

Pensez-vous que {entreprise} dispose aujourd'hui d'une vision claire pour tirer parti de cette vague, ou le sujet reste-t-il encore flou au sein de votre comité de direction ?

La formation est le premier pas pragmatique : elle vous permet de faire l'inventaire de vos données et processus avant de vous lancer, pour décider avec discernement.

Si vous souhaitez en discuter librement, vous pouvez me répondre directement ici."""
    elif variant == "D":
        # Variante D : De Dirigeant à Dirigeant
        subject = f"Échange entre dirigeants : intégrer l'IA dans {secteur}"
        content = f"""{salutation}

En tant que dirigeant d'agence IA à Casablanca, je constate quotidiennement le même fossé : beaucoup d'effets d'annonce, mais très peu de méthodes adaptées à la réalité économique de nos entreprises marocaines.

Comment intégrer l'IA avant qu'elle ne devienne un handicap de compétitivité pour {entreprise} ?

En formant d'abord ceux qui décident. Notre formation pour dirigeants chez Contexte Lab aide à identifier 2 ou 3 cas d'usage à fort retour sur investissement dans votre métier.

Seriez-vous disponible pour un court appel de courtoisie de 10 minutes ?"""
    else:  # variant == "E"
        # Variante E : Le Cadrage Décisionnel 30 min
        subject = f"Quel sujet IA mérite vos 30 prochaines minutes, pour {entreprise} ?"
        content = f"""{salutation}

Si vos équipes ou votre conseil d'administration vous demandaient demain d'arbitrer sur vos investissements IA, sauriez-vous exactement où se trouvent les gains réels et où sont les risques ?

Avant d'engager des budgets ou des prestataires, la formation vous apporte la lucidité stratégique : poser les limites, comprendre ce qui est faisable et piloter vos prochaines décisions avec assurance.

Je vous propose un échange de cadrage gratuit de 30 minutes pour faire le point sur votre secteur. Répondez simplement à cet email pour convenir d'un créneau."""

    signature = """Bien cordialement,

Naoufel Chahid
Fondateur — Contexte Lab SARL AU
Casablanca, Maroc
www.contextelab.ma

PS : Si vous préférez ne plus recevoir nos échanges, répondez simplement 'stop' (désinscription : {{{RESEND_UNSUBSCRIBE_URL}}})."""

    body = f"{content.strip()}\n\n--\n{signature.strip()}\n"
    return subject, body
