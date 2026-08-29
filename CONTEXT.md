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
