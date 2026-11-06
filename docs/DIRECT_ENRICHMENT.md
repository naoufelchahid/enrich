> Guide historique/pilotes. Pour le workflow actif implémenté le 7 octobre 2026, ses règles d’admission et ses cadences, lire [WORKFLOW_OPERATIONS.md](WORKFLOW_OPERATIONS.md).

> Ordre actif : No2Bounce → URL personnelle confirmée via le serveur MCP LinkedIn → FullEnrich URL-only si email absent ou INVALID. Voir [ENRICHMENT_ORDER.md](ENRICHMENT_ORDER.md). Les anciens exemples nom/domaine sont historiques.

> Mise à jour : LinkedIn passe par Codex/MCP et import JSON validé ; Harvest est désactivé dans le code. Les sections Harvest ci-dessous décrivent le parcours historique. Voir [LINKEDIN_MCP.md](LINKEDIN_MCP.md).

# No2Bounce + FullEnrich + Harvest : exploitation du workflow direct

Apify, Microworlds, Pipelinelabs et vdrmota ne sont plus appelés par le workflow. Les anciens clients ont été supprimés du dépôt. Le dispatcher utilise `DirectEnrichment`, les clients directs et le journal SQLite. Aucun test réel payant n'a été exécuté pour cette migration.

## Configuration

Renseigner les entrées de `.env.direct.example` dans `.env`, sans écraser ses autres valeurs. Les clés attendues sont `NO2BOUNCE_API_KEY`, `FULLENRICH_API_KEY` et `HARVEST_API_KEY`. La vérification des emails existants passe par No2Bounce avant toute recherche FullEnrich ; voir [le guide de vérification](EMAIL_VERIFICATION.md). Une clé Apify ne fonctionne pas avec Harvest direct. La clé No2Bounce locale a permis de lire les jobs existants le 6 octobre 2026 ; sa présence ne permet aucune nouvelle soumission sans quota.

Les quotas sont à zéro par défaut. Ils doivent être configurés explicitement : `FULLENRICH_DAILY_CREDITS` en crédits, `HARVEST_DAILY_REQUESTS` en nombre de requêtes. Exemple de petit lot : cinq crédits FullEnrich et deux requêtes Harvest. Ce sont deux compteurs indépendants et **pas un plafond global en USD**. Configurer aussi une limite de consommation sur la clé FullEnrich et les limites proposées par le compte fournisseur lorsque disponibles. La protection locale couvre les appels de ce workflow utilisant la même base SQLite, pas les dépenses faites manuellement ou par un autre projet.

Le client ne demande que `contact.work_emails`. Pas de téléphone, email personnel, recherche de personnes payante ou fallback externe. Le tarif standard documenté est un crédit par contact dont l'email est trouvé, y compris certains statuts non délivrables. Les offres personnalisées peuvent différer : `FULLENRICH_EMAIL_CREDIT_CEILING` doit alors être un plafond par contact au moins égal au tarif de l'offre. Le journal réserve ce maximum avant le POST et rapproche le coût retourné. Un dépassement constaté bloque les prochaines soumissions. Cette estimation locale n'est pas une limite monétaire garantie par l'API.

Harvest est borné par le nombre de requêtes : leur prix en crédits/USD dépend du compte. Les posts sont désactivés par défaut. `HARVEST_POSTS_ENABLED=1` autorise au plus une page par profil confirmé, en consommant le même quota que les profils. Le code conserve trois posts en staging mais **la requête distante récupère une page**, et non exactement trois posts.

## Contrats vérifiés le 6 octobre 2026

FullEnrich v2 : `https://app.fullenrich.com/api/v2`, header `Authorization: Bearer ...`. Crédit disponible : `GET /account/credits`. Soumission : `POST /contact/enrich/bulk`, body `name` et `data[]`. Chaque élément contient `linkedin_url` ou prénom + nom + `company_name`, `enrich_fields: ["contact.work_emails"]` et `custom.request_key` en chaîne. Le schéma accepte un nom d'entreprise ; aucun domaine n'est inventé.

Maximum fournisseur : 100 contacts par lot ; le défaut local est dix et la préparation quotidienne limite le lot à son besoin. Les personnes identiques sont dédupliquées **avant** soumission, car le fournisseur ne garantit pas la déduplication à l'intérieur d'un lot ou entre requêtes simultanées. Le rapprochement se fait par `custom.request_key`, jamais par ordre des résultats.

Le POST retourne `enrichment_id`. Le résultat est lu par `GET /contact/enrich/bulk/{id}`, sans `forceResults`. Le statut `FINISHED` et les clés de correspondance doivent être cohérents. La réponse HTTP 400 avec `error.enrichment.in_progress` signifie encore en cours ; elle ne relance pas l'enrichissement. Le polling est espacé d'au moins trente secondes entre invocations, avec dix vérifications au plus par invocation. Ce choix évite de déployer un endpoint webhook public pour ce runner local. Le fournisseur recommande les webhooks pour les volumes supérieurs.

Harvest direct : `https://api.harvestapi.io`, header `X-API-Key`. Profil : `GET /linkedin/profile?query=<url>&main=true`, réponse `element` et `status=200`. Ne pas utiliser `queries` ou `targetUrls` des anciens Actors. L'option `findEmail` est omise pour éviter de doubler l'enrichissement FullEnrich. Posts : `GET /linkedin/profile-posts?profile=<url>&page=1`, réponse `elements`. La spécification directe consultée n'expose pas de batch de profils : le workflow déduplique les URL et utilise une requête par URL non mise en cache.

Sources : [FullEnrich soumission](https://docs.fullenrich.com/api/v2/contact/enrich/bulk/post), [résultats](https://docs.fullenrich.com/api/v2/contact/enrich/bulk/get), [crédits](https://docs.fullenrich.com/api/v2/general/credit), [statuts email](https://docs.fullenrich.com/api/v2/general/email-status), [spécification Harvest officielle](https://github.com/HarvestAPI/harvestapi-docs/blob/main/linkedin-api-reference/openapi.json). Une capture réduite des contrats utilisés est conservée dans `tests/fixtures/direct_api_contracts.json`.

## Commandes

Depuis la racine du dépôt, aperçu sans appel réseau ni écriture du staging :

```bash
bash scripts/cron_agent_dispatcher.sh --task enrich --dry-run --batch-size 5
```

Le wrapper charge `.env` et écrit le compte rendu dans `logs/outreach_cron_YYYY-MM-DD.log`. L'aperçu ne modifie ni base, ni queue, ni CRM. Si la base n'existe pas, il faut d'abord importer des leads ; l'aperçu ne crée pas silencieusement une base vide.

Après configuration des clés et quotas, la commande suivante est **susceptible de consommer des crédits** :

```bash
bash scripts/cron_agent_dispatcher.sh --task enrich --batch-size 5
```

Une invocation soumet les nouveaux contacts ; une invocation ultérieure récupère les résultats existants puis traite éventuellement de nouvelles cibles dans le quota. Aucun sommeil long ni boucle de polling n'est lancé. Pour ne traiter qu'un pilote, utiliser une base isolée contenant uniquement ses contacts. Les quotas concernent cette base ; ne pas répartir les mêmes contacts sur plusieurs bases en pensant partager une enveloppe globale.

`prepare_daily_batch` réutilise ce moteur. Elle ne paie pas pour retrouver un email déjà validé avec provenance. L'absence de LinkedIn seule ne déclenche pas une recherche FullEnrich payante si l'email est déjà validé. Les contacts sans prénom/nom/société suffisants ni URL LinkedIn personnelle sont `MISSING_INPUT`, sans dépense. Une URL personnelle déjà connue peut être enrichie directement par Harvest, indépendamment de l'état de découverte email. Une réponse vide FullEnrich n'active aucun fournisseur de secours.

## Journal, cache et reprise

Tables : `enrichment_jobs`, `enrichment_cache`, `enrichment_targets`. Les réponses brutes sont conservées dans SQLite avant leur application et ne contiennent pas les clés API. Protéger la base et ses sauvegardes comme des données de contacts. Les résultats restent conservés. Une preuve positive ne permet l’envoi que pendant 90 jours pour la même adresse. Ensuite, No2Bounce doit revérifier sous quota ; le cache ancien ne reçoit jamais une nouvelle date artificielle. Les résultats inconnus ne provoquent pas de relance payante automatique.

Un job est enregistré avec réservation avant tout appel. Le verrou de fichier par base et les clés uniques empêchent deux workers d'envoyer le même lot. Les réservations non rapprochées restent comptées même après minuit UTC. Les erreurs techniques ne deviennent pas des listes vides. Les échecs et réponses ambiguës bloquent les nouvelles dépenses du fournisseur concerné ; leur détail apparaît dans `jobs_requiring_attention`.

Si FullEnrich a accepté un POST dont la réponse a été perdue : retrouver le lot dans le dashboard par son nom `Enrich <identifiant-local>`. Après sa terminaison, rattacher l'identifiant distant existant :

```bash
bash scripts/cron_agent_dispatcher.sh --task enrich \
  --resume-job-id IDENTIFIANT_LOCAL --fullenrich-id IDENTIFIANT_DISTANT
```

Cette opération récupère des résultats existants et ne soumet pas d'enrichissement. Les clés des contacts et l'ID doivent correspondre au lot sauvegardé ; sinon l'import est refusé. Un timeout Harvest est plus délicat : le GET peut avoir été facturé sans résultat reçu, donc il n'est pas répété automatiquement. Il faut examiner le journal et l'historique fournisseur avant toute nouvelle tentative. Les erreurs `FAILED`, réservations sans coût et cas Harvest `UNKNOWN` restent volontairement à examiner ; aucune commande de reset global n'est fournie.

Un résultat complet et vide par contact est `NO_MATCH`, reste non enrichi et n'est pas relancé au prochain cron. Une ligne absente ou dupliquée dans un lot terminé est `NEEDS_REVIEW`, pas une absence de correspondance prouvée. Les informations contredisant le nom ou l'entreprise sont mises en revue. Les positions Harvest multiples restent en revue ; le code ne remplace pas le métier par une simple headline. Les données historiques brutes `experience` restent dans la réponse sauvegardée.

## Validation, migration et envoi

Seul `contact_info.most_probable_work_email.status=DELIVERABLE` produit une validation FullEnrich. `HIGH_PROBABILITY`, catch-all et autres statuts n'autorisent pas l'envoi. La provenance, l'email exact et la date de validation sont sauvegardés. Une validation d'une ancienne adresse ne valide pas sa remplaçante.

Les anciens clients No2Bounce, NeverBounce et Apify ont été retirés, ainsi que le circuit Resend. Un nouveau transport No2Bounce journalisé et bloquant par défaut remplace l’ancien faux validateur ; son adaptateur CSV est confirmé sur les jobs existants et les quotas restent à zéro par défaut. La colonne SQLite `neverbounce_status` garde son nom historique pour préserver les bases existantes ; elle contient désormais le statut de validation utilisé par le pipeline direct, sans appel à NeverBounce.

La première migration d'un ancien staging crée une sauvegarde SQLite cohérente suffixée `.before-direct-enrichment.bak`, puis ajoute les colonnes. Les anciens `VALID` ne reçoivent aucune provenance inventée : ils ne sont pas éligibles tant qu'ils n'ont pas de validation traçable. Les anciens flags LinkedIn sans URL sont remis à zéro par le moteur. Les IDs CRM sont préservés. La migration capture l’email courant dans `original_email` avant toute nouvelle substitution ; elle ne peut pas reconstruire une adresse déjà écrasée avant cette migration. Aucune migration de la base de production n'a été exécutée pendant les tests de développement.

Le dispatcher ne compte comme synchronisés que les succès CRM avec un ID. Il conserve les candidats déjà en file et déduplique leurs adresses. Avant l'envoi, il relit la validation dans le staging, vérifie l'adresse, le statut et le coupe-circuit. Les désinscriptions reconnues dans la revue inbound sont marquées `SUPPRESSED`. Un envoi ambigu reste `SEND_UNKNOWN` et n'est pas retenté automatiquement. Ces choix privilégient l'absence de doublons au prix d'un examen manuel après un crash ou une erreur réseau.

## Tests

Avant nettoyage du dépôt, la suite complète comptait **178 tests réussis**. Le bilan après retrait des tests des composants obsolètes figure dans `archives/conception/REPOSITORY_CLEANUP.md`. L'aperçu via le wrapper cron et `git diff --check` passent également. Les échanges fournisseurs sont simulés dans les tests ; une validation de bout en bout avec les comptes réels reste à effectuer après configuration.

```bash
python3 -m pytest tests/test_direct_enrichment.py tests/test_direct_outreach_safety.py -q
python3 -m pytest tests -q
```

Les nouveaux tests simulent les fournisseurs et couvrent contrat HTTP, budgets, déduplication, résultats vides, cache, polling, timeout, reprise, erreur de coût, validation, absence de faux succès CRM et conservation de la file. Les scripts de test manuel et les tests des composants retirés ont été supprimés. Les preuves non exécutables de l’investigation restent dans `archives/essais/apify-audit-2026-10-06/`.

## Domaine d’entreprise explicitement revu

Le staging accepte `company_domain` et `company_domain_source` (provenance lisible obligatoire). Exemple : `accessenergy.ma` et `Domaine confirmé depuis l’email source, validation utilisateur`. Le modèle d’import et la préparation de pilotes conservent ces champs. Les parseurs historiques ne les remplissent pas automatiquement.

Le domaine doit être un nom d’hôte nu, sans protocole, chemin ou adresse email. Il est normalisé en minuscules avant transmission. Une déclaration invalide ou sans provenance bloque la découverte (`MISSING_INPUT`) au lieu de dépenser avec une entrée incomplète. Le contrôle est syntaxique ; la provenance documente la revue humaine, pas une certification automatique de propriété du domaine.

Avec un domaine renseigné, FullEnrich reçoit `domain` en plus des champs d’identité disponibles ; la provenance reste locale. Sans domaine, le parcours prénom + nom + entreprise reste inchangé. Aucune recherche de domaine payante ni extraction automatique depuis une URL ou une adresse email n’a été ajoutée.

Les requêtes avec domaine utilisent une clé de cache distincte, incluant domaine et identité. Les requêtes sans domaine gardent leurs anciennes clés. Un domaine modifié pendant une recherche empêche l’application du résultat et déclenche `NEEDS_REVIEW`. Un contact déjà `NO_MATCH` n’est pas relancé automatiquement après ajout d’un domaine : une éventuelle nouvelle recherche doit rester une opération volontaire sous quota.

La migration est additive et se produit à l’initialisation d’une base. Elle a été vérifiée sur une copie isolée ; les bases principales n’ont pas été migrées pendant cette étape. Le résultat `HIGH_PROBABILITY` reste `WAITING`, même si le domaine est connu. Aucun assouplissement anti-bounce.

## Recherche d’URL LinkedIn personnelles (7 octobre 2026)

Phase optionnelle `linkedin-search`, strictement explicite et indépendante de `all` : voir [LINKEDIN_SEARCH.md](LINKEDIN_SEARCH.md). Premier pilote : 3 appels, aucun profil, 0,012 USD. Les résultats vides sont mémorisés sans relance ; les éventuels candidats nécessitent une revue et ne remplacent pas automatiquement `linkedin_url`. Rapport : `archives/essais/pilot-linkedin-search-2026-10-07/RESULTAT.md`.
