# Enrich

Workflow local de prospection Contexte Lab : BDD → vérification de l'email professionnel → Twenty → V2/V5 et critique indépendante → approbation Telegram → Thunderbird → suivi des réponses et rebonds. LinkedIn sert à l'enrichissement facultatif et à la recherche d'une adresse manquante.

## Où consulter les données

Dans DBeaver, ouvrir `bdd/workflow/staging.sqlite.journal.sqlite` :
`workflow_candidates` contient les candidats et `workflow_contacts` les admissions
CRM. Le SAS séparé est retiré depuis la [bascule du 5 novembre 2026](docs/COMPACT_CUTOVER_RESULT_2026-11-05.md).

| Emplacement | Usage |
|---|---|
| `bdd/sources/` | Six BDD originales ; sources supplémentaires dans `a-identifier/` |
| `bdd/workflow/staging.sqlite.journal.sqlite` | Journal compact actif : candidats, preuves, opérations, brouillons et historique |
| `reports/<identifiant>/` (local) | Suivi des contacts encore en attente |
| `outputs/` | Classeurs prêts à utiliser |
| `backups/` | Sauvegardes compressées de restauration |
| `archives/` | Anciens essais, conception et migrations |

Chaque exécution utilise un seul dossier `reports/<identifiant>/`.
**Après transfert CRM confirmé, les JSON de contrôle sont supprimés du disque.
Quand tous les contacts d'une exécution sont intégrés, son dossier de rapports est
également supprimé.** Les copies exactes compressées restent consultables dans
`workflow_artifacts` ou `workflow_blobs` du journal SQLite. Les contacts en attente et les opérations
inconnues conservent leurs fichiers.

Les preuves des tests terminés sont conservées dans ce journal.
Les acquisitions et quatre crons Enrich restent suspendus ; `WORKFLOW_STOP_AT_DRAFT=1`
bloque les envois et le réveil Telegram reste désactivé jusqu’à reprise explicite.
Les adresses personnelles sont interdites à l'import et après enrichissement.

Voir [exploitation et nettoyage](docs/WORKFLOW_OPERATIONS.md). Le classement des chemins
et les audits contenant des données réelles restent dans les fichiers locaux non versionnés.

## Installation

Python 3.12, Linux et Codex CLI. Le worker LinkedIn utilise le serveur MCP `linkedin` ; la messagerie utilise l’extension Thunderbird MCP existante.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Compléter les paramètres manquants de [.env.workflow.example](.env.workflow.example) dans `.env`, en conservant les clés existantes. Les acquisitions compactes exigent un brief, des budgets explicites et un adaptateur réel configuré.

Les seuls messages initiaux sont V2/V5. Les prochaines campagnes exigent une cadence explicite dans le journal ; les anciens modèles et l’exception Mohammedia sont retirés. Voir [les règles de mailing](docs/WORKFLOW_OPERATIONS.md).

## Commandes

```bash
bash scripts/cron_agent_dispatcher.sh --task status
bash scripts/cron_agent_dispatcher.sh --service apify --action status --dry-run
bash scripts/cron_agent_dispatcher.sh --service linkedin --action status --dry-run
bash scripts/cron_agent_dispatcher.sh --service campaigns --action status --dry-run
```

Les aperçus restent en lecture seule. Le statut compact `readiness` décrit le journal local ; les sondes des adaptateurs restent distinctes. Les commandes métier peuvent appeler les fournisseurs, modifier Twenty ou envoyer une version approuvée après autorisation de reprise. [Le guide](docs/WORKFLOW_OPERATIONS.md) décrit l’exploitation et les pauses actuelles.

## Vérification

```bash
python3 -m pytest tests -q
git diff --check
```

Les tests interdisent le réseau réel et simulent fournisseurs, CRM et messagerie. Les données BDD, bases, secrets, archives, logs et états de campagne restent locaux et sont ignorés par Git.

## Modules

| Fonction | Modules |
| --- | --- |
| Sources et migration | `workflow_import`, `workflow_migration`, `compact_transition`, `compact_apply` |
| Journal et états | `compact_store`, `workflow_store`, `outreach_journal` (historique) |
| Qualification | `email_hygiene`, `direct_enrichment`, `no2bounce_client`, `enrichment_clients` |
| LinkedIn | `linkedin_queue`, `linkedin_mcp`, `linkedin_contract` |
| CRM et entreprises | `twenty_client`, `crm_transfer`, `kerix_client`, `domain_review` |
| Rédaction et validation | `prompt_engine`, `structured_agent`, `telegram_approval` |
| Envoi et suivi | `workflow_sender`, `thunderbird_client`, `workflow_inbound`, `circuit_breaker` |
| Exploitation | `workflow`, `workflow_schedule`, `cron_agent_dispatcher.sh` |

Les [pilotes historiques](docs/DIRECT_ENRICHMENT.md) restent isolés du journal actif ; les bases de référence et leurs archives sont conservées localement. L’ancien dispatcher ne peut plus envoyer réellement sans passer par le workflow approuvé.
