# 01 : État technique indépendant et contrat de reprise

**À construire :** un module et une configuration propres au workflow, avec un journal SQLite limité aux opérations payantes et aux écritures CRM. Aucune table ni sortie durable ne contient référence de marché, URL de document, empreinte de PDF, participation, texte ou image. Le workflow de prospection existant reste indépendant.

**Dépend de :** aucun ticket.

**Statut :** terminé.

- [x] Base créée dans `bdd/workflow/` avec schéma minimal, verrouillage et opérations idempotentes ; aucun fichier réel ajouté à Git.
- [x] État `UNKNOWN` réconciliable avant resoumission, tentative certaine bornée, identifiants fournisseur et CRM conservés sans données de marché.
- [x] Configuration et CLI d'aperçu ne créant aucune base et n'appelant aucun service ; secrets absents des logs.
- [x] Tests isolés de reprise et de schéma, puis `git diff --check`.
