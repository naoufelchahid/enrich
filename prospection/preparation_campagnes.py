"""Consommer les demandes de rédaction enregistrées par le formulaire Twenty."""
import fcntl
import json
import os
import time
from subprocess import TimeoutExpired
from pathlib import Path

from .crm_campagnes import CAMPAGNES, charger_json
from .envoi_campagne import graphe_campagne


def traiter(crm):
    for campagne in crm.lignes(CAMPAGNES):
        demande = campagne.get("demandeRedaction")
        precedent = charger_json(campagne.get("resultatRedaction")) or {}
        if not demande or precedent.get("demande") == demande:
            continue
        suivi = {"demande": demande}
        try:
            resultat = graphe_campagne(crm).invoke({"campagne": campagne["reference"]})
            suivi.update(etat="terminé", brouillons=[
                {"affectation": b["affectation"], "adresse": b["adresse"], "angle": b["angle"]["cible"],
                 "technique": b["angle"]["technique"]["type"], "revue": b["revue"]["verdict"]}
                for b in resultat["brouillons"]], ecartes=resultat["ecartes"])
        except (RuntimeError, ValueError, OSError, TimeoutExpired) as erreur:
            suivi.update(etat="erreur", motif=str(erreur))
        crm.modifier(CAMPAGNES, campagne["id"], resultatRedaction=json.dumps(suivi, ensure_ascii=False))


def ecouter(crm):
    verrou = Path(os.environ.get("XDG_RUNTIME_DIR", Path.home())) / ".enrich-redaction.lock"
    with verrou.open("a") as fichier:
        fcntl.flock(fichier, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            traiter(crm)
            time.sleep(5)
