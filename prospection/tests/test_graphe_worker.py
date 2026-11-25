import sqlite3
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from prospection.graphe import simuler
from prospection.worker import traiter,creneau

CAMP={"identifiant":"essai"}
def instant(h,m):
    return datetime(2026,11,25,h,m,tzinfo=ZoneInfo("Africa/Casablanca"))
def entree(n):
    return {"profil":{"poste":"Achats","inconnu":17},"qualification":{"email":f"p{n}@exemple.ma"},
            "verification":{"verdict":"accepte"},"twenty_id":str(n)}
def simulation(n, verdict="approuve"):
    return simuler(entree(n),lambda e:{"objet":"Sujet","corps":"Texte","preuves":[{"fait":"Achats","chemin":"/poste"}]},
                   lambda e:{"verdict":verdict,"motif":"revoir le fait"})

class GrapheEtEnvoi(unittest.TestCase):
    def test_deux_revisions(self):
        e=simulation(1,"a_corriger")
        self.assertEqual(e["version"],3)
        self.assertEqual(e["revue"]["version"],3)
        self.assertEqual(e["entree"]["profil"]["inconnu"],17)

    def test_preuve_inventee(self):
        e=simuler(entree(1),lambda s:{"objet":"A","corps":"B","preuves":[{"fait":"Inventé","chemin":"/invente"}]},
                  lambda s:{"verdict":"approuve","motif":""})
        self.assertEqual((e["version"],e["revue"]["verdict"]),(3,"a_corriger"))

    def test_horaires(self):
        self.assertEqual([creneau(CAMP,instant(h,m)) is not None for h,m in
                          [(7,59),(8,0),(16,50),(16,59),(17,0)]],[False,True,True,True,False])

    def test_reprise(self):
        db=sqlite3.connect(":memory:")
        lot=[simulation(i) for i in range(6)]
        recus=[]
        def envoyer(charge):
            recus.append(charge)
            if charge["personne"]=="2": raise TimeoutError()
            return {"success":True}
        panne=[True]
        def sync(*args):
            if panne.pop() if panne else False: raise ConnectionError()
        def appel(h,m):
            traiter(db,CAMP,lot,lambda *a:True,envoyer,sync,lambda:instant(h,m))
        with self.assertRaises(ConnectionError): appel(8,0)
        appel(8,0)
        self.assertEqual(len(recus),1)
        appel(8,10)
        self.assertEqual(len(recus),5)
        appel(8,20)
        self.assertEqual(len(recus),6)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM expeditions WHERE etat='incertain'").fetchone()[0],1)

    def test_concurrence(self):
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from threading import Barrier
        barriere=Barrier(2)
        etat=simulation(1)
        with TemporaryDirectory() as dossier:
            def lancer(_):
                with sqlite3.connect(Path(dossier)/"journal.db") as db:
                    barriere.wait()
                    appels=[]
                    traiter(db,CAMP,[etat],lambda *a:True,lambda ch: appels.append(ch) or {"success":True},
                            lambda *a:None,lambda:instant(8,0))
                    return len(appels)
            with ThreadPoolExecutor(2) as executants:
                self.assertEqual(sum(executants.map(lancer,range(2))),1)

    def test_fermeture_juste_avant_envoi(self):
        db=sqlite3.connect(":memory:")
        heures=iter([instant(16,59),instant(16,59),instant(17,0)])
        traiter(db,CAMP,[simulation(1)],lambda *a:True,lambda _:self.fail("Hors fenêtre"),
                lambda *a:None,lambda:next(heures))
        self.assertEqual(db.execute("SELECT count(*) FROM expeditions").fetchone()[0],0)
