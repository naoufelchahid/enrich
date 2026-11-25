import unittest
from unittest.mock import patch
from prospection.codex import qualifier_codex
from prospection.verification import qualifier


class ValidationCodex(unittest.TestCase):
    def test_preuves_et_profil_complet(self):
        profil = {"emails": [{"email": "nom@exemple.ma"}], "champ_inconnu": ["conservé"],
                  "currentPosition": [{"company": {"website": "https://www.exemple.ma"}}]}
        attendu = qualifier(profil)
        for remplacement in ({}, {"email": "autre@exemple.ma"}, {"preuve_employeur": "/about"}):
            with self.subTest(remplacement=remplacement), patch("prospection.codex.consulter",
                    return_value={**attendu, **remplacement, "motif": "test"}) as appel:
                resultat = qualifier_codex(profil)
                appel.assert_called_once_with(profil)
                self.assertEqual(resultat["verdict"], "indetermine" if remplacement else "correspond")

    def test_sans_adresse(self):
        with patch("prospection.codex.consulter") as appel:
            self.assertEqual(qualifier_codex({"emails": []})["verdict"], "indetermine")
            appel.assert_not_called()
