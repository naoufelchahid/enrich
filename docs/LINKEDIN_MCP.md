# Recherche et lecture de profils LinkedIn par MCP

Le workflow actif confie chaque contact réservé à Codex CLI, qui appelle le serveur MCP `linkedin` configuré dans Codex. La recherche utilise `mcp__linkedin__search_people` ; les informations du profil et ses publications utilisent `mcp__linkedin__get_person_profile`. Les appels sont séquentiels, sans procédure Chrome dans le dépôt. Les instructions exactes du worker sont dans [LINKEDIN_AGENT_INSTRUCTIONS.md](LINKEDIN_AGENT_INSTRUCTIONS.md).

Le worker rend un JSON conforme au [schéma v1](../schemas/linkedin-extraction-v1.schema.json). L'importeur [linkedin_mcp.py](../scripts/linkedin_mcp.py) vérifie la cible réservée, la provenance `linkedin_mcp`, l'URL personnelle, la concordance de nom, l'absence d'alternatives non résolues et la fraîcheur de la preuve. Un résultat ambigu est archivé pour revue sans attribuer de profil. Le profil confirmé et ses sources sont conservés dans le SAS et le journal avant le transfert CRM. L'email n'est jamais validé par cette extraction.

Les tables SQLite `linkedin_browser_queue` et `linkedin_browser_imports` gardent leurs noms pour lire les tâches et preuves antérieures. Les preuves historiques `codex_chrome` restent acceptées pour une reprise exacte ; toutes les nouvelles tâches portent `provider=linkedin_mcp` et exigent `acquisition_method=linkedin_mcp` à la fin.

Aperçu sans réseau ni mutation :

```bash
python3 -m scripts.linkedin_mcp export --db-path bdd/workflow/staging.sqlite --lead-id ID
bash scripts/cron_agent_dispatcher.sh --task linkedin_cycle --db-path bdd/workflow/staging.sqlite --dry-run
```

La phase manuelle `linkedin` traite un contact par invocation, puis importe la sortie validée. Le mode arrêté au brouillon reste obligatoire pour les batches manuels. Voir [WORKFLOW_OPERATIONS.md](WORKFLOW_OPERATIONS.md) et [LINKEDIN_WORKER.md](LINKEDIN_WORKER.md).
