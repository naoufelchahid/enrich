# Domain Glossary

## Core Entities

*   **Company**: Standard Twenty object representing a business entity. Enriched directly using external APIs via isolated background jobs.
*   **Person**: Standard Twenty object representing a contact or prospect. Associated with a Company. Custom fields store the `Persona Report` and `Draft Email`.
*   **EmailQueueRecord**: A custom object introduced by our app. Represents a single scheduled email to be sent. It holds the relationship to the `Person`, the drafted content, and a `status` (e.g., Pending, Sent, Failed).
*   **SkippedLead**: A custom object for audit purposes. Stores rejected rows from the Excel file (e.g., missing critical fields) so they aren't silently lost.

## Concepts

*   **Data Curation & Mapping**: The process of normalizing the uploaded BDD6.xlsx file. Handled by a custom Twenty Front Component located in a **Full-Page Sidebar Navigation Entry**. It reads the file in-browser, detects columns, and presents a Column Mapping UI to the user to confirm mappings before parsing and sending clean JSON to the backend in batches.
*   **Data Enrichment**: Augmenting `Company` and `Person` records. Each company is processed as an isolated **Background Job** to handle rate limits safely. Uses Kerix's hidden native JSON API (`/fr/search?q=`) and a low-cost LinkedIn API. Email deliverability is validated via Node.js libraries.
*   **Persona Report & Autonomous Writer**: A fully autonomous AI step (no human-in-the-loop). Once enrichment succeeds, an AI Agent uses highly specific, user-provided prompt templates to generate the Persona Report and draft the email, pushing it directly to the `EmailQueueRecord`.
*   **Drip Automation**: A cron-based background logic function that picks exactly 1 pending `EmailQueueRecord` every 6 minutes to strictly enforce a 10-email-per-hour limit, sending it via an SMTP library. 
    * *Failure handling*: If SMTP fails, the record is marked as `Failed` with the error message attached (no automatic retry).
    * *Tracking*: No open/click tracking is implemented in V1 to protect deliverability.
*   **Workspace Variables**: Secure configuration fields defined by the App (e.g., SMTP credentials, LinkedIn API key) that you manage directly in the Twenty UI.

# Acquisition d’entreprises par rubrique

Le parcours d’acquisition part d’une rubrique Kerix, enregistre les entreprises dans le CRM et recherche des contacts au sein de ces entreprises.

## Vocabulaire

### Rubrique Kerix

Catégorie principale du répertoire Kerix choisie comme point de départ d’une acquisition. Elle peut regrouper plusieurs sous-rubriques d’activité.

### Entreprise de la rubrique

Entreprise dont la fiche apparaît dans les résultats de la rubrique. Une même entreprise peut apparaître dans plusieurs rubriques.

### Entreprise importable

Entreprise de la rubrique dont la fiche Kerix renseigne l’ICE. Le site web est facultatif.

### Adresse candidate

Adresse associée à un contact par une source de recherche, en attente de vérification de délivrabilité.

### Vérification de délivrabilité

Évaluation datée d’une adresse précise par un vérificateur, avec un résultat et une provenance.

### Contact admissible

Contact professionnel dont l’adresse est rattachée à l’entreprise par un domaine confirmé ou par une publication officielle. Le contrôle DNS est réalisé par `f0rty7even/email-verifier`. Une preuve de délivrabilité portant sur la boîte exacte reste requise pour l’admission à l’envoi. Un résultat DNS positif seul laisse le dossier en attente.

Le poste, le nom et le profil LinkedIn personnel sont facultatifs. Une adresse officielle partagée peut servir de point de contact. Si le nom n’est pas confirmé, la rédaction s’adresse à l’entreprise avec une salutation neutre.

### Entreprise couverte

Entreprise du lot pour laquelle au moins un contact admissible a été identifié. Le taux de couverture du pilote se calcule sur les 30 entreprises sélectionnées.

### Référence de campagne

Identifiant qui rattache un contact à une campagne et permet de retrouver les destinataires lors du lancement de la rédaction et de l’envoi.

### Affectation à une campagne

Lien entre une fiche contact et une campagne. Une personne peut avoir plusieurs affectations actives. Chacune porte le suivi des envois de la campagne correspondante.
