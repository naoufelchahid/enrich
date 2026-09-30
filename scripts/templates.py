from typing import Tuple
from scripts.models import NormalizedContact

AVAILABLE_VARIANTS = ["A", "B", "C", "D", "E"]


def render_email_template(variant: str, contact: NormalizedContact) -> Tuple[str, str]:
    """
    Renders an executive cold email template for the Contexte Lab campaign.
    
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
        salutation = f"Bonjour M. {contact.last_name}"
    elif contact.civility in ("Mme", "Mme.", "Madame") and contact.last_name:
        salutation = f"Bonjour Mme {contact.last_name}"
    elif contact.first_name:
        salutation = f"Bonjour {contact.first_name}"
    elif contact.last_name:
        salutation = f"Bonjour M./Mme {contact.last_name}"
    else:
        salutation = "Bonjour"

    entreprise = (contact.company_name or "").strip() or "votre entreprise"
    secteur = (contact.activity_sector or "").strip() or "votre secteur"

    if variant == "A":
        # Variante A : Le Décalage de Compétitivité
        subject = f"{entreprise} face à l'accélération de l'IA au Maroc"
        content = f"""
        <p>{salutation},</p>
        <p>L’intelligence artificielle n’est plus un sujet de prospective pour 2030 : elle commence déjà à modifier les marges, la productivité et la vitesse d’exécution dans {secteur}.</p>
        <p>Face à ce rythme qui s'accélère, une question stratégique se pose pour tout dirigeant : <strong>est-ce que votre entreprise prépare activement cette transition, ou risquez-vous de voir des concurrents creuser l'écart de compétitivité ?</strong></p>
        <p>Le piège actuel est de vouloir acheter des outils au hasard. Chez <strong>Contexte Lab</strong>, nous pensons que le seul point de départ sain est <strong>la formation de la direction</strong> : acquérir les repères concrets pour distinguer le bruit médiatique des vraies opportunités de rentabilité.</p>
        <p>Une fois ces repères posés, vous pouvez décider avec clarté de vos prochaines étapes.</p>
        <p>Seriez-vous ouvert à échanger 10 minutes cette semaine ? Répondez simplement à cet email pour me dire si le sujet fait écho à vos priorités actuelles.</p>
        """
    elif variant == "B":
        # Variante B : Subir le Rythme ou Fixer la Trajectoire
        subject = "IA : subir le rythme ou fixer la trajectoire ?"
        content = f"""
        <p>{salutation},</p>
        <p>L'accélération des outils IA au Maroc est désormais une réalité de terrain. Dans la plupart des entreprises, les équipes commencent à tester des solutions dans leur coin, souvent sans méthode ni contrôle.</p>
        <p>Pour un dirigeant, le risque n'est pas seulement technologique : <strong>c'est la perte de maîtrise et de compétitivité face à ceux qui structurent leur démarche.</strong></p>
        <p>Notre parcours <em>Formation IA pour Dirigeants</em> a été conçu pour cela : vous donner, sans jargon, les clés pour arbitrer, cadrer les risques et décider où investir utilement dans vos processus.</p>
        <p>Quel est votre sentiment actuel sur la maturité de {entreprise} face à cette transition ?</p>
        """
    elif variant == "C":
        # Variante C : L'Illusion du Temps
        subject = "Avons-nous encore le temps d'attendre sur l'IA ?"
        content = f"""
        <p>{salutation},</p>
        <p>Dans chaque tournant technologique, l'erreur classique est de croire qu'on a le temps de voir venir. Mais la courbe de l'IA est exponentielle, et l'inaction est aujourd'hui le choix le plus coûteux.</p>
        <p>Pensez-vous que {entreprise} dispose aujourd'hui d'une vision claire pour tirer parti de cette vague, ou le sujet reste-t-il encore flou au sein de votre comité de direction ?</p>
        <p>La formation est le premier pas pragmatique : elle vous permet de faire l'inventaire de vos données et processus avant de vous lancer, pour décider avec discernement.</p>
        <p>Si vous souhaitez en discuter librement, vous pouvez me répondre directement ici.</p>
        """
    elif variant == "D":
        # Variante D : De Dirigeant à Dirigeant
        subject = f"Échange entre dirigeants : intégrer l'IA dans {secteur}"
        content = f"""
        <p>{salutation},</p>
        <p>En tant que dirigeant d'agence IA à Casablanca, je constate quotidiennement le même fossé : beaucoup d'effets d'annonce, mais très peu de méthodes adaptées à la réalité économique de nos entreprises marocaines.</p>
        <p>Comment intégrer l'IA avant qu'elle ne devienne un handicap de compétitivité pour {entreprise} ?</p>
        <p>En formant d'abord ceux qui décident. Notre formation pour dirigeants chez Contexte Lab aide à identifier 2 ou 3 cas d'usage à fort retour sur investissement dans votre métier.</p>
        <p>Seriez-vous disponible pour un court appel de courtoisie de 10 minutes ?</p>
        """
    else:  # variant == "E"
        # Variante E : Le Cadrage Décisionnel 30 min
        subject = f"Quel sujet IA mérite vos 30 prochaines minutes, pour {entreprise} ?"
        content = f"""
        <p>{salutation},</p>
        <p>Si vos équipes ou votre conseil d'administration vous demandaient demain d'arbitrer sur vos investissements IA, sauriez-vous exactement où se trouvent les gains réels et où sont les risques ?</p>
        <p>Avant d'engager des budgets ou des prestataires, <strong>la formation vous apporte la lucidité stratégique</strong> : poser les limites, comprendre ce qui est faisable et piloter vos prochaines décisions avec assurance.</p>
        <p>Je vous propose un échange de cadrage gratuit de 30 minutes pour faire le point sur votre secteur : <a href="https://calendly.com/chahid-naoufel/echange-autour-de-vos-enjeux-commerciaux-et-ia">Réserver un créneau</a> ou répondez directement à cet email.</p>
        """

    footer = """
    <p>Bien cordialement,</p>
    <p><strong>Naoufel Chahid</strong><br>
    Fondateur — Contexte Lab SARL AU<br>
    Casablanca, Maroc<br>
    <a href="https://www.contextelab.ma">www.contextelab.ma</a></p>
    <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 24px 0;" />
    <p style="font-size: 11px; color: #888;">
      Vous recevez cet email car vous êtes répertorié comme dirigeant d'entreprise au Maroc. 
      Pour ne plus recevoir nos communications, <a href="{{{RESEND_UNSUBSCRIBE_URL}}}">cliquez ici pour vous désinscrire</a>.
    </p>
    """

    html_body = (
        "<!DOCTYPE html>"
        "<html lang=\"fr\">"
        "<head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"></head>"
        f"<body style=\"font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 14px; line-height: 1.6; color: #222; max-width: 600px; margin: 0 auto; padding: 20px;\">"
        f"{content.strip()}"
        f"{footer.strip()}"
        "</body>"
        "</html>"
    )

    return subject, html_body
