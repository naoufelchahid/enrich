"""Contrats communs des candidats aux trois fonctions de direction."""
import re
import unicodedata
from urllib.parse import urlsplit

from .collecte import url_personnelle

VERSION_TITRES = "2"
TITRES = {
    "gerance": ("gérant", "gérant associé", "associé gérant", "co-gérant", "manager statutaire",
                "gérante", "gérante associée", "associée gérante", "co-gérante", "مسير", "مسيرة"),
    "direction_generale": ("directeur général", "directrice générale", "directeur général délégué",
                          "directrice générale déléguée", "président directeur général",
                          "présidente directrice générale", "DG", "PDG", "CEO", "chief executive officer",
                          "managing director", "general director", "directeur général et cofondateur",
                          "directrice générale et cofondatrice", "Founder & CEO", "CEO & Founder",
                          "Founder and CEO", "Co-founder & CEO", "CEO & Co-founder",
                          "Managing Director & Founder", "المدير العام", "المديرة العامة"),
    "direction_commerciale": ("directeur commercial", "directrice commerciale", "directeur des ventes",
                              "directrice des ventes", "sales director", "commercial director",
                              "chief commercial officer", "directeur commercial et marketing",
                              "directrice commerciale et marketing", "المدير التجاري", "المديرة التجارية"),
}
PRIORITES = {fonction: i for i, fonction in enumerate(TITRES)}


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", str(texte or "").casefold())
    return " ".join(re.findall(r"[^\W_]+", "".join(c for c in texte if not unicodedata.combining(c))))


def fonction(titre):
    titre = normaliser(titre)
    return next((cle for cle, variantes in TITRES.items() if titre in map(normaliser, variantes)), None)


def domaine(site):
    try:
        site = str(site or "").strip()
        hote = urlsplit(site if "://" in site else "//" + site).hostname or ""
        return hote.casefold().removeprefix("www.").rstrip(".") if "." in hote else ""
    except ValueError:
        return ""


def page_entreprise(url):
    url = str(url or "").strip()
    try:
        valeur = urlsplit(url if "://" in url else "//" + url)
        if re.fullmatch(r"(?:[a-z]{2,3}\.)?linkedin\.com", valeur.hostname or ""):
            match = re.fullmatch(r"/company/([\w%-]+)/?", valeur.path)
            if match:
                return "https://www.linkedin.com/company/" + match[1].casefold()
    except ValueError:
        pass
    return ""


def premier(objet, *champs):
    return next((objet[k] for k in champs if objet.get(k)), "")


def postes(ligne, fournisseur):
    if fournisseur == "PipelineLabs":
        return [{
            "titre": premier(ligne, "title", "jobTitle", "personTitle"),
            "nom": premier(ligne, "companyName", "company_name", "organizationName"),
            "domaine": domaine(premier(ligne, "companyDomain", "companyWebsite", "company_domain", "website")),
            "linkedin": page_entreprise(premier(ligne, "companyLinkedinUrl", "companyLinkedInUrl",
                                               "companyLinkedin", "company_linkedin_url")),
            "pointeur": "/",
        }]
    resultats = []
    for i, poste in enumerate(ligne.get("currentPosition") or []):
        fin = poste.get("endDate")
        fin = fin.get("text") if isinstance(fin, dict) else fin
        if fin and normaliser(fin) not in {"present", "aujourd hui", "current", "now"}:
            continue
        societe = poste.get("company") or {}
        resultats.append({
            "titre": premier(poste, "position", "title"),
            "nom": premier(poste, "companyName") or societe.get("name"),
            "domaine": domaine(premier(societe, "website", "domain")),
            "linkedin": page_entreprise(premier(societe, "linkedinUrl", "url")
                                        or premier(poste, "companyLinkedinUrl", "companyUrl")),
            "pointeur": f"/currentPosition/{i}",
        })
    return resultats


def adresses(ligne):
    valeurs = [ligne.get(k) for k in ("email", "workEmail", "businessEmail", "work_email")]
    valeurs.extend(ligne.get("emails") or [] if isinstance(ligne.get("emails"), list) else [])
    emails = [v.get("email") if isinstance(v, dict) else v for v in valeurs]
    return sorted({v.strip().casefold() for v in emails if isinstance(v, str) and v.strip()})


def candidats(lignes, fournisseur, entreprises, personnes=None, provenance=None):
    rapproches, attentes = [], []
    for ligne in lignes:
        url = url_personnelle(premier(ligne, "linkedinUrl", "linkedin_url", "personLinkedinUrl"))
        choix = {}
        for poste in postes(ligne, fournisseur):
            role = fonction(poste["titre"])
            if not role:
                continue
            for entreprise in entreprises:
                if personnes is not None and entreprise["ice"] not in personnes.get(url, set()):
                    continue
                correspond = (
                    poste["domaine"] and poste["domaine"] == domaine(entreprise["site"])
                    or poste["linkedin"] and poste["linkedin"] == page_entreprise(entreprise["linkedin"])
                )
                if correspond:
                    cle = entreprise["ice"], role, poste["pointeur"]
                    choix[cle] = (entreprise, poste, role)
        if not url or len(choix) != 1:
            attentes.append({"fournisseur": fournisseur, "linkedin": url, "motif": "Rattachement ambigu"
                             if len(choix) > 1 else "Profil, fonction ou employeur non confirmé", "brut": ligne})
            continue
        entreprise, poste, role = next(iter(choix.values()))
        rapproches.append({
            "linkedin": url, "prenom": premier(ligne, "firstName", "first_name"),
            "nom": premier(ligne, "lastName", "last_name"), "entreprise": entreprise,
            "poste": {**poste, "fonction": role, "version_titres": VERSION_TITRES},
            "fournisseur": fournisseur, "provenance": provenance, "profil": ligne,
            "adresses": adresses(ligne),
        })
    return rapproches, attentes
