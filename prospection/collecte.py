"""Collecte Apify avec reprise des jeux de données dans SQLite."""
import hashlib
import json
import os
import re
import sqlite3
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from .campagne import requete


def url_personnelle(valeur):
    if not isinstance(valeur, str):
        return None
    valeur = valeur.strip()
    try:
        url = urlsplit(valeur if "://" in valeur else "https://" + valeur)
        if (url.scheme not in {"http", "https"} or url.username or url.password
                or url.port or not re.fullmatch(r"(?:[a-z]{2,3}\.)?linkedin\.com", url.hostname or "")):
            return None
    except ValueError:
        return None
    profil = re.fullmatch(r"/in/([\w%-]+)/?", url.path)
    return "https://www.linkedin.com/in/" + profil[1] if profil else None


class Apify:
    def __init__(self, token):
        self.token = token

    def appeler(self, chemin, donnees=None):
        corps = None if donnees is None else json.dumps(donnees).encode()
        demande = Request("https://api.apify.com/v2/" + chemin, data=corps,
                          headers={"Authorization": "Bearer " + self.token,
                                   "Content-Type": "application/json"})
        with urlopen(demande, timeout=90) as resultat:
            return json.load(resultat)

    def lignes(self, base, cle, acteur, entree):
        ligne = base.execute("SELECT run FROM runs WHERE cle=?", (cle,)).fetchone()
        if ligne:
            run = self.appeler("actor-runs/" + ligne[0])["data"]
        else:
            run = self.appeler("acts/" + acteur + "/runs", entree)["data"]
            with base:
                base.execute("INSERT INTO runs VALUES (?,?,?)",
                             (cle, run["id"], run["defaultDatasetId"]))
        while run["status"] in {"READY", "RUNNING", "TIMING-OUT", "ABORTING"}:
            time.sleep(5)
            run = self.appeler("actor-runs/" + run["id"])["data"]
        if run["status"] != "SUCCEEDED":
            raise RuntimeError(f"Run {run['id']} : {run['status']}")
        offset = 0
        while True:
            params = urlencode({"offset": offset, "limit": 250, "format": "json"})
            page = self.appeler(f"datasets/{run['defaultDatasetId']}/items?{params}")
            if not page:
                return
            yield from page
            offset += len(page)


def collecter(campagne, chemin, fournisseur):
    entree = requete(campagne)
    version = hashlib.sha256(json.dumps(campagne, sort_keys=True).encode()).hexdigest()
    base = sqlite3.connect(chemin)
    try:
        base.executescript("""
            CREATE TABLE IF NOT EXISTS runs(cle TEXT PRIMARY KEY, run TEXT, dataset TEXT);
            CREATE TABLE IF NOT EXISTS profils(campagne TEXT, url TEXT, version TEXT,
                contenu TEXT, PRIMARY KEY(campagne,url,version));
        """)
        urls = set()
        for ligne in fournisseur.lignes(base, version + ":decouverte",
                "pipelinelabs~lead-scraper-apollo-zoominfo-lusha-ppe", entree):
            url = url_personnelle(ligne.get("linkedinUrl"))
            if url:
                urls.add(url)
        for url in sorted(urls):
            lignes = list(fournisseur.lignes(base, version + ":" + url,
                "harvestapi~linkedin-profile-scraper", {"urls": [url],
                "profileScraperMode": "Profile details + email search ($10 per 1k)"}))
            if len(lignes) != 1 or url_personnelle(lignes[0].get("linkedinUrl")) != url:
                raise ValueError(f"Profil HarvestAPI non rapproché : {url}")
            contenu = json.dumps(lignes[0], ensure_ascii=False, sort_keys=True)
            empreinte = hashlib.sha256(contenu.encode()).hexdigest()
            with base:
                base.execute("INSERT OR IGNORE INTO profils VALUES (?,?,?,?)",
                             (version, url, empreinte, contenu))
        return len(urls)
    finally:
        base.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campagne")
    parser.add_argument("base")
    args = parser.parse_args()
    with open(args.campagne, encoding="utf-8") as fichier:
        print(collecter(json.load(fichier), args.base, Apify(os.environ["APIFY_TOKEN"])))
