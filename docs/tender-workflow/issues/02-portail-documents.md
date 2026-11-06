# 02 : Découverte du portail et téléchargements éphémères

**À construire :** parcourir directement les listes publiques des extraits de PV et des résultats définitifs, sur douze mois pour le premier passage puis trois jours glissants. Ouvrir les annonces et récupérer les PDF directs et ceux des ZIP.

**Dépend de :** 01.

**Statut :** terminé.

- [x] Pagination et dates bornées, limite configurable de documents, aucun recours à la base `Tender` de `tender_scrape`.
- [x] Tous les PDF d'une annonce sont traitables ; ZIP contrôlés contre les chemins dangereux et les fichiers hors périmètre.
- [x] Téléchargements temporaires supprimés à la fin du traitement ou après échec ; aucun identifiant de document conservé.
- [x] Tests avec pages et pièces simulées, puis petit contrôle en lecture seule du portail si disponible.
