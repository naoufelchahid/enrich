"""Graphe d’acquisition Kerix, des fiches tirées aux admissions dans Twenty."""
from decimal import Decimal
from typing import TypedDict
from langgraph.graph import END, START, StateGraph

from . import acquisition_elargie
from .budget import montant
from .kerix import tirer
from .recherche_dirigeants import domaine, page_entreprise

PIPELINE = "pipelinelabs~lead-scraper-apollo-zoominfo-lusha-ppe"
PROFILS = "harvestapi~linkedin-profile-scraper"
EFFECTIFS = "harvestapi~linkedin-company-employees"


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


def graphe_acquisition(kerix, crm, budget, *, harvest_profils=True, harvest_effectifs=True, fiche=None):
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
    noeuds.extend(acquisition_elargie.noeuds_elargis(crm, budget, harvest_profils=harvest_profils,
                                 harvest_effectifs=harvest_effectifs,
                                 acteurs=(PIPELINE, PROFILS, EFFECTIFS)))
    noeuds.append(("bilan_acquisition", bilan))
    precedent = START
    for nom, operation in noeuds:
        g.add_node(nom, operation)
        g.add_edge(precedent, nom)
        precedent = nom
    g.add_edge(precedent, END)
    return g.compile()
