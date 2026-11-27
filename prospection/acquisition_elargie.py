"""Cascade Kerix sans condition de poste ni de LinkedIn personnel."""
import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from .collecte import url_personnelle
from .identite_serp import EMAIL, SERP, coordonnees_site, entree_serp, resoudre
from .verification_account56 import ACTEUR_VERIFICATION, plafond_lot, verifier_lot
from .recherche_dirigeants import adresses, domaine, normaliser, page_entreprise, postes, premier


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
            "poste": p,
            "fournisseur": fournisseur, "provenance": provenance, "profil": ligne,
            "adresses": mails, "adresses_recevables": recevables, "domaine_atteste": domaine_atteste,
            "nom_confirme": False,
        })
    return resultats, attentes


def noeuds_elargis(crm, budget, *, harvest_profils, harvest_effectifs, acteurs,
                    lire_site=coordonnees_site, resoudre_serp=resoudre):
    pipeline, profils, effectifs = acteurs

    def rechercher(etat, acteur, entree, maximum, volume):
        reserve = plafond_lot(3 * len(etat["entreprises"]))
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
                                                    "0.50", len(inconnues))
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
                    "poste": {"titre": ""}, "fournisseur": "Site officiel",
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
        couvertes = {c["entreprise"]["ice"] for c in etat["candidats"]
                     if any(etat["controles"].get(a, [{}])[-1].get("verdict") == "accepte"
                            for a in c["adresses_recevables"])}
        return [e for e in etat["entreprises"] if e["ice"] not in couvertes]

    def verifier(etat, *, premier_passage=False):
        controles = dict(etat["controles"])
        deja_admis = {a["entreprise"]["ice"] for a in etat["admis"]}
        par_ice, ices_par_adresse = defaultdict(dict), defaultdict(set)
        def priorite(candidat, adresse):
            generique = adresse.partition("@")[0] in {"contact", "info", "hello", "bonjour", "office"}
            precedents = controles.get(adresse, [])
            deja_ecartee = bool(precedents and precedents[-1]["verdict"] != "accepte")
            return (deja_ecartee, generique, candidat["fournisseur"] != "Site officiel", not candidat["linkedin"], adresse)
        for c in etat["candidats"]:
            ice = c["entreprise"]["ice"]
            for a in c["adresses_recevables"]:
                ices_par_adresse[a].add(ice)
                if not premier_passage and a not in controles and c["fournisseur"] not in ("Harvest profils", "Harvest effectifs"):
                    continue
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
        a_verifier = [a for a in choisies if not any(v["fournisseur"] == ACTEUR_VERIFICATION
                                                    for v in controles.get(a, []))]
        controles_lot = verifier_lot(budget, a_verifier)
        crm.actualiser_rejets(controles_lot)
        for adresse, resultat in controles_lot.items():
            controles[adresse] = [*controles.get(adresse, []), resultat]

        plafond_atteint = etat.get("plafond_atteint", False) or any(
            v["verdict"] == "plafond" for v in controles_lot.values())
        if premier_passage:
            return {"controles": controles, "plafond_atteint": plafond_atteint}

        admissions, attentes = [], []
        personnes = {c["linkedin"] for c in etat["admis"] if c["linkedin"]}
        for adresse, candidat in choisies.items():
            ice = candidat["entreprise"]["ice"]
            if ice in deja_admis:
                continue
            preuves = controles.get(adresse, [])
            resultat = next((v for v in reversed(preuves) if v["fournisseur"] == ACTEUR_VERIFICATION), {})
            verdict = resultat.get("verdict", "hors lot")
            if verdict != "accepte":
                attentes.append({"ice": ice, "motif": "Vérification Account56 · " + verdict,
                                 "adresse": adresse, "verifications": preuves})
                continue
            linkedin = candidat["linkedin"]
            if linkedin and linkedin in personnes:
                attentes.append({"ice": ice, "motif": "Personne déjà retenue", "adresse": adresse,
                                 "verifications": preuves})
                continue
            admissions.append({
                **candidat, "adresse_admise": adresse, "verifications": preuves, "mode_verification": "apify",
                "decision_initiale": {"verdict": "qualifie", "email": adresse, "motif": "Adresse rattachée à l’entreprise"},
                "decision_finale": {"verdict": "admis", "email": adresse, "motif": "Boîte validée par Account56"},
            })
            deja_admis.add(ice)
            if linkedin:
                personnes.add(linkedin)
        return {
            "admis": etat["admis"] + crm.admettre(etat["campagne"], admissions),
            "controles": controles,
            "attentes": etat["attentes"] + attentes,
            "plafond_atteint": plafond_atteint,
        }

    def par_profil(etat):
        es = restantes(etat)
        ids = {e["ice"] for e in es}
        personnes = defaultdict(set)
        for c in etat["candidats"]:
            ice = c["entreprise"]["ice"]
            ecartee = any(etat["controles"].get(a, [{}])[-1].get("verdict") in {"rejete", "risque"}
                          for a in c["adresses_recevables"])
            if ice in ids and c["linkedin"] and (not c["adresses_recevables"] or ecartee):
                personnes[c["linkedin"]].add(ice)
        if not personnes or not harvest_profils:
            return {}
        volume = len(personnes)
        return rattacher(etat, profils, "Harvest profils", {
            "urls": sorted(personnes), "profileScraperMode": "Profile details + email search ($10 per 1k)",
        }, es, Decimal("0.001") + volume * Decimal("0.012"), volume, personnes)

    def par_employeur(etat):
        avec_profil = {c["entreprise"]["ice"] for c in etat["candidats"] if c["linkedin"]}
        es = [e for e in restantes(etat) if e["ice"] not in avec_profil and page_entreprise(e["linkedin"])]
        if not es or not harvest_effectifs:
            return {}
        reserve = plafond_lot(3 * len(etat["entreprises"]))
        taille = min(len(es), int(max(Decimal(0), budget.solde() - reserve) / Decimal("0.055")))
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
        ("pipeline_domaine", par_domaine), ("pipeline_nom", par_nom),
        ("verifier_avant_harvest", lambda etat: verifier(etat, premier_passage=True)),
        ("harvest_profils_elargis", par_profil), ("harvest_effectifs_elargis", par_employeur),
        ("verifier_adresses", verifier),
    ]
