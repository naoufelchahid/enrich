"""Installer les objets et les champs Twenty utilisés par les deux graphes."""
from .crm_campagnes import AFFECTATIONS, CAMPAGNES, EXPEDITIONS, api_twenty

OBJETS = {
    CAMPAGNES: ("prospectingCampaign", "Campagne de prospection", "Campagnes de prospection"),
    AFFECTATIONS: ("prospectingAssignment", "Affectation de prospection", "Affectations de prospection"),
    EXPEDITIONS: ("prospectingDelivery", "Expédition de prospection", "Expéditions de prospection"),
}
CHAMPS = {
    "companies": (
        ("kerixIce", "ICE Kerix", "TEXT", True),
        ("kerixUrl", "Lien Kerix", "TEXT", False),
        ("kerixDossier", "Dossier Kerix", "TEXT", False),
    ),
    "people": (
        ("prospectingIdentity", "Identité LinkedIn de prospection", "TEXT", True),
        ("prospectingDossier", "Dossier de prospection", "TEXT", False),
        ("prospectingUnsubscribed", "Désinscrit de la prospection", "BOOLEAN", False),
    ),
    CAMPAGNES: (
        ("reference", "Référence", "TEXT", True),
        ("briefJson", "Brief éditorial JSON", "TEXT", False),
    ),
    AFFECTATIONS: (
        ("assignmentKey", "Clé d’affectation", "TEXT", True),
        ("campaignRef", "Référence de campagne", "TEXT", False),
        ("contactId", "Identifiant du contact", "TEXT", False),
        ("withdrawn", "Affectation retirée", "BOOLEAN", False),
        ("draftJson", "Brouillon et relecture JSON", "TEXT", False),
    ),
    EXPEDITIONS: (
        ("deliveryKey", "Clé d’expédition", "TEXT", True),
        ("assignmentId", "Identifiant d’affectation", "TEXT", False),
        ("campaignRef", "Référence de campagne", "TEXT", False),
        ("deliveryStatus", "État d’expédition", "TEXT", False),
        ("deliveryJson", "Suivi d’expédition JSON", "TEXT", False),
    ),
}


def installer(api=api_twenty):
    def liste(objet):
        params = {"limit": 100}
        while True:
            page = api("GET", "metadata/" + objet, params=params)
            yield from page["data"]
            if not page["pageInfo"]["hasNextPage"]:
                return
            params["starting_after"] = page["pageInfo"]["endCursor"]

    objets = {o["namePlural"]: o for o in liste("objects")}
    for pluriel, (singulier, etiquette, etiquettes) in OBJETS.items():
        if pluriel not in objets:
            resultat = api("POST", "metadata/objects", contenu={
                "nameSingular": singulier, "namePlural": pluriel, "labelSingular": etiquette,
                "labelPlural": etiquettes, "icon": "IconBriefcase",
            })
            objets[pluriel] = resultat.get("data", resultat)
    champs = {(f["objectMetadataId"], f["name"]): f for f in liste("fields")}
    ajoutes = []
    for objet, declarations in CHAMPS.items():
        identifiant = objets[objet]["id"]
        for nom, etiquette, type_champ, unique in declarations:
            if (identifiant, nom) not in champs:
                resultat = api("POST", "metadata/fields", contenu={
                    "objectMetadataId": identifiant, "name": nom, "label": etiquette, "type": type_champ,
                    "isNullable": True, "isUnique": unique,
                })
                champs[identifiant, nom] = resultat.get("data", resultat)
                ajoutes.append(nom)
            f = champs[identifiant, nom]
            if f["type"] != type_champ or (unique and f.get("isUnique") is not True):
                raise RuntimeError(f"Champ Twenty incompatible : {objet}.{nom}")
    return ajoutes
