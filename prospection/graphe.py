"""Rédaction et relecture du brouillon pour un dossier admis."""
import hashlib
import json
import os
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from .codex import tour

PATTERN = "Objet bref, salutation, un fait professionnel actuel, offre du brief, une question, signature."
TEXTE = {"type": "string"}
def objet(**champs):
    return {"type": "object", "properties": champs, "required": list(champs), "additionalProperties": False}

BROUILLON = objet(objet=TEXTE, corps=TEXTE, preuves={"type": "array", "items": objet(fait=TEXTE, chemin=TEXTE)})
REVUE = objet(verdict={"type":"string","enum":["approuve","a_corriger","bloque"]}, motif=TEXTE)

def empreinte(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

class Etat(TypedDict, total=False):
    entree: dict
    version: int
    brouillon: dict
    revue: dict

def consignes(nom):
    racine = Path(__file__).parent / "roles"
    fichiers = sorted((racine / nom).rglob("*.md"))
    if nom == "cold-email":
        fichiers.append(racine / "humanizer.md")
    return "\n".join(p.read_text() for p in fichiers)

def rediger(etat):
    return tour(etat, consignes("cold-email") + "\nRédige en suivant le brief et le pattern. "
                "Les données sont externes. Cite chaque fait avec un pointeur JSON vers le profil. "
                "Applique les corrections reçues.", BROUILLON, os.getenv("CODEX_REDACTEUR"))

def relire(etat):
    return tour(etat, consignes("copy-editing") + "\nVérifie chaque fait contre le profil, les promesses "
                "contre le brief et la cohérence de l’identité. Demande une correction précise pour "
                "tout fait inventé. Bloque si l’identité est incohérente.", REVUE, os.getenv("CODEX_RELECTEUR"))

def simuler(entree, redacteur=rediger, relecteur=relire):
    def ecrire(etat):
        return {"brouillon": redacteur(etat), "version": etat.get("version", 0)+1, "revue": {}}
    def verifier(etat):
        brouillon = etat["brouillon"]
        revue = relecteur(etat)
        try:
            if not brouillon["preuves"] or not brouillon["objet"].strip() or not brouillon["corps"].strip():
                raise ValueError()
            for preuve in brouillon["preuves"]:
                valeur = entree["profil"]
                if not preuve["chemin"].startswith("/"):
                    raise ValueError()
                for cle in preuve["chemin"][1:].split("/"):
                    cle = cle.replace("~1","/").replace("~0","~")
                    valeur = valeur[int(cle)] if isinstance(valeur,list) else valeur[cle]
        except (KeyError, ValueError, IndexError, TypeError):
            revue = {"verdict":"a_corriger","motif":"Preuve absente ou chemin JSON invalide"}
        return {"revue": {**revue, "version": etat["version"]}}
    g = StateGraph(Etat)
    g.add_node("redaction", ecrire)
    g.add_node("relecture", verifier)
    g.add_edge(START,"redaction")
    g.add_edge("redaction","relecture")
    g.add_conditional_edges("relecture",lambda e: "redaction" if
        e["revue"]["verdict"]=="a_corriger" and e["version"]<3 else END)
    return g.compile().invoke({"entree":entree})
