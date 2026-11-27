import unittest
from prospection.graphe import simuler

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
