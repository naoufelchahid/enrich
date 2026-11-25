"""Traiter un créneau de la campagne. Un ordonnanceur rappelle cette commande chaque minute."""
import argparse
import json
import sqlite3
from pathlib import Path
from .graphe import charger,simuler,empreinte
from .worker import traiter,initialiser
from .transport import eligible,envoyer,synchroniser


def executer(base,campagne):
    nom=campagne["identifiant"]
    initialiser(base)
    base.execute("CREATE TABLE IF NOT EXISTS simulations(cle TEXT PRIMARY KEY, contenu TEXT)")
    def etats():
        for url,crm in base.execute("SELECT identite,twenty_id FROM decisions WHERE campagne=? AND twenty_id IS NOT NULL ORDER BY identite",(nom,)).fetchall():
            if base.execute("SELECT 1 FROM expeditions WHERE campagne=? AND personne=?",(nom,crm)).fetchone():
                continue
            entree=charger(base,campagne,url)
            if not eligible(entree,entree["qualification"]["email"]):
                continue
            cle=empreinte(entree)
            ligne=base.execute("SELECT contenu FROM simulations WHERE cle=?",(cle,)).fetchone()
            if ligne:
                yield json.loads(ligne[0])
            else:
                etat=simuler(entree)
                with base:
                    base.execute("INSERT OR IGNORE INTO simulations VALUES(?,?)",(cle,json.dumps(etat)))
                yield etat
    traiter(base,campagne,etats(),eligible,envoyer,synchroniser)

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("campagne",type=Path);p.add_argument("base")
    a=p.parse_args()
    import fcntl
    with open(str(Path(a.base).resolve())+".worker.lock","a") as verrou:
        try:
            fcntl.flock(verrou,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(0)
        with sqlite3.connect(a.base) as db:
            executer(db,json.loads(a.campagne.read_text()))
