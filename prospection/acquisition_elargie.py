"""Cascade Kerix sans condition de poste ni de LinkedIn personnel."""
import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from .collecte import url_personnelle
from .identite_serp import EMAIL, SERP, coordonnees_site, entree_serp, resoudre
from .qualification_kerix import BOUNCE, ZAIQ, controle
from .recherche_dirigeants import adresses, domaine, fonction, normaliser, page_entreprise, postes, premier


def noms(entreprise):
    nom = entreprise["nom"]
    morceaux = re.split(r"[()]", nom)
    return {normaliser(n) for n in [nom, *morceaux] if len(normaliser(n)) >= 3}


def candidats_elargis(lignes, fournisseur, entreprises, provenance, personnes=None):
    resultats, attentes = [], []
    for ligne in lignes:
        if ligne.get("rowType") == "diagnostic":
            continue
        url = url_personnelle(premier(ligne, "linkedinUrl", "linkedin_url", "personLinkedInUrl", "personLinkedinUrl"))
        mails = adresses(ligne)
        choix = {}
        for p in postes(ligne, fournisseur):
            for e in entreprises:
                if personnes is not None and e["ice"] not in personnes.get(url, set()):
                    continue
                if (p["domaine"] and p["domaine"] == domaine(e["site"])
                        or p["linkedin"] and p["linkedin"] == page_entreprise(e["linkedin"])
                        or normaliser(p["nom"]) in noms(e)):
                    choix.setdefault(e["ice"], (e, p))
        if len(choix) != 1:
            attentes.append({"fournisseur": fournisseur, "motif": "Employeur absent ou ambigu", "brut": ligne})
            continue
        e, p = next(iter(choix.values()))
        # Le poste et l’identité de la personne servent au dossier. L’adresse s’attache à l’entreprise.
        domaine_atteste = domaine(e["site"])
        if not domaine_atteste and p["linkedin"] and p["linkedin"] == page_entreprise(e["linkedin"]):
            domaine_atteste = p["domaine"]
        recevables = [a for a in mails if EMAIL.fullmatch(a) and a.partition("@")[2] == domaine_atteste]
        resultats.append({
            "linkedin": url, "prenom": premier(ligne, "firstName", "first_name"),
            "nom": premier(ligne, "lastName", "last_name"), "entreprise": e,
            "poste": {**p, "fonction": fonction(p["titre"])},
            "fournisseur": fournisseur, "provenance": provenance, "profil": ligne,
            "adresses": mails, "adresses_recevables": recevables, "domaine_atteste": domaine_atteste,
            "nom_confirme": False,
        })
    return resultats, attentes


def noeuds_elargis(crm, budget, *, harvest_profils, harvest_effectifs, zaiq, acteurs,
                    lire_site=coordonnees_site, resoudre_serp=resoudre):
    pipeline, profils, effectifs = acteurs

    def rechercher(etat, acteur, entree, maximum, volume, *, reserve="0.18"):
        try:
            return budget.lignes(acteur, entree, maximum, verification=reserve, max_items=volume), []
        except (OSError, ValueError, RuntimeError) as erreur:
            return ([], None), [{"fournisseur": acteur, "motif": str(erreur)}]

    def identites(etat):
        entreprises = etat["entreprises"]
        inconnues = [e for e in entreprises if not (domaine(e["site"]) or page_entreprise(e["linkedin"]))]
        if not inconnues:
            return {}
        (lignes, provenance), attentes = rechercher(etat, SERP, entree_serp(inconnues),
                                                    "0.50", len(inconnues), reserve="0.20")
        preuves = resoudre_serp(inconnues, lignes)
        entreprises = [{**e, **preuves.get(e["ice"], {})} for e in entreprises]
        for e in entreprises:
            if e["ice"] not in preuves:
                continue
            charge = {"kerixDossier": json.dumps(e, ensure_ascii=False)}
            if e["site"]:
                charge["domainName"] = {"primaryLinkUrl": e["site"]}
            if e["linkedin"]:
                charge["linkedinLink"] = {"primaryLinkUrl": e["linkedin"]}
            crm.modifier("companies", e["twenty_id"], **charge)
        return {"entreprises": entreprises, "attentes": etat["attentes"] + attentes,
                "serp": {"entreprises_recherchees": len(inconnues), "identites_confirmees": len(preuves),
                         "provenance": provenance}}

    def sites(etat):
        entreprises = etat["entreprises"]
        with ThreadPoolExecutor(max_workers=10) as pool:
            par_entreprise = list(pool.map(lire_site, entreprises))
        candidats = []
        for e, mails in zip(entreprises, par_entreprise):
            if mails:
                candidats.append({
                    "linkedin": None, "prenom": "", "nom": "", "entreprise": e,
                    "poste": {"titre": "", "fonction": None}, "fournisseur": "Site officiel",
                    "provenance": {"sources": mails}, "profil": {"coordonnees": mails},
                    "adresses": [m["email"] for m in mails], "adresses_recevables": [m["email"] for m in mails],
                    "nom_confirme": False,
                })
        return {"candidats": etat["candidats"] + candidats}

    def rattacher(etat, acteur, nom, entree, es, maximum, volume, personnes=None):
        (lignes, preuve), incidents = rechercher(etat, acteur, entree, maximum, volume)
        candidats, attentes = candidats_elargis(lignes, nom, es, preuve, personnes)
        return {"candidats": etat["candidats"] + candidats, "attentes": etat["attentes"] + incidents + attentes}

    def par_domaine(etat):
        es = [e for e in etat["entreprises"] if domaine(e["site"])]
        if not es:
            return {}
        volume = len(es) * 3
        return rattacher(etat, pipeline, "PipelineLabs domaine", {
            "companyDomainIncludes": sorted({domaine(e["site"]) for e in es}),
            "companyDomainMatchMode": "strict", "totalResults": volume, "dontSaveProgress": True,
        }, es, Decimal("0.001") + volume * Decimal("0.002"), volume)

    def par_nom(etat):
        trouves = {d["entreprise"]["ice"] for d in etat["candidats"] if d["fournisseur"] == "PipelineLabs domaine"}
        es = [e for e in etat["entreprises"] if e["ice"] not in trouves]
        if not es:
            return {}
        volume = len(es) * 3
        libelles = sorted({n.strip() for e in es for n in re.split(r"[()]", e["nom"]) if len(n.strip()) > 2})
        return rattacher(etat, pipeline, "PipelineLabs nom", {
            "companyNameIncludes": libelles, "companyNameMatchMode": "phrase",
            "companyLocationCountryIncludes": ["Morocco"], "totalResults": volume, "dontSaveProgress": True,
        }, es, Decimal("0.001") + volume * Decimal("0.002"), volume)

    def restantes(etat):
        ices = {a["entreprise"]["ice"] for a in etat["admis"]}
        return [e for e in etat["entreprises"] if e["ice"] not in ices]

    def verifier(etat):
        controles = dict(etat["controles"])
        deja_admis = {a["entreprise"]["ice"] for a in etat["admis"]}
        par_ice, ices_par_adresse = defaultdict(dict), defaultdict(set)
        def priorite(candidat, adresse):
            generique = adresse.partition("@")[0] in {"contact", "info", "hello", "bonjour", "office"}
            return (generique, candidat["fournisseur"] != "Site officiel", not candidat["linkedin"], adresse)
        for c in etat["candidats"]:
            ice = c["entreprise"]["ice"]
            for a in c["adresses_recevables"]:
                ices_par_adresse[a].add(ice)
                precedent = par_ice[ice].get(a)
                if not precedent or priorite(c, a) < priorite(precedent, a):
                    par_ice[ice][a] = c
        choisies = {}
        for index in range(3):
            for ice, mails in par_ice.items():
                if ice in deja_admis:
                    continue
                options = sorted(((a, c) for a, c in mails.items() if len(ices_par_adresse[a]) == 1),
                                 key=lambda p: priorite(p[1], p[0]))
                if index < len(options):
                    a, c = options[index]
                    choisies[a] = c
        a_verifier = [a for a in choisies if a not in controles]
        solde = budget.solde()
        limite = min(100, int(max(Decimal(0), solde - Decimal("0.02")) / Decimal("0.003")))
        lot = sorted(a_verifier[:limite])
        if lot:
            maximum = Decimal("0.01") + len(lot) * Decimal("0.002")
            reserve = Decimal("0.01") + len(lot) * Decimal("0.001") if zaiq else Decimal(0)
            primaires = controle(budget, BOUNCE, lot, maximum, reserve=reserve)
            timeouts = sorted(a for a, v in primaires.items() if v["verdict"] == "timeout")
            recours = controle(budget, ZAIQ, timeouts, reserve) if timeouts and zaiq else {}
            for a, v in primaires.items():
                controles[a] = [v, recours[a]] if a in recours else [v]
        nouveaux, attentes = [], []
        for a, c in choisies.items():
            ice = c["entreprise"]["ice"]
            verifications = controles.get(a, [])
            preuve = verifications[-1] if verifications else {}
            if ice in deja_admis:
                continue
            if preuve.get("verdict") != "accepte":
                attentes.append({"ice": ice, "motif": "Vérification Apify · " + preuve.get("verdict", "hors lot"),
                                 "adresse": a, "verifications": controles.get(a, [])})
                continue
            nouveaux.append({
                **c, "adresse_admise": a, "verifications": verifications, "mode_verification": "apify",
                "decision_initiale": {"verdict": "qualifie", "email": a, "motif": "Adresse rattachée à l’entreprise"},
                "decision_finale": {"verdict": "admis", "email": a, "motif": "Adresse exacte validée par un vérificateur Apify"},
            })
            deja_admis.add(ice)
        return {"admis": etat["admis"] + crm.admettre(etat["campagne"], nouveaux), "controles": controles,
                "attentes": etat["attentes"] + attentes}

    def par_profil(etat):
        es = restantes(etat)
        ids = {e["ice"] for e in es}
        personnes = defaultdict(set)
        for c in etat["candidats"]:
            ice = c["entreprise"]["ice"]
            if ice in ids and c["linkedin"] and not c["adresses_recevables"]:
                personnes[c["linkedin"]].add(ice)
        if not personnes or not harvest_profils:
            return {}
        volume = len(personnes)
        return rattacher(etat, profils, "Harvest profils", {
            "urls": sorted(personnes), "profileScraperMode": "Profile details + email search ($10 per 1k)",
        }, es, Decimal("0.001") + volume * Decimal("0.012"), volume, personnes)

    def par_employeur(etat):
        es = [e for e in restantes(etat) if page_entreprise(e["linkedin"])]
        if not es or not harvest_effectifs:
            return {}
        taille = min(len(es), int(max(Decimal(0), budget.solde() - Decimal("0.18")) / Decimal("0.055")))
        es = es[:taille]
        if not es:
            return {"attentes": etat["attentes"] + [{"fournisseur": effectifs, "motif": "Solde insuffisant"}]}
        volume = len(es) * 2
        return rattacher(etat, effectifs, "Harvest effectifs", {
            "companies": sorted({page_entreprise(e["linkedin"]) for e in es}),
            "maxItems": volume, "maxItemsPerCompany": 2, "companyBatchMode": "one_by_one",
            "profileScraperMode": "Full + email search ($12 per 1k)",
        }, es, Decimal("0.055") * len(es), volume)

    return [
        ("resoudre_identites", identites), ("lire_sites_officiels", sites),
        ("pipeline_domaine", par_domaine), ("pipeline_nom", par_nom), ("verifier_premier_lot", verifier),
        ("harvest_profils_elargis", par_profil), ("harvest_effectifs_elargis", par_employeur),
        ("verifier_recours", verifier),
    ]
