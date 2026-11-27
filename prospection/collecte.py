"""Client Apify pour les lots de prospection."""
import json
import re
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen



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

    def attendre(self, run):
        while run["status"] in {"READY", "RUNNING", "TIMING-OUT", "ABORTING"}:
            time.sleep(5)
            run = self.appeler("actor-runs/" + run["id"])["data"]
        return run

    def dataset(self, identifiant):
        offset = 0
        while True:
            params = urlencode({"offset": offset, "limit": 250, "format": "json"})
            page = self.appeler(f"datasets/{identifiant}/items?{params}")
            if not page:
                return
            yield from page
            offset += len(page)
