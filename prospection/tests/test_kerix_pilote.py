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
from prospection.recherche_dirigeants import fonction
from prospection.qualification_kerix import BOUNCE, ZAIQ, normaliser_verdict


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


def juger(dossier, etape):
    if etape == "initial" and dossier["adresses_recevables"]:
        return {"verdict": "qualifie", "email": dossier["adresses_recevables"][0], "motif": "Poste confirmé"}
    if etape == "final" and dossier["verifications"][-1]["verdict"] == "accepte":
        return {"verdict": "admis", "email": dossier["adresse_admise"], "motif": "Adresse validée"}
    return {"verdict": "en_attente", "email": "", "motif": "Adresse à confirmer"}


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


def bounce_ok(adresse):
    return {"email": adresse, "status": "valid", "smtp_valid": True, "is_catch_all": False,
            "is_disposable": False, "is_role_based": False, "is_free_provider": False}


def test_acquisition_admet_et_regroupe_les_recours():
    a, b, c, d, e, f = [
        fiche(nom, str(i).zfill(15), site=f"https://{nom}.example",
              linkedin=f"https://www.linkedin.com/company/{nom}")
        for i, nom in enumerate("abcdef", start=1)
    ]
    gerante = candidat("lea", a, "lea@a.example", "Co-gérante")
    timeout = candidat("tom", b, "tom@b.example", "Gérant")
    inconnu = candidat("theo", c, "theo@c.example", "Gérant")
    maison_mere = candidat("anne", d, "anne@groupe.example", "Gérante")
    homonyme = {**candidat("autre", e, "autre@ailleurs.example", "Gérante"),
                "companyDomain": "https://ailleurs.example", "companyLinkedinUrl": ""}
    refuse = candidat("luc", f, "luc@f.example", "Gérant")

    def jeux(acteur, entree):
        if acteur == PIPELINE:
            return ([gerante, timeout, inconnu, maison_mere, homonyme, refuse]
                    if "gérant" in entree["personTitleIncludes"] else [])
        if acteur == PROFILS:
            return [harvest({**maison_mere, "email": "anne@d.example"})]
        if acteur == EFFECTIFS:
            return [harvest(candidat("emma", e, "emma@e.example"))]
        if acteur == BOUNCE:
            return [{"email": mail, "status": "timeout"} if mail in ("tom@b.example", "theo@c.example") else
                    {"email": mail, "status": "invalid"} if mail == "luc@f.example" else bounce_ok(mail)
                    for mail in reversed(entree["emails"])]
        assert acteur == ZAIQ
        return [{"email": "tom@b.example", "status": "deliverable", "safeToSend": True,
                 "isCatchAll": False, "isDisposable": False, "isRoleAddress": False, "isFreeProvider": False},
                {"email": "theo@c.example", "status": "unknown"}]

    fiches = {x["kerix"]: x for x in (a, b, c, d, e, f)}
    acteurs, crm = Acteurs(jeux), Twenty(API())
    resultat = graphe_acquisition(Kerix(), crm, acteurs, juger, fiche=fiches.__getitem__).invoke(
        {"source": list(fiches), "rubrique": "21300", "taille": 6, "graine": 4, "campagne": "A"})
    assert {x["adresse_admise"] for x in resultat["admis"]} == {
        "lea@a.example", "tom@b.example", "anne@d.example", "emma@e.example",
    }
    assert any("Rattachement" in x["motif"] or "employeur non confirmé" in x["motif"] for x in resultat["attentes"])
    assert any(x["linkedin"].endswith("/anne") and "groupe.example" in str(x) for x in resultat["attentes"])
    assert resultat["bilan"]["entreprises_couvertes"] == 4
    assert [v["verdict"] for v in resultat["controles"]["theo@c.example"]] == ["timeout", "inconnu"]
    assert [entree["emails"] for acteur, entree in acteurs.appels if acteur == ZAIQ] == [
        ["theo@c.example", "tom@b.example"]]
    assert [entree["urls"] for acteur, entree in acteurs.appels if acteur == PROFILS] == [
        ["https://www.linkedin.com/in/anne", "https://www.linkedin.com/in/luc"]]
    assert [entree["companies"] for acteur, entree in acteurs.appels if acteur == EFFECTIFS] == [[e["linkedin"]]]
    assert all("a.example" not in e["companyDomainIncludes"]
               for e in [entree for acteur, entree in acteurs.appels if acteur == PIPELINE][1:])


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
            "verifications": [{"verdict": "accepte", "adresse": f"{nom}@a.example"}]}


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
    assert fonction("Founder & CEO") == "direction_generale"
    assert fonction("Assistant to CEO") is None
    assert fonction("Ancienne directrice générale") is None



def test_verdicts_zaiq_et_timeout_explicite():
    valide = {"status": "deliverable", "safeToSend": True, "isCatchAll": False,
              "isDisposable": False, "isRoleAddress": False, "isFreeProvider": False}
    assert normaliser_verdict(ZAIQ, valide) == "accepte"
    assert normaliser_verdict(ZAIQ, {**valide, "isCatchAll": True}) == "rejete"
    assert normaliser_verdict(ZAIQ, {**valide, "safeToSend": False}) == "inconnu"
    assert normaliser_verdict(BOUNCE, {"status": "unknown"}) == "inconnu"
    assert normaliser_verdict(BOUNCE, {"status": "unknown", "reason": "SMTP timeout"}) == "timeout"
    assert normaliser_verdict(BOUNCE, {"status": "timeout", "is_catch_all": True}) == "rejete"
    assert normaliser_verdict(BOUNCE, None) == "manquant"


def test_recherche_adresse_du_profil_identifie_par_harvest_effectifs():
    e = fiche("a", "000000000000001", site="https://a.example",
              linkedin="https://www.linkedin.com/company/a")
    dirigeante = harvest(candidat("lea", e, "lea@groupe.example", "Founder, CEO"))

    def jeux(acteur, entree):
        if acteur == PIPELINE:
            return []
        if acteur == EFFECTIFS:
            return [dirigeante]
        if acteur == PROFILS:
            assert entree["urls"] == [dirigeante["linkedinUrl"]]
            return [{**dirigeante, "email": "lea@a.example"}]
        assert acteur == BOUNCE and entree["emails"] == ["lea@a.example"]
        return [bounce_ok("lea@a.example")]

    acteurs = Acteurs(jeux)
    resultat = graphe_acquisition(Kerix(), Twenty(API()), acteurs, juger, fiche=lambda url: e).invoke({
        "source": [e["kerix"]], "rubrique": "21300", "taille": 1, "graine": 0, "campagne": "A",
    })
    admis, = resultat["admis"]
    assert admis["adresse_admise"] == "lea@a.example"
    assert admis["recherches_precedentes"][0]["fournisseur"] == "Harvest effectifs"
