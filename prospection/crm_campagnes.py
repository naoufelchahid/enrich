"""Dossiers, affectations et suivi de campagne dans l’API Twenty."""
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlencode

from .collecte import url_personnelle
from .transport import crm
from .verification_account56 import ACTEUR_VERIFICATION, preuve_acceptee

AFFECTATIONS = "prospectingAssignments"
CAMPAGNES = "prospectingCampaigns"
EXPEDITIONS = "prospectingDeliveries"


def reference_valide(reference):
    if not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", reference):
        raise ValueError("Référence de campagne invalide")
    return reference


def api_twenty(methode, chemin, *, params=None, contenu=None):
    return crm(chemin + ("?" + urlencode(params) if params else ""), contenu, methode)


def charger_json(texte):
    return json.loads(texte) if texte else None


def dernier_controle(dossier, adresse):
    preuves = [p for p in dossier.get("verifications", [])
               if p.get("adresse") == adresse and p.get("fournisseur") == ACTEUR_VERIFICATION]
    return max(preuves, key=date_controle, default={})


def date_controle(preuve):
    date = preuve.get("date")
    return datetime.fromisoformat(date) if date else datetime.min.replace(tzinfo=timezone.utc)


class Twenty:
    def __init__(self, api=api_twenty):
        self.api = api

    def lignes(self, ressource, **params):
        params = {"limit": 60, "depth": 0, **params}
        curseurs = set()
        while True:
            page = self.api("GET", ressource, params=params)
            yield from page["data"][ressource]
            if not page["pageInfo"]["hasNextPage"]:
                return
            curseur = page["pageInfo"]["endCursor"]
            if not curseur or curseur in curseurs:
                raise ValueError(f"Pagination Twenty incohérente pour {ressource}")
            curseurs.add(curseur)
            params = {**params, "starting_after": curseur}

    def record(self, ressource, identifiant):
        data = self.api("GET", f"{ressource}/{identifiant}", params={"depth": 0})["data"]
        ligne, = data.values()
        return ligne

    def lots(self, ressource, lignes, *, upsert=False):
        for debut in range(0, len(lignes), 60):
            reponse = self.api("POST", f"batch/{ressource}",
                               params={"upsert": "true"} if upsert else None,
                               contenu=lignes[debut:debut + 60])
            lot, = reponse["data"].values()
            yield from lot

    def modifier(self, ressource, identifiant, **contenu):
        return self.api("PATCH", f"{ressource}/{identifiant}", contenu=contenu)

    def importer_entreprises(self, fiches):
        existantes, groupees = defaultdict(list), defaultdict(list)
        for fiche in fiches:
            groupees[fiche["ice"]].append(fiche)
        if groupees.keys() - {""}:
            for ligne in self.lignes("companies", fields="id,kerixIce"):
                existantes[ligne.get("kerixIce")].append(ligne)
        charges, exclusions = [], []
        for ice, groupe in groupees.items():
            motif = ("ICE absent" if not ice else
                     "ICE ambigu" if len(groupe) > 1 or len(existantes[ice]) > 1 else "")
            if motif:
                exclusions.extend({"kerix": f["kerix"], "motif": motif, "ice": ice} for f in groupe)
                continue
            fiche = groupe[0]
            if existantes[ice]:
                # Relire les champs conservés par le CRM pour les passages suivants.
                ligne = self.record("companies", existantes[ice][0]["id"])
                dossier = charger_json(ligne.get("kerixDossier")) or {}
                fiche = {**dossier, **fiche,
                         "site": (ligne.get("domainName") or {}).get("primaryLinkUrl") or fiche["site"],
                         "linkedin": (ligne.get("linkedinLink") or {}).get("primaryLinkUrl") or fiche["linkedin"]}
                groupe[0] = fiche
            charge = {"name": fiche["nom"], "kerixIce": ice, "kerixUrl": fiche["kerix"],
                      "kerixDossier": json.dumps(fiche, ensure_ascii=False)}
            if fiche["site"] and not existantes[ice]:
                charge["domainName"] = {"primaryLinkUrl": fiche["site"]}
            if fiche["linkedin"] and not existantes[ice]:
                charge["linkedinLink"] = {"primaryLinkUrl": fiche["linkedin"]}
            charges.append(charge)
        ids = {ligne["kerixIce"]: ligne["id"] for ligne in self.lots("companies", charges, upsert=True)}
        if set(ids) != {c["kerixIce"] for c in charges}:
            raise RuntimeError("Résultats d’import Twenty incomplets")
        return [{**groupees[ice][0], "twenty_id": identifiant} for ice, identifiant in ids.items()], exclusions

    def actualiser_rejets(self, controles):
        rejets = {a: p for a, p in controles.items() if p.get("provenance") and not preuve_acceptee(p, a)}
        if not rejets:
            return
        for personne in self.lignes("people", fields="id,emails,prospectingDossier"):
            adresse = ((personne.get("emails") or {}).get("primaryEmail") or "").casefold()
            preuve = rejets.get(adresse)
            dossier = charger_json(personne.get("prospectingDossier")) or {}
            if not preuve or (dossier.get("decision_finale") or {}).get("email") != adresse:
                continue
            precedente = dernier_controle(dossier, adresse)
            if preuve == precedente or date_controle(preuve) < date_controle(precedente):
                continue
            dossier.setdefault("verifications", []).append(preuve)
            dossier["adresse_admise"] = ""
            dossier["decision_finale"] = {"email": adresse, "verdict": "en_attente",
                                         "motif": "Vérification Account56 · " + preuve["verdict"]}
            self.modifier("people", personne["id"], prospectingDossier=json.dumps(dossier, ensure_ascii=False))

    def admettre(self, reference, dossiers):
        if not dossiers:
            return []
        personnes, adresses, fiches = defaultdict(list), defaultdict(set), {}
        for p in self.lignes("people", fields="id,linkedinLink,emails,companyId,prospectingIdentity,prospectingDossier"):
            url = url_personnelle((p.get("linkedinLink") or {}).get("primaryLinkUrl"))
            if url:
                personnes[url].append(p)
            fiches[p["id"]] = p
            emails = p.get("emails") or {}
            for adresse in [emails.get("primaryEmail"), *(emails.get("additionalEmails") or [])]:
                if adresse:
                    adresses[adresse.casefold()].add(p["id"])
        charges, retenus = [], {}
        adresses_lot = set()
        for d in dossiers:
            url, adresse = d["linkedin"], d["adresse_admise"]
            crm_id = d["entreprise"]["twenty_id"]
            if adresse in adresses_lot:
                raise ValueError("Adresse de contact en double")
            adresses_lot.add(adresse)
            ids = set(adresses[adresse])
            if url:
                ids.update(p["id"] for p in personnes[url])
            precedent = fiches[next(iter(ids))] if len(ids) == 1 else {}
            if len(ids) > 1 or (precedent and precedent.get("companyId") not in (None, crm_id)):
                raise ValueError("Identité de contact ambiguë dans Twenty")
            identite = precedent.get("prospectingIdentity") or url or "email:" + adresse
            if identite in retenus or adresses[adresse] - {precedent.get("id")}:
                raise ValueError("Identité ou adresse de contact en double")
            preuve = (d.get("verifications") or [{}])[-1]
            if (d["decision_finale"]["verdict"] != "admis" or d["decision_finale"].get("email") != adresse or not crm_id
                    or not preuve_acceptee(preuve, adresse)):
                raise ValueError("Dossier non admis")
            archive = charger_json(precedent.get("prospectingDossier")) or {}
            controle_archive = dernier_controle(archive, adresse)
            if (controle_archive and not preuve_acceptee(controle_archive, adresse)
                    and date_controle(controle_archive) >= date_controle(preuve)):
                continue
            if controle_archive and date_controle(controle_archive) > date_controle(preuve):
                d = {**d, "verifications": [*d["verifications"], controle_archive]}
            retenus[identite] = d
            emails = precedent.get("emails") or {}
            autres = [e for e in [emails.get("primaryEmail"), *(emails.get("additionalEmails") or [])]
                      if e and e.casefold() != adresse]
            nom = d.get("nom") or (d["entreprise"].get("nom", "") if not url else "")
            charge = {"name": {"firstName": d.get("prenom", ""), "lastName": nom},
                      "companyId": crm_id, "jobTitle": d["poste"]["titre"],
                      "prospectingIdentity": identite,
                      "emails": {"primaryEmail": adresse, "additionalEmails": list(dict.fromkeys(autres))},
                      "prospectingDossier": json.dumps(d, ensure_ascii=False)}
            if url:
                charge["linkedinLink"] = {"primaryLinkUrl": url}
            if precedent and not precedent.get("prospectingIdentity"):
                self.modifier("people", precedent["id"], prospectingIdentity=identite)
            charges.append(charge)
        ids = {p["prospectingIdentity"]: p["id"] for p in self.lots("people", charges, upsert=True)}
        if set(ids) != set(retenus):
            raise RuntimeError("Résultats d’admission Twenty incomplets")
        actives = {a["contactId"]: a for a in self.affectations(reference) if a["campaignRef"] == reference}
        charges = [{"name": reference, "assignmentKey": reference + ":" + identifiant, "campaignRef": reference,
                    "contactId": identifiant}
                   for identifiant in ids.values() if identifiant not in actives]
        # Les mises à jour concurrentes d’envoi et de retrait conservent leurs champs.
        nouvelles = {a["contactId"]: a for a in self.lots(AFFECTATIONS, charges, upsert=True)}
        affectations = {**actives, **nouvelles}
        return [{**retenus[url], "twenty_id": identifiant,
                 "affectation_id": affectations[identifiant]["id"]} for url, identifiant in ids.items()]

    def affectations(self, reference):
        return self.lignes(AFFECTATIONS, filter="campaignRef[eq]:" + reference_valide(reference))

    def campagne(self, reference):
        lignes = list(self.lignes(CAMPAGNES, filter="reference[eq]:" + reference_valide(reference)))
        if len(lignes) != 1 or lignes[0]["reference"] != reference:
            raise ValueError("Référence de campagne Twenty inconnue ou ambiguë")
        return charger_json(lignes[0]["briefJson"])
