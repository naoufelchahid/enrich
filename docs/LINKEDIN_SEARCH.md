> Historique : remplacé par le worker Codex/MCP. Aucun appel Harvest possible dans le code actif. Voir [LINKEDIN_MCP.md](LINKEDIN_MCP.md).

# Recherche d’URL LinkedIn personnelles

Phase explicite `linkedin-search`, exclue de `all` et du cron normal. Aucun changement automatique des quotas `.env`.

## Contrat vérifié le 7 octobre 2026

`GET https://api.harvestapi.io/linkedin/profile-search`, authentification `X-API-Key`.
Paramètres : `firstName`, `lastName`, `keywordsCompany` (texte de la société), `page=1`.
Pas de tableau batch, pas de pagination, pas de profil détaillé, pas de posts, pas de recherche email. `currentCompany` attend une URL/ID entreprise : ne pas y placer du texte libre.

Sources :
- https://docs.harvestapi.io/linkedin-api-reference/openapi.json
- https://docs.harvestapi.io/guides/profile-search
- https://api.harvestapi.io/users/subscriptions-info (tarifs publics utilisés par le site)

Tarif constaté pour le palier free : `pricePerJob.linkedinProfileSearch.base / priceMultiplier = 400 / 100000 = 0.004 USD/page`. Revalider avant un nouveau pilote. Les pages vides sont facturées : confirmé réellement. La recherche lead-search est un autre produit ; aucun basculement automatique.

## Protection du pipeline

- Prénom, nom et société obligatoires. Contacts avec URL existante exclus. Suppressions et contacts hors outreach PENDING exclus.
- Réutilisation du journal/réservations/cache existants. Réservation avant réseau, verrou de base, plafond journalier partagé Harvest et plafond cumulé persistant.
- Empreinte normalisée des trois champs, version et page. Une recherche par identité distincte ; doublons mutualisés.
- UNKNOWN/FAILED bloque les appels suivants ; aucun retry automatique. Réponse malformée : NEEDS_REVIEW et arrêt des appels restants.
- Réponse vide : NO_MATCH ; résultats sans URL personnelle exploitable : NO_PUBLIC_URL ; URLs candidates : NEEDS_REVIEW, même avec un candidat unique.
- Les URLs candidates sont conservées séparément dans linkedin_search_candidates. Aucune affectation automatique à linkedin_url. Changement d’identité pendant la requête : résultat en revue, non appliqué.
- Les réponses intégrales restent dans enrichment_jobs.response et enrichment_cache. linkedin_search_observed_at conserve la date d’observation.
- Aucun changement des preuves email ni autorisation d’envoi. L’outil de validation/affectation des candidats sera une étape séparée, à réaliser après validation du pilote.
- enrichment_jobs.cost conserve des **unités de requêtes Harvest** pour le quota existant. Le coût monétaire réel est `response.cost`, à ne pas confondre avec ces unités.

## Pilote isolé

Base : `bdd/pilot-linkedin-search/staging.sqlite`, copie du pilote anti-bounce existant. Plafonds persistants : No2Bounce 0, FullEnrich 0, Harvest 3, maximum trois contacts. Ne pas réinitialiser ces plafonds pour contourner les recherches vides. Le flag historique `--pilot` impose Harvest 0 : ne pas l’utiliser pour ce pilote dont les limites sont déjà enregistrées.

```bash
bash scripts/cron_agent_dispatcher.sh --task enrich --phase linkedin-search \
  --db-path bdd/pilot-linkedin-search/staging.sqlite --batch-size 3 \
  --harvest-request-limit 3 --dry-run
```

Le pilote réel a été exécuté sur autorisation utilisateur. Il est épuisé : rejouer ne déclenche aucun nouvel appel. Voir le rapport correspondant.
