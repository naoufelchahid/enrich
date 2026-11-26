"""Journaliser la qualification et transmettre au CRM les admissions confirmées."""
import json
import hashlib

from .verification import verifier
from .codex import qualifier_codex


def traiter_lot(base, campagne, profils, fournisseur, importer, qualification=qualifier_codex):
    base.execute("""CREATE TABLE IF NOT EXISTS decisions (
        campagne TEXT, identite TEXT, qualification TEXT, verification TEXT,
        twenty_id TEXT, PRIMARY KEY(campagne, identite))""")
    decisions = {}
    for url, profil in profils.items():
        contenu = json.dumps(profil, ensure_ascii=False, sort_keys=True).encode()
        decisions[url] = {**qualification(profil), "profil_sha256": hashlib.sha256(contenu).hexdigest()}
    controles = verifier(fournisseur, base, campagne,
                         [d["email"] for d in decisions.values() if d["verdict"] == "correspond"])
    for url, decision in decisions.items():
        controle = controles.get(decision.get("email"))
        with base:
            base.execute("""INSERT INTO decisions VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(campagne, identite) DO UPDATE SET
                qualification=excluded.qualification, verification=excluded.verification, twenty_id=NULL""",
                         (campagne, url, json.dumps(decision), json.dumps(controle)))
        if controle and controle["verdict"] == "accepte":
            identifiant = importer(url, profils[url], decision, controle)
            with base:
                base.execute("UPDATE decisions SET twenty_id=? WHERE campagne=? AND identite=?",
                             (identifiant, campagne, url))


def main():
    import argparse
    import os
    import sqlite3
    from pathlib import Path
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
    from .collecte import Apify
    from .twenty import ImportTwenty

    parser = argparse.ArgumentParser(description="Qualifier et admettre les profils collectés")
    parser.add_argument("campagne", type=Path)
    parser.add_argument("base")
    args = parser.parse_args()
    campagne = json.loads(args.campagne.read_text())
    version = hashlib.sha256(json.dumps(campagne, sort_keys=True).encode()).hexdigest()

    def api(methode, chemin, params=None, json=None):
        from json import dumps, load
        url = os.environ["TWENTY_BASE_URL"].rstrip("/") + "/rest/" + chemin
        if params:
            url += "?" + urlencode(params)
        req = Request(url, method=methode, data=dumps(json).encode() if json else None,
                      headers={"Authorization": "Bearer " + os.environ["TWENTY_API_KEY"],
                               "Content-Type": "application/json"})
        with urlopen(req, timeout=30) as reponse:
            return load(reponse)

    with sqlite3.connect(args.base) as base:
        lignes = base.execute("SELECT url, contenu FROM profils WHERE campagne=? ORDER BY rowid", (version,))
        profils = {url: json.loads(contenu) for url, contenu in lignes}
        traiter_lot(base, campagne["identifiant"], profils, Apify(os.environ["APIFY_TOKEN"]), ImportTwenty(api))
        print(f"{len(profils)} profils traités")


if __name__ == "__main__":
    main()
