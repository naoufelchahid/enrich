# Exploitation du workflow entreprises issues des résultats de marchés

## Périmètre

Ce workflow est indépendant du SAS de prospection. Il lit directement les listes publiques « Tous les extraits de PV » et « Tous les résultats définitifs » du portail marocain. Il rend les pages PDF en images éphémères, demande à Codex les noms exacts d'entreprises, résout une identité LinkedIn ou Kerix, puis crée ou complète les entreprises et, si une coordonnée professionnelle est qualifiée, au plus deux personnes par entreprise dans Twenty.

Aucune participation, référence de marché, URL de pièce, empreinte de PDF, image ou texte extrait n'est enregistré. Les téléchargements et images sont supprimés après usage, y compris lorsqu'une extraction échoue. Le seul état durable est `bdd/workflow/tender_acquisition.sqlite` : nature, sujet, statut et identifiant distant des opérations FullEnrich, No2Bounce et Twenty. Les opérations `UNKNOWN` ne sont pas renvoyées automatiquement.

## Commandes

Lancer depuis la racine du dépôt. Le wrapper charge `.env` et choisit Python comme les autres commandes du projet.

```bash
bash scripts/tender_acquisition/run.sh --task preview
bash scripts/tender_acquisition/run.sh --task status
bash scripts/tender_acquisition/run.sh --task simulate
bash scripts/tender_acquisition/run.sh --task readiness
```

`preview` n'appelle aucun service et ne crée pas de base. `simulate` utilise uniquement des données en mémoire et une base temporaire supprimée en fin de commande. `readiness` lit une page du portail, interroge le schéma Twenty, les crédits FullEnrich, une recherche LinkedIn MCP de test et lance une extraction visuelle sur un PDF synthétique. Le contrat No2Bounce et la présence de la clé sont vérifiés ; son API bulk observée ne fournit pas de contrôle gratuit de l'authentification, qui est confirmée au premier envoi réel. Aucun POST fournisseur ni écriture CRM n'a lieu pendant `readiness`.

Si `readiness.ready` vaut `true`, lancer le pilote de reprise :

```bash
bash scripts/tender_acquisition/run.sh --task backfill
```

La reprise part de douze mois avant la date locale d'Afrique/Casablanca. Le run traite au maximum 200 annonces et 200 PDF, puis s'arrête après vingt entreprises identifiées ou 120 minutes. Ces limites sont réglables par `TENDER_MAX_DOCUMENTS`, `TENDER_MAX_COMPANIES`, `TENDER_MAX_RUN_MINUTES`, `TENDER_FIRST_RUN_MONTHS` et `TENDER_DAILY_WINDOW_DAYS`. Une reprise ultérieure relit les annonces, car aucun identifiant de marché n'est conservé ; les opérations fournisseur/CRM restent idempotentes via le journal. Les doublons d'entreprise sont rapprochés par domaine ou page LinkedIn, jamais par nom seul.

Pour prévisualiser ou réinstaller la ligne cron indépendante :

```bash
bash scripts/tender_acquisition/run.sh --task schedule
bash scripts/tender_acquisition/run.sh --task schedule --apply
```

`--apply` refait les contrôles, sauvegarde le crontab dans `bdd/workflow/cron-backups/` et ajoute uniquement son bloc. La cadence prévue est `03:27` selon le fuseau du service cron ; le filtre de dates utilise toujours Africa/Casablanca. Le run quotidien lit les trois derniers jours. L'installation ne supprime aucun cron existant.

## Reprise des réponses incertaines

Une perte de réponse après un POST est marquée `UNKNOWN`. Retrouver l'identifiant de soumission dans le fournisseur avant de le rattacher ; la commande vérifie l'identifiant distant en lecture seule puis débloque les futures lectures du résultat :

```bash
bash scripts/tender_acquisition/run.sh --task reconcile --kind FULLENRICH --subject 'https://www.linkedin.com/in/identifiant' --remote-id 'ID_DISTANT'
bash scripts/tender_acquisition/run.sh --task reconcile --kind NO2BOUNCE --subject 'prenom.nom@entreprise.ma' --remote-id 'ID_DISTANT'
```

Les créations CRM incertaines sont rapprochées automatiquement par identité lors du prochain run. Un doublon ou un conflit reste ignoré. Le journal peut être consulté localement pour une intervention technique ; ne pas copier ses sujets personnels dans les logs ou le dépôt.

## Statut de validation du 15 octobre 2026

- 39 tests propres au workflow passent ; la simulation crée une entreprise et une personne fictives sans réseau.
- Portail, schéma Twenty, lecture des crédits FullEnrich et contrat No2Bounce contrôlés sans écriture.
- L'extraction visuelle a réussi sur un PDF synthétique puis sur une pièce PDF réelle du portail lue uniquement en mémoire ; un nom y a été identifié et les images temporaires ont été effacées.
- Le MCP LinkedIn répond avec une version Chrome 156 compatible installée dans `~/.cache/enrich-tender-chrome/156.0.8078.4/`. `TENDER_CHROME_PATH` permet de choisir un autre binaire compatible. Le contrôle complet renvoie `readiness.ready=true`.
- Le pilote réel a lu cinq annonces et cinq PDF, sans échec technique ni entreprise suffisamment confirmée. Le journal d'opérations est vide : aucune dépense fournisseur ni écriture CRM. Aucun PDF réel n'a été conservé.
- Le cron indépendant de 03:27 est installé ; le crontab initial a été sauvegardé et ses quatre tâches ont été conservées. La chaîne d'enrichissement et d'écriture CRM réelle reste à observer lors d'une prochaine entreprise vérifiée.

Sources du contrat fournisseur : [FullEnrich, résultat d'enrichissement](https://docs.fullenrich.com/api/v2/contact/enrich/bulk/get), [FullEnrich, dictionnaire des données](https://docs.fullenrich.com/api/v2/general/data-dictionary), [No2Bounce, API bulk](https://www.no2bounce.com/api-documentation).
