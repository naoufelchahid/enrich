# 03 : Extraction visuelle Codex sans OCR

**À construire :** rendre chaque page PDF en image temporaire, appeler un agent Codex doté de vision et valider une sortie structurée contenant les noms exacts des entreprises mentionnées.

**Dépend de :** 02.

**Statut :** terminé.

- [x] Aucune utilisation de moteur OCR ni du format large de `data_extractor.py` ; aucune limite arbitraire de quinze entreprises par lot.
- [x] Contrat de sortie strict ; document illisible ou sortie incertaine ignoré sans création CRM.
- [x] Images, PDF et sortie locale supprimés après succès ou échec ; aucune preuve de marché persistée.
- [x] Tests sur PDF synthétiques avec plusieurs pages, entreprises multiples et échec d'agent.
