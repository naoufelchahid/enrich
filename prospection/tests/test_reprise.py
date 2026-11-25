import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prospection.collecte import Apify, collecter


class TestReprise(unittest.TestCase):
    def test_reprise_apres_reservation_harvest(self):
        campagne = json.loads((Path(__file__).resolve().parents[1] / "exemple.json").read_text())
        url = "https://www.linkedin.com/in/profil-exemple"
        profil = {"linkedinUrl": url, "champ_inconnu": {"valeurs": [1, 2]}}
        decouverte = [
            {"linkedinUrl": url, "email": "source@exemple.invalid"},
            {"linkedinUrl": "linkedin.com/in/profil-exemple/?origine=test"},
            {"linkedinUrl": "https://www.linkedin.com/company/exemple"},
        ]
        lancements = []
        interruption = True

        def transport(chemin, donnees=None):
            nonlocal interruption
            if chemin.startswith("acts/"):
                run = "decouverte" if "pipelinelabs" in chemin else "enrichissement"
                lancements.append(run)
                if run == "enrichissement":
                    self.assertEqual(donnees["urls"], [url])
            elif chemin.startswith("actor-runs/"):
                run = chemin.split("/")[1]
                self.assertIn(run, lancements)
            elif chemin.startswith("datasets/"):
                run = chemin.split("/")[1]
                if run == "enrichissement" and interruption:
                    interruption = False
                    raise ConnectionError("Coupure simulée")
                if "offset=0&" in chemin:
                    return decouverte if run == "decouverte" else [profil]
                return []
            else:
                self.fail(chemin)
            return {"data": {"id": run, "defaultDatasetId": run, "status": "SUCCEEDED"}}

        with tempfile.TemporaryDirectory() as dossier, patch.object(Apify, "appeler", side_effect=transport):
            base = Path(dossier) / "campagne.sqlite"
            with self.assertRaisesRegex(ConnectionError, "Coupure simulée"):
                collecter(campagne, base, Apify(""))
            with sqlite3.connect(base) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT count(*) FROM profils").fetchone()[0], 0)
            for _ in range(2):
                self.assertEqual(collecter(campagne, base, Apify("")), 1)
            self.assertEqual(lancements, ["decouverte", "enrichissement"])
            with sqlite3.connect(base) as db:
                (identite, version, contenu), = db.execute("SELECT url, version, contenu FROM profils")
            self.assertEqual(identite, url)
            self.assertEqual(json.loads(contenu), profil)
            self.assertEqual(version, hashlib.sha256(contenu.encode()).hexdigest())
