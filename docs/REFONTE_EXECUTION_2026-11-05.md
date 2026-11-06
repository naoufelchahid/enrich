> Référence datée. Pour les règles de mailing applicables depuis le 6 novembre 2026, lire [WORKFLOW_OPERATIONS.md](WORKFLOW_OPERATIONS.md). Les anciennes règles de mailing sont retirées.

# Exécution des tickets de refonte — 5 novembre 2026

L'utilisateur demande l'exécution des quinze tickets approuvés par des sous-agents. Les spécifications et dépendances sont conservées dans `.scratch/refonte-enrich/issues/`. Le présent suivi distingue le code testé des opérations réellement effectuées.

**État final : les quinze tickets sont livrés localement, bascule réelle du ticket 15 comprise.** Le [résultat de bascule](COMPACT_CUTOVER_RESULT_2026-11-05.md) consigne les preuves réelles, les 682 tests réussis et les volumes mesurés. Les acquisitions, emails et crons Enrich restent en pause. Les sections de préparation ci-dessous conservent l’état historique antérieur à l’autorisation de procéder.

## Point de départ

Le dépôt contient de nombreuses modifications antérieures ; elles sont conservées. Aucun commit n'est créé. Chaque sous-agent livre un patch incrémental, vérifié par empreinte et contrôle inverse dans l'espace partagé.

La suite initiale donne 443 tests réussis et deux échecs : des assertions attendent encore une signature textuelle, alors que le rédacteur prépare désormais le corps pour la signature HTML Thunderbird. L'environnement virtuel manque également de `python-dotenv`, déjà importé par les commandes de campagne ; cette dépendance est déclarée et installée.

## Suivi

| Ticket | État | Validation |
|---|---|---|
| 01 | Terminé localement | 32 tests ciblés réussis ; 11 tests journal rejoués par le parent ; patch vérifié |
| 02 | Terminé localement | 15 tests migration rejoués par le parent ; 26 avec le journal ; patch vérifié |
| 03 | Terminé localement | 10 tests stockage rejoués par le parent ; 93 avec les compatibilités ; patch vérifié |
| 04 | Terminé localement | 118 tests avec compatibilités ; reprises et provenance vérifiées ; patch vérifié |
| 05 | Terminé localement | 12 tests LinkedIn rejoués par le parent ; 50 avec compatibilités ; patch vérifié |
| 09 | Terminé localement | 17 tests budgets rejoués par le parent ; 28 avec stockage ; patch vérifié |
| 06 | Terminé localement | 130 tests avec compatibilités ; préparation sans SAS et approbation versionnée |
| 07 | Terminé localement | 22 tests CRM/preuves rejoués par le parent ; 96 avec compatibilités ; patch vérifié |
| 11 | Terminé localement | 14 tests découverte rejoués par le parent ; 26 avec confirmation ; patch vérifié |
| 10 | Terminé localement | 12 tests Apify rejoués par le parent ; 47 avec budgets/qualification ; patch vérifié |
| 12 | Terminé localement | 19 tests copie rejoués par le parent ; 119 avec compatibilités ; patch vérifié |
| 08 | Terminé localement | Routage/migration transitoire sans SAS ; 94 tests élargis puis régressions ciblées ; patch vérifié |
| 13 | Terminé localement | 16 tests pilote rejoués par le parent ; 141 avec compatibilités ; patch vérifié |
| 14 | Terminé localement | Telegram simulé, présentation confirmée avant lancement ; 111 tests avec compatibilités ; patch vérifié |
| 15 | Terminé : bascule réelle contrôlée | Sauvegarde restaurée, reconstruction exacte, revue indépendante, installation et retrait SAS vérifiés ; suite complète 682 réussis |

Les deux assertions obsolètes de signature sont corrigées ; les 27 tests concernés passent après revalidation par le parent. L'aperçu de `prepare_daily_batch --dry-run` fonctionne avec zéro action externe.

La revue croisée a également fermé un défaut au transport : le destinataire approuvé doit être exactement l'adresse portée par la preuve de vérification du contact. Une adresse modifiée avec une preuve ancienne attend une nouvelle vérification.

Une revue indépendante des raccords a identifié puis fait corriger deux défauts : alimenter l'objectif du brief dans la transaction d'admission CRM, et imposer la confirmation d'identité pour les candidats issus de découverte avant qualification/admission. Les dix tests d'intégration rejoués par le parent passent, notamment la limite concurrente d'objectif, le rejeu d'une création incertaine et le rollback atomique admission/compteur. LinkedIn reste facultatif pour les imports généraux.

Un audit réel en lecture seule a été exécuté ; son export agrégé est conservé hors Git dans `outputs/refonte-enrich-2026-11-05/audit-summary.json`. Les écrivains n'étaient pas suspendus : cet audit n'autorise aucune purge et ne remplace pas la comparaison finale sous pause. Aucun volume n'a été récupéré.

L'adaptateur Apify livré exige un transport injecté et un contrat/tarif de compte attesté. Le code testé ne constitue pas une connexion Apify de production configurée. Les budgets réels et les adaptateurs d'acquisition restent à renseigner avant tout appel payant.

## Rétention de cette intervention

Treize copies de patches et snapshots de code régénérables ont été retirées de `.scratch/` après vérification et archivage des patches dans MemPalace : 485 919 octets. La liste exacte reste dans `outputs/refonte-enrich-2026-11-05/cleanup-summary.json`. Les quinze tickets, sources, exports humains, bases actives et fichiers des contacts/runs en attente sont conservés. Aucune copie de contrôle CRM réelle n'a été supprimée ; le volume récupéré sur les bases reste nul.

## Répétition et limites de bascule

Le [dossier de bascule](COMPACT_CUTOVER_RUNBOOK.md) et `scripts.compact_cutover` livrent une répétition synthétique, pas un outil d'application en production. La sauvegarde fraîche a été restaurée à l'identique ; la reprise fonctionne avec l'ancien SAS indisponible, les preuves et exclusions restent lisibles et le retour vers un checkpoint obsolète est refusé, y compris après allocation puis suppression d'un identifiant AUTOINCREMENT. Le résumé est conservé dans `outputs/refonte-enrich-2026-11-05/rehearsal-summary.json`.

La fenêtre, l'arrêt de tous les écrivains, la sauvegarde finale figée et la réconciliation des dépendances réelles FullEnrich/CRM restent requis avant tout retrait du SAS. Les adaptateurs et budgets concernent l'activation des acquisitions ; une migration laissant celles-ci arrêtées possède ses propres préconditions. Aucune de ces étapes n'autorise une reprise des emails.

## Validation de la préparation initiale

`.venv/bin/python -m pytest tests -q` : **663 tests réussis en 204,41 secondes**. Un avertissement de dépréciation existant de `fitz` est émis. L'environnement virtuel est celui choisi par le wrapper du cron.

Les trois aperçus `scripts.compact_workflow` des services `apify`, `linkedin` et `campaigns`, avec `--action status --dry-run`, passent sur le journal actif en lecture seule : zéro action externe pour chacun. `git diff --check` réussit. Les tests incluent la restauration, les interruptions, les reprises, les opérations inconnues, les plafonds concurrents, la confirmation d'identité, les textes exacts et les approbations périmées.

À la fin de cette préparation initiale, les quinze délégations sont documentées ; les tickets 01–14 sont livrés localement et le ticket 15 reste alors partiel. Aucun tracker externe n'est utilisé ni présenté comme mis à jour. Aucun commit n'est créé ; les modifications préexistantes du dépôt sont conservées. Le relevé de validation et l'empreinte des sources testées restent hors Git dans `outputs/refonte-enrich-2026-11-05/validation-summary.json`.

## Mémoire et coordination

Les patches, leurs vérifications et les accusés de réception sont conservés dans le logstream MemPalace. L'ajout du rapport à un drawer a été tenté deux fois mais refusé : un autre writer détient le verrou du palace. Aucun processus pair n'a été arrêté. Le rapport reste durable dans ce fichier et dans un événement `knowledge.note` du logstream ; son indexation en drawer reste à reprendre après libération du verrou. Le watcher de cette intervention est arrêté à la clôture.

## Opérations réelles

La fenêtre autorisée ultérieurement réalise la migration : journal compact installé, SAS retiré après reconstruction exacte et contrôles RO, 37 343 candidats conservés et 1 173 014 événements concordants. Réduction active finale : 4 536 401 920 octets ; sauvegarde compressée conservée : 785 041 876 octets. Les deux créations CRM UNKNOWN et 21 tâches LinkedIn héritées restent en attente protégée. Aucun appel payant, création CRM, email ni notification Telegram ; quatre crons Enrich suspendus de façon réversible. Voir le rapport réel pour les reçus, la rétention, les attentes et les limites de reprise.
