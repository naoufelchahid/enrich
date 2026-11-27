"""Conserver les preuves et compléter les champs manquants de l’entreprise."""
import json
import re

from .identite_serp import WEBMAIL
from .recherche_dirigeants import domaine, normaliser, page_entreprise


def cle_valeur(champ, valeur):
    if champ == "site":
        return domaine(valeur)
    if champ == "telephone":
        chiffres = re.sub(r"\D", "", valeur).removeprefix("00")
        return "212" + chiffres[1:] if chiffres.startswith("0") and len(chiffres) == 10 else chiffres
    return normaliser(valeur)


def completer(crm, e, faits):
    fiche = crm.record("companies", e["twenty_id"])
    dossier = json.loads(fiche.get("kerixDossier") or "{}")
    preuves = list(dossier.get("coordonnees_documentees", []))
    def identifiant(f):
        return tuple(f.get(k) for k in ("champ", "valeur", "url", "extrait", "origine", "incident"))
    connus = {identifiant(f) for f in preuves}
    for f in faits:
        if identifiant(f) not in connus:
            preuves.append(f)
            connus.add(identifiant(f))
    charge = {}
    valeurs = {}
    for champ in ("site", "linkedin", "adresse", "ville", "telephone", "activite"):
        choix = {cle_valeur(champ, f["valeur"]): f["valeur"] for f in preuves if f.get("champ") == champ}
        if len(choix) == 1:
            valeurs[champ] = next(iter(choix.values()))
    entreprise = {**dossier, **e, "coordonnees_documentees": preuves}
    for champ, colonne in (("site", "domainName"), ("linkedin", "linkedinLink")):
        lien = fiche.get(colonne) or {}
        valeur = lien.get("primaryLinkUrl") or e.get(champ) or valeurs.get(champ, "")
        entreprise[champ] = valeur
        if valeur and not lien.get("primaryLinkUrl"):
            charge[colonne] = {**lien, "primaryLinkUrl": valeur}
    adresse = dict(fiche.get("address") or {})
    for champ, cle in (("adresse", "addressStreet1"), ("ville", "addressCity")):
        if valeurs.get(champ) and not adresse.get(cle):
            adresse[cle] = valeurs[champ]
            charge["address"] = adresse
    tel = fiche.get("phones") or {}
    if valeurs.get("telephone") and not tel.get("primaryPhoneNumber"):
        numero = cle_valeur("telephone", valeurs["telephone"])
        if numero.startswith("212") and len(numero) == 12:
            charge["phones"] = {**tel, "primaryPhoneNumber": numero[3:], "primaryPhoneCountryCode": "MA",
                                "primaryPhoneCallingCode": "+212"}
        else:
            charge["phones"] = {**tel, "primaryPhoneNumber": valeurs["telephone"]}
    if valeurs.get("activite") and not fiche.get("activitySector"):
        charge["activitySector"] = valeurs["activite"]
    if entreprise != dossier:
        charge["kerixDossier"] = json.dumps(entreprise, ensure_ascii=False)
    if charge:
        crm.modifier("companies", e["twenty_id"], **charge)
    return entreprise


def boites_publiees(e):
    resultat = {}
    site, linkedin = domaine(e.get("site")), page_entreprise(e.get("linkedin"))
    for f in e.get("coordonnees_documentees", []):
        if f.get("champ") != "email":
            continue
        mail = f["valeur"].casefold()
        hote = mail.partition("@")[2]
        officielle = ((f["origine"] == "Site officiel" and site and domaine(f["url"]) == site)
                      or (f["origine"] in ("Citation", "Organique") and linkedin
                          and page_entreprise(f["url"]) == linkedin))
        if (hote == site and hote not in WEBMAIL) or (officielle and hote in WEBMAIL):
            resultat[mail] = {**f, "email": mail}
    return list(resultat.values())
