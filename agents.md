# Enrich — consignes d’exécution

Le point d’entrée actif est `scripts/workflow.py`, lancé par `scripts/cron_agent_dispatcher.sh`. Depuis la bascule du 5 novembre 2026, le stockage actif est le journal compact `bdd/workflow/staging.sqlite.journal.sqlite` ; l’ancien SAS `staging.sqlite` est retiré. Pour les chemins, les preuves de conservation ou une restauration, lire `docs/COMPACT_CUTOVER_RESULT_2026-11-05.md` et `docs/COMPACT_CUTOVER_RUNBOOK.md`.

Les acquisitions, emails, réveils Telegram et quatre crons Enrich restent en pause. Conserver `WORKFLOW_STOP_AT_DRAFT=1` et `TELEGRAM_AGENT_HOOK_ENABLED=0` jusqu’à instruction explicite de reprise ; la bascule de stockage ne vaut pas reprise métier.

## Avant une intervention

1. Lire `docs/WORKFLOW_OPERATIONS.md` pour modifier une étape, une cadence, une intégration ou une reprise. Les règles actuelles de mailing sont dans ce guide ; les archives décrivent uniquement les décisions passées.
2. Consulter les logs du wrapper dans `logs/`, les états via `--task status` et les accès via `--task readiness --probe` avant une exécution réelle.
3. Tester avec les simulations de `tests/` et les aperçus du wrapper ; les aperçus n’appellent aucun service et ne modifient pas les bases. Utiliser les clients existants.

## Règles de mutation

- Journaliser une opération avant une soumission fournisseur, une création CRM ou un envoi. Réconcilier un résultat inconnu avant toute nouvelle soumission. Trois tentatives maximum pour une erreur temporaire certaine ; conserver `NO_MATCH` sans relance automatique.
- Conserver les données sources et les preuves exactes. L’import reste distinct de la vérification. Nom seul : revue, jamais fusion. Garder catch-all distinct de délivrable ; vérifier la même adresse avec No2Bounce, fraîcheur maximale 90 jours.
- Acquisition LinkedIn : suivre `docs/LINKEDIN_AGENT_INSTRUCTIONS.md`. Un worker MCP à la fois, avec les seuls outils `search_people` et `get_person_profile` du serveur `linkedin`, en appels séquentiels. FullEnrich URL-only si l’adresse est absente/rejetée ; qualifier et vérifier ensuite toute nouvelle adresse. Harvest reste retiré.
- Pour l'admission Twenty, exiger une adresse professionnelle avec verdict No2Bounce `VALID` récent sur la même adresse ; LinkedIn est facultatif. Conserver la concordance du nom et de l'email, la relecture de la personne, de l'entreprise et de leur lien, ainsi que les homonymies en revue. Transférer les champs disponibles et pris en charge par le schéma Twenty, notamment mobile, fixe, poste, ville, adresse et secteur ; ne pas attribuer un profil LinkedIn non confirmé. Conserver les preuves dans le journal avant retrait du sas ; supprimer ensuite la copie JSON de contrôle vérifiée. Un échec Kerix laisse cet enrichissement en attente sans bloquer l’admission.
- Pour le mailing initial, utiliser exclusivement V2/V5 depuis `scripts/compact_pilot.py` et `scripts/compact_copy.py`. Lire `docs/EMAIL_PATTERNS.md` avant préparation. Le pilote comporte au plus 20 contacts, avec affectation persistée et observation de 14 jours après envoi. Une critique indépendante appliquant `cold-email` et `marketing-psychology` précède chaque brouillon ; seuls civilité confirmée, nom et entreprise sont substitués. Les rendez-vous Cal.com sont renseignés manuellement. Le rédacteur libre traite uniquement les réponses. Un email initial par contact, aucune relance automatique.
- Envoyer uniquement la version exacte approuvée sur Telegram, avec signature HTML versionnée. Toute modification exige une nouvelle validation. La cadence vient exclusivement de `workflow_meta.mailing_policy` ; sans configuration explicite, attendre. Les cinq anciens modèles, l'exception Mohammedia et les anciennes cadences sont retirés. Les archives et anciens callbacks ne valent aucune autorisation.
- Le coupe-circuit reste actif après bounce jusqu'à reprise explicite ; le suivi peut continuer lorsque sa tâche est activée. La pause globale actuelle reste maintenue.
- Après rebond permanent, conserver une exclusion durable et supprimer une personne CRM seulement après absence attestée d’échange humain et d’opportunité. Rebond temporaire : attente/revérification, aucune suppression CRM ni exclusion permanente présumée.

## Exploitation

Les quinze tickets sont livrés localement, bascule de stockage comprise. Une activation métier ultérieure exige des sondes réelles réussies et une instruction de reprise. Conserver les autres lignes du crontab et les définitions Enrich suspendues. Les paramètres sont dans `.env.workflow.example`. Vérifier les capacités réelles de Thunderbird avant tout envoi ; le client refuse d’altérer un corps approuvé.

Les acquisitions compactes exigent un brief avec budgets explicites et un adaptateur réel configuré ; coût inconnu ou budget absent maintiennent l’attente. Les anciens pilotes gardent leurs plafonds et leurs bases isolées. Les instructions LinkedIn sont dans `docs/LINKEDIN_AGENT_INSTRUCTIONS.md`, le schéma dans `schemas/linkedin-extraction-v1.schema.json`.

## Fin d’intervention

Exécuter les tests adaptés et `git diff --check`. Rapporter séparément implémentation testée et activation réellement effectuée. Conserver les blocages d’accès ou de capacité dans le statut du ticket ; une simulation ne démontre pas une intégration de production.

## Classement et taille du dossier

Avant de créer, déplacer ou supprimer des fichiers, lire la section « Classement et nettoyage » de `docs/WORKFLOW_OPERATIONS.md`.

- Utiliser `bdd/sources/` pour les sources, `bdd/workflow/` pour le SAS et le journal, `reports/<execution>/` pour un run et `outputs/` pour les livrables humains. Les essais historiques sont dans `archives/` ; les sauvegardes dans `backups/<date-intervention>/`.
- Après transfert CRM confirmé et retrait du SAS, supprimer les archives JSON de contrôle via `scripts.workflow_files`. Après intégration de tous les contacts du run, supprimer son dossier de rapports via ce même module : il conserve et vérifie d'abord les preuves compressées dans le journal. Les opérations inconnues et les contacts en attente restent protégés.
- Stocker les preuves exactes une seule fois dans le journal ; régénérer les vues de lecture. Ne pas recréer durablement les rapports d'un run terminé. Pour une vérification demandée, restaurer seulement le fichier utile et nettoyer cette copie après usage.
- Créer une sauvegarde avant une migration, pas à chaque run. La ranger dans `backups/`, la compresser et vérifier l'empreinte de la restitution avant de retirer la copie brute. Conserver les sources originales, le journal actif et les sauvegardes vérifiées ; le SAS retiré se restaure vers un chemin distinct.
- Utiliser `.scratch/` uniquement pour des intermédiaires régénérables et les retirer en fin d'intervention. Les commandes de reprise maintenues vivent dans `scripts/`. Garder LinkedIn séquentiel et les tests arrêtés au brouillon.
- En fin de run, exécuter la rétention, vérifier les chemins de preuves et communiquer les fichiers supprimés, les dossiers en attente et le volume économisé. Aucun fichier contenant des données réelles ne doit être ajouté à Git.
