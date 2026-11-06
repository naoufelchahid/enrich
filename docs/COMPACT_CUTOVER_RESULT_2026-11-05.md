# Bascule réelle du journal compact — 5 novembre 2026

Le ticket 15 est terminé. Sur instruction de l’opérateur, le journal compact est installé dans `bdd/workflow/staging.sqlite.journal.sqlite`, les sept autorités sont compactes et l’ancien SAS `bdd/workflow/staging.sqlite` est retiré. `.env` sélectionne explicitement ce backend et ce journal. Aucun commit ni push n’a été créé.

Les acquisitions, emails, réveils Telegram et quatre crons Enrich restent suspendus. `WORKFLOW_STOP_AT_DRAFT=1`, `TELEGRAM_AGENT_HOOK_ENABLED=0` et le paramètre de campagne restreinte sont conservés. Les autres lignes du crontab sont intactes ; retirer les quatre préfixes de pause retrouve exactement sa définition initiale. Aucune reprise automatique n’est prévue.

## Conservation et validation

La pause technique est vérifiée à **16:42:31 UTC**. Aucun écrivain de workflow n’est observé avant installation. Une sauvegarde cohérente des deux originaux, prise sous leurs verrous SQLite après la pause, est compressée, restaurée et vérifiée par empreinte, contenu et intégrité. La préparation et la réconciliation travaillent exclusivement sur ces copies.

| Contrôle réel | Résultat |
|---|---:|
| Candidats conservés | 37 343 |
| Liaisons vers les identifiants SAS | 37 343 |
| Alias des clés d’import | 37 343 |
| Autorités compactes | 7 |
| Événements exportés concordants après réconciliation | 1 173 014 |
| Événements absents, divergents ou restant à exporter | 0 |
| Blobs vérifiés pendant la préparation | 113 463 |
| Contacts CRM / brouillons / opérations / envois historiques | 20 / 59 / 140 / 6 |
| Artefacts historiques conservés | 55 |
| Références de preuve candidates sans blob | 0 |

Toutes les lignes préexistantes du journal réconcilié sont comparées exactement. Le SAS entier est reconstruit indépendamment depuis le candidat : schéma, cellules typées, séquences AUTOINCREMENT et outbox concordent avec la copie réconciliée. La revue indépendante confirme les identités, preuves, états inconnus et versions de brouillons. Les exclusions et politiques existantes sont conservées.

L’installation remplace atomiquement le journal sous verrous exclusifs, en conservant d’abord le SAS. Les contrôles en lecture seule sur le journal installé passent avant une seconde invocation qui retire le SAS à **17:20:57 UTC**. Aucun statut métier, nettoyage ou autre écriture n’intervient entre ces deux étapes. La configuration compacte est vérifiée à **17:21:17 UTC**.

Après retrait, les trois aperçus Apify, LinkedIn et campagnes passent avec zéro action externe ; le statut réel retrouve les 37 343 candidats sans recréer le SAS. La rétention est prévisualisée puis appliquée par `scripts.workflow_files` : aucun dossier métier supprimé, huit éléments en attente protégés. Un événement `compact_cutover_installed` archive ensuite la réception et les contrôles dans le journal. Cette nouvelle écriture clôt l’état exact d’installation : le plan initial ne doit plus servir de commande de rejeu ou de retour arrière.

Suite complète du parent : **682 tests réussis en 108,22 s**, avec un avertissement de dépréciation `fitz` préexistant. Les tests couvrent interruptions, reprise, remplacement atomique, verrous, reconstruction exacte, réconciliation et interdiction de soumission sur résultat inconnu. `git diff --check` passe.

## Opérations en attente et services externes

La réconciliation ciblée utilise **sept requêtes GET**, sans soumission, création CRM, notification ni email :

- Le job FullEnrich précédemment `SUBMITTED` est confirmé `FINISHED`, sans adresse et avec un coût constaté de zéro. Son état devient `NO_MATCH`, avec réponse exacte et corrélation, sans relance automatique.
- Les deux créations CRM `CREATE_PERSON UNKNOWN` restent exactement `UNKNOWN`. Les relectures n’établissent pas le résultat initial ; leurs requêtes, identités et preuves sont conservées de façon autonome dans le journal.
- Les 21 tâches LinkedIn héritées concernant 19 candidats restent `WAITING`, avec leur dossier exact et sans nouvelle recherche automatique.

Les 36 fichiers de réconciliation, soit 418 996 octets, sont archivés et vérifiés dans le journal ; leurs copies privées restent dans le run en attente. Aucune donnée de contact ni réponse fournisseur n’est ajoutée à Git.

Les adaptateurs d’acquisition réels et leurs budgets restent à configurer avant acquisition. La sonde Thunderbird est négative ; elle ne conditionne pas cette migration de stockage. Aucun parcours métier n’est réactivé et aucune reprise fournisseur n’est exécutée pour mesurer un coût ou une croissance de production.

## Durées et volumes mesurés

| Phase réelle | Durée |
|---|---:|
| Sauvegarde et restauration vérifiée | 848,08 s |
| Réconciliation sur copies | 6,73 s |
| Préparation et conservation du candidat | 829,90 s |
| Installation avec maintien du SAS | 136,52 s |
| Vérification finale et retrait du SAS | 86,31 s |

Le retrait intervient environ 38 min 26 s après la pause vérifiée. La pause métier reste ensuite maintenue. Ces mesures sur les bases réelles remplacent toute estimation fondée sur les fixtures synthétiques.

| Stockage | Octets |
|---|---:|
| Deux bases actives avant bascule | 9 482 170 368 |
| Journal candidat installé | 4 945 760 256 |
| Journal après statuts, rétention et archivage de réception | 4 945 768 448 |
| Réduction du stockage actif | **4 536 401 920** |
| Nouvelle sauvegarde compressée conservée | 785 041 876 |
| Réduction après prise en compte de cette sauvegarde | **3 751 360 044** |

La réduction active est d’environ **4,54 Go**, soit **3,75 Go** après conservation de la nouvelle sauvegarde. Ces nombres comparent les fichiers concernés ; ils ne mesurent pas l’espace libre global, partagé avec d’autres projets. Les initialisations et l’archivage final ajoutent 8 192 octets au candidat installé. Aucune croissance après reprise métier n’est extrapolée.

Les deux copies SQLite restaurées utilisées comme intermédiaires sont retirées après validation de leur conservation exacte ; elles occupaient 9 482 182 656 octets. Le patch régénérable vérifié est retiré de `.scratch/`. Le point de mesure avant installation représente 14 427 942 912 octets de copies de travail et candidat, hors sauvegarde compressée. Le pic disque n’a pas été mesuré en continu ; la reconstruction ajoute un fichier temporaire pendant la préparation. Les sources, exports humains, sauvegardes compressées, rapports en attente et preuves sont conservés.

## Preuves et restauration

Les relevés privés sont dans `outputs/refonte-enrich-2026-11-05/live-*.json`, notamment `live-backup`, `live-candidate`, `live-install-final`, `live-post-install-readonly`, `live-runtime`, `live-retention`, `live-storage` et `live-validation`. Le journal conserve aussi les pièces de réception par URI `journal://artifacts/<sha256>`.

La sauvegarde est dans `backups/2026-11-05-compact-cutover/migration-20261105T164256-e30c4aca996342929c8e73c7b11363d0/` : `manifest.json`, `staging.sqlite.gz`, `journal.sqlite.gz`, plan et réception. La configuration avant pause est conservée séparément avec des permissions privées. Les copies brutes de travail ne constituent pas une sauvegarde permanente supplémentaire.

Pour restaurer ou traiter une interruption, suivre le [runbook](COMPACT_CUTOVER_RUNBOOK.md). Restaurer vers un chemin neuf et vérifier la sauvegarde. Réconcilier les réponses, preuves et écritures postérieures avant tout remplacement par la paire initiale ; une restauration vérifiée ne vaut pas autorisation d’écraser le journal courant. Celui-ci permet également de reconstruire l’état exact du SAS réconcilié. Les tests refusent le retour silencieux vers un checkpoint antérieur à de nouvelles écritures, y compris une allocation AUTOINCREMENT supprimée ensuite.

## Mémoire et coordination

Les livraisons des sous-agents, empreintes de patches, revue et accusés de réception sont conservés dans le logstream MemPalace. Les demandes de bascule et du ticket 15 portent maintenant un accusé `applied`. L’indexation de ce rapport en drawer est refusée par le verrou d’un autre writer ; aucun processus pair n’est arrêté. Le rapport reste dans ce fichier et est déposé textuellement dans un événement `knowledge.note` du logstream. Le watcher propre à cette intervention est arrêté à la clôture.
