"""Adaptateurs du worker pour Twenty et Thunderbird MCP."""
import json
import os
from pathlib import Path
from urllib.request import Request,urlopen
from .collecte import url_personnelle
from .verification_account56 import preuve_acceptee


def requete(url,jeton,corps=None,methode=None):
    r=Request(url,data=json.dumps(corps).encode() if corps is not None else None,method=methode,
              headers={"Content-Type":"application/json","Authorization":"Bearer "+jeton})
    with urlopen(r,timeout=30) as reponse:
        return json.load(reponse)


def crm(chemin,corps=None,methode=None):
    return requete(os.environ["TWENTY_BASE_URL"].rstrip("/")+"/rest/"+chemin,
                   os.environ["TWENTY_API_KEY"],corps,methode)


def eligible(entree,adresse):
    if not preuve_acceptee(entree.get("verification"), adresse):
        return False
    data=crm("people/"+entree["twenty_id"])["data"]
    p=next(iter(data.values()))
    return (p["id"]==entree["twenty_id"] and p.get("campaignStatus") in (None,"","READY_TO_MAIL")
            and p["emails"]["primaryEmail"].casefold()==adresse
            and url_personnelle(p["linkedinLink"]["primaryLinkUrl"])==entree["identite"])


def envoyer(charge):
    chemin=Path(os.environ["THUNDERBIRD_CONNECTION"])
    connexion=json.loads(chemin.read_text())
    r=requete(f"http://127.0.0.1:{int(connexion['port'])}/",connexion["token"],{
        "jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"sendMail","arguments":{
            "to":charge["adresse"],"subject":charge["objet"],"body":charge["corps"],
            "isHtml":False,"includeSignature":False,"skipReview":True}}})
    resultat=r.get("result",{})
    if resultat.get("isError"):
        return None
    for bloc in resultat.get("content",[]):
        if bloc.get("type")=="text":
            try:
                preuve=json.loads(bloc["text"])
            except ValueError:
                continue
            if (isinstance(preuve, dict) and not preuve.get("error")
                    and (preuve.get("messageId") or (preuve.get("success") is True
                         and preuve.get("message") == "Message sent"))):
                return preuve
    return None


def synchroniser(charge,recu,date):
    chemin="people/"+charge["personne"]
    p=next(iter(crm(chemin)["data"].values()))
    trace=f"envoi {date} | {charge['empreinte']}"
    if "adresse_qualifiee" in charge:
        trace = "Pilote vers la boîte de test " + trace
    source=p.get("sourceFile") or ""
    patch={} if trace in source else {"sourceFile":(source+" | "+trace).strip(" |")}
    if "adresse_qualifiee" not in charge and p.get("campaignStatus") in (None,"","READY_TO_MAIL"):
        patch["campaignStatus"]="EMAIL_SENT"
    if patch:
        crm(chemin,patch,"PATCH")
