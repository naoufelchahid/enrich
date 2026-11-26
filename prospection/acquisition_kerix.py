"""Graphe d’acquisition Kerix, des fiches tirées aux admissions dans Twenty."""
from copy import deepcopy
from decimal import Decimal
from typing import TypedDict
from urllib.error import URLError
from urllib.parse import urlsplit

from langgraph.graph import END, START, StateGraph

from .budget import BudgetInsuffisant, montant
from .kerix import tirer
from .qualification_kerix import adresse_recevable, qualifier
from .verification_dns import controler_dns, plafond_lot
from .recherche_dirigeants import TITRES, candidats, domaine, page_entreprise

PIPELINE = "pipelinelabs~lead-scraper-apollo-zoominfo-lusha-ppe"
PROFILS = "harvestapi~linkedin-profile-scraper"
EFFECTIFS = "harvestapi~linkedin-company-employees"
TARIFS = {
    # Une marge au-delà des frais affichés distingue la réservation du coût observé.
    PIPELINE: ("0.001", "0.002"),
    PROFILS: ("0.001", "0.012"),
    EFFECTIFS: ("0.025", "0.015"),
}


def cout(acteur, volume, demarrages=1):
    demarrage, unitaire = map(montant, TARIFS[acteur])
    return demarrages * demarrage + volume * unitaire if volume else Decimal(0)


class Etat(TypedDict, total=False):
    rubrique: str
    taille: int
    graine: int
    campagne: str
    source: list
    tirage: list
    entreprises: list
    exclusions: list
    candidats: list
    attentes: list
    admis: list
    controles: dict
    bilan: dict
    serp: dict
    plafond_atteint: bool


def graphe_acquisition(kerix, crm, budget, juger=qualifier, *, harvest_profils=True,
                      harvest_effectifs=True, fiche=None, elargi=False):
    def selection(etat):
        source = sorted(set(etat.get("source") or kerix.source(etat["rubrique"])))
        return {"source": source, "tirage": tirer(source, etat["taille"], etat["graine"]),
                "candidats": [], "attentes": [], "admis": [], "controles": {}}

    def importer(etat):
        entreprises, exclusions = crm.importer_entreprises(
            [(fiche or kerix.fiche)(url) for url in etat["tirage"]])
        return {"entreprises": entreprises, "exclusions": exclusions}

    def restantes(etat):
        couvertes = {p["entreprise"]["ice"] for p in etat["admis"]}
        return [e for e in etat["entreprises"] if e["ice"] not in couvertes]

    def chercher(etat, acteur, fournisseur, entree, entreprises, volume, personnes=None, *, demarrages=1):
        try:
            lignes, provenance = budget.lignes(
                acteur, entree, cout(acteur, volume, demarrages),
                verification=plafond_lot(volume),
                max_items=volume,
            )
        except (BudgetInsuffisant, URLError, TimeoutError, RuntimeError) as erreur:
            return {"plafond_atteint": isinstance(erreur, BudgetInsuffisant),
                    "attentes": etat["attentes"] + [
                        {"ice": e["ice"], "fournisseur": fournisseur, "motif": str(erreur)} for e in entreprises]}
        trouves, attentes = candidats(lignes, fournisseur, entreprises, personnes, provenance)
        return qualifier_lot(etat, trouves, attentes)

    def avis(dossier, etape):
        try:
            return juger(dossier, etape)
        except (OSError, RuntimeError, ValueError) as erreur:
            return {"verdict": "en_attente", "email": "", "sources": [],
                    "motif": "Incident Codex : " + type(erreur).__name__}

    def qualifier_lot(etat, trouves, attentes):
        dossiers = {}
        for d in trouves:
            cle = d["linkedin"], d["entreprise"]["ice"]
            if cle in dossiers:
                dossiers[cle]["resultats_fournisseur"].append(d["profil"])
                dossiers[cle]["adresses"] = sorted(set(dossiers[cle]["adresses"] + d["adresses"]))
            else:
                dossiers[cle] = deepcopy(d)
                dossiers[cle]["resultats_fournisseur"] = [d["profil"]]
                dossiers[cle]["recherches_precedentes"] = [
                    {"fournisseur": precedent["fournisseur"], "provenance": precedent["provenance"],
                     "resultats": precedent["resultats_fournisseur"], "decision": precedent["decision_initiale"]}
                    for precedent in etat["candidats"]
                    if (precedent["linkedin"], precedent["entreprise"]["ice"]) == cle]
        controles = deepcopy(etat["controles"])
        choisis = {}
        for d in dossiers.values():
            d["adresses_recevables"] = [a for a in d["adresses"] if adresse_recevable(a, d["entreprise"])]
            q = avis(d, "initial")
            d["decision_initiale"] = q
            if q["verdict"] != "qualifie" or q["email"] not in d["adresses_recevables"]:
                attentes.append({**d, "motif": q["motif"] if not q["email"] else
                                 "Adresse ou décision initiale incompatible avec le dossier"})
                continue
            choisis.setdefault(q["email"], []).append(d)
        distincts = {}
        for adresse, choix in choisis.items():
            if len(choix) == 1:
                distincts[adresse] = choix[0]
            else:
                attentes.extend({**d, "motif": "Adresse partagée entre plusieurs dossiers"} for d in choix)
        emails = sorted(distincts.keys() - controles.keys())
        controles_dns = controler_dns(budget, emails)
        controles.update({adresse: [resultat] for adresse, resultat in controles_dns.items()})
        for adresse, d in distincts.items():
            d["verifications"] = controles[adresse]
            verdict = d["verifications"][-1]["verdict"]
            motif = ("DNS positif · preuve SMTP requise" if verdict == "dns_ok" else "Contrôle DNS · " + verdict)
            d["decision_finale"] = {"verdict": "en_attente", "email": adresse, "motif": motif}
            preuve_identite = d["entreprise"].get("preuve_identite")
            d["preuves"] = [
                {"url": d["entreprise"]["kerix"], "extrait": "ICE " + d["entreprise"]["ice"]},
                {"url": d["linkedin"], "extrait": d["poste"]["titre"], "origine": d["provenance"],
                 "pointeur": d["poste"]["pointeur"]},
                *([preuve_identite] if preuve_identite else []),
                *(source for source in d["decision_initiale"].get("sources", [])
                  if urlsplit(source["url"]).scheme in {"https", "http"} and urlsplit(source["url"]).hostname
                  and source["extrait"].strip()),
            ]
            attentes.append({**d, "motif": motif})
        return {"candidats": etat["candidats"] + list(dossiers.values()),
                "attentes": etat["attentes"] + attentes, "controles": controles,
                "plafond_atteint": any(v["verdict"] == "plafond" for v in controles_dns.values())}

    def pipeline(etat, role):
        entreprises = restantes(etat)
        ciblees = [e for e in entreprises if domaine(e["site"])]
        if not ciblees or etat.get("plafond_atteint"):
            return {}
        volume = len(ciblees) * 4
        return chercher(etat, PIPELINE, "PipelineLabs", {
            "companyDomainIncludes": sorted({domaine(e["site"]) for e in ciblees}),
            "companyDomainMatchMode": "strict", "personTitleIncludes": list(TITRES[role]),
            "includeTitleVariants": False, "totalResults": volume, "dontSaveProgress": True,
        }, ciblees, volume)

    def profils(etat, *, apres_effectifs=False):
        entreprises = restantes(etat)
        ices = {e["ice"] for e in entreprises}
        personnes = {}
        for d in etat["candidats"]:
            if apres_effectifs and d["fournisseur"] != "Harvest effectifs":
                continue
            ice = d["entreprise"]["ice"]
            if ice in ices and (not d["adresses_recevables"] or any(
                    etat["controles"].get(a, [{}])[-1].get("verdict") in {"rejete", "risque"}
                    for a in d["adresses_recevables"])):
                personnes.setdefault(d["linkedin"], set()).add(ice)
        if not personnes or not harvest_profils or etat.get("plafond_atteint"):
            return {}
        return chercher(etat, PROFILS, "Harvest profils", {
            "urls": sorted(personnes), "profileScraperMode": "Profile details + email search ($10 per 1k)",
        }, entreprises, len(personnes), personnes)

    def effectifs(etat):
        connus = {d["entreprise"]["ice"] for d in etat["candidats"]}
        entreprises = [e for e in restantes(etat) if page_entreprise(e["linkedin"]) and e["ice"] not in connus]
        if not entreprises or not harvest_effectifs or etat.get("plafond_atteint"):
            return {}
        volume = len(entreprises) * 4
        return chercher(etat, EFFECTIFS, "Harvest effectifs", {
            "companies": sorted({page_entreprise(e["linkedin"]) for e in entreprises}),
            "maxItems": volume, "maxItemsPerCompany": 4, "companyBatchMode": "one_by_one",
            "jobTitles": [titre for variantes in TITRES.values() for titre in variantes],
            "profileScraperMode": "Full + email search ($12 per 1k)",
        }, entreprises, volume, demarrages=len(entreprises))

    def bilan(etat):
        couts = budget.bilan()
        depense = sum((montant(v["depense"]) for v in couts.values()), Decimal(0))
        engage = sum((montant(v["engage"]) for v in couts.values()), Decimal(0))
        couvertes = {d["entreprise"]["ice"] for d in etat["admis"]}
        attentes = etat["attentes"] + [
            {"ice": e["ice"], "kerix": e["kerix"],
             "motif": "Identité insuffisante pour la recherche payante" if not
             (domaine(e["site"]) or page_entreprise(e["linkedin"])) else "Aucun contact admissible"}
            for e in restantes(etat)]
        mesures = {}
        if elargi:
            controles = [vv[-1] for vv in etat["controles"].values() if vv]
            mesures = {
                "serp": etat.get("serp"),
                "dossiers_candidats": len(etat["candidats"]),
                "entreprises_avec_candidats": len({c["entreprise"]["ice"] for c in etat["candidats"]}),
                "adresses_candidates": len({a for c in etat["candidats"] for a in c["adresses_recevables"]}),
                "adresses_soumises": sum(any(v.get("provenance") for v in vv) for vv in etat["controles"].values()),
                "verdicts_verification": {v: sum(x["verdict"] == v for x in controles)
                                          for v in sorted({x["verdict"] for x in controles})},
            }
        return {"attentes": attentes, "bilan": {
            "rubrique": etat["rubrique"], "graine": etat["graine"], "campagne": etat["campagne"],
            "fiches_tirees": len(etat["tirage"]), "exclusions": len(etat["exclusions"]),
            "entreprises_importees": len(etat["entreprises"]), "contacts_admis": len(etat["admis"]),
            **mesures,
            "entreprises_couvertes": len(couvertes), "couverture": len(couvertes) / len(etat["tirage"]),
            "plafond_atteint": etat.get("plafond_atteint", False), "couts_usd": couts, "total_usd": str(depense), "engage_usd": str(engage),
            "cout_par_entreprise_usd": str(depense / len(couvertes)) if couvertes and not engage else None,
        }}

    g = StateGraph(Etat)
    noeuds = [("tirage_kerix", selection), ("import_twenty", importer)]
    if elargi:
        from .acquisition_elargie import noeuds_elargis
        noeuds.extend(noeuds_elargis(crm, budget, harvest_profils=harvest_profils,
                                     harvest_effectifs=harvest_effectifs,
                                     acteurs=(PIPELINE, PROFILS, EFFECTIFS)))
    else:
        noeuds.extend((role, lambda etat, role=role: pipeline(etat, role)) for role in TITRES)
        noeuds.extend([
            ("harvest_profils", profils), ("harvest_effectifs", effectifs),
            ("profils_identifies_par_harvest", lambda etat: profils(etat, apres_effectifs=True)),
        ])
    noeuds.append(("bilan_acquisition", bilan))
    precedent = START
    for nom, operation in noeuds:
        g.add_node(nom, operation)
        g.add_edge(precedent, nom)
        precedent = nom
    g.add_edge(precedent, END)
    return g.compile()
