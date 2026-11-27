"""Conserver les preuves et compléter les champs manquants de l’entreprise."""
import json
from urllib.error import HTTPError

import phonenumbers

from .identite_serp import WEBMAIL, email_complet, telephone_complet
from .recherche_dirigeants import domaine, normaliser, page_entreprise


def cle_valeur(champ, valeur):
    if champ == "site":
        return domaine(valeur)
    if champ == "telephone":
        numero = telephone_complet(valeur)
        return phonenumbers.format_number(numero, phonenumbers.PhoneNumberFormat.E164) if numero else ""
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
        choix.pop("", None)
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
    telephone = valeurs.get("telephone")
    deja_refuse = any(f.get("origine") == "Twenty" and f.get("telephone") == telephone
                      and f.get("incident") for f in preuves)
    if telephone and not tel.get("primaryPhoneNumber") and not deja_refuse:
        numero = telephone_complet(valeurs["telephone"])
        charge["phones"] = {**tel, "primaryPhoneNumber": phonenumbers.national_significant_number(numero),
                            "primaryPhoneCountryCode": phonenumbers.region_code_for_number(numero) or "",
                            "primaryPhoneCallingCode": "+" + str(numero.country_code)}
    if valeurs.get("activite") and not fiche.get("activitySector"):
        charge["activitySector"] = valeurs["activite"]
    if entreprise != dossier:
        charge["kerixDossier"] = json.dumps(entreprise, ensure_ascii=False)
    if charge:
        try:
            crm.modifier("companies", e["twenty_id"], **charge)
        except HTTPError as erreur:
            if erreur.code != 400 or "phones" not in charge:
                raise
            try:
                code = json.loads(erreur.read()).get("code")
            except ValueError:
                raise erreur
            if code not in {"INVALID_PHONE_NUMBER", "CONFLICTING_PHONE_COUNTRY_CODE"}:
                raise
            preuves.append({"origine": "Twenty", "incident": code, "telephone": telephone})
            charge.pop("phones")
            charge["kerixDossier"] = json.dumps(entreprise, ensure_ascii=False)
            crm.modifier("companies", e["twenty_id"], **charge)
    return entreprise


ROLES = {"contact", "contacts", "info", "infos", "information", "hello", "bonjour", "office",
         "accueil", "commercial", "commerciale", "sales", "support", "direction", "secretariat",
         "compta", "administration", "reception", "service", "assistance", "communication",
         "enquiries", "inquiries", "cabinet", "bureau", "agence", "marketing", "rh", "export"}


def boite_standard(mail, e):
    local = normaliser(mail.partition("@")[0])
    if set(local.split()) & ROLES:
        return True
    libelle = local.replace(" ", "")
    domaine_societe = domaine(e.get("site")).split(".")[0]
    noms = {normaliser(v).replace(" ", "") for v in (e.get("nom"), domaine_societe) if v}
    return libelle in noms


def boites_publiees(e, *, standard_uniquement=True):
    resultat = {}
    site, linkedin = domaine(e.get("site")), page_entreprise(e.get("linkedin"))
    for f in e.get("coordonnees_documentees", []):
        if f.get("champ") != "email":
            continue
        mail = f["valeur"].casefold()
        if not email_complet(mail) or (standard_uniquement and not boite_standard(mail, e)):
            continue
        hote = mail.partition("@")[2]
        officielle = ((f["origine"] == "Site officiel" and site and domaine(f["url"]) == site)
                      or (f["origine"] in ("Citation", "Organique") and linkedin
                          and page_entreprise(f["url"]) == linkedin))
        if (hote == site and hote not in WEBMAIL) or (officielle and hote in WEBMAIL):
            resultat[mail] = {**f, "email": mail}
    return list(resultat.values())
