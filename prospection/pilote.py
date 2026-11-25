"""Recette manuelle sur une boîte contrôlée, avec un journal distinct de la campagne."""
import argparse
import json
import sqlite3
from pathlib import Path
from .graphe import empreinte
from .worker import traiter
from .transport import eligible,envoyer,synchroniser

def executer(db,etat,destinataire):
    if any(c in destinataire for c in "\r\n,; ") or destinataire.count("@")!=1:
        raise ValueError("Une seule adresse de test est requise")
    if destinataire.casefold()==etat["entree"]["qualification"]["email"].casefold():
        raise ValueError("La boîte de recette doit être distincte de celle du prospect")
    # Une recette manuelle a son propre calendrier et espace de réservation.
    campagne={"identifiant":"recette-"+empreinte([etat["entree"]["twenty_id"],destinataire]),
              "heure_debut":"00:00","heure_fin":"23:59","contacts_par_creneau":1}
    traiter(db,campagne,[etat],eligible,envoyer,synchroniser,destinataire_test=destinataire)
    ligne=db.execute("SELECT etat,date,recu FROM expeditions WHERE campagne=?",(campagne["identifiant"],)).fetchone()
    return ligne

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    for champ in ("simulation","journal","destinataire"):
        p.add_argument(champ)
    a=p.parse_args()
    with sqlite3.connect(a.journal) as db:
        print(executer(db,json.loads(Path(a.simulation).read_text()),a.destinataire))
