# Journal durable — ticket 01

Chaque ligne du sas reçoit un `journal_contact_id` immuable. Ce journal ne fusionne
aucune personne sur son nom ou son email ; le rapprochement des imports relève du
ticket 02. Une migration additive conserve les champs et statuts existants, crée
une sauvegarde `.before-outreach-journal.bak` et archive leur état observé sans
fabriquer de preuve de vérification.

Les déclencheurs SQLite inscrivent chaque modification dans
`outreach_journal_outbox`, dans la transaction de l'opération. Une transaction
annulée ne produit aucune preuve. Les instantanés conservent l'identité, les
coordonnées vérifiées, dates, sources, résultats et identifiants CRM. Le journal
conserve aussi les requêtes et réponses brutes des jobs fournisseur, leurs liens
avec les contacts, les imports LinkedIn et les preuves email locales. Les secrets
de configuration ne font pas partie de ces données.

L'export écrit dans `<sas>.journal.sqlite` avant d'acquitter l'outbox. Le couple
identifiant de base / numéro d'événement rend le rejeu idempotent. Une interruption
avant l'export laisse les événements dans le sas ; une interruption après le commit
de l'archive permet un rejeu sans doublon. Un échec d'export remonte une erreur et
conserve les événements à reprendre.

L'initialisation, l'import, les mutations d'enrichissement, les imports de preuves,
la synchronisation CRM et les transitions d'envoi exportent les événements après
leur commit. Les écritures SQL directes produisent aussi des événements ; exporter
explicitement avant de supprimer ou archiver le fichier du sas :

```bash
python3 -m scripts.outreach_journal --db-path sas.sqlite
```

Le retrait d'une ligne du sas conserve un instantané `contact_removed`. La lecture
suivante ne nécessite plus le fichier du sas :

```bash
python3 -m scripts.outreach_journal --journal-path sas.sqlite.journal.sqlite --contact-id IDENTIFIANT
```

Les exclusions sont conservées séparément dans `journal_exclusions` ; retirer une
exclusion du sas n'efface pas cette trace. Leur prise en compte lors d'un nouvel
import relève du ticket 02. Le journal ne modifie pas les règles d'admission email,
de rapprochement CRM ou d'approbation d'envoi. Le parcours actuel reste actif ;
aucun nouveau cron n'est installé. La purge des archives à 12 mois sera raccordée
au cycle d'exploitation, avec conservation indépendante des exclusions.
