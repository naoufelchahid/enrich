# Worker LinkedIn MCP

`linkedin_cycle` met les contacts éligibles en file durable et lance une extraction Codex CLI à la fois. Codex utilise seulement les outils de lecture `mcp__linkedin__search_people` et `mcp__linkedin__get_person_profile`. Le parent importe le JSON après validation. Aucun email, CRM ou fournisseur d'enrichissement n'est appelé dans ce cycle.

```bash
bash scripts/cron_agent_dispatcher.sh --task linkedin_cycle --db-path copie-sas.sqlite --batch-size 1 --linkedin-daily-limit 1 --dry-run
bash scripts/cron_agent_dispatcher.sh --task linkedin_cycle --db-path copie-sas.sqlite --batch-size 1 --linkedin-daily-limit 1
bash scripts/cron_agent_dispatcher.sh --task linkedin_status --db-path copie-sas.sqlite
```

Le plafond du pilote vaut zéro par défaut et accepte 0 à 3 lancements par jour UTC. Le workflow actif n'a pas ce plafond. Un verrou commun garantit un seul worker MCP à la fois, même pour plusieurs bases. La tâche est réservée avant l'appel. `RUNNING`, `FAILED` et `BLOCKED` exigent réconciliation ; aucun retry automatique. Un profil ambigu devient `REVIEW`, un changement de cible `STALE`, une preuve confirmée `IMPORTED`.

Pour reprendre un résultat existant sans nouvelle recherche :

```bash
python3 -m scripts.linkedin_queue complete --db-path copie-sas.sqlite --job-id ID_RESERVE --result-path bdd/linkedin-worker/ID_RESERVE/result.json --dry-run
python3 -m scripts.linkedin_queue complete --db-path copie-sas.sqlite --job-id ID_RESERVE --result-path bdd/linkedin-worker/ID_RESERVE/result.json
```

Le worker utilise `codex --no-daemon -a never exec --sandbox read-only --ephemeral -o ... -`. La configuration Codex doit déclarer le serveur `linkedin` actif (`codex mcp get linkedin`). Le modèle ne reçoit pas l'autorisation de modifier le SAS ; seul l'importeur déterministe le fait. Un profil importé ouvre ensuite, si nécessaire, l'enrichissement FullEnrich sur URL personnelle uniquement.
