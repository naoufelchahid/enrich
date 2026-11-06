# Journal compact — parcours et transition

La configuration locale utilise désormais `compact`, après la [bascule réelle du 5 novembre 2026](COMPACT_CUTOVER_RESULT_2026-11-05.md). Le journal garde son chemin et le SAS est retiré. Les acquisitions, campagnes et crons restent en pause. Le code conserve `legacy` comme valeur par défaut pour les usages isolés.

## Entrées indépendantes

Les trois entrées partagent seulement le journal sélectionné explicitement :

```bash
python3 -m scripts.compact_workflow --service apify --journal-path /copie/journal.sqlite --action status
python3 -m scripts.compact_workflow --service linkedin --journal-path /copie/journal.sqlite --action status
python3 -m scripts.compact_workflow --service campaigns --journal-path /copie/journal.sqlite --action status
```

`--dry-run` lit uniquement le journal déjà présent ; il ne crée aucun fichier, client ou schéma. Le wrapper accepte ces mêmes arguments `--service` et `--action`. Les commandes `scripts.workflow --backend compact --state-path PATH` passent par ces parcours avant l’ancienne vérification globale des accès ou tout aperçu du SAS. Les anciennes tâches de préparation exigent un parcours explicite et une cible ou un brief ; elles ne relancent pas une acquisition héritée.

`ApifyWorkflow`, `LinkedInWorkflow` et `CampaignWorkflow` acceptent des dépendances injectées. Le statut LinkedIn inclut confirmation et découverte. Une acquisition indisponible n’empêche pas les campagnes admises. Les adaptateurs réels de découverte/acquisition doivent être configurés avec contrat vérifié, budgets et spécialistes ; le CLI livré indique leur absence comme une attente. Le statut local ne démontre pas l’accès aux fournisseurs. Aucun bootstrap implicite ne transforme une absence d’adaptateur en appel historique.

`WORKFLOW_BACKEND=compact` et `WORKFLOW_STATE_PATH` désignent explicitement le journal commun. Chaque lane est activée séparément par `CompactStore.set_authority` après arrêt/réconciliation de son ancien écrivain. Construire un objet ne donne pas cette autorité. Le mode compact ne produit ni troisième base permanente, ni table miroir `staging_leads`, ni nouvelle outbox.

## Inventaire des lecteurs et écrivains

| Ancien point d’entrée | Parcours compact et traitement |
|---|---|
| `workflow.py`, `cron_agent_dispatcher.sh` | Routage explicite vers les trois services ; statut, aperçu, import, brouillons, suivi, rétention et rapprochement fonctionnent sans SAS. Les accès sont évalués à l’action. |
| `workflow_import.py`, `import_leads.py`, ETL | `import_all(..., backend='compact', compact_store=store)` conserve sources et provenance, sans migration implicite du SAS. L’import isolé historique reste disponible en mode legacy. |
| `direct_enrichment.py`, `staging_db.py` | `CompactQualification` utilise candidats/tâches/preuves du journal. Les écritures héritées de qualification sont refusées après changement d’autorité pour la paire concernée. |
| `linkedin_queue.py`, `linkedin_mcp.py` | `CompactLinkedIn` pour la confirmation, `CompactDiscovery` pour la découverte ; cible, preuve et verrou global MCP conservés. Les diagnostics historiques restent lisibles ; les mutations héritées sont gardées. |
| `crm_transfer.py` | `compact_crm.transfer_compact` : admission No2Bounce, opérations et relecture CRM, contrôle exact en blob, publication atomique de l’admission. Les transferts hérités sont gardés par la lane CRM. |
| `workflow_sender.py`, `telegram_approval.py`, `workflow_inbound.py` | Réutilisent le journal commun ; aucune dépendance au SAS. Les versions, réservations et exclusions restent communes. |
| `workflow_files.py` | `cleanup_completed(store, None, ...)`, lecture/restauration des chemins et URI historiques ou compacts ; `--backend compact --journal-path PATH` pour le CLI. |
| `workflow_batch.py`, `run_authorized_batch.py` | Anciennes sélections manuelles sur identifiants SAS retirées du mode compact ; refus explicite avant accès. Les nouvelles acquisitions utilisent des briefs ; le pilote mailing utilise les affectations du ticket 13. Un ancien batch doit être réconcilié par la transition avant de reprendre par ces parcours. |
| `mohammedia_campaign.py` | Retiré le 6 novembre 2026. Les preuves et envois restent conservés ; la politique exceptionnelle est retirée et les anciens brouillons sont inactifs. Les campagnes suivent exclusivement V2/V5 et une cadence explicite. |
| `assign_company_domain.py`, `import_verification_proof.py` | Écritures héritées gardées ; remplacements `compact_maintenance.assign_domain_compact` et `import_verification_compact` sur candidats explicites, avec preuves et contrôles conservés. |
| `outreach_dispatcher.py` | Ancien pilote autonome ; garde sur les écritures et le verrou de file. Les crons compacts utilisent `workflow` ou les services explicites. |
| `prepare_pilot.py` | Ancien outil de copie/essai ; réservé aux bases isolées, sans rôle dans l’exploitation compacte. |
| `outreach_journal.py` | Lecture et export de l’historique source pour audit/reprise de migration ; aucun nouveau workflow compact ne l’appelle pour écrire des snapshots. |
| `workflow_migration.py`, `workflow_backup.py`, `compact_transition.py` | Audit/restauration et transition explicitement séparés du runtime. Le SAS n’est nécessaire que comme source à vérifier avant son retrait. |
| Imports de sociétés Apify, remplissage emails/téléphones et `import_mohammedia_remaining.py` | Opèrent sur les données sociétés/CRM, pas sur le SAS de personnes. Leur autorisation et leurs preuves restent distinctes ; aucun appel n’est ajouté par la transition. |
| `workflow_schedule.py` | Préparation du crontab conservée ; aucune installation par les nouveaux modules. L’ancienne purge d’archives datées reste une opération historique distincte du nettoyage compact vérifié. |

Pour un ancien pilote isolé, utiliser explicitement `WORKFLOW_BACKEND=legacy` avec ses propres bases. Cette sélection ne contourne pas une autorité compacte déjà enregistrée sur la même paire de bases. Les aperçus/audits en lecture seule restent disponibles.

## Répétition et conservation

`compact_transition.stage_legacy` exige soit une pause d’écriture déclarée, soit une répétition sur copies distinctes des chemins actifs/configurés. Il commence par `workflow_migration.audit_pair` : chaque événement exporté doit correspondre exactement et aucun export ne doit rester en attente. Il ne purge rien.

Les identités et correspondances héritées sont conservées. Les sources faibles sans email retrouvent leur ancien `import_key` lors d’un réimport, sans fusion sur un nom. Les gros champs et les tables opérationnelles exactes sont conservés par blobs adressés par empreinte ; le schéma source est conservé également. L’historique déjà vérifié reste dans ses tables du journal, sans nouvelle copie de l’outbox.

Une vérification No2Bounce SUBMITTED, attachée à une seule identité et une seule adresse inchangée, peut être adoptée avec son identifiant distant et reprise par polling. Les anciens batchs multi-adresses, FullEnrich non adoptable et états LinkedIn incomplets sont conservés comme tâches de rapprochement explicites. Les candidats liés portent `pending_legacy_jobs` ; cette attente interdit nouvelle qualification payante, nouvelle recherche LinkedIn et transfert CRM jusqu’à réconciliation. Un état inconnu n’est jamais remis en file automatiquement.

Cette attente est une limite opérationnelle volontaire : la répétition prouve leur conservation, pas la résolution de ces opérations chez les fournisseurs. Leur sort réel est consigné dans le résultat du ticket 15. Les textes approuvés, exclusions et preuves déjà présents dans le journal restent inchangés. `scripts.compact_apply` fournit la préparation sur copies et l’installation vérifiée sous pause ; voir le [runbook](COMPACT_CUTOVER_RUNBOOK.md).
