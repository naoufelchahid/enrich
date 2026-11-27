import json
import sqlite3
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from prospection.acquisition_kerix import EFFECTIFS, PIPELINE, PROFILS, graphe_acquisition
from prospection.budget import Budget, BudgetInsuffisant
from prospection.crm_campagnes import AFFECTATIONS, CAMPAGNES, EXPEDITIONS, Twenty
from prospection.envoi_campagne import graphe_campagne
from prospection.kerix import RUBRIQUES, Kerix, tirer
from prospection.kerix_pilote import confirmer_identites
from prospection.verification_account56 import ACTEUR_VERIFICATION, normaliser_verdict, plafond_lot, verifier_lot


class API:
    def __init__(self):
        self.collections = {n: [] for n in ("companies", "people", AFFECTATIONS, CAMPAGNES, EXPEDITIONS, "blocklists")}
        self.appels = []

    def __call__(self, methode, chemin, *, params=None, contenu=None):
        self.appels.append((methode, chemin, dict(params or {})))
        if chemin.startswith("batch/"):
            ressource = chemin.split("/")[1]
            lignes = self.collections[ressource]
            cle = {"companies": "kerixIce", "people": "prospectingIdentity",
                   AFFECTATIONS: "assignmentKey", EXPEDITIONS: "deliveryKey",
                   CAMPAGNES: "reference"}[ressource]
            ecrites = []
            for charge in contenu:
                precedent = next((r for r in lignes if r[cle] == charge[cle]), None)
                if precedent and not (params or {}).get("upsert"):
                    raise HTTPError(chemin, 409, "Conflit", None, None)
                if precedent:
                    precedent.update(charge)
                else:
                    precedent = {**charge, "id": f"{ressource}-{len(lignes)+1}"}
                    lignes.append(precedent)
                ecrites.append(dict(precedent))
            return {"data": {"ecriture": ecrites[::-1]}}
        if "/" in chemin:
            ressource, identifiant = chemin.split("/")
            ligne = next((r for r in self.collections[ressource] if r["id"] == identifiant), None)
            if ligne is None:
                raise HTTPError(chemin, 404, "Absent", None, None)
            if methode == "PATCH":
                ligne.update(contenu)
            return {"data": {"record": dict(ligne)}}
        lignes = self.collections[chemin]
        if "filter" in params:
            champ, valeur = params["filter"].split("[eq]:")
            lignes = [r for r in lignes if r.get(champ) == valeur]
        offset = int(params.get("starting_after", "0"))
        return {"data": {chemin: [dict(x) for x in lignes[offset:offset+1]]},
                "pageInfo": {"hasNextPage": len(lignes) > offset+1, "endCursor": str(offset+1)}}


def fiche(nom, ice, *, site="", linkedin=""):
    return {"kerix": "https://www.kerix.net/fr/annuaire-entreprise/" + nom, "nom": nom,
            "ice": ice, "site": site, "linkedin": linkedin, "fiche": "Données Kerix"}


def candidat(nom, societe, adresse, titre="Directrice générale"):
    return {"linkedinUrl": "https://www.linkedin.com/in/" + nom, "firstName": nom, "lastName": "Pilote",
            "title": titre, "companyName": societe["nom"], "companyDomain": societe["site"],
            "email": adresse, "companyLinkedinUrl": societe["linkedin"]}


def harvest(ligne):
    return {**ligne, "currentPosition": [{"position": ligne["title"], "companyName": ligne["companyName"],
             "company": {"website": ligne["companyDomain"], "linkedinUrl": ligne["companyLinkedinUrl"]}}]}


def test_kerix_pagine_et_importe_par_ice():
    racine = RUBRIQUES["21300"]
    premiere, seconde, troisieme = [fiche(n, i) for n, i in (("a", "000000000000001"), ("b", ""), ("c", "000000000000003"))]

    def lien(f):
        return f"[Voir plus]({f['kerix']})"

    pages = {racine: f"3 entreprises trouvées #21300\n{lien(premiere)}\n{lien(seconde)}\n[2]({racine}?page=2)",
             racine+"?page=2": f"{lien(premiere)}\n{lien(troisieme)}"}
    for f in (premiere, seconde, troisieme):
        pages[f["kerix"]] = "# " + f["nom"] + "\n**Ice:**\n" + f["ice"]
    pages[premiere["kerix"]] += '\n[![](image.png)\nSite Web ![voir site](icone.png)](https://example.org/ "Consulter le site web")'

    kerix = Kerix(pages.__getitem__)
    source = kerix.source("21300")
    assert len(source) == 3
    assert tirer(source, 3, 21300) == tirer(source[::-1], 3, 21300)
    api = API()
    crm = Twenty(api)
    importees, exclusions = crm.importer_entreprises([kerix.fiche(url) for url in source])
    assert {f["ice"] for f in importees} == {"000000000000001", "000000000000003"}
    assert {f["site"] for f in importees} == {"", "https://example.org/"}
    assert [f["motif"] for f in exclusions] == ["ICE absent"]
    assert any(chemin == "batch/companies" for _, chemin, _ in api.appels)
    assert crm.importer_entreprises([premiere, {**premiere, "kerix": troisieme["kerix"]}])[0] == []


class Acteurs:
    def __init__(self, jeux):
        self.jeux, self.appels = jeux, []

    def lignes(self, acteur, entree, montant, **kwargs):
        self.appels.append((acteur, entree))
        return self.jeux(acteur, entree), {"run": acteur + "-test"}

    def bilan(self):
        return {PIPELINE: {"depense": "0.15", "engage": "0"}}


def account56_ok(adresse):
    return {"email": adresse, "result": "ok", "quality": "good", "subresult": "ok", "error": ""}


def test_budget_reserve_verification_et_reutilise_run():
    class Apify:
        def __init__(self):
            self.starts = []

        def appeler(self, chemin, entree=None):
            if chemin == "acts/acteur":
                return {"data": {"pricingInfos": [{"pricingModel": "PAY_PER_EVENT"}]}}
            if entree:
                self.starts.append(parse_qs(urlsplit(chemin).query))
            return {"data": {"id": "run", "status": "SUCCEEDED", "usageTotalUsd": 0.4, "defaultDatasetId": "ds"}}

        def attendre(self, run):
            return run

        def dataset(self, identifiant):
            return [{"test": identifiant}]

    apify = Apify()
    with sqlite3.connect(":memory:") as db:
        budget = Budget(db, apify, 3)
        with pytest.raises(BudgetInsuffisant):
            budget.lignes("acteur", {"lot": 1}, "2.75", verification="0.26")
        assert apify.starts == []
        a = budget.lignes("acteur", {"lot": 2}, "2.75", verification="0.25")
        assert budget.lignes("acteur", {"lot": 2}, "2.75") == a
        assert len(apify.starts) == 1
        assert apify.starts[0]["maxTotalChargeUsd"] == ["2.75"]
        assert budget.bilan()["acteur"] == {"depense": "0.4", "engage": "0"}
        with pytest.raises(BudgetInsuffisant):
            budget.lignes("acteur", {"lot": 3}, "0.26")
        assert len(apify.starts) == 1


def dossier(entreprise, nom="lea"):
    return {"adresse_admise": f"{nom}@a.example", "linkedin": f"https://www.linkedin.com/in/{nom}",
            "nom": "Exemple", "prenom": nom, "entreprise": entreprise, "poste": {"titre": "Gérante"},
            "decision_initiale": {"verdict": "qualifie"}, "decision_finale": {"verdict": "admis", "email": f"{nom}@a.example"},
            "verifications": [{
                "verdict": "accepte", "adresse": f"{nom}@a.example",
                "fournisseur": ACTEUR_VERIFICATION, "brut": account56_ok(f"{nom}@a.example"),
            }]}


def test_redaction_fige_les_contacts_et_suit_a_sans_toucher_b():
    api = API()
    crm = Twenty(api)
    e, = crm.importer_entreprises([fiche("a", "000000000000001")])[0]
    initiale, retiree = dossier(e), dossier(e, "zoe")
    a1, a2 = crm.admettre("A", [initiale, retiree])[::-1]
    b1, = crm.admettre("B", [initiale])
    for ref in ("A", "B"):
        list(crm.lots(CAMPAGNES, [{"reference": ref, "briefJson": json.dumps({"offre": "Recette"})}]))
    transmis = []

    def ecrire(entree):
        crm.admettre("A", [dossier(e, "ajout")])
        assert entree["profil"]["entreprise"]["ice"] == e["ice"]
        return {"version": 1, "revue": {"verdict": "approuve", "version": 1},
                "brouillon": {"objet": "Recette", "corps": "Texte de test"}}

    g = graphe_campagne(crm, ecrire=ecrire, transmettre=lambda charge: transmis.append(charge) or {"id": "recu"})
    etat = g.invoke({"campagne": "A"})
    assert set(map(tuple, etat["selection"])) == {
        (a["twenty_id"], a["affectation_id"]) for a in (a1, a2)}
    rediges = {b["affectation"]: b for b in etat["brouillons"]}
    assert crm.record(AFFECTATIONS, b1["affectation_id"]).get("draftJson") is None
    crm.modifier(AFFECTATIONS, a2["affectation_id"], withdrawn=True)

    def envoyer(a):
        identifiant = a["affectation_id"]
        return g.invoke({"campagne": "A", "affectation_id": identifiant,
                         "approbation": rediges[identifiant]["empreinte"],
                         "boite_test": "controle@example.invalid"})["resultat"]

    entree = {"campagne": "A", "affectation_id": a1["affectation_id"],
              "approbation": rediges[a1["affectation_id"]]["empreinte"]}
    for mode in ({}, {"boite_test": ""}, {"production": True, "boite_test": "test@example.org"}):
        with pytest.raises(ValueError):
            g.invoke({**entree, **mode})
    assert transmis == []
    assert envoyer(a2)["etat"] == "ecarte"
    personne = crm.record("people", a1["twenty_id"])
    crm.modifier("people", personne["id"], prospectingUnsubscribed=True)
    assert envoyer(a1)["etat"] == "ecarte"
    crm.modifier("people", personne["id"], prospectingUnsubscribed=False, emails={"primaryEmail": "autre@a.example"})
    assert envoyer(a1)["etat"] == "ecarte"
    crm.modifier("people", personne["id"], emails=personne["emails"])
    assert envoyer(a1)["etat"] == "confirme"
    assert envoyer(a1)["etat"] == "confirme"
    assert len(transmis) == 1 and transmis[0]["adresse"] == "controle@example.invalid"
    reimporte, = crm.admettre("A", [initiale])
    assert reimporte["twenty_id"] == b1["twenty_id"] == a1["twenty_id"]
    assert reimporte["affectation_id"] == a1["affectation_id"] != b1["affectation_id"]
    assert len(list(crm.lignes(EXPEDITIONS, filter="campaignRef[eq]:A"))) == 1
    assert list(crm.lignes(EXPEDITIONS, filter="campaignRef[eq]:B")) == []


def test_identite_revue_ne_change_ni_les_fiches_ni_les_adresses_candidates():
    f = fiche("a", "000000000000001")
    source = {f["kerix"]: f}
    preuve = {"site": "https://a.example", "url": "https://a.example/coordonnees",
              "extrait": "Entreprise A, Casablanca"}
    avec_identite = confirmer_identites(source, {f["ice"]: preuve})
    assert avec_identite[f["kerix"]]["preuve_identite"] == preuve
    assert avec_identite[f["kerix"]]["site"] == "https://a.example"
    assert source[f["kerix"]]["site"] == ""
    with pytest.raises(ValueError, match="hors tirage"):
        confirmer_identites(source, {"000000000000002": preuve})



def test_verdicts_account56_et_plafond():
    from decimal import Decimal

    valide = account56_ok("lea@a.example")
    assert normaliser_verdict(valide) == "accepte"
    assert normaliser_verdict({**valide, "result": "catch_all"}) == "risque"
    assert normaliser_verdict({**valide, "result": "disposable"}) == "rejete"
    assert normaliser_verdict({**valide, "result": "unknown", "subresult": "timeout"}) == "timeout"
    assert normaliser_verdict({**valide, "result": "error"}) == "erreur"
    assert normaliser_verdict({**valide, "error": "échec"}) == "erreur"
    assert normaliser_verdict({**valide, "quality": "risky"}) == "inconnu"
    assert normaliser_verdict({**valide, "subresult": "no_mailbox"}) == "inconnu"
    assert normaliser_verdict({**valide, "didyoumean": "autre@a.example"}) == "inconnu"
    assert normaliser_verdict(None) == "manquant"
    assert plafond_lot(19) == Decimal("0.50")
    assert plafond_lot(500) == Decimal("0.5001")


def test_lot_account56_deduplique_et_refuse_une_reponse_ambigue():
    appels = []

    class Apify:
        def appeler(self, chemin, entree=None):
            if chemin == "acts/" + ACTEUR_VERIFICATION:
                return {"data": {"pricingInfos": [{"pricingModel": "PAY_PER_EVENT"}]}}
            if entree is not None:
                appels.append((chemin, entree))
            return {"data": {"id": "run-account56", "status": "SUCCEEDED", "usageTotalUsd": 0.0011,
                             "finishedAt": "2026-11-26T12:00:00Z", "defaultDatasetId": "ds-account56"}}

        def attendre(self, run):
            return run

        def dataset(self, identifiant):
            a = account56_ok("lea@a.example")
            return [a, {**a, "email": "LEA@a.example"}]

    with sqlite3.connect(":memory:") as db:
        budget = Budget(db, Apify(), "0.50")
        controles = verifier_lot(budget, [" LEA@a.example ", "lea@a.example", "emma@b.example"])
        assert verifier_lot(budget, ["emma@b.example", "lea@a.example"]) == controles

    assert len(appels) == 1
    chemin, entree = appels[0]
    assert urlsplit(chemin).path == "acts/" + ACTEUR_VERIFICATION + "/runs"
    assert parse_qs(urlsplit(chemin).query)["maxTotalChargeUsd"] == ["0.50"]
    assert entree == {"emails": ["emma@b.example", "lea@a.example"]}
    assert controles["lea@a.example"]["verdict"] == "reponse_ambigue"
    assert controles["emma@b.example"]["verdict"] == "manquant"


def test_cascade_elargie_admet_avec_account56_et_refuse_les_preuves_historiques(monkeypatch):
    from decimal import Decimal
    from prospection import acquisition_elargie as module
    from prospection.envoi_campagne import adresse_admise
    a = fiche("a", "000000000000001", site="https://a.example", linkedin="https://www.linkedin.com/company/a")
    b = fiche("b", "000000000000002")
    c = fiche("c", "000000000000003", site="https://c.example", linkedin="https://www.linkedin.com/company/c")
    d = fiche("d", "000000000000004", site="https://d.example", linkedin="https://www.linkedin.com/company/d")
    profil_d = "https://www.linkedin.com/in/luc"
    fiches = {x["kerix"]: x for x in (a, b, c, d)}
    appels = []

    class ActeursElargis(Acteurs):
        def solde(self):
            return Decimal("3")

    def jeux(acteur, entree):
        appels.append((acteur, entree))
        if acteur == module.SERP:
            return [{"serp": True}]
        if acteur == PIPELINE and "companyDomainIncludes" in entree:
            assert "personTitleIncludes" not in entree
            return [
                {"firstName": "Camille", "title": "Comptable", "companyName": "a",
                 "companyDomain": "a.example", "email": "camille@a.example"},
                {"firstName": "Dani", "title": "Assistante", "companyName": "autre",
                 "companyDomain": "c.example", "email": "dani@c.example"},
                {"linkedinUrl": profil_d, "companyDomain": "d.example", "email": "ancien@d.example"},
            ]
        if acteur == PIPELINE:
            return []
        if acteur == EFFECTIFS:
            assert entree["companies"] == [c["linkedin"]]
            return [{"email": "paul@c.example", "linkedinUrl": "https://www.linkedin.com/in/paul",
                     "currentPosition": [{"position": "Comptable", "company": {"website": c["site"]}}]}]
        if acteur == PROFILS:
            assert entree["urls"] == [profil_d]
            return [{"email": "luc@d.example", "linkedinUrl": profil_d,
                     "currentPosition": [{"position": "Comptable", "company": {"website": d["site"]}}]}]
        if acteur == ACTEUR_VERIFICATION:
            return [{**account56_ok(a), "result": "catch_all", "quality": "risky"}
                    if a in {"dani@c.example", "ancien@d.example"} else account56_ok(a)
                    for a in entree["emails"]]
        return []

    original = module.noeuds_elargis
    monkeypatch.setattr(module, "noeuds_elargis", lambda *args, **kwargs: original(
        *args, **kwargs, resoudre_serp=lambda e, l: {b["ice"]: {
            "site": "https://b.example", "linkedin": "", "preuve_identite": {
                "url": "https://b.example", "extrait": "Entreprise B"}}},
        lire_site=lambda e: [{"email": "contact@b.example", "url": e["site"], "extrait": "contact@b.example"}]
        if e["ice"] == b["ice"] else [],
    ))
    crm = Twenty(API())
    graphe = graphe_acquisition(Kerix(), crm, ActeursElargis(jeux), fiche=fiches.__getitem__)
    bilan = graphe.invoke({"source": list(fiches), "rubrique": "21300", "taille": 4, "graine": 4, "campagne": "A"})
    assert {d["adresse_admise"] for d in bilan["admis"]} == {
        "camille@a.example", "contact@b.example", "paul@c.example", "luc@d.example",
    }
    assert sum(not d["linkedin"] for d in bilan["admis"]) == 2
    assert len(bilan["candidats"]) == 6
    assert [(acteur, entree["emails"]) for acteur, entree in appels if acteur == ACTEUR_VERIFICATION] == [
        (ACTEUR_VERIFICATION, ["ancien@d.example", "camille@a.example", "contact@b.example", "dani@c.example"]),
        (ACTEUR_VERIFICATION, ["luc@d.example", "paul@c.example"])]
    assert bilan["bilan"]["verdicts_verification"] == {"accepte": 4, "risque": 2}
    contact = next(d for d in bilan["admis"] if d["adresse_admise"] == "camille@a.example")
    p = crm.record("people", contact["twenty_id"])
    assert adresse_admise(p) == "camille@a.example"
    preuve, = contact["verifications"]
    for modifiee in ({**preuve, "fournisseur": "bounceverify~bounceverify-email-verifier"},
                     {**preuve, "brut": account56_ok("autre@a.example")},
                     {**preuve, "brut": {**preuve["brut"], "result": "catch_all"}}):
        invalide = {**contact, "verifications": [modifiee]}
        assert adresse_admise({**p, "prospectingDossier": json.dumps(invalide)}) is None

def test_rejet_recent_invalide_le_dossier_et_empeche_sa_readmission_depuis_un_ancien_lot():
    from prospection.envoi_campagne import adresse_admise

    crm = Twenty(API())
    entreprise, = crm.importer_entreprises([fiche("a", "000000000000001")])[0]
    contact = dossier(entreprise)
    adresse = contact["adresse_admise"]
    valide, = contact["verifications"]
    valide["date"] = "2026-11-26T12:00:00Z"
    admis, = crm.admettre("A", [contact])
    rejet = {**valide, "date": "2026-11-27T12:00:00Z", "verdict": "rejete",
             "brut": {"email": adresse, "result": "invalid"}, "provenance": {"run": "second"}}
    crm.actualiser_rejets({adresse: rejet})
    assert adresse_admise(crm.record("people", admis["twenty_id"])) is None
    assert crm.admettre("A", [contact]) == []
    valide["date"] = "2026-11-28T12:00:00Z"
    crm.admettre("A", [contact])
    crm.actualiser_rejets({adresse: rejet})
    assert adresse_admise(crm.record("people", admis["twenty_id"])) == adresse
