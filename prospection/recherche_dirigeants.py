"""Normalisation des informations d’entreprise et de poste."""
import re
import unicodedata
from urllib.parse import urlsplit


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", str(texte or "").casefold())
    return " ".join(re.findall(r"[^\W_]+", "".join(c for c in texte if not unicodedata.combining(c))))


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
    if fournisseur.startswith("PipelineLabs"):
        if ligne.get("isCurrent") is False or ligne.get("is_current") is False:
            return []
        organisation = ligne.get("organization") or ligne.get("company")
        organisation = organisation if isinstance(organisation, dict) else {}
        return [{
            "titre": premier(ligne, "title", "jobTitle", "personTitle"),
            "nom": premier(ligne, "companyName", "company_name", "organizationName") or organisation.get("name"),
            "domaine": domaine(premier(ligne, "companyDomain", "companyWebsite", "company_domain", "website")
                                or premier(organisation, "primary_domain", "domain", "website_url", "website")),
            "linkedin": page_entreprise(premier(ligne, "companyLinkedinUrl", "companyLinkedInUrl",
                                               "companyLinkedin", "company_linkedin_url")
                                         or premier(organisation, "linkedin_url", "linkedinUrl")),
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
