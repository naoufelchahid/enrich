"""Admission par identité LinkedIn dans une instance Twenty."""
from urllib.parse import urlsplit

from .collecte import url_personnelle


class ImportTwenty:
    def __init__(self, api):
        self.api = api

    def lignes(self, ressource):
        params = {"limit": 100}
        while True:
            page = self.api("GET", ressource, params=params)
            yield from page["data"][ressource]
            if not page["pageInfo"]["hasNextPage"]:
                return
            params["starting_after"] = page["pageInfo"]["endCursor"]

    def __call__(self, url, profil, decision, controle):
        if decision["verdict"] != "correspond" or controle["verdict"] != "accepte":
            raise ValueError("Admission non confirmée")
        identite = url_personnelle(url)
        if not identite or identite != url_personnelle(profil.get("linkedinUrl")):
            raise ValueError("Identité LinkedIn incohérente")
        email = decision["email"]
        personne = None
        for ligne in self.lignes("people"):
            lien = (ligne.get("linkedinLink") or {}).get("primaryLinkUrl")
            emails = ligne.get("emails") or {}
            principal = (emails.get("primaryEmail") or "").casefold()
            secondaires = [e.casefold() for e in emails.get("additionalEmails") or []]
            if url_personnelle(lien) == identite:
                if personne or (principal and principal != email):
                    raise ValueError("Collision entre identité et adresse")
                personne = ligne
            elif email in [principal, *secondaires]:
                raise ValueError("Adresse déjà affectée à une autre identité")
        if personne:
            if personne.get("campaignStatus") or (personne.get("emails") or {}).get("primaryEmail"):
                return personne["id"]
            emails = dict(personne.get("emails") or {}, primaryEmail=email)
            self.api("PATCH", "people/" + personne["id"], json={"emails": emails})
            return personne["id"]

        index = int(decision["preuve_employeur"].split("/")[2])
        poste = profil["currentPosition"][index]
        entreprises = []
        for ligne in self.lignes("companies"):
            site = (ligne.get("domainName") or {}).get("primaryLinkUrl") or ""
            domaine = urlsplit(site if "://" in site else "//" + site).hostname or ""
            if domaine.removeprefix("www.") == decision["domaine"]:
                entreprises.append(ligne["id"])
        if len(entreprises) > 1:
            raise ValueError("Plusieurs entreprises pour le domaine")
        if entreprises:
            entreprise = entreprises[0]
        else:
            entreprise = self.api("POST", "companies", json={
                "name": poste["companyName"], "domainName": {
                    "primaryLinkUrl": poste["company"]["website"]}})["data"]["createCompany"]["id"]
        charge = {"name": {"firstName": profil["firstName"], "lastName": profil["lastName"]},
                  "linkedinLink": {"primaryLinkUrl": identite}, "emails": {"primaryEmail": email},
                  "companyId": entreprise, "jobTitle": poste.get("position"),
                  "sourceFile": f"HarvestAPI {decision['preuve_employeur']} {decision['domaine']} | BounceVerify valid"}
        return self.api("POST", "people", json=charge)["data"]["createPerson"]["id"]
