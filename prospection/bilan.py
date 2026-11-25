"""Compteurs de recette, calculés depuis les observations et les journaux persistés."""
import json
def bilan(base, campagne, expeditions, decouvertes, urls, enrichis):
    compteurs=dict(resultats_decouverts=decouvertes, profils_uniques=urls, profils_enrichis=enrichis,
                   profils_introuvables=urls-enrichis, sans_adresse=0, rejetes_qualification=0,
                   rejetes_verification=0, attente_technique=0, admis=0, envoyes=0, incertains=0)
    for q,v,crm in base.execute("SELECT qualification,verification,twenty_id FROM decisions WHERE campagne=?",(campagne,)):
        qual,verif=json.loads(q),json.loads(v)
        if qual["verdict"]!="correspond":
            cle="sans_adresse" if qual.get("motif")=="sans adresse HarvestAPI" else "rejetes_qualification"
            compteurs[cle]+=1
        elif verif["verdict"]=="attente_technique":
            compteurs["attente_technique"]+=1
        elif verif["verdict"]=="rejete":
            compteurs["rejetes_verification"]+=1
        elif crm:
            compteurs["admis"]+=1
    for etat, in expeditions:
        compteurs["envoyes"]+=etat in ("confirme","synchronise")
        compteurs["incertains"]+=etat=="incertain"
    return compteurs
