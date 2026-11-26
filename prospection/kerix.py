"""Tirage reproductible et lecture des fiches publiques Kerix."""
import os
import random
import re
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit

from .transport import requete

RUBRIQUES = {
    "21300": "https://www.kerix.net/fr/annuaire-entreprise/conseils-en-gestion-et-management-comptabilite.html",
}
FICHE = re.compile(r"/fr/annuaire-entreprise/[\w%-]+/?")
LIEN = re.compile(r"\[([^\[\]\n]+)\]\(([^\s)]+)\)")


def lire_kerix(url):
    for attente in (2, 5, 15, None):
        try:
            reponse = requete("https://api.firecrawl.dev/v2/scrape", os.environ["FIRECRAWL_API_KEY"],
                              {"url": url, "formats": ["markdown"], "onlyMainContent": False,
                               "maxAge": 600000})
            break
        except HTTPError as erreur:
            if erreur.code not in {429, 502, 503, 504} or attente is None:
                raise
            time.sleep(attente)
    data = reponse.get("data", {})
    if not reponse.get("success") or data.get("metadata", {}).get("statusCode", 200) != 200:
        raise RuntimeError("Lecture Kerix indisponible")
    return data["markdown"]


class Kerix:
    def __init__(self, lire=lire_kerix):
        self.lire = lire

    def source(self, rubrique):
        racine = RUBRIQUES[rubrique]
        pages, deja, fiches = {racine}, set(), set()
        attendu = None
        while pages:
            url = min(pages)
            pages.remove(url)
            deja.add(url)
            contenu = self.lire(url)
            if url == racine:
                nombre = re.search(r"([\d ]+)\s+entreprises? trouvées?", contenu, re.I)
                if not nombre or not re.search(r"#\s*" + re.escape(rubrique) + r"\b", contenu):
                    raise ValueError("Rubrique Kerix non reconnue")
                attendu = int(nombre[1].replace(" ", ""))
            for texte, lien in LIEN.findall(contenu):
                cible = urlsplit(urljoin(url, lien))
                if cible.scheme != "https" or cible.netloc != "www.kerix.net":
                    continue
                if texte.casefold().strip() == "voir plus" and FICHE.fullmatch(cible.path):
                    fiches.add("https://www.kerix.net" + cible.path.rstrip("/"))
                if cible.path == urlsplit(racine).path and re.fullmatch(r"page=\d+", cible.query):
                    page = racine if cible.query == "page=1" else racine + "?" + cible.query
                    if page not in deja:
                        pages.add(page)
        if attendu and not fiches:
            raise ValueError("Aucun lien de fiche trouvé dans une rubrique non vide")
        return sorted(fiches)

    def fiche(self, url):
        contenu = self.lire(url)
        titre = re.search(r"^#(?!#)[ \t]+(.+)$", contenu, re.M)
        if not titre:
            raise ValueError("Titre de fiche Kerix absent")
        ice = re.search(r"\bI\.?C\.?E\.?\b[*\s:：-]*((?:\d[ \u00a0\u202f-]*){15})(?!\d)", contenu, re.I)
        lien_site = re.search(r'\]\((https?://[^\s)]+)\s+"Consulter le site web"\)', contenu, re.I)
        site = lien_site[1] if lien_site else ""
        linkedin = ""
        for texte, lien in LIEN.findall(contenu):
            cible = urlsplit(lien)
            if cible.scheme not in {"http", "https"} or not cible.hostname:
                continue
            if re.fullmatch(r"(?:[\w-]+\.)?linkedin\.com", cible.hostname) and cible.path.startswith("/company/"):
                linkedin = lien
            if re.match(r"(?i)^(?:site\s*(?:web|internet)|(?:https?://)?www\.)", texte.strip()):
                if not cible.hostname.endswith(("kerix.net", "linkedin.com")):
                    site = lien
        return {"kerix": url, "nom": titre[1].strip(), "ice": re.sub(r"\D", "", ice[1]) if ice else "",
                "site": site, "linkedin": linkedin, "fiche": contenu}


def tirer(source, taille, graine):
    source = sorted(set(source))
    if type(taille) is not int or not 0 < taille <= len(source):
        raise ValueError(f"Tirage de {taille} impossible sur {len(source)} fiches")
    return random.Random(graine).sample(source, taille)
