import unittest
from unittest.mock import Mock

from prospection.verification import candidats_employeur_actuel, verifier, qualifier


def poste(site, fin="Present"):
    return {"position": "Autre fonction", "endDate": {"text": fin},
            "company": {"website": site}}


class Qualification(unittest.TestCase):
    def test_domaine_employeur_actuel(self):
        profil = {
            "currentPosition": [poste("https://www.actuel.example"), poste("ancien.example", "2020")],
            "emails": [{"email": adresse} for adresse in
                       ("p@actuel.example", "p@ancien.example", "p@alias.example", "p@gmail.com")],
            "about": "autre@actuel.example",
        }
        self.assertEqual(candidats_employeur_actuel(profil), ["p@actuel.example"])

    def test_rapprochement_et_verdicts(self):
        fournisseur = Mock()
        valide = dict(deliverability="valid", hasValidSyntax=True, hasMxRecord=True,
                      isDisposable=False, isAcceptable=True)
        fournisseur.lignes.return_value = [
            dict(valide, email="E@EXAMPLE.ORG", deliverability="invalid"),
            dict(valide, email="d@example.org", hasMxRecord=False),
            dict(valide, email="c@example.org", deliverability="risky"),
            dict(valide, email="A@example.org"),
        ]
        adresses = [f"{lettre}@example.org" for lettre in "abcde"]
        resultats = verifier(fournisseur, None, "recette", adresses + [adresses[0].upper()])
        self.assertEqual(fournisseur.lignes.call_args.args[-1]["emails"], adresses)
        self.assertEqual([resultats[a]["verdict"] for a in adresses],
                         ["dns_ok", "manquant", "risque", "inconnu", "rejete"])

    def test_ambiguite(self):
        profil = {"currentPosition": [poste("entreprise.example")],
                  "emails": [{"email": "a@entreprise.example"}, {"email": "b@entreprise.example"}]}
        self.assertEqual(candidats_employeur_actuel(profil), [])

    def test_reimport_et_journal(self):
        import sqlite3
        from prospection.admission import traiter_lot
        from prospection.twenty import ImportTwenty
        url = "https://www.linkedin.com/in/test"
        profil = {"linkedinUrl": url, "emails": [{"email": "a@entreprise.example"}],
                  "currentPosition": [poste("entreprise.example")]}
        fournisseur = Mock()
        fournisseur.lignes.return_value = [dict(email="a@entreprise.example", deliverability="valid",
            hasValidSyntax=True, hasMxRecord=True, isDisposable=False, isAcceptable=True)]
        for statut in ("envoyé", "désinscrit"):
            api = Mock(return_value={"pageInfo": {"hasNextPage": False}, "data": {"people": [{
                "id": "personne", "linkedinLink": {"primaryLinkUrl": url}, "campaignStatus": statut,
                "emails": {"primaryEmail": "a@entreprise.example"}}]}})
            with sqlite3.connect(":memory:") as base:
                for _ in range(2):
                    traiter_lot(base, "pilote", {url: profil, "sans-adresse": {}}, fournisseur, ImportTwenty(api), qualification=qualifier)
                self.assertEqual(base.execute("SELECT count(*) FROM decisions").fetchone()[0], 2)
                self.assertIsNone(base.execute("SELECT twenty_id FROM decisions WHERE identite=?", (url,)).fetchone()[0])
                self.assertIn("aucun domaine", base.execute("SELECT qualification FROM decisions WHERE identite='sans-adresse'").fetchone()[0])
            api.assert_not_called()
