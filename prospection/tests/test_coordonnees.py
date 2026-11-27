from copy import deepcopy

from prospection.coordonnees import boites_publiees, completer
from prospection.identite_serp import analyser, documents_serp, explorer, requete_serp


def fait(champ, valeur, origine="Résumé IA", url="https://google.com/search?q=exemple"):
    return {"champ": champ, "valeur": valeur, "origine": origine, "url": url,
            "extrait": valeur, "date": "2026-11-27T12:00:00Z"}


def test_digest_extrait_exact_et_domaine_confirme():
    e = {"nom": "Exemple", "kerix": "https://kerix.net/exemple", "site": "https://exemple.ma"}
    docs = documents_serp({"url": "https://google.com/search?q=exemple",
                           "aiOverview": {"content": "Contact : info@exemple.ma", "sources": []},
                           "organicResults": [{"url": e["site"], "title": "Exemple", "description": "Siège"}]})
    def avis(*args):
        return {"pages": [], "faits": [
            {"champ": "email", "valeur": valeur, "extrait": ex, "source": idx}
            for valeur, ex, idx in (("info@exemple.ma", "info@exemple.ma", 0),
                                   ("invente@exemple.ma", "Siège", 1),
                                   ("info@exemple.ma", "info@exemple.ma", -1))]}
    preuves, _ = analyser(e, docs, agent=avis)
    autres = [fait("email", "prenom.nom@exemple.ma", "Site officiel", e["site"]),
              fait("email", "cabinet@gmail.com"), fait("email", "info@homonyme.ma"),
              fait("email", "accueil@hotmail.fr", "Site officiel", e["site"])]
    assert {v["email"] for v in boites_publiees({**e, "coordonnees_documentees": preuves + autres})} == {
        "info@exemple.ma", "accueil@hotmail.fr"}


def test_exploration_bornee_aux_liens_du_site():
    e = {"nom": "Exemple", "site": "https://exemple.ma", "kerix": "https://kerix.net/exemple"}
    pages = []
    def lire(url):
        pages.append(url)
        return url, ''.join(f'<a href="/contact-{i}">Contact</a>' for i in range(9)) + '<a href="https://autre.ma">x</a>'
    def agent(e, documents, liens):
        return {"pages": ["https://tiers.ma", *liens], "faits": []}
    assert explorer(e, lire=lire, agent=agent) == []
    assert len(set(pages)) == len(pages) == 5
    assert all(u.startswith(e["site"]) for u in pages)


def test_conflit_sans_ecrasement_et_reimport_idempotent():
    class CRM:
        def __init__(self):
            self.fiche = {"domainName": {"primaryLinkUrl": "https://exemple.ma"},
                          "address": {"addressCity": "Rabat", "addressPostcode": "10000"}}
            self.modifications = 0
        def record(self, *args):
            return deepcopy(self.fiche)
        def modifier(self, *args, **charge):
            self.fiche.update(charge)
            self.modifications += 1
    crm = CRM()
    e = {"ice": "123", "twenty_id": "id", "nom": "Exemple", "kerix": "https://kerix.net/exemple"}
    preuves = [fait("ville", "Casablanca"), fait("telephone", "+212 666550772"),
               fait("telephone", "+212 666550773"), fait("activite", "Conseil")]
    e = completer(crm, e, preuves)
    assert crm.fiche["address"] == {"addressCity": "Rabat", "addressPostcode": "10000"}
    assert "phones" not in crm.fiche
    assert crm.fiche["activitySector"] == "Conseil"
    assert len(e["coordonnees_documentees"]) == 4
    assert completer(crm, e, preuves) == e
    assert crm.modifications == 1


def test_requete_kerix():
    e = {"nom": "Exemple", "kerix": "https://kerix.net/exemple",
         "fiche": "12 rue Exemple\n20100\nCasablanca\n -\n Maroc\n\n##### ACTIVITES\n\nConseil en gestion"}
    assert "Casablanca Maroc Conseil en gestion" in requete_serp(e)
    assert requete_serp(e, True) != requete_serp(e)


def test_recours_apres_trois_echecs_et_option_par_campagne():
    from decimal import Decimal
    from prospection.acquisition_elargie import noeuds_elargis
    from prospection.verification_account56 import ACTEUR_VERIFICATION

    entreprise = {"ice": "ice", "nom": "Exemple", "site": "https://exemple.ma"}
    def candidat(s, mails):
        return {"entreprise": entreprise, "fournisseur": s, "linkedin": None, "prenom": "", "nom": "",
                "poste": {"titre": ""}, "adresses_recevables": mails}
    personnelles = [f"prenom.{n}@exemple.ma" for n in range(3)]
    boite = "contact@exemple.ma"
    class Budget:
        def __init__(self, nom_valide):
            self.nom_valide = nom_valide
            self.lots = []
        def lignes(self, acteur, entree, plafond):
            assert acteur == ACTEUR_VERIFICATION and plafond >= Decimal("0.50")
            self.lots.append(entree["emails"])
            return [dict(email=a, result="ok" if a in {boite, self.nom_valide} else "unknown",
                         subresult="ok" if a in {boite, self.nom_valide} else "timeout",
                         quality="good" if a in {boite, self.nom_valide} else "risky")
                    for a in entree["emails"]], {"run": "simulation"}
    class CRM:
        def actualiser_rejets(self, controles):
            pass
        def admettre(self, campagne, candidats):
            return candidats
    for active, positive, attendue in ((True, None, boite), (False, None, None),
                                        (True, personnelles[0], personnelles[0])):
        budget = Budget(positive)
        noeuds = dict(noeuds_elargis(CRM(), budget, harvest_profils=False, harvest_effectifs=False,
                                     acteurs=("p", "h", "e"), boite_recours=active))
        etat = {"campagne": "test", "candidats": [candidat("PipelineLabs nom", personnelles),
                candidat("Coordonnées publiées", [boite])], "controles": {}, "admis": [], "attentes": []}
        for nom in ("verifier_avant_harvest", "verifier_adresses", "verifier_boites_recours"):
            if nom in noeuds:
                etat.update(noeuds[nom](etat))
        assert [c["adresse_admise"] for c in etat["admis"]] == ([attendue] if attendue else [])
        assert budget.lots == ([personnelles, [boite]] if attendue == boite else [personnelles])


def test_coordonnees_masquees_dans_un_extrait():
    from prospection.identite_serp import email_complet, telephone_complet

    assert not email_complet("mede...@gmail.com")
    assert telephone_complet("06611875...") is None
    assert telephone_complet("+212 6 66 55 07 72").country_code == 212


def test_telephone_refuse_par_twenty_preserve_les_autres_champs():
    import json
    from io import BytesIO
    from urllib.error import HTTPError

    class CRM:
        fiche = {}
        tentatives_telephone = 0
        def record(self, *args):
            return self.fiche.copy()
        def modifier(self, *args, **charge):
            if "phones" in charge:
                self.tentatives_telephone += 1
                corps = BytesIO(b'{"code":"CONFLICTING_PHONE_COUNTRY_CODE"}')
                raise HTTPError("https://crm.test", 400, "Bad request", {}, corps)
            self.fiche.update(charge)

    crm = CRM()
    e = {"ice": "1", "twenty_id": "t", "nom": "Exemple"}
    faits = [fait("telephone", "+212 666550772"), fait("ville", "Rabat")]
    for _ in range(2):
        e = completer(crm, e, faits)
    assert crm.tentatives_telephone == 1
    assert crm.fiche["address"]["addressCity"] == "Rabat"
    preuves = json.loads(crm.fiche["kerixDossier"])["coordonnees_documentees"]
    assert any(p.get("incident") == "CONFLICTING_PHONE_COUNTRY_CODE" for p in preuves)
