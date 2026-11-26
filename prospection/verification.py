"""Interpréter les candidats HarvestAPI et contrôler leur DNS."""
from hashlib import sha256
from urllib.parse import urlsplit

from .verification_dns import ACTEUR_DNS, entree_lot, rapprocher_dns


def candidats(profil):
    lignes = profil.get("emails", [])
    if lignes is None:
        return []
    if not isinstance(lignes, list):
        raise ValueError("Format HarvestAPI inattendu")
    adresses = set()
    for ligne in lignes:
        if not isinstance(ligne, dict) or not isinstance(ligne.get("email"), str):
            raise ValueError("Adresse HarvestAPI illisible")
        adresses.add(ligne["email"].strip().casefold())
    return sorted(adresses - {""})


def qualifier(profil):
    """Rattacher une seule adresse à un employeur actuel avec ses preuves."""
    preuves = []
    adresses = candidats(profil)
    for i, poste in enumerate(profil.get("currentPosition") or []):
        fin = (poste.get("endDate") or {}).get("text")
        if fin and fin.casefold() not in {"present", "présent", "aujourd’hui"}:
            continue
        site = (poste.get("company") or {}).get("website") or ""
        domaine = urlsplit(site if "://" in site else "//" + site).hostname
        if not domaine:
            continue
        domaine = domaine.removeprefix("www.").rstrip(".")
        if domaine in {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "live.com"}:
            continue
        for adresse in adresses:
            if adresse.count("@") != 1 or adresse.partition("@")[2] != domaine:
                continue
            index = next(j for j, ligne in enumerate(profil["emails"])
                         if ligne["email"].strip().casefold() == adresse)
            preuves.append({"email": adresse, "domaine": domaine,
                            "preuve_adresse": f"/emails/{index}/email",
                            "preuve_employeur": f"/currentPosition/{i}/company/website"})
    if len(preuves) == 1:
        return {"verdict": "correspond", **preuves[0]}
    return {"verdict": "indetermine", "motif": "ambiguïté" if preuves else "aucun domaine attesté"}


def candidats_employeur_actuel(profil):
    decision = qualifier(profil)
    return [decision["email"]] if decision["verdict"] == "correspond" else []



def verifier(fournisseur, base, cle, selection):
    adresses = sorted({a.strip().casefold() for a in selection if a.strip()})
    resultats = {}
    for debut in range(0, len(adresses), 5000):
        lot = adresses[debut:debut + 5000]
        identifiant = sha256("\n".join(lot).encode()).hexdigest()
        lignes = fournisseur.lignes(base, cle + ":dns:" + identifiant, ACTEUR_DNS, entree_lot(lot))
        resultats.update(rapprocher_dns(lot, lignes))
    return resultats
