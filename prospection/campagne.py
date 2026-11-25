"""Préparer une requête de découverte avec python -m prospection.campagne campagne.json."""
import json
import re
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def texte(valeur):
    return isinstance(valeur, str) and bool(valeur.strip())


JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def valider_horaires(campagne):
    fuseau = campagne.get("fuseau", "Africa/Casablanca")
    if not texte(fuseau):
        raise ValueError("Fuseau IANA attendu")
    try:
        ZoneInfo(fuseau)
    except (ZoneInfoNotFoundError, ValueError) as erreur:
        raise ValueError(f"Fuseau inconnu : {fuseau}") from erreur

    jours = campagne.get("jours_actifs", JOURS)
    if not isinstance(jours, list) or not jours or any(j not in JOURS for j in jours):
        raise ValueError("jours_actifs doit contenir des jours de la semaine en français")

    debut = campagne.get("heure_debut", "08:00")
    fin = campagne.get("heure_fin", "17:00")
    for heure in (debut, fin):
        if not isinstance(heure, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", heure):
            raise ValueError("Heure attendue au format HH:MM, entre 00:00 et 23:59")
    if debut >= fin:
        raise ValueError("La fin du créneau doit suivre son début")

    intervalle = campagne.get("intervalle_minutes", 10)
    if type(intervalle) is not int or intervalle < 1:
        raise ValueError("intervalle_minutes doit être un entier positif")
    contacts = campagne.get("contacts_par_creneau", 4)
    if type(contacts) is not int or not 1 <= contacts <= 4:
        raise ValueError("contacts_par_creneau doit être compris entre 1 et 4")


def requete(campagne):
    for champ in ("identifiant", "cible"):
        if not texte(campagne.get(champ)):
            raise ValueError(f"Champ obligatoire : {champ}")
    brief = campagne.get("brief")
    for champ in ("offre", "public", "raison", "langue", "ton", "signature", "question"):
        if not isinstance(brief, dict) or not texte(brief.get(champ)):
            raise ValueError(f"Champ du brief obligatoire : {champ}")
    valider_horaires(campagne)
    resultat = {}
    filtres = {
        "fonctions": "personTitleIncludes",
        "fonctions_exclues": "personTitleExcludes",
        "entreprises": "companyNameIncludes",
        "entreprises_exclues": "companyNameExcludes",
        "secteurs": "companyIndustryIncludes",
        "pays": "personLocationCountryIncludes",
    }
    for cle, destination in filtres.items():
        valeurs = campagne.get(cle, [])
        if not isinstance(valeurs, list) or not all(texte(v) for v in valeurs):
            raise ValueError(f"Liste de textes attendue : {cle}")
        if valeurs:
            valeurs = [v.strip() for v in valeurs]
            if cle == "pays":
                valeurs = ["Morocco" if v.casefold() in {"ma", "maroc"} else v for v in valeurs]
            resultat[destination] = valeurs
    for cle, destination in (("volume", "totalResults"), ("effectif_min", "companyEmployeeMin"), ("effectif_max", "companyEmployeeMax")):
        if cle in campagne:
            valeur = campagne[cle]
            if type(valeur) is not int or valeur < (1 if cle == "volume" else 0):
                raise ValueError(f"Entier hors limites : {cle}")
            resultat[destination] = valeur
    if campagne.get("effectif_min", 0) > campagne.get("effectif_max", float("inf")):
        raise ValueError("Bornes d’effectif inversées")
    return resultat


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as source:
        print(json.dumps(requete(json.load(source)), ensure_ascii=False, indent=2))
