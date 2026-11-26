"""Exécuter le pilote Kerix et lancer séparément le graphe de campagne."""
import argparse
import fcntl
import json
import os
import sqlite3
from pathlib import Path

from .acquisition_kerix import graphe_acquisition
from .budget import Budget
from .collecte import Apify
from .crm_campagnes import CAMPAGNES, Twenty, reference_valide
from .envoi_campagne import graphe_campagne
from .kerix import Kerix, tirer
from .schema_kerix import installer
from .recherche_dirigeants import domaine, page_entreprise


def lire(path):
    return json.loads(path.read_text())


def archive(path, contenu):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        json.dump(contenu, f, ensure_ascii=False, indent=2)


def confirmer_identites(fiches, identites):
    """Joindre des preuves revues à des entreprises issues du tirage Kerix."""
    par_ice = {f["ice"] for f in fiches.values() if f["ice"]}
    if set(identites) - par_ice:
        raise ValueError("Preuve fournie pour une entreprise hors tirage")
    mises_a_jour = {}
    for url, fiche in fiches.items():
        preuve = identites.get(fiche["ice"])
        if not preuve:
            mises_a_jour[url] = fiche
            continue
        if not preuve.get("extrait", "").strip() or not domaine(preuve.get("url")):
            raise ValueError("Chaque identité confirmée doit avoir une source et un extrait")
        site = preuve.get("site") or fiche["site"]
        linkedin = preuve.get("linkedin") or fiche["linkedin"]
        if (site and not domaine(site)) or (linkedin and not page_entreprise(linkedin)):
            raise ValueError("Domaine ou page LinkedIn d’entreprise invalide")
        if (fiche["site"] and domaine(fiche["site"]) != domaine(site)
                or fiche["linkedin"] and page_entreprise(fiche["linkedin"]) != page_entreprise(linkedin)):
            raise ValueError("Identité confirmée en conflit avec la fiche Kerix")
        mises_a_jour[url] = {**fiche, "site": site, "linkedin": linkedin, "preuve_identite": preuve}
    return mises_a_jour


def acquerir(args, crm):
    args.repertoire.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (args.repertoire / "acquisition.lock").open("a") as fichier:
        fcntl.flock(fichier, fcntl.LOCK_EX)
        return _acquerir(args, crm)


def _acquerir(args, crm):
    configuration = lire(args.configuration)
    reference_valide(configuration["campagne"])
    repertoire = args.repertoire
    instantane = repertoire / "source.json"
    etat = {k: configuration[k] for k in ("rubrique", "taille", "graine", "campagne")}
    etat["rubrique"] = str(etat["rubrique"])
    kerix = Kerix()
    if instantane.exists():
        source = lire(instantane)
        if source["configuration"] != etat:
            raise ValueError("Le tirage archivé appartient à une autre configuration")
        etat["source"] = source["source"]
    else:
        source = kerix.source(etat["rubrique"])
        archive(instantane, {"configuration": etat, "source": source})
        etat["source"] = source
    chemin_bilan = repertoire / "bilan.json"
    if chemin_bilan.exists():
        raise ValueError("Cette acquisition dispose déjà d’un bilan")
    chemin_fiches = repertoire / "fiches.json"
    if chemin_fiches.exists():
        fiches = lire(chemin_fiches)
    else:
        fiches = {url: kerix.fiche(url) for url in tirer(etat["source"], etat["taille"], etat["graine"])}
        archive(chemin_fiches, fiches)
    if args.identites:
        fiches = confirmer_identites(fiches, lire(args.identites))
    with sqlite3.connect(repertoire / "budget.sqlite") as db:
        budget = Budget(db, Apify(os.environ["APIFY_TOKEN"]), configuration["plafond_usd"])
        graphe = graphe_acquisition(kerix, crm, budget, fiche=fiches.__getitem__,
                                    elargi=configuration.get("criteres_elargis", True),
                                    **configuration.get("recours", {}))
        resultat = graphe.invoke(etat)
    archive(chemin_bilan, resultat)
    return resultat["bilan"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commandes = parser.add_subparsers(dest="commande", required=True)
    commandes.add_parser("installer-schema")
    preparation = commandes.add_parser("configurer-campagne")
    preparation.add_argument("campagne")
    preparation.add_argument("brief", type=Path)
    acquisition = commandes.add_parser("acquerir")
    acquisition.add_argument("configuration", type=Path)
    acquisition.add_argument("repertoire", type=Path)
    acquisition.add_argument("--identites", type=Path, help="Preuves d’identité revues, indexées par ICE")
    commandes.add_parser("rediger").add_argument("campagne")
    envoi = commandes.add_parser("envoyer")
    envoi.add_argument("campagne")
    envoi.add_argument("affectation_id")
    envoi.add_argument("approbation")
    mode = envoi.add_mutually_exclusive_group(required=True)
    mode.add_argument("--boite-test")
    mode.add_argument("--production", action="store_true")
    args = parser.parse_args()
    crm = Twenty()
    if args.commande == "installer-schema":
        resultat = installer()
    elif args.commande == "configurer-campagne":
        reference = reference_valide(args.campagne)
        brief = lire(args.brief)
        if not isinstance(brief, dict) or not brief:
            raise ValueError("Brief éditorial JSON requis")
        resultat = [{"id": c["id"], "reference": c["reference"]} for c in crm.lots(CAMPAGNES, [{
            "name": reference, "reference": reference, "briefJson": json.dumps(brief, ensure_ascii=False),
        }], upsert=True)]
    elif args.commande == "acquerir":
        resultat = acquerir(args, crm)
    elif args.commande == "rediger":
        resultat = graphe_campagne(crm).invoke({"campagne": reference_valide(args.campagne)})
    else:
        entree = {k: getattr(args, k) for k in ("campagne", "affectation_id", "approbation", "boite_test")}
        reference_valide(args.campagne)
        resultat = graphe_campagne(crm).invoke(entree)["resultat"]
    print(json.dumps(resultat, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
