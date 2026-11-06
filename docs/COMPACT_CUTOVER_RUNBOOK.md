# Bascule vers le journal compact — procédure et preuves

Statut au 5 novembre 2026 : la bascule réelle est terminée et ses contrôles sont consignés dans le [rapport de résultat](COMPACT_CUTOVER_RESULT_2026-11-05.md). Les parcours restent en pause. Ce document décrit la procédure reproductible `scripts.compact_apply prepare` / `install` ; ses chemins d’exemple ne sont pas une instruction de rejeu du plan déjà clôturé.

`compact_cutover rehearse` conserve son périmètre synthétique ; son indicateur `production_apply_implemented=False` ne décrit pas le module distinct `compact_apply`. La préparation travaille sur copies restaurées, puis une revue en lecture seule précède l’installation. Pause démontrée et sauvegarde fraîche restaurable conditionnent les deux phases. La reprise des emails reste une décision séparée.

## État observé en lecture seule

L’audit du 5 novembre a commencé à 16:07:30 UTC et a duré 80,25 secondes. Son résumé humain est `outputs/refonte-enrich-2026-11-05/audit-summary.json`, conservé hors Git. Les écrivains n’étaient pas arrêtés ; il ne remplace pas le manifeste final figé.

| Mesure | Résultat observé |
|---|---:|
| Candidats dans le SAS | 37 343 |
| Identités manquantes | 0 |
| Événements exportés et comparés exactement | 1 173 011 |
| Événements absents ou divergents | 0 |
| Exports encore en attente | 0 |
| SAS | 4 769 013 760 octets |
| Journal | 4 713 156 608 octets |
| Taille des payloads identiques comparés | 2 548 486 391 octets |
| Job fournisseur SUBMITTED | 1 FullEnrich, type contact, une cible |
| Opérations CRM UNKNOWN | 2 CREATE_PERSON |
| Brouillons approuvés / en attente | 0 / 3 |
| Artefacts | 55 |
| Octets réellement récupérés | 0 |

La file LinkedIn observée contenait 9 BLOCKED, 3 FAILED, 2 IMPORTED, 6 REVIEW et 1 STALE. Ses attentes et preuves doivent survivre à la migration. Un marqueur d’export ne permet aucune purge : l’audit compare chaque événement par source, identifiant, contenu, type, contact et date. La taille des payloads n’est ni une prévision d’espace libérable ni une mesure du volume d’outbox alloué.

## Répétition livrée

Commande disponible dès maintenant, exclusivement synthétique :

```bash
python3 -m scripts.compact_cutover rehearse --directory .scratch --output outputs/refonte-enrich-2026-11-05/rehearsal-next.json
```

Le chemin de sortie doit être neuf. Le script crée un sous-dossier temporaire, y construit ses trois candidats fictifs, sauvegarde fraîchement les deux bases, restaure vers un autre dossier et vérifie empreintes, contenu exact et intégrité. Il simule une interruption entre tâche héritée durable et rattachement du candidat, rouvre le stockage, reprend la migration puis la rejoue. L’ancien SAS restauré est ensuite rendu indisponible.

Un No2Bounce SUBMITTED fictif à une seule adresse reprend par polling, avec interdiction de soumettre un nouveau job. Un FullEnrich hérité fictif reste en attente explicite. Deux opérations CREATE_PERSON UNKNOWN, un brouillon approuvé, une exclusion et une preuve exacte restent présents. Les statuts Apify/campagnes et les aperçus restent disponibles quand LinkedIn attend. Aucun fournisseur, CRM, Telegram ou transport réel n’est appelé.

Mesure du run conservé dans `outputs/refonte-enrich-2026-11-05/rehearsal-summary.json` :

| Mesure synthétique | Résultat |
|---|---:|
| Durée totale | 0,6584 s |
| Création des fixtures | 0,1994 s |
| Sauvegarde fraîche et restauration vérifiée | 0,0465 s |
| Migration interrompue, reprise et rejeu | 0,2781 s |
| Espace observé initial | 323 621 octets |
| Après sauvegarde et restauration | 718 627 octets |
| Après migration | 821 027 octets |
| Après reprise métier simulée | 821 027 octets |
| Maximum observé aux points de mesure | 821 027 octets |
| Événements fictifs conservés exactement | 15 / 15 |
| Candidats fictifs retrouvés | 3 / 3 |
| Événements manquants / divergents | 0 / 0 |

Ces durées et volumes décrivent uniquement les fixtures. Le maximum est observé entre phases, pas une mesure continue du pic disque. Ils ne sont pas extrapolés aux bases de plusieurs Go. Le sous-dossier temporaire et ses 821 027 octets de fichiers synthétiques ont été retirés ; le résumé humain demeure. Aucun fichier contenant des données réelles n’a été supprimé.

## Préconditions de la fenêtre réelle

1. Consigner début de pause horodaté avec fuseau, responsable, durée maximale et marge de retour arrière. Vérifier l’arrêt des écrivains selon la section suivante ; la pause concerne aussi les campagnes, les acquisitions et les interventions manuelles.
2. Relever l’espace libre et choisir les chemins distincts de sauvegarde, restauration et candidat. Conserver simultanément la paire active, la sauvegarde compressée, les copies restaurées et le candidat, plus les bruts temporaires de vérification. Mesurer la croissance sur copie ; les chiffres synthétiques ne donnent pas le pic réel.
3. Valider la version exacte du code avec la suite d’intégration et les aperçus des trois parcours. Les tests de `tests/test_compact_apply.py` couvrent préparation, conservation, reconstruction, interruptions, remplacement atomique et maintien des verrous. Conserver le résultat du contrôle retenu dans le dossier de fenêtre.
4. Préparer le traitement des opérations en attente : consultation ciblée d’un job FullEnrich déjà soumis, relecture de Twenty pour les créations inconnues, conservation des attentes LinkedIn. Ces consultations ne soumettent aucun nouveau job ni aucune création CRM. Une commande générale `enrich` n’est pas un outil de réconciliation ciblée.

Les paramètres `writes_paused=True` et `--writes-paused` n’arrêtent aucun producteur. `backup_pair` prend les deux verrous SQLite pendant sa sauvegarde ; `install` prend les verrous exclusifs pendant la vérification et le remplacement. Ces verrous complètent la preuve de pause sans couvrir un appel distant déjà en vol.

## Activation d’acquisition, préconditions distinctes

Configurer et sonder les adaptateurs réels avant leur activation. Les classes injectables et simulations n’attestent pas d’une connexion de production. Les briefs doivent porter leurs montants et unités avant toute acquisition payante ; aucune conversion de crédits en argent n’est inventée. Ces conditions ne bloquent pas une migration de stockage qui conserve les acquisitions arrêtées. Une opération héritée encore dépendante du SAS doit être résolue sur copie ou disposer d’une reprise autonome démontrée avant son retrait.

Le rapport sépare donc `storage_cutover_blockers` et `acquisition_activation_blockers`. Aucun de ces contrôles ni leur réussite ne vaut reprise des emails : instruction explicite de reprise et approbations individuelles restent nécessaires.

## Arrêter et vérifier tous les écrivains

L’[inventaire des lecteurs/écrivains](COMPACT_STORAGE_TRANSITION.md) est la liste de référence. Pendant la fenêtre, suspendre leurs déclencheurs en conservant les autres crons et leurs définitions. Les éléments à contrôler sont :

- tous les ticks du wrapper, préparation, campagnes, inbox, rétention et réveils d’agent Telegram ;
- workers Codex/MCP et processus fournisseur/CRM déjà démarrés, y compris ceux lancés depuis une autre session ;
- batches manuels, continuation autorisée et script historique Mohammedia ;
- imports de contacts/sociétés, maintenance domaine et preuves, scripts de remplissage CRM ;
- outils d’édition SQLite et interventions manuelles capables de modifier le SAS ou le journal ;
- actions d’opérateur et commandes Telegram pouvant écrire une approbation ou un brief pendant la pause.

`WORKFLOW_STOP_AT_DRAFT=1` bloque l’envoi mais ne suspend pas tous ces écrivains. Vérifier les processus et détenteurs des fichiers ; un statut vide ne prouve pas à lui seul l’absence d’un appel distant en vol. Tout résultat inconnu doit rester représenté dans le manifeste. Ne pas arrêter un processus en cours sans prendre en compte sa soumission éventuelle.

Commandes de constat à adapter aux gestionnaires effectivement utilisés, sans suppression du crontab :

```bash
crontab -l
pgrep -af 'scripts\.(workflow|workflow_batch|run_authorized_batch|mohammedia_campaign|outreach_dispatcher|linkedin_queue|linkedin_mcp|direct_enrichment|compact_)'
lsof bdd/workflow/staging.sqlite bdd/workflow/staging.sqlite.journal.sqlite
df -B1 bdd/workflow backups
```

La preuve de pause doit combiner les déclencheurs suspendus, la fin/réconciliation des workers, les détenteurs de fichiers et les états d’opérations. Aucune de ces commandes n’est exécutée automatiquement par le script de répétition.

## Sauvegarde fraîche et réconciliation sur copies

Après arrêt démontré des écrivains, auditer la paire active figée en lecture seule :

```bash
python3 -m scripts.workflow_migration audit --staging bdd/workflow/staging.sqlite --journal bdd/workflow/staging.sqlite.journal.sqlite
```

Critère : aucun export absent, divergent ou encore en attente ; identités, états, réservations, exclusions, brouillons et artefacts sont recensés. Une égalité de comptes seule ne suffit pas.

Sauvegarder ensuite la paire originale et restaurer vers un dossier neuf :

```bash
python3 -m scripts.workflow_migration backup --staging bdd/workflow/staging.sqlite --journal bdd/workflow/staging.sqlite.journal.sqlite --directory backups/DATE-FENETRE --writes-paused
python3 -m scripts.workflow_migration restore --manifest backups/DATE-FENETRE/BUNDLE/manifest.json --directory backups/DATE-FENETRE/restauration-verifiee
```

Remplacer les chemins et horodatages d’exemple par ceux de la fenêtre. La sauvegarde doit être créée après le début de pause, puis sa restauration vérifiée par SHA-256, intégrité, schéma et contenu exact. Une sauvegarde ancienne présente sur disque ne remplit pas ce critère. Les fichiers restaurés sont `staging.sqlite` et `journal.sqlite` ; le second n’est pas un suffixe à recalculer depuis le premier.

Appliquer les réponses déjà capturées uniquement à ces copies :

```bash
python3 -m scripts.compact_reconcile --db-path backups/DATE-FENETRE/restauration-verifiee/staging.sqlite --journal-path backups/DATE-FENETRE/restauration-verifiee/journal.sqlite --evidence-dir reports/compact-live-cutover-2026-11-05/reconciliation --offline-copy
```

Le helper refuse les bases actives et leurs alias. Il ne fait aucun appel réseau. Il conserve les preuves exactes, vérifie la corrélation du résultat fournisseur et exporte l’outbox vers le journal explicitement fourni. Un résultat FullEnrich terminé sans adresse devient `NO_MATCH` sans nouvelle soumission. Les créations CRM dont le résultat initial reste indéterminé gardent `UNKNOWN` avec leur requête, leur identité et leur preuve de relecture autonomes. Une absence de résultat ou une adresse différente ne prouve jamais un succès. Les attentes LinkedIn et leurs preuves restent conservées sans recherche automatique supplémentaire.

Critère : chaque modification des copies possède une preuve ; chaque attente reste récupérable dans le journal candidat. Les originaux actifs conservent exactement l’état de la sauvegarde fraîche.

## Phase 1 — Préparer le journal candidat

Choisir un chemin candidat neuf, distinct des copies et des actifs, sur le même système de fichiers que le journal actif. Préparer le candidat avec le manifeste original et l’horodatage réel de début de pause :

```bash
python3 -m scripts.compact_apply prepare \
  --staging-copy backups/DATE-FENETRE/restauration-verifiee/staging.sqlite \
  --journal-copy backups/DATE-FENETRE/restauration-verifiee/journal.sqlite \
  --candidate bdd/workflow/journal-candidat-DATE-FENETRE.sqlite \
  --backup-manifest backups/DATE-FENETRE/BUNDLE/manifest.json \
  --pause-started-at HORODATAGE-ISO8601-AVEC-FUSEAU \
  --proof reports/compact-live-cutover-2026-11-05/reconciliation/proposals.json
```

Répéter `--proof` pour les autres preuves de réconciliation utiles ; si les copies ont changé depuis la sauvegarde, ces preuves sont obligatoires. Leurs références complètent les réponses exactes déjà archivées dans le journal de copie.

`prepare` copie le journal restauré, active les autorités compactes dans le candidat et migre les données conservées par `stage_legacy`. Il compare les lignes préexistantes du journal, valide les blobs et leurs références, puis reconstruit indépendamment le SAS depuis le candidat. La reconstruction doit retrouver le schéma, les valeurs exactes, l’outbox vérifiée et les séquences AUTOINCREMENT de la copie réconciliée. Les deux copies sont contrôlées de nouveau en fin de préparation.

Critère : le fichier `journal-candidat-DATE-FENETRE.sqlite.plan.json` est produit seulement après ces vérifications, avec `prepared=True`, `source_roundtrip_exact=True`, les empreintes des actifs attendus, du candidat et du manifeste. Un candidat laissé par une préparation interrompue sans plan complet n’est pas installable ; conserver le diagnostic et repartir d’un chemin neuf.

## Revue en lecture seule entre les deux phases

Maintenir tous les écrivains arrêtés. Lire le plan, les rapports et les preuves sans ouvrir une commande métier capable de créer un schéma ou une opération. Exécuter seulement les aperçus explicites :

```bash
python3 -m scripts.workflow_migration audit --staging backups/DATE-FENETRE/restauration-verifiee/staging.sqlite --journal bdd/workflow/journal-candidat-DATE-FENETRE.sqlite
for parcours in apify linkedin campaigns; do
  python3 -m scripts.compact_workflow --service "$parcours" --journal-path bdd/workflow/journal-candidat-DATE-FENETRE.sqlite --action status --dry-run
done
```

Critères avant installation :

- audit exact sans événement manquant, divergent ou en attente ; reconstruction du SAS exacte et tables préexistantes préservées ;
- identités, exclusions, versions approuvées, réserves, états inconnus et preuves résolus par leurs références, y compris les deux `CREATE_PERSON UNKNOWN` conservés ;
- paire active toujours identique au manifeste original, copies réconciliées et candidat conformes au plan ;
- absence d’un lecteur/écrivain restant dépendant de l’ancien SAS pour les parcours à basculer ; acquisitions et emails toujours arrêtés ;
- chemin du journal actif et entrées compactes préparés pour la suite, sans recréer un second suffixe de journal ; décision explicite sur le retrait physique du SAS.

Un contrôle en échec maintient la pause et le SAS. Les commandes de statut sans `--dry-run`, les sondes de fournisseurs et la rétention n’appartiennent pas à cette revue en lecture seule.

## Phase 2 — Installer puis constater

Après validation de la revue, installer le candidat avec le plan exact :

```bash
python3 -m scripts.compact_apply install --plan bdd/workflow/journal-candidat-DATE-FENETRE.sqlite.plan.json --writes-paused
```

Par défaut, cette commande conserve le SAS. Ajouter `--retire-staging` seulement lorsque les critères de conservation, d’autonomie des attentes et d’absence de dépendants sont validés. Cette option peut aussi être appliquée par une seconde invocation du même plan après une installation contrôlée, tant que les écrivains restent arrêtés et que l’état installé demeure identique au plan.

`install` revérifie la sauvegarde et son empreinte, prend les verrous exclusifs, compare la paire active à la sauvegarde originale et vérifie l’empreinte du candidat. Un WAL non vide bloque le remplacement : traiter son checkpoint sous la pause avant de reprendre cette étape. Le remplacement atomique exige le même système de fichiers ; le verrou sur le candidat reste attaché à l’inode installé pendant l’opération.

Le SAS n’est retiré qu’après vérification de l’empreinte du journal installé ; ses éventuels autres liens physiques bloquent ce retrait. Une installation achevée normalement produit une réception `*.plan.json.receipt.json` avec le chemin installé, le retrait effectué ou non et les octets du fichier SAS effectivement retiré. Ce nombre n’est pas l’espace net récupéré après prise en compte des sauvegardes et copies conservées.

Après une interruption survenue après remplacement, conserver la pause et examiner le journal et le SAS en lecture seule. Si l’état installé correspond toujours au plan et que le SAS original est inchangé, la même commande sait reprendre ; elle n’installe pas un deuxième candidat. Toute écriture nouvelle invalide cette reprise fondée sur l’état exact et demande une réconciliation préalable. Un résultat `ALREADY_INSTALLED` doit être rapproché du plan et de la réception disponible avant clôture ; il ne constitue pas une nouvelle mesure d’espace libéré.

Critère de clôture à consigner par le responsable : réception et empreintes vérifiées, autorités compactes et attentes consultables sans le SAS, mesures avant/après, fichiers retirés et sauvegardes conservées. `compact_apply` ne modifie ni `.env` ni les crons et n’appelle aucun fournisseur, CRM ou transport. Le raccord des entrées compactes se fait séparément en conservant la pause. La réussite de l’installation ne relance ni les acquisitions ni les emails ; toute reprise d’emails exige une instruction explicite et les approbations individuelles normales.

## Retour arrière et croissance après reprise

Avant reprise métier, conserver un checkpoint logique exact du journal et le manifeste de la paire finale associée. `rollback_checkpoint` inclut toutes les tables, dont `sqlite_sequence`, les valeurs de blobs, le schéma et les identifiants d’application/version. `assess_rollback` est en lecture seule.

Une nouvelle ligne, une modification, un changement de schéma ou même l’allocation puis suppression d’un identifiant AUTOINCREMENT rendent le retour vers l’ancienne sauvegarde non sûr. La répétition produit `restore_allowed=False` après reprise du résultat No2Bounce. Il n’existe aucun paramètre permettant de contourner ce refus.

Après de nouvelles écritures, arrêter de nouveau les producteurs, rapprocher tous les résultats nouveaux/ambigus, les préserver dans un état cohérent et créer une nouvelle sauvegarde vérifiée. L’outil ne sait pas fusionner ces résultats vers une ancienne paire et n’exécute pas de rollback de production. La comparaison logique seule ne remplace pas les preuves de résultats distants en vol.

Après toute reprise future, relever espace physique, éventuels WAL/SHM, comptes d’événements et croissance sur des initialisations/mises à jour identiques. Mesurer séparément l’espace réellement libéré, les sauvegardes conservées et les attentes protégées. Conserver sources, exports humains et exclusions. Les copies de contrôle et rapports intégrés sont nettoyés uniquement par `scripts.workflow_files` après vérification de leurs preuves.
