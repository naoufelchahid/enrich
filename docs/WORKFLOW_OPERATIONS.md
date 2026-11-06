# Workflow de prospection — règles actuelles

Les règles de mailing ci-dessous remplacent les anciens modèles, la campagne
exceptionnelle Mohammedia et les anciennes cadences depuis le 6 novembre 2026.
Le journal actif est `bdd/workflow/staging.sqlite.journal.sqlite` ; Twenty est
le référentiel des personnes et entreprises admises. Les acquisitions, emails,
réveils Telegram et quatre crons Enrich restent en pause. Conserver
`WORKFLOW_STOP_AT_DRAFT=1` et `TELEGRAM_AGENT_HOOK_ENABLED=0` jusqu'à reprise explicite.

## Trois parcours indépendants

- Apify : acquisition selon un brief versionné, des plafonds de volume et des
  budgets par campagne et fournisseur. Un coût inconnu maintient l'attente.
- LinkedIn MCP : acquisition selon un brief et les mêmes contrôles de budget.
  Un seul worker à la fois, appels séquentiels. Instructions dans
  [LINKEDIN_AGENT_INSTRUCTIONS.md](LINKEDIN_AGENT_INSTRUCTIONS.md).
- Campagnes et suivi : contacts déjà admis dans Twenty, préparation V2/V5,
  critique indépendante, approbation Telegram exacte, Thunderbird et suivi.

Le planificateur de mailing ne prépare aucun lot quotidien d'acquisition.
`prepare_daily_batch` renvoie une attente vers le service compact concerné.
Les acquisitions réelles exigent un adaptateur configuré et ses sondes réussies.

## Admission des contacts

L'import conserve la source exacte et ne vérifie pas une adresse. Le parcours
est : hygiène et identité → No2Bounce → confirmation CRM → préparation du message.

Exiger une adresse professionnelle nominative, concordante avec l'identité,
avec un verdict No2Bounce `VALID` datant de moins de 90 jours sur la même adresse.
`CATCHALL` reste distinct et ne permet pas l'admission. Les messageries personnelles,
adresses jetables et génériques sont rejetées. Un domaine d'entreprise hébergé
par Google Workspace ou Microsoft 365 reste un domaine professionnel.

Si l'adresse est absente ou rejetée, FullEnrich recherche uniquement depuis une
URL LinkedIn confirmée. Toute nouvelle adresse passe ensuite No2Bounce.
LinkedIn est facultatif pour l'admission si l'adresse professionnelle est valide.
Un nom seul et une homonymie ne suffisent pas pour fusionner des identités.

Relire personne, entreprise et leur lien avant de confirmer l'admission ; conserver
les identifiants et preuves dans le journal. Transférer les champs disponibles
pris en charge par le schéma Twenty. Une erreur Kerix reste non bloquante.

## Messages et préparation

Les seuls textes initiaux sont V2 « construire votre avance » et V5 « au-delà des
chatbots », dans `scripts/email_patterns.py`. Voir [EMAIL_PATTERNS.md](EMAIL_PATTERNS.md).
Le code insère uniquement la civilité confirmée, le nom et l'entreprise, puis
un worker indépendant critique le texte exact avec les skills `cold-email`
et `marketing-psychology`. Un rejet ou un résultat incertain reste en attente.

Le pilote sélectionne au maximum 20 contacts admissibles parmi Twenty, selon la
pertinence observée du rôle. L'affectation aléatoire V2/V5 est persistée, équilibrée
au mieux, avec une même variante par entreprise. Un email initial par contact ;
aucune relance automatique. L'observation dure 14 jours après chaque envoi.
Les rendez-vous Cal.com de 30 minutes avec Cal Video sont renseignés manuellement ;
le rapport distingue réservation, rendez-vous tenu, annulation et absence, sans
désigner automatiquement de gagnant sur cet échantillon exploratoire.

```bash
python3 -m scripts.compact_pilot --journal-path bdd/workflow/staging.sqlite.journal.sqlite --pilot-id IDENTIFIANT --action create
python3 -m scripts.compact_pilot --journal-path bdd/workflow/staging.sqlite.journal.sqlite --pilot-id IDENTIFIANT --action prepare-drafts
```

Ces commandes préparent un pilote et des brouillons, sans soumission Telegram ni
envoi. Le rédacteur ordinaire traite seulement les réponses aux messages reçus.
Une demande de changement d'un texte initial reste en revue ; elle ne déclenche
pas une réécriture libre ni un retour à un ancien modèle.

## Approbation, signature et transport

Telegram présente destinataire, objet et corps exacts au compte et dans le chat
configurés. Le silence conserve l'attente. Toute modification remplace la version
précédente et exige une nouvelle approbation. Les anciens callbacks restent inactifs.
La soumission, l'approbation et le transport refusent les textes initiaux retirés
et exigent une preuve de critique indépendante du texte V2/V5 exact.

La signature HTML versionnée du compte Thunderbird est ajoutée après
« Cordialement, ». Son logo utilise une URL publique. Un changement de signature
exige une nouvelle version approuvée. Le transport relit l'éligibilité, les
exclusions, le contenu et l'approbation immédiatement avant réservation de l'envoi.

Une réponse native exige `replyToMessage.subject` et `replyToMessage.includeQuoted`.
Sans ces capacités réelles, elle attend. Une fenêtre de composition ouverte ne
constitue pas une confirmation d'envoi. La sonde de bascule du 5 novembre était
négative ; vérifier l'état actuel avant reprise.

## Cadence explicite

Le journal porte la politique `workflow_meta.mailing_policy`. Elle exige tous les
champs suivants : `timezone` (IANA), `weekdays` (0=lundi à 6=dimanche), `start_time`,
`end_time` (HH:MM, même journée) et `min_interval_minutes` (entier positif).
Aucune valeur métier n'est appliquée par défaut. Sans politique valide, le statut
est `MAILING_POLICY_REQUIRED` et aucun envoi n'est réservé.

Les créneaux partent de l'heure de début selon l'intervalle configuré. Une
approbation reçue après le début d'un créneau attend le suivant. Le délai global
se mesure entre confirmations, réponses comprises ; les réponses approuvées ont
priorité. Un créneau utilisé ne donne pas lieu à rattrapage.

```bash
python3 -m scripts.mailing_policy --journal-path bdd/workflow/staging.sqlite.journal.sqlite
```

Pour configurer une cadence explicitement décidée, fournir son JSON complet avec
`--policy-json`, prévisualiser, puis ajouter `--apply`. Cette commande journalise
la politique et n'active ni cron ni envoi. La suppression des anciennes règles
n'a défini aucune nouvelle cadence de production.

Après reprise explicitement autorisée, le planificateur peut consulter la file
chaque minute, suivre l'inbox toutes les 30 minutes et lancer la rétention chaque
nuit. Ces fréquences de contrôle n'autorisent aucun envoi hors politique.

## Suivi et incidents

Le worker d'inbox classe les messages avec une preuve textuelle exacte. Le parent
rattache l'identité et applique les effets. Un intérêt explicite peut créer une
opportunité Twenty ; une simple réponse ne suffit pas. Une réponse à rédiger
repasse par Telegram. Le worker d'analyse n'envoie rien directement.

Refus et désinscription excluent durablement l'adresse. Tout rebond active le
coupe-circuit global et une alerte. Seul l'opérateur peut reprendre après examen
avec `/resume IDENTIFIANT_INCIDENT motif`. Un rebond temporaire impose une attente
puis une revérification ; il ne justifie pas une suppression CRM. Après rebond
permanent confirmé, la suppression d'une personne exige l'absence attestée
d'échange humain et d'opportunité. Un historique ambigu maintient la personne.

Journaliser avant toute soumission fournisseur, création CRM ou envoi. Un résultat
inconnu doit être réconcilié avant toute nouvelle soumission ; absence de message
retrouvé ne vaut pas preuve de non-envoi. Trois tentatives maximum pour une erreur
temporaire certaine ; `NO_MATCH` reste sans relance automatique.

## Exploitation et vérification

Les identifiants sont dans `.env`, jamais dans les rapports publics. Les aperçus
restent en lecture seule. Le statut compact `readiness` décrit le journal local ;
les sondes réelles des services sont distinctes.

```bash
bash scripts/cron_agent_dispatcher.sh --service campaigns --action status --dry-run
python3 -m scripts.retire_mailing_rules --journal-path bdd/workflow/staging.sqlite.journal.sqlite
python3 -m pytest tests -q
git diff --check
```

`retire_mailing_rules --apply` exige la pause et aucun envoi inconnu. Il conserve
les lignes exactes en événement, rend les anciens brouillons `SUPERSEDED` et retire
la politique exceptionnelle. Il n'efface aucun envoi, preuve ou candidat. Il est
idempotent. Les règles retirées restent uniquement dans
`archives/conception/mailing-retire-2026-11-06/` comme traces documentaires.
La [bascule du journal](COMPACT_CUTOVER_RESULT_2026-11-05.md) et son
[runbook](COMPACT_CUTOVER_RUNBOOK.md) restent les références de restauration.

## Classement et nettoyage

Les chemins sont ceux du README et de `docs/CLASSEMENT.csv`. Le journal compact
garde son emplacement ; le SAS retiré reste récupérable depuis la sauvegarde de
migration et les données exactes du journal. Les commandes d'import utilisent par défaut
`bdd/sources/`. Les guides historiques décrivent une intervention datée.

Un run utilise `reports/<identifiant>/` : `selection.json`, `RESULTATS.md`,
`contacts.csv` et `preuves/`. Une exécution hors batch doit enregistrer un
`run.json` avec `contacts: [{journal_contact_id: "identifiant"}]` afin que le
nettoyage puisse vérifier tous les contacts sans déduire leur identité du nom du
dossier. Le journal conserve les événements et les identifiants fournisseurs.

Le transfert conserve le contrôle CRM dans `workflow_artifacts`, vérifie son
SHA-256 puis retire sa copie JSON après retrait du SAS. Le rapport d'un batch
est nettoyé quand tous ses contacts ont une admission et un événement de transfert
concordant, qu'aucun ne reste dans le SAS et qu'aucune opération les concernant
n'attend de réconciliation. Sinon le dossier reste disponible.
Les fichiers sont copiés intégralement et compressés avant suppression ; les
références LinkedIn deviennent `journal://artifacts/<sha256>`.

```bash
python3 -m scripts.workflow_files --backend compact --journal-path bdd/workflow/staging.sqlite.journal.sqlite --dry-run
python3 -m scripts.workflow_files --backend compact --journal-path bdd/workflow/staging.sqlite.journal.sqlite
bash scripts/cron_agent_dispatcher.sh --task retention
```

La phase `report` du batch, la rédaction et le suivi des messages rattachés à
un contact intégré appliquent déjà le nettoyage automatiquement. Un batch
terminé reste consultable via son résumé dans `workflow_batches` ; son manifeste
peut être relu depuis le journal pour rédiger un brouillon ou reprendre son suivi.
Aucun cron supplémentaire n'est installé par le classement.

Pour rechercher une preuve dans DBeaver, ouvrir le journal :

```sql
SELECT path, sha256, original_bytes, length(content_gzip) AS octets_stockes,
       saved_at FROM workflow_artifacts ORDER BY saved_at DESC;
SELECT kind, contact_id, payload, created_at FROM workflow_events
WHERE kind IN ('file_cleanup', 'run_cleanup', 'cleanup_waiting')
ORDER BY id DESC;
```

Restaurer uniquement une preuve nécessaire (le chemin d'origine est celui de la
table ; `--sha256` permet de choisir une version historique) :

```bash
python3 -m scripts.workflow_files --restore-path /chemin/original/result.json --output /tmp/preuve-result.json
```

Les sauvegardes SQLite sont compressées dans `backups/`. Décompresser celle
nécessaire vers un fichier distinct et tester sa restauration avant de remplacer
une base. Aucune sauvegarde de plusieurs centaines de Mo ne doit rester dans
un rapport. Les intermédiaires `.scratch/` sont nettoyés en fin d'intervention ;
les sources, exports humains et exclusions durables sont conservés. Les logs
globaux utiles aux incidents sont distincts des logs des runs terminés.

Les sauvegardes des migrations LinkedIn et domaine sont créées par
`scripts/workflow_backup.py`, compressées et réutilisées pour éviter une nouvelle
copie du SAS à chaque import de preuve. Les fichiers bruts temporaires sont
retirés après vérification SQLite et SHA-256.
