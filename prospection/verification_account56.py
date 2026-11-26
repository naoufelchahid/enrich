"""Vérification des boîtes mail par l’Actor Account56."""
from datetime import datetime, timezone
from decimal import Decimal
from urllib.error import URLError

from .budget import BudgetInsuffisant

ACTEUR_VERIFICATION = "account56~email-verifier"
PLAFOND_MINIMAL = Decimal("0.50")
FRAIS_DEMARRAGE = Decimal("0.0001")
FRAIS_ADRESSE = Decimal("0.001")


def plafond_lot(volume):
    return max(PLAFOND_MINIMAL, FRAIS_DEMARRAGE + volume * FRAIS_ADRESSE) if volume else Decimal(0)


def entree_lot(adresses):
    return {"emails": adresses}


def normaliser_verdict(ligne):
    if not isinstance(ligne, dict):
        return "manquant"
    statut = ligne.get("result")
    if statut in ("invalid", "disposable"):
        return "rejete"
    if statut == "catch_all":
        return "risque"
    if statut == "error" or ligne.get("error"):
        return "erreur"
    if (statut == "ok" and ligne.get("quality") == "good" and ligne.get("subresult") == "ok"
            and not ligne.get("didyoumean")):
        return "accepte"
    if statut == "unknown" and ligne.get("subresult") in ("timeout", "timeout_error", "connection_timeout"):
        return "timeout"
    return "inconnu"


def preuve_acceptee(preuve, adresse):
    if (not isinstance(preuve, dict) or preuve.get("fournisseur") != ACTEUR_VERIFICATION
            or preuve.get("adresse") != adresse or preuve.get("verdict") != "accepte"):
        return False
    brut = preuve.get("brut")
    return (normaliser_verdict(brut) == "accepte"
            and str(brut.get("email") or "").strip().casefold() == adresse)


def rapprocher_verification(adresses, lignes, provenance=None, *, incident=None):
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
        verdict = incident or ("reponse_ambigue" if len(correspondances) > 1 else normaliser_verdict(brut))
        resultats[adresse] = {"fournisseur": ACTEUR_VERIFICATION, "date": (provenance or {}).get("date") or maintenant,
                              "adresse": adresse, "provenance": provenance, "verdict": verdict, "brut": brut}
    return resultats


def verifier_lot(budget, adresses):
    lot = sorted({a.strip().casefold() for a in adresses if a.strip()})
    if not lot:
        return {}
    try:
        lignes, provenance = budget.lignes(ACTEUR_VERIFICATION, entree_lot(lot), plafond_lot(len(lot)))
    except BudgetInsuffisant:
        return rapprocher_verification(lot, [], incident="plafond")
    except (URLError, TimeoutError, RuntimeError):
        return rapprocher_verification(lot, [], incident="echec_lot")
    return rapprocher_verification(lot, lignes, provenance)
