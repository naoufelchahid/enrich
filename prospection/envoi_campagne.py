"""Graphe de rédaction et d’envoi explicite depuis les fiches Twenty."""
import json
import re
from datetime import datetime, timezone
from typing import TypedDict
from urllib.error import HTTPError

from langgraph.graph import END, START, StateGraph

from .crm_campagnes import AFFECTATIONS, EXPEDITIONS, charger_json
from .graphe import PATTERN, empreinte, simuler
from .qualification_kerix import BOUNCE, ZAIQ, normaliser_verdict
from .transport import envoyer


class Etat(TypedDict, total=False):
    campagne: str
    affectation_id: str
    approbation: str
    boite_test: str
    selection: list
    brouillons: list
    resultat: dict


def adresse_admise(personne, *, preuve_apify_exigee=False):
    dossier = charger_json(personne.get("prospectingDossier")) or {}
    adresse = dossier.get("adresse_admise")
    verifications = dossier.get("verifications") or []
    decision = dossier.get("decision_finale", {})
    if (not adresse or decision.get("verdict") != "admis" or decision.get("email") != adresse
            or not verifications or verifications[-1]["verdict"] != "accepte"
            or verifications[-1].get("adresse") != adresse
            or ((personne.get("emails") or {}).get("primaryEmail") or "").casefold() != adresse
            or personne.get("companyId") != dossier.get("entreprise", {}).get("twenty_id")
            or personne.get("prospectingUnsubscribed") is True):
        return None
    if preuve_apify_exigee or dossier.get("mode_verification") == "apify":
        preuve = verifications[-1]
        fournisseur = preuve.get("fournisseur")
        brut = preuve.get("brut") or {}
        if (fournisseur not in {BOUNCE, ZAIQ}
                or str(brut.get("email") or "").strip().casefold() != adresse
                or normaliser_verdict(fournisseur, brut) != "accepte"):
            return None
    return adresse


def graphe_campagne(crm, ecrire=simuler, transmettre=envoyer, horloge=lambda: datetime.now(timezone.utc)):
    def selection(etat):
        lignes = [a for a in crm.affectations(etat["campagne"])
                  if a["campaignRef"] == etat["campagne"] and not a.get("withdrawn")]
        uniques = {a["contactId"]: a["id"] for a in lignes}
        if len(uniques) != len(lignes):
            raise ValueError("Affectations de campagne en double")
        return {"selection": sorted(uniques.items())}

    def ecriture(etat):
        briefing = crm.campagne(etat["campagne"])
        if not isinstance(briefing, dict) or not briefing:
            raise ValueError("Brief éditorial absent de Twenty")
        brouillons = []
        for contact, identifiant in etat["selection"]:
            personne = crm.record("people", contact)
            adresse = adresse_admise(personne)
            if not adresse:
                continue
            dossier = charger_json(personne["prospectingDossier"])
            pattern = briefing.get("structure", PATTERN)
            if dossier.get("nom_confirme") is False:
                pattern += ("\nUtilise une salutation neutre, par exemple Bonjour. "
                            "Adresse-toi à l’entreprise sans citer de nom ni de poste individuel.")
            resultat = ecrire({"profil": dossier, "brief": briefing, "pattern": pattern,
                               "twenty_id": contact, "campagne": etat["campagne"],
                               "identite": dossier.get("linkedin") or adresse, "qualification": dossier["decision_initiale"],
                               "verification": dossier["verifications"][-1]})
            charge = {"campagne": etat["campagne"], "affectation": identifiant, "contact": contact,
                      "adresse": adresse, "version": resultat["version"], "revue": resultat["revue"],
                      "brouillon": resultat.get("brouillon"), "dossier": empreinte(dossier)}
            charge["empreinte"] = empreinte(charge)
            crm.modifier(AFFECTATIONS, identifiant, draftJson=json.dumps(charge, ensure_ascii=False))
            brouillons.append(charge)
        return {"brouillons": brouillons}

    def relecture_crm(etat, approuve):
        try:
            affectation = crm.record(AFFECTATIONS, etat["affectation_id"])
            if affectation.get("withdrawn") or affectation["campaignRef"] != etat["campagne"]:
                return None
            personne = crm.record("people", affectation["contactId"])
        except HTTPError as erreur:
            if erreur.code == 404:
                return None
            raise
        adresse = adresse_admise(personne, preuve_apify_exigee=not etat.get("boite_test"))
        if (not adresse or adresse != approuve["adresse"] or affectation["contactId"] != approuve["contact"]
                or empreinte(charger_json(personne["prospectingDossier"])) != approuve["dossier"]
                or (charger_json(affectation.get("draftJson")) or {}).get("empreinte") != approuve["empreinte"]):
            return None
        for bloc in crm.lignes("blocklists", fields="id,handle"):
            handle = (bloc.get("handle") or "").strip().casefold().removeprefix("*")
            if handle in {adresse, "@" + adresse.partition("@")[2], adresse.partition("@")[2]}:
                return None
        return adresse

    def envoi(etat):
        identifiant, reference = etat["affectation_id"], etat["campagne"]
        try:
            affectation = crm.record(AFFECTATIONS, identifiant)
        except HTTPError as erreur:
            if erreur.code == 404:
                return {"resultat": {"etat": "ecarte", "motif": "Affectation introuvable"}}
            raise
        approuve = charger_json(affectation.get("draftJson")) or {}
        marque = approuve.get("empreinte")
        if (not marque or marque != etat["approbation"]
                or marque != empreinte({k: v for k, v in approuve.items() if k != "empreinte"})
                or approuve["campagne"] != reference or approuve["affectation"] != identifiant
                or approuve["revue"]["verdict"] != "approuve"
                or approuve["revue"]["version"] != approuve["version"]):
            return {"resultat": {"etat": "ecarte", "motif": "Approbation invalide"}}
        adresse = relecture_crm(etat, approuve)
        if not adresse:
            return {"resultat": {"etat": "ecarte", "motif": "Affectation, dossier ou adresse modifiés"}}
        boite = etat.get("boite_test")
        if boite and (not re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+", boite) or boite.casefold() == adresse):
            raise ValueError("Une boîte de test contrôlée distincte du prospect est requise")
        cle = empreinte([identifiant, boite.casefold() if boite else "production"])
        precedent = list(crm.lignes(EXPEDITIONS, filter="deliveryKey[eq]:" + cle))
        if precedent:
            return {"resultat": charger_json(precedent[0]["deliveryJson"])}
        charge = {"adresse": boite or adresse, "objet": approuve["brouillon"]["objet"],
                  "corps": approuve["brouillon"]["corps"]}
        if boite:
            charge["objet"] = "[Pilote] " + charge["objet"]
        suivi = {"etat": "incertain", "campagne": reference, "contact": approuve["contact"],
                 "affectation": identifiant, "approbation": marque, "test": bool(boite),
                 "date": horloge().isoformat(), "charge": charge}
        # La contrainte unique Twenty fait de la création le point de réservation.
        try:
            reservation, = crm.lots(EXPEDITIONS, [{
                "name": reference, "deliveryKey": cle, "assignmentId": identifiant, "campaignRef": reference,
                "deliveryStatus": "incertain", "deliveryJson": json.dumps(suivi),
            }])
        except HTTPError as erreur:
            precedent = list(crm.lignes(EXPEDITIONS, filter="deliveryKey[eq]:" + cle))
            if precedent:
                return {"resultat": charger_json(precedent[0]["deliveryJson"])}
            raise erreur
        if not relecture_crm(etat, approuve):
            suivi.update(etat="ecarte", motif="Affectation ou éligibilité modifiée avant transmission")
        else:
            try:
                recu = transmettre(charge)
            except (OSError, RuntimeError, ValueError):
                recu = None
            if recu:
                suivi.update(etat="confirme", recu=recu, date=horloge().isoformat())
        crm.modifier(EXPEDITIONS, reservation["id"], deliveryStatus=suivi["etat"],
                     deliveryJson=json.dumps(suivi, ensure_ascii=False))
        return {"resultat": suivi}

    return (StateGraph(Etat)
            .add_node("figer_contacts", selection)
            .add_node("rediger_et_relire", ecriture)
            .add_node("expedier_brouillon", envoi)
            .add_conditional_edges(START, lambda e: "expedier_brouillon" if e.get("affectation_id") else
                                   "figer_contacts", ["expedier_brouillon", "figer_contacts"])
            .add_edge("figer_contacts", "rediger_et_relire")
            .add_edge("rediger_et_relire", END)
            .add_edge("expedier_brouillon", END)
            .compile())
