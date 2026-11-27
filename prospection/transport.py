"""Adaptateurs de campagne pour Twenty et Thunderbird MCP."""
import json
import os
from pathlib import Path
from urllib.request import Request,urlopen


def requete(url,jeton,corps=None,methode=None):
    r=Request(url,data=json.dumps(corps).encode() if corps is not None else None,method=methode,
              headers={"Content-Type":"application/json","Authorization":"Bearer "+jeton})
    with urlopen(r,timeout=30) as reponse:
        return json.load(reponse)


def crm(chemin,corps=None,methode=None):
    return requete(os.environ["TWENTY_BASE_URL"].rstrip("/")+"/rest/"+chemin,
                   os.environ["TWENTY_API_KEY"],corps,methode)


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
