"""Expédition séquentielle par créneau avec réservation et reçu persistants."""
import json
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .campagne import JOURS, valider_horaires
from .graphe import empreinte


def creneau(campagne, instant):
    valider_horaires(campagne)
    local = instant.astimezone(ZoneInfo(campagne.get("fuseau","Africa/Casablanca")))
    def minutes(texte):
        h,m = map(int,texte.split(":"))
        return h*60+m
    debut = minutes(campagne.get("heure_debut","08:00"))
    minute = local.hour*60+local.minute
    if JOURS[local.weekday()] not in campagne.get("jours_actifs",JOURS) or not debut<=minute<minutes(campagne.get("heure_fin","17:00")):
        return None
    return f"{local.date()}/{(minute-debut)//campagne.get('intervalle_minutes',10)}"


def initialiser(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS creneaux(campagne TEXT,slot TEXT,PRIMARY KEY(campagne,slot));
    CREATE TABLE IF NOT EXISTS expeditions(campagne TEXT, personne TEXT, adresse TEXT,
        charge TEXT, etat TEXT, recu TEXT, date TEXT,
        PRIMARY KEY(campagne, personne), UNIQUE(campagne, adresse));
    """)


def traiter(db,campagne,etats,eligible,transmettre,synchroniser,horloge=lambda:datetime.now(timezone.utc), destinataire_test=None):
    initialiser(db)
    nom=campagne["identifiant"]
    def synchronisations():
        for personne,charge,recu,date in db.execute("SELECT personne,charge,recu,date FROM expeditions WHERE campagne=? AND etat='confirme'",(nom,)).fetchall():
            synchroniser(json.loads(charge),json.loads(recu),date)
            with db:
                db.execute("UPDATE expeditions SET etat='synchronise' WHERE campagne=? AND personne=? AND etat='confirme'",(nom,personne))
    synchronisations()
    slot=creneau(campagne,horloge())
    if slot is None:
        return
    with db:
        if not db.execute("INSERT OR IGNORE INTO creneaux VALUES (?,?)",(nom,slot)).rowcount:
            return
    compteur=0
    file = iter(etats)
    while compteur < campagne.get("contacts_par_creneau",4) and creneau(campagne,horloge()) == slot:
        etat = next(file, None)
        if etat is None:
            break
        revue=etat["revue"]
        if revue["verdict"]!="approuve" or revue["version"]!=etat["version"]:
            continue
        entree=etat["entree"]
        adresse=entree["qualification"]["email"]
        if entree["verification"]["verdict"]!="accepte" or not eligible(entree,adresse):
            continue
        charge={"campagne":nom,"personne":entree["twenty_id"],"adresse":adresse,
                "objet":etat["brouillon"]["objet"],"corps":etat["brouillon"]["corps"],
                "version":etat["version"],"empreinte":empreinte(etat)}
        if destinataire_test:
            charge.update(adresse=destinataire_test, objet="[Pilote] "+charge["objet"],
                          adresse_qualifiee=adresse)
        with db:
            if not db.execute("INSERT OR IGNORE INTO expeditions VALUES(?,?,?,?,'incertain',NULL,NULL)",
                    (nom,charge["personne"],charge["adresse"],json.dumps(charge))).rowcount:
                continue
        if creneau(campagne,horloge())!=slot:
            with db:
                db.execute("DELETE FROM expeditions WHERE campagne=? AND personne=?",(nom,charge["personne"]))
            break
        compteur+=1
        try:
            recu=transmettre(charge)
        except (OSError,ValueError,RuntimeError):
            continue
        if not recu:
            continue
        with db:
            db.execute("UPDATE expeditions SET etat='confirme',recu=?,date=? WHERE campagne=? AND personne=?",
                       (json.dumps(recu),horloge().isoformat(),nom,charge["personne"]))
        synchronisations()
