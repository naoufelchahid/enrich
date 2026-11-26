"""Contrôle DNS des adresses. Un résultat DNS ne constitue pas une preuve SMTP."""
from datetime import datetime, timezone
from decimal import Decimal
from urllib.error import URLError

from .budget import BudgetInsuffisant

ACTEUR_DNS = "f0rty7even~email-verifier"
FRAIS_MAX_LOT = Decimal("0.01")
FRAIS_MAX_ADRESSE = Decimal("0.003")


def plafond_lot(volume):
    return FRAIS_MAX_LOT + volume * FRAIS_MAX_ADRESSE if volume else Decimal(0)


def entree_lot(adresses):
    return {"emails": adresses, "deduplicate": True, "maxEmails": len(adresses)}


def normaliser_dns(ligne):
    if not isinstance(ligne, dict):
        return "manquant"
    statut = ligne.get("deliverability")
    if statut == "invalid" or ligne.get("isDisposable") is True:
        return "rejete"
    if statut == "risky" or ligne.get("didYouMean") is True or ligne.get("suggestedEmail"):
        return "risque"
    if (statut == "valid" and ligne.get("hasValidSyntax") is True and ligne.get("hasMxRecord") is True
            and ligne.get("isDisposable") is False and ligne.get("isAcceptable") is True):
        return "dns_ok"
    return "inconnu"


def rapprocher_dns(adresses, lignes, provenance=None, *, incident=None):
    recus = {}
    maintenant = datetime.now(timezone.utc).isoformat()
    lot = set(adresses)
    for ligne in lignes:
        if not isinstance(ligne, dict):
            continue
        adresse = str(ligne.get("email") or "").strip().casefold()
        if adresse in lot:
            recus.setdefault(adresse, []).append(ligne)
    resultats = {}
    for adresse in adresses:
        correspondances = recus.get(adresse, [])
        brut = correspondances[0] if len(correspondances) == 1 else correspondances or None
        verdict = incident or ("reponse_ambigue" if len(correspondances) > 1 else normaliser_dns(brut))
        resultats[adresse] = {"fournisseur": ACTEUR_DNS, "date": (provenance or {}).get("date") or maintenant,
                              "adresse": adresse, "provenance": provenance, "verdict": verdict, "brut": brut}
    return resultats


def controler_dns(budget, adresses):
    lot = sorted({a.strip().casefold() for a in adresses if a.strip()})
    if not lot:
        return {}
    try:
        lignes, provenance = budget.lignes(ACTEUR_DNS, entree_lot(lot), plafond_lot(len(lot)), max_items=len(lot))
    except BudgetInsuffisant:
        return rapprocher_dns(lot, [], incident="plafond")
    except (URLError, TimeoutError, RuntimeError):
        return rapprocher_dns(lot, [], incident="echec_lot")
    return rapprocher_dns(lot, lignes, provenance)
